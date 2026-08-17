import json
import sqlite3
import tempfile
import time
import unittest
from importlib import resources
from pathlib import Path
from unittest.mock import patch

from vsg.config import AppConfig, validate_config
from vsg.launch_status import record_version_conflict
from vsg.project_cleanup import build_project_cleanup_plans
from vsg.startup import configure_windows_startup, windows_startup_status
from vsg.storage import CURRENT_SCHEMA_VERSION, Storage
from vsg.tray import TrayController


ROOT = Path(__file__).resolve().parents[1]


def service(
    service_id: str,
    pid: int,
    project_name: str,
    *,
    risk: str = "normal",
    score: int = 0,
    source: str = "host",
) -> dict:
    return {
        "id": service_id,
        "fingerprint": f"fingerprint-{service_id}",
        "display_name": service_id,
        "source": source,
        "runtime": "node",
        "project": {"name": project_name, "path": f"C:/projects/{project_name}"},
        "process": {"pid": pid, "name": "node.exe"},
        "risk": {"level": risk, "score": score, "scored": True},
        "metadata": {},
    }


class ProjectCleanupPlanTests(unittest.TestCase):
    def test_plan_is_read_only_and_keeps_managed_services_visible(self):
        stale = service("stale", 4101, "alpha", risk="likely_stale", score=82)
        managed = service("managed", 4102, "alpha", source="docker")
        review = service("review", 4103, "beta", risk="review", score=48)
        relationships = {
            "assessments": {
                "stale": {
                    "decision": "allowed",
                    "can_request_stop": True,
                    "requires_confirmation": "STOP 4101",
                    "impact": {"endpoint_count": 1, "client_count": 0},
                },
                "managed": {
                    "decision": "blocked",
                    "can_request_stop": False,
                    "blockers": ["Docker 管理"],
                    "impact": {"endpoint_count": 1, "client_count": 0},
                    "recommended_operations": [{"title": "docker stop", "copy_text": "docker stop abc"}],
                },
                "review": {
                    "decision": "review",
                    "can_request_stop": True,
                    "requires_confirmation": "STOP 4103",
                    "impact": {"endpoint_count": 2, "client_count": 1},
                },
            }
        }

        result = build_project_cleanup_plans([managed, review, stale], relationships)

        self.assertFalse(result["automatic_cleanup"])
        self.assertEqual(result["execution_mode"], "individual_confirmation_only")
        self.assertEqual(result["summary"]["recommended"], 1)
        self.assertEqual(result["summary"]["reviewable"], 1)
        self.assertEqual(result["summary"]["protected_or_managed"], 1)
        alpha = next(item for item in result["plans"] if item["project_name"] == "alpha")
        self.assertEqual(alpha["items"][0]["service_id"], "stale")
        self.assertEqual(alpha["items"][0]["requires_confirmation"], "STOP 4101")
        self.assertFalse(alpha["items"][1]["can_request_stop"])


