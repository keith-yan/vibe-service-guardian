import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from vsg.app import AppState, _create_server, _get_json, _post_json
from vsg.config import load_config


class ModelApiTests(unittest.TestCase):
    def test_loopback_model_planner_status_estimate_and_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            state = AppState(data_dir, load_config(data_dir))
            server = _create_server(0, state)
            state.server = server
            state.collector.start()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            base = f"http://127.0.0.1:{port}"
            try:
                bootstrap = _get_json(base + "/api/bootstrap")
                status = _get_json(base + "/api/model-planner/status", timeout=10)
                self.assertIn(status["hardware"]["platform"]["key"], {"windows", "macos", "linux"})
                self.assertGreaterEqual(status["catalog"]["model_count"], 10)
                estimate = _post_json(
                    base + "/api/model-planner/estimate",
                    bootstrap["token"],
                    {
                        "total_users": 25,
                        "concurrency": 4,
                        "prompt_tokens": 1024,
                        "context_tokens": 8192,
                        "output_tokens": 512,
                        "target_tps_per_user": 8,
                        "target_ttft_seconds": 5,
                        "preference": "balanced",
                        "runtime": "auto",
                        "kv_cache_bits": 16,
                    },
                    timeout=10,
                )
                self.assertEqual(len(estimate["estimate"]["candidates"]), status["catalog"]["model_count"])
                self.assertIn("127.0.0.1", estimate["estimate"]["runtime_plan"]["binding"])
                self.assertFalse(estimate["estimate"]["runtime_plan"]["will_execute"])

                bad = urllib.request.Request(
                    base + "/api/model-planner/benchmark",
                    data=json.dumps(
                        {
                            "model_id": "gpt-oss-20b",
                            "quantization": "Q4_K_M",
                            "model_path": "relative.gguf",
                            "confirmation": "",
                        }
                    ).encode("utf-8"),
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-VSG-Token": bootstrap["token"],
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(bad, timeout=5)
                self.assertEqual(raised.exception.code, 409)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
                state.close()


if __name__ == "__main__":
    unittest.main()
