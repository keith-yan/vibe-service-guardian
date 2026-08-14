import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import psutil

from vsg.actions import ActionError, _launch_project_path, _listener_snapshot, terminate_process_tree
from vsg.models import Endpoint


class ActionTests(unittest.TestCase):
    def test_confirmation_and_pid_reuse_guard(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            process = psutil.Process(child.pid)
            created = process.create_time()
            with self.assertRaises(ActionError):
                terminate_process_tree(child.pid, created, "wrong", [])
            with self.assertRaises(ActionError):
                terminate_process_tree(child.pid, created - 50, f"STOP {child.pid}", [])
            result = terminate_process_tree(child.pid, created, f"STOP {child.pid}", [])
            self.assertEqual(result["pid"], child.pid)
            self.assertTrue(result["completed"])
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["still_alive"], [])
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_macos_open_project_uses_argument_array_without_shell(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            with (
                patch("vsg.actions.shutil.which", return_value="/usr/bin/open"),
                patch("vsg.actions.subprocess.Popen") as popen,
            ):
                _launch_project_path(project.resolve(), "darwin")
            self.assertEqual(popen.call_args.args[0], ["/usr/bin/open", str(project.resolve())])
            self.assertNotIn("shell", popen.call_args.kwargs)

    def test_listener_snapshot_uses_platform_collector_and_keeps_known_owners(self):
        endpoint_map = {
            321: [Endpoint("TCP", "127.0.0.1", 8080)],
            -1: [Endpoint("UDP", "*", 5353, state="BOUND", exposure="all_interfaces")],
        }
        with patch(
            "vsg.actions.collect_connections",
            return_value=(
                endpoint_map,
                {},
                [],
                {"status": "partial", "method": "lsof", "visibility": "current_user"},
            ),
        ) as collector:
            listeners = _listener_snapshot()

        collector.assert_called_once_with(include_udp=True)
        self.assertEqual(listeners[0]["pid"], 321)
        self.assertEqual(listeners[0]["port"], 8080)
        self.assertIsNone(listeners[1]["pid"])
        self.assertEqual(listeners[1]["protocol"], "UDP")


if __name__ == "__main__":
    unittest.main()
