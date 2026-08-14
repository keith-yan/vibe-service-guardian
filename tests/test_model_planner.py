import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from vsg.hardware import CommandResult
from vsg.model_catalog import load_catalog
from vsg.model_planner import BenchmarkError, ModelPlanner, parse_llama_bench_json
from vsg.storage import Storage


class ModelPlannerTests(unittest.TestCase):
    def test_llama_bench_json_parser_separates_prompt_and_generation(self):
        raw = json.dumps(
            [
                {"n_prompt": 128, "n_gen": 0, "avg_ts": 321.5, "model_filename": "private.gguf"},
                {"n_prompt": 0, "n_gen": 64, "avg_ts": 42.25, "model_filename": "private.gguf"},
            ]
        )
        result = parse_llama_bench_json(raw)
        self.assertEqual(result["prompt_tps"], 321.5)
        self.assertEqual(result["generation_tps"], 42.25)
        self.assertNotIn("model_filename", result["records"][0])

    def test_benchmark_requires_confirmation_and_stores_no_absolute_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_path = root / "fixture.Q4_K_M.gguf"
            model_path.write_bytes(b"GGUF" + b"0" * 2048)
            storage = Storage(root / "data")
            planner = object.__new__(ModelPlanner)
            planner.storage = storage
            planner.catalog = load_catalog()
            planner._lock = threading.RLock()
            planner._hardware = {"hardware_fingerprint": "fixture"}
            planner._runtimes = []

            with self.assertRaises(BenchmarkError):
                planner.benchmark(
                    {
                        "model_id": "gpt-oss-20b",
                        "quantization": "Q4_K_M",
                        "model_path": str(model_path),
                        "confirmation": "",
                    }
                )

            captured = []

            def runner(args, timeout):
                captured.append((list(args), timeout))
                return CommandResult(
                    0,
                    json.dumps(
                        [
                            {"n_prompt": 128, "n_gen": 0, "avg_ts": 250},
                            {"n_prompt": 0, "n_gen": 64, "avg_ts": 31.5},
                        ]
                    ),
                    "",
                )

            with patch("vsg.model_planner.shutil.which", return_value="llama-bench"):
                result = planner.benchmark(
                    {
                        "model_id": "gpt-oss-20b",
                        "quantization": "Q4_K_M",
                        "model_path": str(model_path),
                        "confirmation": "BENCHMARK",
                    },
                    runner=runner,
                )
            self.assertEqual(result["generation_tps"], 31.5)
            self.assertIn(str(model_path.resolve()), captured[0][0])
            stored = storage.recent_model_benchmarks()
            self.assertEqual(stored[0]["model_file_name"], model_path.name)
            self.assertNotIn(str(root.resolve()), json.dumps(stored, ensure_ascii=False))
            storage.close()


if __name__ == "__main__":
    unittest.main()
