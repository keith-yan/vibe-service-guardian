import json
import socket
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_capacity import hardware_fixture
from tests.test_v081_features import service_fixture, snapshot_fixture
from vsg.config import AppConfig, validate_config
from vsg.diagnostics import create_snapshot_manifest
from vsg.log_monitor import (
    MAX_ACTIVE_WATCHES,
    LogMonitor,
    LogMonitorError,
    _service_identity_matches,
)
from vsg.model_inventory import (
    ModelInventoryError,
    _Cursor,
    _gguf_value,
    parse_model_config,
)
from vsg.models import Endpoint, ProcessSnapshot, ProjectAttribution, ServiceRecord
from vsg.project_rules import (
    AttributionRuleError,
    apply_project_manifest,
    apply_rules,
    parse_vsg_manifest,
)
from vsg.service_benchmark import (
    ServiceBenchmarkError,
    _stream_payloads,
    run_service_benchmark,
)
from vsg.storage import Storage
from vsg.trusted_nodes import _private_addresses
from vsg.workload_matrix import WorkloadMatrixError, WorkloadMatrixManager


def attributed_service(project: Path, *, protected: bool = False) -> ServiceRecord:
    return ServiceRecord(
        id="host:123:1",
        fingerprint="abc123",
        source="host",
        display_name="python",
        runtime="Python",
        process=ProcessSnapshot(
            pid=123, name="python", cmdline=["python", "serve.py"], cwd=str(project)
        ),
        endpoints=[Endpoint("TCP", "127.0.0.1", 8000)],
        project=ProjectAttribution(
            name=project.name, path=str(project), confidence=70
        ),
        protected=protected,
    )


class StreamAndPersistenceHardeningTests(unittest.TestCase):
    def test_stream_read_is_bounded_before_allocation(self):
        class OversizedResponse:
            def __init__(self):
                self.requested_sizes = []

            def readline(self, size=-1):
                self.requested_sizes.append(size)
                return b"x" * size

        response = OversizedResponse()
        with patch("vsg.service_benchmark.MAX_STREAM_BYTES", 16):
            with self.assertRaises(ServiceBenchmarkError):
                _stream_payloads(response)
        self.assertEqual(response.requested_sizes, [17])

    def test_benchmark_errors_are_redacted_before_persistence(self):
        service = service_fixture()
        probe = snapshot_fixture(service)["runtime_probes"][0]
        with patch(
            "vsg.service_benchmark._one_request",
            side_effect=RuntimeError("token=super-secret-value C:\\Users\\private\\model"),
        ):
            result = run_service_benchmark(
                service,
                probe,
                {
                    "model": "demo-model",
                    "concurrency": 1,
                    "context_tokens": 512,
                    "output_tokens": 16,
                    "confirmation": "BENCHMARK 11434",
                },
                20,
            )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("super-secret-value", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_long_benchmark_details_remain_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                storage.add_service_benchmark(
                    {
                        "service_fingerprint": "fp",
                        "runtime": "Ollama",
                        "port": 11434,
                        "concurrency": 1,
                        "requested_context_tokens": 512,
                        "requested_output_tokens": 16,
                        "successful_requests": 1,
                        "failed_requests": 0,
                        "details": {
                            "requests": [{"payload": "x" * 40_000}],
                            "matrix": {"matrix_id": "m1", "matrix_step_id": "baseline"},
                        },
                    }
                )
                details = storage.recent_service_benchmarks()[0]["details"]
                self.assertTrue(details["_truncated"])
                self.assertEqual(details["matrix"]["matrix_id"], "m1")
                self.assertEqual(details["requests_omitted"], 1)
            finally:
                storage.close()

    def test_cleanup_includes_model_benchmarks(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                storage.add_model_benchmark(
                    {
                        "hardware_fingerprint": "hardware",
                        "model_id": "model",
                        "model_file_name": "model.gguf",
                        "model_file_size_bytes": 1,
                        "quantization": "Q4_K_M",
                        "runtime": "llama-bench",
                        "generation_tps": 1.0,
                    }
                )
                with storage._lock, storage._connection:
                    storage._connection.execute(
                        "UPDATE model_benchmarks SET created_at = ?", (time.time() - 3 * 86400,)
                    )
                storage.cleanup(1)
                self.assertEqual(storage.recent_model_benchmarks(), [])
            finally:
                storage.close()


class AttributionAndInventoryHardeningTests(unittest.TestCase):
    def test_json_manifest_version_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / ".vsg.yaml"
            manifest.write_text('{"version":2,"services":[]}', encoding="utf-8")
            with self.assertRaises(AttributionRuleError):
                parse_vsg_manifest(manifest)

    def test_rules_and_manifest_cannot_remove_existing_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".vsg.yaml").write_text(
                "version: 1\nservices:\n  - port: 8000\n    protected: false\n",
                encoding="utf-8",
            )
            service = attributed_service(project, protected=True)
            self.assertTrue(apply_project_manifest(service))
            self.assertTrue(service.protected)
            apply_rules(
                service,
                [
                    {
                        "name": "cannot downgrade",
                        "priority": 100,
                        "enabled": True,
                        "match": {"fingerprint": "abc123"},
                        "override": {"protected": False},
                    }
                ],
            )
            self.assertTrue(service.protected)

    def test_nested_gguf_arrays_have_a_depth_limit(self):
        nested_array = struct.pack("<IQ", 9, 1) * 20
        with self.assertRaises(ModelInventoryError):
            _gguf_value(_Cursor(nested_array), 9, keep=False)

    def test_model_config_metadata_is_bounded_and_modelfile_path_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "architectures": ["x" * 500],
                        "quantization_config": {
                            "quant_method": "gptq",
                            "private_path": "C:/private/model",
                        },
                    }
                ),
                encoding="utf-8",
            )
            parsed = parse_model_config(config)
            self.assertEqual(len(parsed["architecture"]), 120)
            self.assertNotIn("private_path", parsed["quantization_config"])
            modelfile = root / "Modelfile"
            modelfile.write_text("FROM C:\\private\\models\\demo.gguf\n", encoding="utf-8")
            manifest = parse_model_config(modelfile)
            self.assertEqual(manifest["base_model"], "demo.gguf")
            self.assertNotIn("private", json.dumps(manifest))

    def test_inventory_history_has_a_final_size_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                storage.add_model_inventory_scan(
                    {
                        "created_at": time.time(),
                        "root_name": "models",
                        "root_hash": "root",
                        "summary": {"assets": 1},
                        "assets": [{"file_name": "x", "oversized": "x" * 2_100_000}],
                        "models": [],
                        "duplicates": [],
                        "warnings": [],
                    }
                )
                stored = storage.recent_model_inventory_scans()[0]
                self.assertEqual(stored["assets"], [])
                self.assertTrue(stored["history_assets_omitted"])
            finally:
                storage.close()


