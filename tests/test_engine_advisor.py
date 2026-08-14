import unittest

from vsg.engine_advisor import compare_service_benchmarks, recommend_engines


def hardware(platform: str, vendor: str, compute: float | None = None) -> dict:
    return {
        "platform": {"key": platform},
        "gpus": [
            {
                "vendor": vendor,
                "name": f"{vendor} GPU",
                "memory_total_gib": 24,
                "compute_capability": compute,
            }
        ],
    }


class EngineAdvisorTests(unittest.TestCase):
    def test_windows_amd_does_not_recommend_native_vllm(self):
        result = recommend_engines(
            hardware("windows", "AMD"),
            [{"id": "wsl-runtime", "installed": False}],
            {"model_format": "gguf", "priority": "balanced", "concurrency": 2},
        )
        ids = [item["id"] for item in result["top3"]]
        self.assertIn("llama.cpp", ids)
        vllm = next(item for item in result["candidates"] if item["id"] == "vllm")
        self.assertEqual(vllm["state"], "incompatible")

    def test_linux_nvidia_throughput_prefers_batching_engine(self):
        result = recommend_engines(
            hardware("linux", "NVIDIA", 8.9),
            [{"id": "vllm", "installed": True}],
            {
                "model_format": "safetensors",
                "priority": "throughput",
                "concurrency": 16,
                "context_tokens": 8192,
            },
        )
        self.assertEqual(result["top3"][0]["id"], "vllm")

    def test_apple_silicon_mlx_is_eligible(self):
        result = recommend_engines(
            hardware("macos", "Apple"),
            [{"id": "mlx", "installed": True}],
            {"model_format": "mlx", "priority": "latency"},
        )
        self.assertEqual(result["top3"][0]["id"], "mlx-lm")

    def test_only_identical_workloads_form_comparable_cohort(self):
        common = {
            "model_name": "demo",
            "concurrency": 2,
            "requested_context_tokens": 1024,
            "requested_output_tokens": 32,
            "successful_requests": 2,
            "failed_requests": 0,
        }
        result = compare_service_benchmarks(
            [
                {**common, "runtime": "vLLM", "generation_tps": 80, "ttft_seconds": 0.4, "created_at": 2},
                {**common, "runtime": "llama.cpp", "generation_tps": 40, "ttft_seconds": 0.8, "created_at": 1},
                {**common, "runtime": "Ollama", "requested_context_tokens": 2048, "created_at": 3},
            ]
        )
        self.assertEqual(result["comparable_cohorts"], 1)
        comparable = next(item for item in result["cohorts"] if item["comparable"])
        self.assertEqual(comparable["rows"][0]["runtime"], "vLLM")


if __name__ == "__main__":
    unittest.main()
