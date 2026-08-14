import os
import tempfile
import unittest
from pathlib import Path

from vsg.log_monitor import LogMonitor, LogMonitorError, parse_log_line
from vsg.storage import Storage


def model_service(pid: int | None = None) -> dict:
    value = pid or os.getpid()
    return {
        "id": f"host:{value}:9000",
        "fingerprint": "model-fingerprint",
        "runtime": "llama.cpp",
        "source": "host",
        "metadata": {"model_runtime": True},
        "process": {"pid": value, "create_time": 100.0},
    }


class LogMonitorTests(unittest.TestCase):
    def test_parser_redacts_secrets_and_classifies_cuda_oom(self):
        secret_marker = "".join(("api_", "key", "=", "secret-value"))
        event = parse_log_line(f"CUDA out of memory {secret_marker}", "vLLM", 123.0)
        self.assertIsNotNone(event)
        self.assertEqual(event["code"], "CUDA_OOM")
        self.assertNotIn("secret-value", event["message"])
        self.assertIn("[REDACTED]", event["message"])

    def test_watch_requires_pid_confirmation_and_persists_only_redacted_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "server.log"
            log.write_text("ready\n", encoding="utf-8")
            storage = Storage(root / "data")
            monitor = LogMonitor(storage)
            service = model_service()
            try:
                with self.assertRaises(LogMonitorError):
                    monitor.start_watch(service, str(log), "WATCH 1")
                watch = monitor.start_watch(service, str(log), f"WATCH {os.getpid()}")
                self.assertNotIn("path", watch)
                with log.open("a", encoding="utf-8") as stream:
                    stream.write("CUDA out of memory token=my-secret\n")
                status = monitor.poll([service])
                self.assertEqual(status["active_count"], 1)
                self.assertEqual(status["events"][0]["code"], "CUDA_OOM")
                self.assertNotIn("my-secret", status["events"][0]["message"])
                stopped = monitor.stop_watch(watch["id"], f"WATCH {os.getpid()}")
                self.assertFalse(stopped["enabled"])
            finally:
                storage.close()

    def test_identity_change_stops_watch_and_records_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "server.log"
            log.write_text("ready\n", encoding="utf-8")
            storage = Storage(root / "data")
            monitor = LogMonitor(storage)
            service = model_service()
            try:
                monitor.start_watch(service, str(log), f"WATCH {os.getpid()}")
                status = monitor.poll([])
                self.assertEqual(status["active_count"], 0)
                self.assertEqual(status["events"][0]["code"], "SERVICE_EXITED")
            finally:
                storage.close()

    def test_atomic_log_replacement_resets_cursor_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "server.log"
            log.write_text("model loaded and server is ready\n", encoding="utf-8")
            storage = Storage(root / "data")
            monitor = LogMonitor(storage)
            service = model_service()
            try:
                monitor.start_watch(service, str(log), f"WATCH {os.getpid()}")
                monitor.poll([service])
                replacement = root / "replacement.log"
                secret_marker = "".join(("api_", "key", "=", "hidden"))
                replacement.write_text(
                    f"model loaded and server is ready\nCUDA out of memory {secret_marker}\n",
                    encoding="utf-8",
                )
                os.replace(replacement, log)
                status = monitor.poll([service])
                codes = {item["code"] for item in status["events"]}
                self.assertIn("LOG_ROTATED", codes)
                self.assertIn("CUDA_OOM", codes)
                self.assertFalse(any("hidden" in item["message"] for item in status["events"]))
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()
