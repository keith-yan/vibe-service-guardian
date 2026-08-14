"""Disposable TCP/process-tree fixture used by destructive-action E2E tests.

This program is intentionally independent from VSG.  Tests may terminate only
processes whose PID and state file were created by this fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


STOP = threading.Event()


def _handle_stop(_signum: int, _frame: object) -> None:
    STOP.set()


def _install_handlers() -> None:
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _handle_stop)


def _write_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _popen_arguments() -> dict[str, object]:
    arguments: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        arguments["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return arguments


def run_idle() -> int:
    _install_handlers()
    while not STOP.wait(0.1):
        pass
    return 0


def run_server(port: int, state_path: Path, generation: int, spawn_child: bool) -> int:
    _install_handlers()
    child: subprocess.Popen[bytes] | None = None
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(8)
    listener.settimeout(0.1)
    try:
        if spawn_child:
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "idle"],
                **_popen_arguments(),
            )
        _write_state(
            state_path,
            {
                "mode": "serve",
                "generation": generation,
                "pid": os.getpid(),
                "child_pid": child.pid if child else None,
                "port": port,
                "ready": True,
            },
        )
        while not STOP.is_set():
            try:
                client, _address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            client.close()
    finally:
        listener.close()
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
    return 0


def run_watchdog(
    port: int,
    state_path: Path,
    stop_marker: Path,
    restart_delay: float,
) -> int:
    _install_handlers()
    generation = 0
    child: subprocess.Popen[bytes] | None = None
    script = str(Path(__file__).resolve())
    try:
        while not STOP.is_set() and not stop_marker.exists():
            if child is None or child.poll() is not None:
                if child is not None:
                    child.wait(timeout=2)
                    if STOP.wait(restart_delay) or stop_marker.exists():
                        break
                generation += 1
                child = subprocess.Popen(
                    [
                        sys.executable,
                        script,
                        "serve",
                        "--port",
                        str(port),
                        "--state",
                        str(state_path),
                        "--generation",
                        str(generation),
                        "--spawn-child",
                    ],
                    **_popen_arguments(),
                )
            STOP.wait(0.05)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("idle")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--state", type=Path, required=True)
    serve.add_argument("--generation", type=int, default=1)
    serve.add_argument("--spawn-child", action="store_true")
    watchdog = subparsers.add_parser("watchdog")
    watchdog.add_argument("--port", type=int, required=True)
    watchdog.add_argument("--state", type=Path, required=True)
    watchdog.add_argument("--stop-marker", type=Path, required=True)
    watchdog.add_argument("--restart-delay", type=float, default=0.15)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.mode == "idle":
        return run_idle()
    if not 1 <= args.port <= 65535:
        raise SystemExit("fixture port out of range")
    if args.mode == "serve":
        return run_server(args.port, args.state.resolve(), args.generation, args.spawn_child)
    return run_watchdog(
        args.port,
        args.state.resolve(),
        args.stop_marker.resolve(),
        max(0.01, args.restart_delay),
    )


if __name__ == "__main__":
    raise SystemExit(main())
