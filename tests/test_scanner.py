import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vsg.config import AppConfig
from vsg.models import Endpoint, ProcessSnapshot
from vsg.scanner import Scanner, redact_arguments
from vsg.storage import Storage


class ScannerTests(unittest.TestCase):
    def test_sensitive_arguments_are_redacted(self):
        result = redact_arguments([
            "server.exe",
            "--api-key",
            "secret-value",
            "--token=abc123456",
            "sk-" + "example-secret-value",
        ])
        joined = " ".join(result)
        self.assertNotIn("secret-value", joined)
        self.assertNotIn("abc123456", joined)
        self.assertIn("[REDACTED]", joined)

    def test_uri_query_and_provider_tokens_are_redacted(self):
        github_token = "ghp_" + "a" * 32
        scheme = "postgresql"
        database_uri = scheme + "://" + "app:database-password@localhost:5432/app"
        result = redact_arguments(
            [
                database_uri,
                "http://localhost/callback?token=query-secret&safe=value",
                github_token,
            ]
        )
        joined = " ".join(result)
        self.assertNotIn("database-password", joined)
        self.assertNotIn("query-secret", joined)
        self.assertNotIn(github_token, joined)
        self.assertIn(scheme + "://" + "app:[REDACTED]@", joined)
        self.assertIn("safe=value", joined)

    def test_non_secret_option_with_token_substring_does_not_hide_next_argument(self):
        result = redact_arguments(["tool", "--tokenize", "public-value"])
        self.assertEqual(result, ["tool", "--tokenize", "public-value"])

    def test_current_tcp_listener_is_discovered(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as directory:
                storage = Storage(Path(directory))
                try:
                    config = AppConfig(
                        project_roots=[str(Path.cwd().resolve())],
                        include_udp=False,
                        include_windows_services=False,
                        include_docker=False,
                        include_wsl=False,
                    )
                    snapshot = Scanner(config, storage).scan()
                finally:
                    storage.close()
            matching = [
                service
                for service in snapshot["services"]
                if service["process"]["pid"] == os.getpid()
                and any(endpoint["port"] == port for endpoint in service["endpoints"])
            ]
            self.assertTrue(matching, f"listener {port} was not discovered")
            self.assertTrue(matching[0]["protected"])
        finally:
            listener.close()

    def test_agent_inventory_collapses_desktop_helper_processes(self):
        package = r"C:\Program Files\WindowsApps\OpenAI.Codex_1.0_x64__id\app"

        class DummyProcess:
            def __init__(self, info):
                self.info = info

        processes = [
            DummyProcess({
                "pid": 100,
                "ppid": 1,
                "name": "ChatGPT.exe",
                "exe": package + r"\ChatGPT.exe",
                "cmdline": [package + r"\ChatGPT.exe"],
            }),
            DummyProcess({
                "pid": 101,
                "ppid": 100,
                "name": "ChatGPT.exe",
                "exe": package + r"\ChatGPT.exe",
                "cmdline": [package + r"\ChatGPT.exe", "--type=renderer"],
            }),
            DummyProcess({
                "pid": 102,
                "ppid": 100,
                "name": "codex.exe",
                "exe": package + r"\resources\codex.exe",
                "cmdline": [package + r"\resources\codex.exe", "app-server"],
            }),
        ]
        scanner = Scanner(AppConfig(project_roots=[str(Path.cwd())]))
        with patch("vsg.scanner.psutil.process_iter", return_value=iter(processes)):
            self.assertEqual(scanner._agent_pids(), {100})

    def test_agent_without_port_is_a_read_only_service_record(self):
        scanner = Scanner(AppConfig(project_roots=[str(Path.cwd())]))
        process = ProcessSnapshot(
            pid=200,
            ppid=None,
            name="opencode",
            cmdline=["opencode"],
            cwd=str(Path.cwd()),
            create_time=1.0,
        )
        with (
            patch.object(scanner, "_sessions", return_value=[]),
            patch.object(scanner, "_windows_service_map", return_value={}),
        ):
            services = scanner._host_services({200: process}, {}, {}, {200})
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0].source, "agent")
        self.assertEqual(services[0].agent.provider, "OpenCode")
        self.assertFalse(services[0].endpoints)
        self.assertTrue(services[0].protected)
        self.assertFalse(services[0].metadata["stoppable_candidate"])

    def test_model_server_listener_is_grouped_and_actionable(self):
        scanner = Scanner(AppConfig(project_roots=[str(Path.cwd())]))
        process = ProcessSnapshot(
            pid=201,
            ppid=None,
            name="llama-server.exe",
            cmdline=["llama-server.exe", "--host", "127.0.0.1", "--port", "8080"],
            cwd=str(Path.cwd()),
            create_time=1.0,
        )
        endpoint = Endpoint(protocol="tcp", address="127.0.0.1", port=8080)
        with (
            patch.object(scanner, "_sessions", return_value=[]),
            patch.object(scanner, "_windows_service_map", return_value={}),
        ):
            services = scanner._host_services({201: process}, {201: [endpoint]}, {}, set())
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0].runtime, "llama.cpp")
        self.assertTrue(services[0].metadata["model_runtime"])
        self.assertTrue(services[0].metadata["openable_candidate"])
        self.assertTrue(services[0].metadata["stoppable_candidate"])

    def test_lm_studio_listener_is_openable_but_not_directly_stoppable(self):
        scanner = Scanner(AppConfig(project_roots=[str(Path.cwd())]))
        process = ProcessSnapshot(
            pid=202,
            ppid=None,
            name="LM Studio.exe",
            cmdline=["LM Studio.exe"],
            cwd=str(Path.cwd()),
            create_time=1.0,
        )
        endpoint = Endpoint(protocol="tcp", address="127.0.0.1", port=1234)
        with (
            patch.object(scanner, "_sessions", return_value=[]),
            patch.object(scanner, "_windows_service_map", return_value={}),
        ):
            services = scanner._host_services({202: process}, {202: [endpoint]}, {}, set())
        self.assertEqual(len(services), 1)
        self.assertTrue(services[0].metadata["model_runtime"])
        self.assertTrue(services[0].metadata["openable_candidate"])
        self.assertFalse(services[0].metadata["stoppable_candidate"])


if __name__ == "__main__":
    unittest.main()
