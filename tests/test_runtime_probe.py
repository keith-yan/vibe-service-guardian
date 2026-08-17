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
        elif self.path == "/health":
            payload = {"status": "ok"}
        elif self.path == "/props":
            payload = {"model_path": "/models/qwen.Q5_K_M.gguf", "n_ctx": 16384}
        elif self.path == "/slots":
            payload = [{"id": 0, "is_processing": True}, {"id": 1, "is_processing": False}]
        elif self.path == "/v1/models":
            payload = {"data": [{"id": "Qwen3-8B-FP8", "quantization": "FP8"}]}
        elif self.path == "/metrics":
            body = (
                b"vllm:num_requests_running 2\n"
                b"vllm:num_requests_waiting 1\n"
                b"vllm:kv_cache_usage_perc 0.42\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
        self.assertEqual(result["adapter"]["id"], "ollama_native")
        self.assertEqual(result["evidence_summary"]["loaded_model"], "qwen:test")

    def test_llama_cpp_adapter_reports_model_slots_and_context(self):
        item = self.service()
        item["runtime"] = "llama.cpp"
        item["process"]["command"] = "llama-server -m /models/qwen.Q5_K_M.gguf -c 16384"
        result = RuntimeProbeCollector(cache_seconds=0).collect([item])[0]
        self.assertEqual(result["adapter"]["id"], "llama_cpp_native")
        self.assertEqual(result["adapter"]["evidence_quality"], "native")
        self.assertEqual(result["models"][0]["name"], "qwen.Q5_K_M.gguf")
        self.assertEqual(result["capacity"]["context_tokens"], 16384)
        self.assertEqual(result["capacity"]["slots"], 2)
        self.assertEqual(result["performance"]["requests_running"], 2)

    def test_vllm_adapter_reports_scheduler_and_kv_cache_metrics(self):
        item = self.service()
        item["runtime"] = "vLLM"
        item["process"]["command"] = "vllm serve Qwen3-8B-FP8 --max-num-seqs 8"
        result = RuntimeProbeCollector(cache_seconds=0).collect([item])[0]
        self.assertEqual(result["adapter"]["id"], "vllm_native")
        self.assertEqual(result["models"][0]["name"], "Qwen3-8B-FP8")
        self.assertEqual(result["performance"]["requests_running"], 2)
        self.assertEqual(result["performance"]["requests_waiting"], 1)
        self.assertEqual(result["performance"]["kv_cache_usage_percent"], 42.0)

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