class NotificationCenterTests(unittest.TestCase):
    def test_dedup_read_and_recurrence_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                first = time.time() - 2
                event = {
                    "observed_at": first,
                    "category": "security",
                    "code": "PUBLIC_BINDING",
                    "severity": "warning",
                    "title_zh": "检测到公开绑定",
                    "title_en": "Public binding detected",
                    "details": {"port": 8000},
                    "dedup_key": "security:public:8000",
                }
                first_id = storage.add_timeline_event(event, dedup_seconds=60)
                second_id = storage.add_timeline_event(
                    {**event, "observed_at": first + 1}, dedup_seconds=60
                )
                self.assertEqual(first_id, second_id)
                center = storage.notification_center()
                self.assertEqual(center["unread_count"], 1)
                self.assertEqual(center["items"][0]["occurrences"], 2)
                self.assertNotIn("dedup_key", center["items"][0])

                self.assertEqual(storage.acknowledge_notifications([first_id]), 1)
                self.assertEqual(storage.notification_center()["unread_count"], 0)
                storage.add_timeline_event(
                    {**event, "observed_at": time.time() + 2}, dedup_seconds=60
                )
                self.assertEqual(storage.notification_center()["unread_count"], 1)
            finally:
                storage.close()

    def test_v6_database_gets_acknowledged_column(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            data_dir.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(data_dir / "history.sqlite3")
            try:
                connection.execute(
                    """
                    CREATE TABLE timeline_events(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        first_seen REAL NOT NULL,
                        last_seen REAL NOT NULL,
                        category TEXT NOT NULL,
                        code TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        service_fingerprint TEXT,
                        service_id TEXT,
                        project_name TEXT,
                        agent_provider TEXT,
                        title_zh TEXT NOT NULL,
                        title_en TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        dedup_key TEXT NOT NULL,
                        occurrences INTEGER NOT NULL DEFAULT 1
                    )
                    """
                )
                connection.execute(
                    "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
                )
                connection.execute("PRAGMA user_version = 6")
                connection.commit()
            finally:
                connection.close()
            storage = Storage(data_dir)
            try:
                self.assertEqual(storage.status()["schema_version"], CURRENT_SCHEMA_VERSION)
                columns = {
                    row[1]
                    for row in storage._connection.execute(
                        "PRAGMA table_info(timeline_events)"
                    ).fetchall()
                }
                self.assertIn("acknowledged_at", columns)
            finally:
                storage.close()


class DesktopWorkflowTests(unittest.TestCase):
    def test_config_defaults_keep_integrations_off(self):
        config = AppConfig()
        self.assertFalse(config.enable_windows_tray)
        self.assertEqual(config.windows_hotkey, "disabled")
        self.assertFalse(config.onboarding_completed)
        updated = validate_config(
            {"enable_windows_tray": True, "windows_hotkey": "ctrl_alt_g"},
            config,
        )
        self.assertTrue(updated.enable_windows_tray)
        with self.assertRaisesRegex(ValueError, "windows_hotkey"):
            validate_config({"windows_hotkey": "ctrl_shift_v"}, config)

    def test_disabled_tray_never_starts_a_backend(self):
        controller = TrayController(lambda: None, lambda: None, lambda: None, lambda: 3)
        status = controller.configure(False, "disabled")
        self.assertFalse(status["enabled"])
        self.assertFalse(status["running"])
        controller.close()

    def test_source_mode_does_not_offer_portable_startup_mutation(self):
        status = windows_startup_status()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["available"])
        self.assertTrue(status["requires_explicit_confirmation"])

    def test_startup_mutation_rejects_wrong_confirmation_before_registry_access(self):
        with (
            patch("vsg.startup.os.name", "posix"),
            patch("vsg.startup._portable_command", return_value='"C:\\VSG.exe" --open'),
        ):
            with self.assertRaisesRegex(ValueError, "ENABLE STARTUP"):
                configure_windows_startup(True, "enable startup")

    def test_version_conflict_record_contains_only_safe_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = record_version_conflict(
                Path(directory),
                requested_version="0.8.5.2",
                running_version="0.8.5.1",
                running_pid=1234,
                running_port=43921,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "existing_instance_version_mismatch")
            self.assertEqual(payload["running_port"], 43921)
            self.assertNotIn("token", payload)
            self.assertNotIn("data_dir", payload)

    def test_web_assets_expose_p1_workflows(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        i18n = resources.files("vsg").joinpath("web", "i18n.js").read_text(encoding="utf-8")
        for element_id in (
            "instance-version",
            "onboarding-panel",
            "notification-button",
            "notification-dialog",
            "project-cleanup-button",
            "project-cleanup-dialog",
            "exit-button",
            "setting-windows-tray",
            "startup-toggle",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in (
            "/api/notifications",
            "/api/projects/cleanup-plans",
            "/api/onboarding/complete",
            "/api/startup/configure",
            "/api/shutdown",
        ):
            self.assertIn(endpoint, script)
        self.assertIn("Local notification center", i18n)
        self.assertIn("Project safe cleanup", i18n)
        self.assertNotIn("Ctrl+Shift+V", html)

    def test_windows_packager_preserves_a_running_extracted_portable_root(self):
        content = (ROOT / "scripts" / "Build-Portable.ps1").read_text(encoding="utf-8")
        self.assertIn('$PackageStagingRoot = Join-Path $ReleaseRoot', content)
        self.assertIn("$PublishedRuntime = Join-Path $PublishedPortableRoot", content)
        self.assertIn("if (Test-Path -LiteralPath $PublishedRuntime)", content)
        self.assertIn("The existing extracted portable directory is running", content)
        self.assertNotIn(
            "Remove-Item -LiteralPath $PortableRoot -Recurse -Force",
            content,
        )


if __name__ == "__main__":
    unittest.main()
