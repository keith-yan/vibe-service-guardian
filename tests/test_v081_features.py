import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from tests.test_capacity import hardware_fixture
from vsg.actions import verify_post_stop
from vsg.capacity import estimate_capacity
from vsg.model_catalog import load_catalog
from vsg.service_benchmark import run_service_benchmark
from vsg.service_relationships import build_service_relationships, build_stop_assessment
from vsg.storage import Storage
from vsg.workload_matrix import WorkloadMatrixManager, resource_guard


def service_fixture(
    *,
    service_id="service:demo",
    pid=200,
    port=11434,
    source="host",
    stoppable=True,
    protected=False,
):
    return {
        "id": service_id,
        "fingerprint": f"fp-{pid}",
        "source": source,
        "display_name": "demo model service",
        "runtime": "Ollama",
        "protected": protected,
        "process": {
            "pid": pid,
            "create_time": 100.0,
            "name": "ollama.exe",
            "cwd": r"E:\vibe coding\demo",
            "command": "ollama serve --token [REDACTED]",
        },
        "project": {"name": "demo", "path": r"E:\vibe coding\demo"},
        "agent": {},
        "metadata": {"model_runtime": True, "stoppable_candidate": stoppable},
        "endpoints": [
            {
                "protocol": "TCP",
                "address": "127.0.0.1",
                "port": port,
                "state": "LISTEN",
                "exposure": "loopback",
            }
        ],
    }


def snapshot_fixture(service):
    return {
        "generated_at": 123.0,
        "services": [service],
        "runtime_probes": [
            {
                "service_id": service["id"],
                "port": service["endpoints"][0]["port"],
                "health": "ready",
                "security": {"auth_posture": "unauthenticated_read"},
                "models": [{"name": "demo-model"}],
                "capacity": {"context_tokens": 4096},
                "configuration": {},
            }
        ],
        "telemetry": {
            "memory": {"used_percent": 31.0},
            "gpus": [
                {
                    "name": "GPU 0",
                    "memory_util_percent": 20.0,
                    "temperature_c": 48.0,
                }
            ],
            "sensors": {"temperatures": []},
            "disks": [{"root": "E:\\", "free_gib": 200.0}],
        },
    }


class RelationshipAndStopTests(unittest.TestCase):
    def test_local_tcp_client_creates_dependency_and_review_decision(self):
        target = service_fixture()
        connection = SimpleNamespace(
            type=socket.SOCK_STREAM,
            status=psutil.CONN_ESTABLISHED,
            pid=301,
            raddr=SimpleNamespace(ip="127.0.0.1", port=11434),
        )
        graph = build_service_relationships(
            [target],
            connection_provider=lambda: [connection],
            process_name_provider=lambda _pid: "vscode.exe",
        )
        self.assertEqual(graph["summary"]["local_dependencies"], 1)
        self.assertEqual(graph["assessments"][target["id"]]["decision"], "review")
        self.assertEqual(graph["dependencies"][0]["source_name"], "vscode.exe")
        self.assertTrue(any(edge["kind"] == "depends_on" for edge in graph["edges"]))

    def test_protected_or_managed_service_is_read_only_blocked(self):
        target = service_fixture(source="docker", stoppable=False, protected=True)
        result = build_stop_assessment(target, [])
        self.assertEqual(result["decision"], "blocked")
        self.assertFalse(result["can_request_stop"])
        self.assertGreaterEqual(len(result["blockers"]), 2)

    def test_wildcard_listener_does_not_treat_public_same_port_as_local_dependency(self):
        target = service_fixture()
        target["endpoints"][0]["address"] = "0.0.0.0"
        public = SimpleNamespace(
            type=socket.SOCK_STREAM,
            status=psutil.CONN_ESTABLISHED,
            pid=301,
            raddr=SimpleNamespace(ip="203.0.113.9", port=11434),
        )
        local = SimpleNamespace(
            type=socket.SOCK_STREAM,
            status=psutil.CONN_ESTABLISHED,
            pid=302,
            raddr=SimpleNamespace(ip="192.168.1.10", port=11434),
        )
        graph = build_service_relationships(
            [target],
            connection_provider=lambda: [public, local],
            process_name_provider=lambda pid: f"client-{pid}",
            local_address_provider=lambda: ["127.0.0.1", "192.168.1.10"],
        )
        self.assertEqual(len(graph["dependencies"]), 1)
        self.assertEqual(graph["dependencies"][0]["source_pid"], 302)

    def test_post_stop_detects_replacement_pid_without_second_stop(self):
        target = service_fixture(pid=220, port=18080)
        result = verify_post_stop(
            target,
            [220],
            observation_seconds=0,
            process_probe=lambda _pid: None,
            listener_provider=lambda: [
                {"protocol": "TCP", "address": "127.0.0.1", "port": 18080, "pid": 221}
            ],
        )
        self.assertEqual(result["outcome"], "relaunched")
        self.assertEqual(result["replacement_pids"], [221])
        self.assertFalse(result["second_stop_attempted"])


