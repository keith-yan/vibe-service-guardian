import json
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path

from vsg.app import AppState, _create_server, _get_json, _post_json
from vsg.config import load_config
from vsg.model_inventory import ModelInventoryError, scan_model_directory
from vsg.models import Endpoint, ProcessSnapshot, ProjectAttribution, ServiceRecord
from vsg.network_topology import build_network_topology
from vsg.project_rules import apply_project_manifest, apply_rules, validate_rule_payload
from vsg.storage import Storage
from vsg.telemetry import parse_nvidia_process_memory
from vsg.timeline import TimelineTracker, build_incident_view


def _gguf_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def _minimal_gguf() -> bytes:
    items = [
        ("general.name", 8, _gguf_string("Tiny Test")),
        ("general.architecture", 8, _gguf_string("llama")),
        ("general.file_type", 4, struct.pack("<I", 15)),
    ]
    body = b"".join(_gguf_string(key) + struct.pack("<I", kind) + value for key, kind, value in items)
    return b"GGUF" + struct.pack("<IQQ", 3, 0, len(items)) + body


def _service(project: Path) -> ServiceRecord:
    return ServiceRecord(
        id="host:123:1",
        fingerprint="abc123",
        source="host",
        display_name="python",
        runtime="Python",
        process=ProcessSnapshot(pid=123, name="python", cmdline=["python", "serve.py"], cwd=str(project)),
        endpoints=[Endpoint("TCP", "127.0.0.1", 8000)],
        project=ProjectAttribution(name=project.name, path=str(project), confidence=70),
    )


class V08FeatureTests(unittest.TestCase):
    def test_inventory_reads_bounded_headers_and_never_persists_absolute_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "models"
            root.mkdir()
            (root / "tiny.Q4_K_M.gguf").write_bytes(_minimal_gguf())
            header = json.dumps({"weight": {"dtype": "F16", "shape": [2, 3], "data_offsets": [0, 12]}}).encode()
            (root / "tiny.safetensors").write_bytes(len(header).to_bytes(8, "little") + header + b"\0" * 12)
            result = scan_model_directory(str(root), "SCAN MODELS")
            self.assertEqual(result["summary"]["weight_files"], 2)
            gguf = next(item for item in result["assets"] if item["format"] == "gguf")
            self.assertEqual(gguf["quantization"], "Q4_K_M")
            self.assertEqual(gguf["architecture"], "llama")
            self.assertNotIn(str(root), json.dumps(result))
            with self.assertRaises(ModelInventoryError):
                scan_model_directory(str(root), "scan models")

    def test_attribution_rule_and_project_manifest_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            (project / ".vsg.yaml").write_text(
                "version: 1\nproject_name: Manifest Project\nservices:\n  - name: API\n    port: 8000\n    expected: true\n",
                encoding="utf-8",
            )
            service = _service(project)
            self.assertTrue(apply_project_manifest(service))
            self.assertEqual(service.display_name, "API")
            rule = validate_rule_payload(
                {
                    "name": "manual",
                    "match": {"fingerprint": "abc123"},
                    "override": {"project_name": "Corrected", "agent_provider": "OpenCode"},
                },
                [str(Path(directory))],
            )
            rule["id"] = 9
            apply_rules(service, [rule])
            self.assertEqual(service.project.name, "Corrected")
            self.assertEqual(service.agent.provider, "OpenCode")

    def test_timeline_hashes_remote_endpoint_and_correlates_incident(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            tracker = TimelineTracker(storage)
            service = _service(Path(directory)).to_dict()
            telemetry = {
                "cpu": {"percent": 10},
                "memory": {"used_percent": 20},
                "gpus": [],
                "disks": [],
                "network": {"model_remote_connections": []},
            }
            now = time.time()
            tracker.observe({"generated_at": now, "services": [service]}, telemetry)
            service["endpoints"][0]["address"] = "0.0.0.0"
            service["endpoints"][0]["exposure"] = "all_interfaces"
            telemetry["network"]["model_remote_connections"] = [
                {"pid": 123, "remote_address": "8.8.8.8", "remote_port": 443, "scope": "public", "protocol": "TCP"}
            ]
            tracker.observe({"generated_at": now + 1, "services": [service]}, telemetry)
            events = storage.recent_timeline_events()
            self.assertTrue(any(item["code"] == "EXPOSURE_CHANGED" for item in events))
            serialized = json.dumps(events)
            self.assertNotIn("8.8.8.8", serialized)
            incident = build_incident_view(storage, 24 * 30)
            self.assertEqual(incident["health"], "critical")
            storage.close()

    def test_network_topology_keeps_live_remote_and_groups_listener(self):
        service = _service(Path("C:/project")).to_dict()
        topology = build_network_topology(
            [service],
            {"network": {"model_remote_connections": [{"pid": 123, "remote_address": "10.0.0.2", "remote_port": 443, "scope": "private"}]}},
        )
        self.assertEqual(topology["summary"]["listeners"], 1)
        self.assertTrue(any(node.get("live_only") for node in topology["nodes"]))

    def test_nvidia_process_memory_aggregates_multiple_devices(self):
        items = parse_nvidia_process_memory("123, 1024, uuid-a\n123, 512, uuid-b\nN/A, N/A, uuid-c\n")
        self.assertEqual(items[123]["gpu_memory_used_gib"], 1.5)
        self.assertNotIn("uuid-a", json.dumps(items))

    def test_loopback_v08_apis_require_confirmation_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory)
            data_dir = base_path / "data"
            models = base_path / "models"
            models.mkdir()
            (models / "sample.Q4_K_M.gguf").write_text("invalid test fixture", encoding="utf-8")
            state = AppState(data_dir, load_config(data_dir))
            server = _create_server(0, state)
            state.server = server
            state.collector.start()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                token = _get_json(base + "/api/bootstrap")["token"]
                scan = _post_json(
                    base + "/api/model-inventory/scan",
                    token,
                    {"root": str(models), "confirmation": "SCAN MODELS"},
                    timeout=15,
                )["scan"]
                self.assertEqual(scan["summary"]["models"], 1)
                self.assertNotIn(str(models), json.dumps(scan))
                created = _post_json(
                    base + "/api/attribution/rules",
                    token,
                    {
                        "name": "test",
                        "match": {"runtime": "Python"},
                        "override": {"project_name": "Local test"},
                    },
                )
                rule_id = created["id"]
                self.assertTrue(_get_json(base + "/api/attribution/rules")["items"])
                with self.assertRaises(urllib.error.HTTPError):
                    _post_json(
                        base + "/api/attribution/rules/delete",
                        token,
                        {"rule_id": rule_id, "confirmation": "DELETE"},
                    )
                deleted = _post_json(
                    base + "/api/attribution/rules/delete",
                    token,
                    {"rule_id": rule_id, "confirmation": f"DELETE RULE {rule_id}"},
                )
                self.assertEqual(deleted["deleted"], rule_id)
                self.assertIn("items", _get_json(base + "/api/timeline"))
                self.assertIn("health", _get_json(base + "/api/incidents"))
                self.assertIn("topology", _get_json(base + "/api/network-topology"))
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
                state.close()


if __name__ == "__main__":
    unittest.main()
