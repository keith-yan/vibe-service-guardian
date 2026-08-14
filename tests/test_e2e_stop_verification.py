import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Callable

import psutil

from vsg.actions import terminate_process_tree, verify_post_stop


FIXTURE = Path(__file__).parent / "fixtures" / "vsg_fixture_service.py"


def free_loopback_port() -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def fixture_popen(arguments: list[str]) -> subprocess.Popen[bytes]:
    options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen([sys.executable, str(FIXTURE), *arguments], **options)


def wait_until(predicate: Callable[[], bool], timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("fixture condition was not reached before timeout")


def read_state(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def port_is_open(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.15)
    try:
        return probe.connect_ex(("127.0.0.1", port)) == 0
    finally:
        probe.close()


def terminate_known_fixture_pids(pids: set[int]) -> None:
    targets: dict[tuple[int, float], psutil.Process] = {}
    for pid in pids:
        try:
            process = psutil.Process(pid)
            for item in [*process.children(recursive=True), process]:
                targets[(item.pid, item.create_time())] = item
        except (psutil.Error, OSError):
            continue
    for target in targets.values():
        try:
            if target.is_running():
                target.terminate()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(list(targets.values()), timeout=2)
    for target in alive:
        try:
            target.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=2)


def service_fixture(pid: int, create_time: float, port: int) -> dict[str, object]:
    return {
        "id": f"fixture:{pid}",
        "fingerprint": f"fixture-fingerprint-{port}",
        "source": "host",
        "process": {"pid": pid, "create_time": create_time},
        "endpoints": [
            {
                "protocol": "TCP",
                "address": "127.0.0.1",
                "port": port,
                "state": "LISTEN",
            }
        ],
    }


class StopVerificationE2ETests(unittest.TestCase):
    def test_fixture_process_tree_stops_and_releases_port(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            port = free_loopback_port()
            process = fixture_popen(
                [
                    "serve",
                    "--port",
                    str(port),
                    "--state",
                    str(state_path),
                    "--spawn-child",
                ]
            )
            known_pids = {process.pid}
            try:
                wait_until(lambda: bool((read_state(state_path) or {}).get("ready")))
                state = read_state(state_path) or {}
                child_pid = int(state["child_pid"])
                known_pids.add(child_pid)
                self.assertTrue(port_is_open(port))
                create_time = psutil.Process(process.pid).create_time()
                target = service_fixture(process.pid, create_time, port)

                result = terminate_process_tree(
                    process.pid,
                    create_time,
                    f"STOP {process.pid}",
                    [],
                    timeout=3,
                )
                process.wait(timeout=5)
                verification = verify_post_stop(
                    target,
                    [*result["terminated"], *result["forced"]],
                    observation_seconds=0.5,
                    poll_interval=0.05,
                )

                self.assertTrue(result["completed"])
                self.assertIn(process.pid, result["terminated"])
                self.assertIn(child_pid, result["terminated"])
                self.assertEqual(verification["outcome"], "stopped")
                self.assertFalse(verification["original_pid_alive"])
                self.assertEqual(verification["surviving_pids"], [])
                self.assertTrue(verification["endpoint_verification"][0]["closed"])
                self.assertFalse(verification["second_stop_attempted"])
                self.assertFalse(port_is_open(port))
            finally:
                terminate_known_fixture_pids(known_pids)
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)

    def test_watchdog_relaunch_is_observed_but_not_stopped_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            stop_marker = root / "stop"
            port = free_loopback_port()
            watchdog = fixture_popen(
                [
                    "watchdog",
                    "--port",
                    str(port),
                    "--state",
                    str(state_path),
                    "--stop-marker",
                    str(stop_marker),
                    "--restart-delay",
                    "0.1",
                ]
            )
            known_pids = {watchdog.pid}
            try:
                wait_until(lambda: int((read_state(state_path) or {}).get("generation") or 0) == 1)
                first = read_state(state_path) or {}
                original_pid = int(first["pid"])
                original_child_pid = int(first["child_pid"])
                known_pids.update({original_pid, original_child_pid})
                create_time = psutil.Process(original_pid).create_time()
                target = service_fixture(original_pid, create_time, port)

                result = terminate_process_tree(
                    original_pid,
                    create_time,
                    f"STOP {original_pid}",
                    [],
                    timeout=3,
                )
                verification = verify_post_stop(
                    target,
                    [*result["terminated"], *result["forced"]],
                    observation_seconds=2.0,
                    poll_interval=0.05,
                )
                wait_until(lambda: int((read_state(state_path) or {}).get("generation") or 0) >= 2)
                second = read_state(state_path) or {}
                replacement_pid = int(second["pid"])
                known_pids.update({replacement_pid, int(second["child_pid"])})

                self.assertNotEqual(replacement_pid, original_pid)
                self.assertEqual(verification["outcome"], "relaunched")
                self.assertIn(replacement_pid, verification["replacement_pids"])
                self.assertTrue(verification["restart_detected"])
                self.assertEqual(
                    verification["automatic_restart_evidence"], "observed"
                )
                self.assertFalse(verification["second_stop_attempted"])
                self.assertTrue(port_is_open(port))
            finally:
                stop_marker.touch(exist_ok=True)
                try:
                    watchdog.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                latest = read_state(state_path) or {}
                for key in ("pid", "child_pid"):
                    if latest.get(key):
                        known_pids.add(int(latest[key]))
                terminate_known_fixture_pids(known_pids)
                if watchdog.poll() is None:
                    watchdog.kill()
                watchdog.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