class WorkloadAndCalibrationTests(unittest.TestCase):
    def test_resource_guard_blocks_high_vram(self):
        target = service_fixture()
        snapshot = snapshot_fixture(target)
        snapshot["telemetry"]["gpus"][0]["memory_util_percent"] = 96
        guard = resource_guard(snapshot, 20)
        self.assertFalse(guard["allowed"])
        self.assertTrue(any("85%" in item for item in guard["blockers"]))

    def test_preview_is_fixed_bounded_and_explicit(self):
        target = service_fixture()
        snapshot = snapshot_fixture(target)
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                manager = WorkloadMatrixManager(
                    storage,
                    snapshot_provider=lambda: snapshot,
                    hardware_provider=hardware_fixture,
                    prediction_provider=lambda model, quant, workload: {
                        "model_id": model,
                        "quantization": quant,
                        "per_user_generation_tps": {"expected": 10},
                        "aggregate_generation_tps": 10 * workload["concurrency"],
                        "ttft_seconds": {"expected": 1},
                    },
                    low_disk_provider=lambda: 20,
                )
                plan = manager.preview(
                    target,
                    snapshot["runtime_probes"][0],
                    catalog_model_id="catalog-demo",
                    quantization="Q4_K_M",
                )
                self.assertEqual([item["concurrency"] for item in plan["steps"]], [1, 2, 4])
                self.assertEqual([item["request_count"] for item in plan["steps"]], [5, 10, 20])
                self.assertEqual(plan["total_requests"], 35)
                self.assertEqual(plan["confirmation"], "BENCHMARK PLAN 11434")
                self.assertFalse(plan["fixed_policy"]["automatic_expansion"])
                self.assertFalse(plan["fixed_policy"]["deliberate_oom"])
            finally:
                storage.close()

    def test_preview_degrades_when_capacity_prediction_fails(self):
        target = service_fixture()
        snapshot = snapshot_fixture(target)
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                manager = WorkloadMatrixManager(
                    storage,
                    snapshot_provider=lambda: snapshot,
                    hardware_provider=hardware_fixture,
                    prediction_provider=lambda *_args: (_ for _ in ()).throw(ValueError("bad model")),
                    low_disk_provider=lambda: 20,
                )
                plan = manager.preview(
                    target,
                    snapshot["runtime_probes"][0],
                    catalog_model_id="catalog-demo",
                    quantization="Q4_K_M",
                )
                self.assertEqual(len(plan["steps"]), 3)
                self.assertTrue(all(item["prediction"] is None for item in plan["steps"]))
                self.assertEqual(len(plan["prediction_limitations"]), 3)
            finally:
                storage.close()

    def test_service_benchmark_supports_twenty_sample_matrix_and_p95(self):
        target = service_fixture()
        probe = snapshot_fixture(target)["runtime_probes"][0]
        measured = {
            "success": True,
            "ttft_seconds": 0.2,
            "wall_seconds": 1.0,
            "prompt_tokens": 500,
            "completion_tokens": 20,
            "server_generation_tps": 20.0,
            "server_prompt_tps": 1000.0,
            "client_generation_tps": 20.0,
            "inter_token_latency_seconds": 0.05,
            "response_content_persisted": False,
        }
        with patch("vsg.service_benchmark._one_request", return_value=measured):
            result = run_service_benchmark(
                target,
                probe,
                {
                    "model": "demo-model",
                    "concurrency": 4,
                    "context_tokens": 2048,
                    "output_tokens": 64,
                    "confirmation": "BENCHMARK 11434",
                },
                30,
                request_count=20,
            )
        self.assertEqual(result["sample_count"], 20)
        self.assertEqual(result["request_count"], 20)
        self.assertTrue(result["ttft_p95_sample_sufficient"])
        self.assertEqual(result["ttft_p95_seconds"], 0.2)

    def test_exact_matrix_sample_calibrates_capacity_and_exposes_error(self):
        catalog = load_catalog()
        workload = {
            "total_users": 4,
            "concurrency": 4,
            "prompt_tokens": 1024,
            "context_tokens": 2048,
            "output_tokens": 64,
        }
        baseline = estimate_capacity(hardware_fixture(), catalog, workload)
        candidate = baseline["candidates"][0]
        sample = {
            "id": 99,
            "created_at": 999,
            "calibration_source": "service_matrix",
            "hardware_fingerprint": "fixture-24gb",
            "model_id": candidate["model_id"],
            "quantization": candidate["quantization"],
            "concurrency": 4,
            "requested_context_tokens": 2048,
            "requested_output_tokens": 64,
            "generation_tps": 12.3,
            "aggregate_generation_tps": 49.2,
            "ttft_seconds": 1.5,
            "prompt_tps": 800,
            "sample_count": 20,
        }
        calibrated = estimate_capacity(hardware_fixture(), catalog, workload, benchmarks=[sample])
        matched = next(item for item in calibrated["candidates"] if item["model_id"] == candidate["model_id"])
        evidence = matched["performance"]["calibration"]
        self.assertEqual(evidence["scope"], "workload_exact")
        self.assertEqual(evidence["measured_generation_tps"], 12.3)
        self.assertIsNotNone(evidence["absolute_error_percent"])
        self.assertGreaterEqual(calibrated["calibration_summary"]["calibrated_candidates"], 1)
        mismatched = estimate_capacity(
            hardware_fixture(),
            catalog,
            {**workload, "output_tokens": 32},
            benchmarks=[sample],
        )
        unmatched = next(item for item in mismatched["candidates"] if item["model_id"] == candidate["model_id"])
        self.assertIsNone(unmatched["performance"]["calibration"])