class SnapshotAndLogHardeningTests(unittest.TestCase):
    def test_snapshot_deduplicates_and_never_copies_sensitive_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            sensitive = root / ".env"
            config.write_text('{"value":1}', encoding="utf-8")
            sensitive.write_text("TOKEN=secret", encoding="utf-8")
            result = create_snapshot_manifest(
                [str(config), str(config), str(sensitive)], root / "data", "SNAPSHOT"
            )
            self.assertEqual(len(result["items"]), 2)
            secret_item = next(item for item in result["items"] if item["file_name"] == ".env")
            self.assertTrue(secret_item["sensitive_file"])
            self.assertFalse(secret_item["rollback_available"])

    def test_snapshot_has_a_total_hash_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(b"1234")
            second.write_bytes(b"5678")
            with patch("vsg.diagnostics.MAX_SNAPSHOT_TOTAL_HASH_BYTES", 5):
                result = create_snapshot_manifest(
                    [str(first), str(second)], root / "data", "SNAPSHOT"
                )
            self.assertEqual(result["items"][0]["sha256_status"], "measured")
            self.assertEqual(result["items"][1]["sha256_status"], "skipped_total_budget")

    def test_missing_process_create_time_fails_closed(self):
        watch = {"service_fingerprint": "fp", "pid": 8, "process_create_time": 100.0}
        service = {"fingerprint": "fp", "process": {"pid": 8, "create_time": None}}
        self.assertFalse(_service_identity_matches(watch, service))

    def test_log_errors_do_not_expose_absolute_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "private-server.log"
            log.write_text("ready\n", encoding="utf-8")
            storage = Storage(root / "data")
            monitor = LogMonitor(storage)
            service = service_fixture(pid=4321)
            service["process"]["create_time"] = 100.0
            service["runtime"] = "Ollama"
            try:
                monitor.start_watch(service, str(log), "WATCH 4321")
                log.unlink()
                status = monitor.poll([service])
                error = status["watches"][0]["last_error"]
                self.assertNotIn(str(root), error)
                self.assertEqual(error, "OSError: 日志文件不可访问")
            finally:
                storage.close()

    def test_log_watch_count_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "server.log"
            log.write_text("ready\n", encoding="utf-8")
            storage = Storage(root / "data")
            monitor = LogMonitor(storage)
            active = [
                {"service_fingerprint": f"other-{index}", "path": str(root / f"{index}.log")}
                for index in range(MAX_ACTIVE_WATCHES)
            ]
            try:
                with patch.object(storage, "log_watches", return_value=active):
                    with self.assertRaises(LogMonitorError):
                        monitor.start_watch(
                            service_fixture(pid=4321), str(log), "WATCH 4321"
                        )
            finally:
                storage.close()


