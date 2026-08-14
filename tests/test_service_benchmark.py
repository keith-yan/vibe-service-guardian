import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from vsg.service_benchmark import ServiceBenchmarkError, run_service_benchmark


class BenchmarkHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        lines = [
            {"response": "hello", "done": False},
            {"response": "", "done": True, "prompt_eval_count": 500, "prompt_eval_duration": 500000000, "eval_count": 20, "eval_duration": 1000000000},
        ]
        body = b"".join(json.dumps(item).encode() + b"\n" for item in lines)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ServiceBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BenchmarkHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_explicit_ollama_benchmark_uses_server_token_counts(self):
        port = self.server.server_port
        service = {"id": "s", "fingerprint": "fp", "runtime": "Ollama", "metadata": {"model_runtime": True}}
        probe = {"port": port, "health": "ready", "security": {"auth_posture": "unauthenticated_read"}, "models": [{"name": "demo"}]}
        value = run_service_benchmark(
            service,
            probe,
            {"concurrency": 1, "context_tokens": 512, "output_tokens": 20, "confirmation": f"BENCHMARK {port}"},
            30,
        )
        self.assertEqual(value["successful_requests"], 1)
        self.assertEqual(value["verified_prompt_tokens_min"], 500)
        self.assertEqual(value["generation_tps"], 20)
        self.assertFalse(value["oom_observed"])

    def test_model_path_cannot_be_persisted_as_model_identifier(self):
        port = self.server.server_port
        service = {"id": "s", "fingerprint": "fp", "runtime": "Ollama", "metadata": {"model_runtime": True}}
        probe = {"port": port, "health": "ready", "security": {"auth_posture": "unauthenticated_read"}, "models": []}
        with self.assertRaises(ServiceBenchmarkError):
            run_service_benchmark(
                service,
                probe,
                {"model": r"C:\\models\\private.gguf", "concurrency": 1, "context_tokens": 512, "output_tokens": 20, "confirmation": f"BENCHMARK {port}"},
                30,
            )


if __name__ == "__main__":
    unittest.main()
