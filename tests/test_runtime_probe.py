import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from vsg.runtime_probe import RuntimeProbeCollector, extract_command_configuration, parse_prometheus


class OllamaHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path == "/api/ps":
            payload = {
                "models": [
                    {
                        "name": "qwen:test",
                        "size": 100,
                        "size_vram": 80,
                        "context_length": 8192,
                        "details": {"format": "gguf", "family": "qwen", "parameter_size": "7B", "quantization_level": "Q4_K_M"},
                    }
                ]
            }
        elif self.path == "/api/version":
            payload = {"version": "0.test"}
        else:
            self.send_error(404)
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RuntimeProbeTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), OllamaHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def service(self):
        return {
            "id": "host:1:1",
            "runtime": "Ollama",
            "process": {"pid": 1, "command": "ollama serve --host 127.0.0.1"},
            "metadata": {"model_runtime": True},
            "endpoints": [{"protocol": "TCP", "state": "LISTEN", "port": self.server.server_port}],
        }

    def test_ollama_adapter_reads_loaded_model_without_prompt(self):
        result = RuntimeProbeCollector(cache_seconds=0).collect([self.service()])[0]
        self.assertEqual(result["health"], "ready")
        self.assertEqual(result["model_load"], "loaded")
        self.assertEqual(result["models"][0]["quantization"], "Q4_K_M")
        self.assertEqual(result["capacity"]["context_tokens"], 8192)
        self.assertEqual(result["security"]["auth_posture"], "unauthenticated_read")

    def test_command_config_never_returns_auth_value(self):
        service = self.service()
        service["process"]["command"] = "llama-server -m C:\\models\\a.Q4_K_M.gguf --api-key [REDACTED] -c 4096"
        value = extract_command_configuration(service)
        self.assertEqual(value["model"], "a.Q4_K_M.gguf")
        self.assertTrue(value["auth_flag_present"])
        self.assertNotIn("[REDACTED]", json.dumps(value))

    def test_prometheus_parser_aggregates_labelled_metrics(self):
        values = parse_prometheus('vllm:num_requests_running{model="a"} 2\nvllm:num_requests_running{model="b"} 1\n')
        self.assertEqual(values["vllm:num_requests_running"], 3)

    def test_command_config_separates_backend_from_accelerator(self):
        service = self.service()
        service["runtime"] = "Text Generation WebUI"
        service["process"]["command"] = "python server.py --loader ExLlamav2 --cuda"
        value = extract_command_configuration(service)
        self.assertEqual(value["backend"], "ExLlamaV2")
        self.assertEqual(value["accelerator"], "CUDA")


if __name__ == "__main__":
    unittest.main()