class StorageV081Tests(unittest.TestCase):
    def test_matrix_calibration_and_stop_verification_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                benchmark_id = storage.add_service_benchmark(
                    {
                        "service_fingerprint": "fp",
                        "runtime": "Ollama",
                        "port": 11434,
                        "model_name": "demo-model",
                        "concurrency": 4,
                        "requested_context_tokens": 2048,
                        "requested_output_tokens": 64,
                        "successful_requests": 20,
                        "failed_requests": 0,
                        "generation_tps": 12.3,
                        "aggregate_generation_tps": 49.2,
                        "matrix_id": "matrix-1",
                        "matrix_step_id": "concurrency",
                        "request_count": 20,
                        "hardware_fingerprint": "fixture-24gb",
                        "catalog_model_id": "model-1",
                        "quantization": "Q4_K_M",
                        "sample_count": 20,
                    }
                )
                calibration = storage.recent_service_calibrations("fixture-24gb")[0]
                self.assertEqual(calibration["id"], benchmark_id)
                self.assertEqual(calibration["matrix_step_id"], "concurrency")
                verification_id = storage.add_stop_verification(
                    {
                        "service_fingerprint": "fp",
                        "service_id": "service-1",
                        "original_pid": 200,
                        "outcome": "stopped",
                        "restart_detected": False,
                        "endpoint_verification": [],
                    }
                )
                verification = storage.recent_stop_verifications()[0]
                self.assertEqual(verification["id"], verification_id)
                self.assertEqual(verification["outcome"], "stopped")
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()
