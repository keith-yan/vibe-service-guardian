import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from tests.fixtures.fake_model_runtime import FakeModelRuntime
from vsg.service_benchmark import run_service_benchmark
from vsg.storage import Storage
from vsg.workload_matrix import TERMINAL_JOB_STATES, WorkloadMatrixManager


def model_service(port: int, runtime: str = "Ollama") -> dict[str, object]:
    return {
        "id": f"fixture-model:{port}",
        "fingerprint": f"fixture-model-fingerprint-{port}",
        "source": "host",
        "runtime": runtime,
        "process": {"pid": os.getpid(), "create_time": 1.0},
        "metadata": {"model_runtime": True},
        "endpoints": [
            {"protocol": "TCP", "address": "127.0.0.1", "port": port}
        ],
    }


def model_probe(service: dict[str, object], port: int) -> dict[str, object]:
    return {
        "service_id": service["id"],
        "port": port,
        "health": "ready",
        "security": {"auth_posture": "unauthenticated_read"},
        "models": [{"name": "fixture-model:q4"}],
        "capacity": {"context_tokens": 4096},
        "configuration": {},
    }


def snapshot(service: dict[str, object], probe: dict[str, object]) -> dict[str, object]:
    return {
        "generated_at": time.time(),
        "services": [service],
        "runtime_probes": [probe],
        "telemetry": {
            "memory": {"used_percent": 30.0},
            "gpus": [
                {
                    "name": "Fixture GPU",
                    "memory_util_percent": 20.0,
                    "temperature_c": 45.0,
                }
            ],
            "sensors": {"temperatures": []},
            "disks": [{"root": "fixture", "free_gib": 200.0}],
        },
    }


def prediction(_model: str, _quantization: str, workload: dict[str, object]):
    concurrency = int(workload["concurrency"])
    return {
        "per_user_generation_tps": {"expected": 20.0},
        "aggregate_generation_tps": 20.0 * concurrency,
        "ttft_seconds": {"expected": 0.05},
    }