class LifecycleAndNetworkHardeningTests(unittest.TestCase):
    def test_workload_audit_failure_does_not_leave_active_job(self):
        service = service_fixture()
        snapshot = snapshot_fixture(service)
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = WorkloadMatrixManager(
                storage,
                snapshot_provider=lambda: snapshot,
                hardware_provider=hardware_fixture,
                prediction_provider=lambda *_args: None,
                low_disk_provider=lambda: 20,
            )

            def measured(*args, **_kwargs):
                body = args[2]
                return {
                    "service_fingerprint": service["fingerprint"],
                    "service_id": service["id"],
                    "runtime": service["runtime"],
                    "port": 11434,
                    "model_name": "demo-model",
                    "concurrency": int(body["concurrency"]),
                    "requested_context_tokens": int(body["context_tokens"]),
                    "requested_output_tokens": int(body["output_tokens"]),
                    "successful_requests": 1,
                    "failed_requests": 0,
                    "generation_tps": 10.0,
                    "aggregate_generation_tps": 10.0,
                    "sample_count": 1,
                    "details": {},
                }

            try:
                plan = manager.preview(
                    service,
                    snapshot["runtime_probes"][0],
                    catalog_model_id=None,
                    quantization=None,
                )
                with (
                    patch("vsg.workload_matrix.run_service_benchmark", side_effect=measured),
                    patch.object(storage, "add_audit", side_effect=RuntimeError("db busy")),
                    self.assertLogs("vsg", level="WARNING") as captured,
                ):
                    job = manager.start(plan["plan_id"], plan["confirmation"])
                    deadline = time.time() + 5
                    status = manager.status(job["job_id"])
                    while status["job"]["status"] not in {
                        "completed",
                        "failed",
                        "cancelled",
                        "guard_stopped",
                        "identity_changed",
                    } and time.time() < deadline:
                        time.sleep(0.02)
                        status = manager.status(job["job_id"])
                self.assertEqual(status["job"]["status"], "completed")
                self.assertIsNone(status["active_job_id"])
                self.assertTrue(any("audit failed" in item for item in captured.output))
            finally:
                manager.close()
                storage.close()

    def test_thread_start_failure_rolls_back_active_state(self):
        service = service_fixture()
        snapshot = snapshot_fixture(service)
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = WorkloadMatrixManager(
                storage,
                snapshot_provider=lambda: snapshot,
                hardware_provider=hardware_fixture,
                prediction_provider=lambda *_args: None,
                low_disk_provider=lambda: 20,
            )
            try:
                plan = manager.preview(
                    service,
                    snapshot["runtime_probes"][0],
                    catalog_model_id=None,
                    quantization=None,
                )
                with patch("vsg.workload_matrix.threading.Thread.start", side_effect=RuntimeError("no thread")):
                    with self.assertRaises(WorkloadMatrixError):
                        manager.start(plan["plan_id"], plan["confirmation"])
                self.assertIsNone(manager.status()["active_job_id"])
                self.assertIn(plan["plan_id"], manager._previews)
            finally:
                manager.close()
                storage.close()

    def test_unspecified_addresses_are_not_trusted_nodes(self):
        unresolved = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("0.0.0.0", 43921))
        ]
        with patch("vsg.trusted_nodes.socket.getaddrinfo", return_value=unresolved):
            with self.assertRaises(ValueError):
                _private_addresses("node.local", 43921)
        with self.assertRaises(ValueError):
            validate_config(
                {"trusted_nodes": ["http://0.0.0.0:43921"]},
                AppConfig(project_roots=["C:\\projects"]),
            )

    def test_protected_name_count_and_length_are_bounded(self):
        base = AppConfig(project_roots=["C:\\projects"])
        with self.assertRaises(ValueError):
            validate_config({"protected_names": ["x"] * 257}, base)
        with self.assertRaises(ValueError):
            validate_config({"protected_names": ["x" * 129]}, base)


if __name__ == "__main__":
    unittest.main()
