import tempfile
import threading
import unittest
from pathlib import Path

from vsg.app import AppState, _create_server, _get_json, _post_json
from vsg.config import load_config


class AdvisorApiTests(unittest.TestCase):
    def test_loopback_advisor_status_and_evaluate(self):
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
                bootstrap = _get_json(base + "/api/bootstrap")
                status = _get_json(base + "/api/advisor/status", timeout=15)
                self.assertIn("top3", status["engine"])
                self.assertIn("recommendations", status["advice"])
                self.assertEqual(status["workflow"]["stages"][-1], "rollback")
                result = _post_json(
                    base + "/api/advisor/evaluate",
                    bootstrap["token"],
                    {
                        "model_format": "gguf",
                        "priority": "ease",
                        "concurrency": 2,
                        "context_tokens": 8192,
                        "features": ["tools"],
                    },
                    timeout=15,
                )
                self.assertEqual(result["engine"]["request"]["priority"], "ease")
                self.assertFalse(result["workflow"]["automatic_changes"])
                self.assertEqual(result["log_monitor"]["requires_confirmation"], "WATCH <PID>")
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
                state.close()


if __name__ == "__main__":
    unittest.main()