def wait_for_terminal(manager: WorkloadMatrixManager, job_id: str, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.status(job_id)["job"]
        if job and job.get("status") in TERMINAL_JOB_STATES:
            return job
        time.sleep(0.02)
    raise AssertionError("workload matrix did not reach a terminal state")


class BenchmarkMatrixE2ETests(unittest.TestCase):
    def test_fixed_matrix_sends_35_real_http_requests_and_persists_metrics_only(self):
        with FakeModelRuntime(delay_seconds=0.01) as runtime:
            service = model_service(runtime.port)
            probe = model_probe(service, runtime.port)
            current_snapshot = snapshot(service, probe)
            with tempfile.TemporaryDirectory() as directory:
                storage = Storage(Path(directory))
                manager = WorkloadMatrixManager(
                    storage,
                    snapshot_provider=lambda: current_snapshot,
                    hardware_provider=lambda: {
                        "hardware_fingerprint": "fixture-hardware"
                    },
                    prediction_provider=prediction,
                    low_disk_provider=lambda: 20.0,
                )
                try:
                    plan = manager.preview(
                        service,
                        probe,
                        catalog_model_id="fixture-catalog-model",
                        quantization="Q4_K_M",
                    )
                    started = manager.start(plan["plan_id"], plan["confirmation"])
                    job = wait_for_terminal(manager, started["job_id"])

                    self.assertEqual(job["status"], "completed")
                    self.assertEqual(job["completed_steps"], 3)
                    self.assertEqual(runtime.request_count, 35)
                    self.assertGreaterEqual(runtime.max_active_requests, 4)
                    self.assertEqual(set(runtime.paths), {"/api/generate"})
                    self.assertEqual(
                        sum(int(item["request_count"]) for item in job["results"]),
                        35,
                    )
                    self.assertTrue(
                        all(int(item["successful_requests"]) > 0 for item in job["results"])
                    )

                    stored = storage.recent_service_benchmarks(10)
                    self.assertEqual(len(stored), 3)
                    self.assertEqual(
                        sum(int(item["request_count"]) for item in stored), 35
                    )
                    serialized = json.dumps(stored, ensure_ascii=False)
                    self.assertNotIn("fixture-secret-response", serialized)
                    self.assertTrue(
                        all(
                            item["details"]["response_content_persisted"] is False
                            for item in stored
                        )
                    )
                    self.assertTrue(
                        all(item["prediction_error"] is not None for item in job["results"])
                    )
                finally:
                    self.assertTrue(manager.close())
                    storage.close()

    def test_matrix_cancellation_finishes_inflight_wave_and_stops_later_waves(self):
        with FakeModelRuntime(delay_seconds=0.15) as runtime:
            service = model_service(runtime.port)
            probe = model_probe(service, runtime.port)
            current_snapshot = snapshot(service, probe)
            with tempfile.TemporaryDirectory() as directory:
                storage = Storage(Path(directory))
                manager = WorkloadMatrixManager(
                    storage,
                    snapshot_provider=lambda: current_snapshot,
                    hardware_provider=lambda: {
                        "hardware_fingerprint": "fixture-hardware"
                    },
                    prediction_provider=prediction,
                    low_disk_provider=lambda: 20.0,
                )
                try:
                    plan = manager.preview(
                        service,
                        probe,
                        catalog_model_id="fixture-catalog-model",
                        quantization="Q4_K_M",
                    )
                    started = manager.start(plan["plan_id"], plan["confirmation"])
                    deadline = time.monotonic() + 5
                    while runtime.request_count < 1 and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertGreaterEqual(runtime.request_count, 1)
                    manager.cancel(started["job_id"])
                    job = wait_for_terminal(manager, started["job_id"])

                    self.assertEqual(job["status"], "cancelled")
                    self.assertTrue(job["cancel_requested"])
                    self.assertLess(runtime.request_count, 35)
                    self.assertLess(job["completed_steps"], 3)
                finally:
                    self.assertTrue(manager.close())
                    storage.close()

    def test_runtime_auth_and_oom_errors_are_bounded_and_redacted(self):
        for mode in ("auth", "oom"):
            with self.subTest(mode=mode), FakeModelRuntime(
                mode=mode, delay_seconds=0
            ) as runtime:
                service = model_service(runtime.port)
                probe = model_probe(service, runtime.port)
                result = run_service_benchmark(
                    service,
                    probe,
                    {
                        "model": "fixture-model:q4",
                        "concurrency": 1,
                        "context_tokens": 128,
                        "output_tokens": 8,
                        "confirmation": f"BENCHMARK {runtime.port}",
                    },
                    30.0,
                )
                self.assertEqual(result["successful_requests"], 0)
                self.assertEqual(result["failed_requests"], 1)
                self.assertNotIn(
                    "fixture-secret-value", json.dumps(result, ensure_ascii=False)
                )
                if mode == "oom":
                    self.assertTrue(result["oom_observed"])
                else:
                    self.assertFalse(result["oom_observed"])
                    self.assertIn("不读取或代填 API Key", result["errors"][0])

    def test_openai_compatible_runtime_uses_sse_contract(self):
        with FakeModelRuntime(delay_seconds=0) as runtime:
            service = model_service(runtime.port, runtime="vLLM")
            probe = model_probe(service, runtime.port)
            result = run_service_benchmark(
                service,
                probe,
                {
                    "model": "fixture-model:q4",
                    "concurrency": 1,
                    "context_tokens": 128,
                    "output_tokens": 8,
                    "confirmation": f"BENCHMARK {runtime.port}",
                },
                30.0,
            )
            self.assertEqual(result["successful_requests"], 1)
            self.assertEqual(result["verified_prompt_tokens_min"], 512)
            self.assertEqual(runtime.paths, ["/v1/completions"])
            self.assertNotIn(
                "fixture-secret-response", json.dumps(result, ensure_ascii=False)
            )


if __name__ == "__main__":
    unittest.main()
