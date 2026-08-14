import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vsg import __version__
from vsg.app import (
    AppState,
    VSGServer,
    _create_server,
    _health_is_vsg,
    _read_runtime,
    _read_control_json,
    _validated_control_url,
    _write_runtime,
    control_existing,
)
from vsg.config import AppConfig


class AppTests(unittest.TestCase):
    def test_second_server_never_reuses_an_existing_control_port(self):
        first = VSGServer(("127.0.0.1", 0), object())
        second = None
        try:
            occupied_port = first.server_address[1]
            second = _create_server(occupied_port, object())
            self.assertNotEqual(second.server_address[1], occupied_port)
        finally:
            if second is not None:
                second.server_close()
            first.server_close()

    def test_health_check_requires_matching_vsg_version(self):
        payload = {"ok": True, "version": __version__, "instance_id": "instance-a"}
        self.assertTrue(_health_is_vsg(payload))
        self.assertTrue(_health_is_vsg(payload, "instance-a"))
        self.assertFalse(_health_is_vsg(payload, "instance-b"))
        self.assertFalse(_health_is_vsg({"ok": True}))
        self.assertFalse(_health_is_vsg({"ok": True, "version": "other", "instance_id": "x"}))
        self.assertFalse(_health_is_vsg({"ok": False, "version": __version__, "instance_id": "x"}))

    def test_runtime_file_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            _write_runtime(data_dir, 43210, "runtime-instance")
            runtime = _read_runtime(data_dir)
            self.assertIsNotNone(runtime)
            assert runtime is not None
            self.assertEqual(runtime["port"], 43210)
            self.assertEqual(runtime["instance_id"], "runtime-instance")

    def test_foreign_service_on_stale_runtime_port_is_not_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _write_runtime(data_dir, 43210, "expected-instance")
            with (
                patch(
                    "vsg.app._get_json",
                    return_value={"ok": True, "version": __version__, "instance_id": "foreign"},
                ),
                patch("vsg.app.webbrowser.open") as browser_open,
            ):
                self.assertEqual(control_existing(data_dir, "open"), 3)
                browser_open.assert_not_called()

    def test_http_responses_include_local_control_plane_headers(self):
        state = SimpleNamespace(instance_id="test-instance")
        server = VSGServer(("127.0.0.1", 0), state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_address[1]}/healthz", timeout=5
            ) as response:
                self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
                self.assertIn("camera=()", response.headers["Permissions-Policy"])
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_settings_api_rejects_unknown_keys_before_writing(self):
        state = AppState.__new__(AppState)
        state.config = AppConfig(project_roots=[str(Path.cwd())])
        with self.assertRaisesRegex(ValueError, "未知字段"):
            state.update_config({"refresh_second": 5})

    def test_internal_control_client_rejects_non_loopback_and_large_responses(self):
        with self.assertRaises(ValueError):
            _validated_control_url("file:///tmp/runtime.json")
        with self.assertRaises(ValueError):
            _validated_control_url("http://0.0.0.0:43921/healthz")

        class LargeResponse:
            def read(self, size):
                return b"x" * size

        with patch("vsg.app.MAX_CONTROL_RESPONSE", 16):
            with self.assertRaisesRegex(ValueError, "1 MiB"):
                _read_control_json(LargeResponse())


if __name__ == "__main__":
    unittest.main()
