import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from vsg.app import AppState, _create_server, _get_json, _post_json
from vsg.config import AppConfig, load_config
from vsg.containers import DOCKER_INSPECT_FORMAT, scan_docker
from vsg.models import Endpoint, ProcessSnapshot, ProjectAttribution, ServiceRecord
from vsg.project_rules import apply_rules, validate_rule_payload
from vsg.rule_packs import (
    RulePackError,
    build_rule_pack,
    preview_rule_pack,
    rebind_imported_rule,
    validate_rule_pack,
)
from vsg.scanner import Scanner, ownership_signature, redacted_command_hash
from vsg.stale import assess_service
from vsg.storage import CURRENT_SCHEMA_VERSION, Storage


def service_record(root: Path, fingerprint: str = "fp-current") -> ServiceRecord:
    process = ProcessSnapshot(
        pid=200,
        ppid=100,
        name="node",
        exe=str(root / "node.exe"),
        cmdline=["node", "server.js", "--port", "3000"],
        cwd=str(root),
        create_time=100.0,
    )
    return ServiceRecord(
        id="host:200:100000",
        fingerprint=fingerprint,
        source="host",
        display_name="node",
        runtime="Node.js",
        process=process,
        endpoints=[Endpoint("TCP", "127.0.0.1", 3000)],
        project=ProjectAttribution(name=root.name, path=str(root), confidence=80),
        metadata={
            "ownership_signature": ownership_signature(process),
            "command_hash": redacted_command_hash(process.cmdline),
        },
    )


class AttributionRuleEvolutionTests(unittest.TestCase):
    def test_v5_rule_migrates_to_schema_six_with_auditable_version(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            data_dir.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(data_dir / "history.sqlite3")
            try:
                connection.execute(
                    """
                    CREATE TABLE attribution_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        name TEXT NOT NULL,
                        priority INTEGER NOT NULL DEFAULT 100,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        match_json TEXT NOT NULL,
                        override_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO attribution_rules(
                        created_at, updated_at, name, priority, enabled,
                        match_json, override_json
                    ) VALUES(1, 2, 'legacy', 100, 1, ?, ?)
                    """,
                    (
                        json.dumps({"ownership_signature": "a" * 64}),
                        json.dumps({"project_name": "demo"}),
                    ),
                )
                connection.execute("PRAGMA user_version = 5")
                connection.commit()
            finally:
                connection.close()

            storage = Storage(data_dir)
            try:
                rule = storage.attribution_rules()[0]
                self.assertEqual(storage.status()["schema_version"], CURRENT_SCHEMA_VERSION)
                self.assertEqual(rule["scope"], "standard")
                self.assertEqual(rule["source"], "migration")
                versions = storage.attribution_rule_versions(rule["id"])
                self.assertEqual(len(versions), 1)
                self.assertEqual(versions[0]["action"], "migration")
            finally:
                storage.close()

    def test_conflicts_resolve_by_specificity_then_latest_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            record = service_record(Path(directory))
            signature = record.metadata["ownership_signature"]
            older = {
                "id": 1,
                "updated_at": 10,
                "scope": "standard",
                "match": {"ownership_signature": signature},
                "override": {"project_name": "old"},
                "enabled": True,
            }
            newer = {
                "id": 2,
                "updated_at": 20,
                "scope": "standard",
                "match": {"ownership_signature": signature},
                "override": {"project_name": "new"},
                "enabled": True,
            }
            apply_rules(record, [older, newer])
            self.assertEqual(record.project.name, "new")
            self.assertEqual(record.metadata["attribution_rule_conflict"]["winner_rule_id"], "2")

            instance = {
                "id": 3,
                "updated_at": 1,
                "scope": "instance",
                "match": {"fingerprint": record.fingerprint},
                "override": {"project_name": "instance", "protected": False},
                "enabled": True,
            }
            record.protected = True
            apply_rules(record, [newer, instance])
            self.assertEqual(record.project.name, "instance")
            self.assertTrue(record.protected)

    def test_versions_are_bounded_and_restore_creates_a_new_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root)
            try:
                rule = validate_rule_payload(
                    {
                        "name": "v1",
                        "match": {"fingerprint": "fp"},
                        "override": {"project_name": "p1"},
                    },
                    [directory],
                )
                rule_id = storage.add_attribution_rule(rule)
                for number in range(2, 8):
                    rule = {**rule, "name": f"v{number}", "override": {"project_name": f"p{number}"}}
                    storage.update_attribution_rule(rule_id, rule)
                versions = storage.attribution_rule_versions(rule_id)
                self.assertEqual([item["version"] for item in versions], [7, 6, 5, 4, 3])
                restored = storage.restore_attribution_rule(rule_id, 3)
                self.assertIsNotNone(restored)
                assert restored is not None
                self.assertEqual(restored["name"], "v3")
                self.assertEqual(restored["revision"], 8)
                self.assertEqual(
                    [item["version"] for item in storage.attribution_rule_versions(rule_id)],
                    [8, 7, 6, 5, 4],
                )
            finally:
                storage.close()

    def test_hits_and_correction_rate_use_unique_service_episodes(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                rule_id = storage.add_attribution_rule(
                    validate_rule_payload(
                        {
                            "name": "standard",
                            "match": {"ownership_signature": "a" * 64},
                            "override": {"project_name": "demo"},
                        },
                        [directory],
                    )
                )
                evaluation = {
                    "episode_key": "episode-1",
                    "service_fingerprint": "fp-1",
                    "source": "host",
                    "initial": {"project_name": None},
                    "winner_rule_id": rule_id,
                }
                storage.record_attribution_evaluations([evaluation])
                storage.record_attribution_evaluations([evaluation])
                self.assertEqual(storage.attribution_rules()[0]["hit_count"], 1)
                storage.record_attribution_correction(
                    episode_key="episode-1",
                    service_fingerprint="fp-1",
                    before={"project_name": "demo"},
                    after={"project_name": "corrected"},
                    matched_rule_ids=[rule_id],
                )
                storage.record_attribution_correction(
                    episode_key="episode-1",
                    service_fingerprint="fp-1",
                    before={"project_name": "corrected"},
                    after={"project_name": "again"},
                    matched_rule_ids=[rule_id],
                )
                self.assertEqual(storage.attribution_rules()[0]["override_count"], 1)

                second = {**evaluation, "episode_key": "episode-2", "service_fingerprint": "fp-2"}
                storage.record_attribution_evaluations([second])
                storage.record_attribution_correction(
                    episode_key="episode-2",
                    service_fingerprint="fp-2",
                    before={"project_name": "demo"},
                    after={"project_name": "corrected"},
                    matched_rule_ids=[rule_id],
                )
                stored_rule = storage.attribution_rules()[0]
                self.assertEqual(stored_rule["override_count"], 2)
                self.assertTrue(stored_rule["needs_review"])
                metrics = storage.attribution_metrics(30)
                self.assertEqual(metrics["episodes"], 2)
                self.assertEqual(metrics["corrected_episodes"], 2)
                self.assertEqual(metrics["correction_rate"], 1.0)
                self.assertEqual(metrics["denominator"], "unique_service_episodes")
            finally:
                storage.close()


class RulePackTests(unittest.TestCase):
    def test_empty_rule_set_cannot_create_a_misleading_pack(self):
        with self.assertRaisesRegex(RulePackError, "没有可导出"):
            build_rule_pack([], "0.8.5")

    def test_export_strips_paths_and_import_requires_explicit_rebind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = service_record(root)
            rule = {
                "id": 1,
                "name": "portable",
                "priority": 100,
                "enabled": True,
                "scope": "standard",
                "match": {
                    "ownership_signature": record.metadata["ownership_signature"],
                    "cwd_prefix": str(root),
                    "command_contains": "--secret local",
                },
                "override": {
                    "project_name": "demo",
                    "project_path": str(root),
                    "note": "local decision",
                },
            }
            pack = build_rule_pack([rule], "0.8.5")
            exported = pack["rules"][0]
            self.assertNotIn("cwd_prefix", exported["match"])
            self.assertNotIn("command_contains", exported["match"])
            self.assertNotIn("project_path", exported["override"])
            self.assertNotIn("note", exported["override"])
            self.assertTrue(exported["portability"]["requires_explicit_rebind"])

            validated = validate_rule_pack(pack)
            preview = preview_rule_pack(pack, [record.to_dict()], [])
            self.assertEqual(preview["items"][0]["status"], "exact_candidate")
            self.assertTrue(preview["items"][0]["requires_explicit_rebind"])
            rebound = rebind_imported_rule(
                validated["rules"][0], record.to_dict(), "strict", [directory]
            )
            self.assertEqual(rebound["scope"], "strict")
            self.assertEqual(
                rebound["match"]["redacted_command_hash"], record.metadata["command_hash"]
            )

            tampered = json.loads(json.dumps(pack))
            tampered["rules"][0]["name"] = "tampered"
            with self.assertRaisesRegex(RulePackError, "完整性校验失败"):
                validate_rule_pack(tampered)

            recursive: dict[str, object] = {}
            recursive["self"] = recursive
            with self.assertRaisesRegex(RulePackError, "无法序列化或嵌套过深"):
                validate_rule_pack(recursive)


class AttributionRuleApiTests(unittest.TestCase):
    def test_loopback_rule_management_is_confirmed_versioned_and_exportable(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            state = AppState(data_dir, load_config(data_dir))
            server = _create_server(0, state)
            state.server = server
            state.collector.start()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                token = _get_json(base + "/api/bootstrap")["token"]
                created = _post_json(
                    base + "/api/attribution/rules",
                    token,
                    {
                        "name": "api-rule",
                        "match": {"runtime": "Python"},
                        "override": {"project_name": "Before"},
                    },
                )
                rule_id = created["id"]
                with self.assertRaises(urllib.error.HTTPError):
                    _post_json(
                        base + "/api/attribution/rules/update",
                        token,
                        {
                            "rule_id": rule_id,
                            "rule": {
                                "name": "api-rule-v2",
                                "match": {"runtime": "Python"},
                                "override": {"project_name": "After"},
                            },
                            "confirmation": "UPDATE",
                        },
                    )
                updated = _post_json(
                    base + "/api/attribution/rules/update",
                    token,
                    {
                        "rule_id": rule_id,
                        "rule": {
                            "name": "api-rule-v2",
                            "match": {"runtime": "Python"},
                            "override": {"project_name": "After"},
                        },
                        "confirmation": f"UPDATE RULE {rule_id}",
                    },
                )["rule"]
                self.assertEqual(updated["revision"], 2)
                with urllib.request.urlopen(
                    base + f"/api/attribution/rules/versions?rule_id={rule_id}", timeout=3
                ) as response:
                    versions = json.loads(response.read().decode("utf-8"))["items"]
                self.assertEqual([item["version"] for item in versions], [2, 1])
                disabled = _post_json(
                    base + "/api/attribution/rules/status",
                    token,
                    {
                        "rule_id": rule_id,
                        "enabled": False,
                        "confirmation": f"DISABLE RULE {rule_id}",
                    },
                )["rule"]
                self.assertFalse(disabled["enabled"])
                exported = _post_json(
                    base + "/api/attribution/rules/export",
                    token,
                    {"confirmation": "EXPORT RULES"},
                )["export"]
                self.assertEqual(exported["kind"], "vsg-attribution-rule-pack")
                current = service_record(Path(directory))
                current.runtime = "Python"
                snapshot = {"services": [current.to_dict()]}
                with patch.object(state.collector, "get_snapshot", return_value=snapshot):
                    preview = _post_json(
                        base + "/api/attribution/rules/import/preview",
                        token,
                        {"pack": exported},
                    )["preview"]
                    self.assertEqual(preview["summary"]["exact_candidates"], 1)
                    phrase = f"IMPORT RULES 1 {preview['digest'][:12]}"
                    imported = _post_json(
                        base + "/api/attribution/rules/import",
                        token,
                        {
                            "pack": exported,
                            "bindings": [
                                {
                                    "index": 0,
                                    "service_id": current.id,
                                    "scope": "standard",
                                }
                            ],
                            "confirmation": phrase,
                        },
                    )
                self.assertEqual(len(imported["rule_ids"]), 1)
                restored = _post_json(
                    base + "/api/attribution/rules/restore",
                    token,
                    {
                        "rule_id": rule_id,
                        "version": 1,
                        "confirmation": f"RESTORE RULE {rule_id} VERSION 1",
                    },
                )["rule"]
                self.assertEqual(restored["revision"], 4)
                self.assertEqual(restored["name"], "api-rule")
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
                state.close()


class ManagedProcessTests(unittest.TestCase):
    def test_agent_child_inherits_attribution_and_stop_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = ProcessSnapshot(
                pid=100,
                ppid=1,
                name="opencode",
                exe=str(root / "opencode"),
                cmdline=["opencode"],
                cwd=str(root),
                create_time=50.0,
            )
            child = ProcessSnapshot(
                pid=200,
                ppid=100,
                name="node",
                exe=str(root / "node"),
                cmdline=["node", "server.js"],
                cwd=str(root),
                create_time=60.0,
            )
            scanner = Scanner(AppConfig(project_roots=[directory]))
            with (
                patch.object(scanner, "_sessions", return_value=[]),
                patch.object(scanner, "_windows_service_map", return_value={}),
            ):
                services = scanner._host_services(
                    {100: parent, 200: child},
                    {200: [Endpoint("TCP", "127.0.0.1", 3000)]},
                    {},
                    {100},
                )
            managed = next(item for item in services if item.process.pid == 200)
            self.assertEqual(managed.agent.provider, "OpenCode")
            self.assertEqual(managed.agent.kind, "managed_child")
            self.assertTrue(managed.protected)
            self.assertTrue(managed.metadata["agent_managed_child"])
            self.assertFalse(managed.metadata["stoppable_candidate"])
            self.assertEqual(assess_service(managed, scanner.config).level, "not_scored")

    def test_docker_inspect_uses_fixed_allowlist_and_never_requests_environment(self):
        container_id = "c" * 64
        ps_row = json.dumps(
            {
                "ID": container_id,
                "Image": "postgres:17",
                "Names": "db",
                "Ports": "127.0.0.1:5432->5432/tcp",
                "State": "running",
                "Status": "Up",
            }
        )
        inspect_line = "\t".join(
            json.dumps(value)
            for value in (
                container_id,
                1234,
                False,
                0,
                "no",
                None,
                None,
                None,
                None,
            )
        )
        calls: list[list[str]] = []

        def fake_run(command, timeout=3.0):
            calls.append(command)
            return (0, ps_row, "") if command[1] == "ps" else (0, inspect_line, "")

        with (
            patch("vsg.containers.shutil.which", return_value="docker"),
            patch("vsg.containers._run", side_effect=fake_run),
        ):
            services, status = scan_docker()
        self.assertEqual(len(services), 1)
        inspect_command = calls[1]
        self.assertIn("--format", inspect_command)
        self.assertIn(DOCKER_INSPECT_FORMAT, inspect_command)
        self.assertNotIn(".Config.Env", " ".join(inspect_command))
        self.assertEqual(status["metadata_policy"], "fixed_allowlist_no_environment")


if __name__ == "__main__":
    unittest.main()
