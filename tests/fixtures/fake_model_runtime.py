"""Loopback-only fake model runtime for real HTTP benchmark tests."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, mode: str, delay_seconds: float):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.mode = mode
        self.delay_seconds = max(0.0, delay_seconds)
        self.request_count = 0
        self.active_requests = 0
        self.max_active_requests = 0
        self.paths: list[str] = []
        self.lock = threading.Lock()

    def request_started(self, path: str) -> None:
        with self.lock:
            self.request_count += 1
            self.active_requests += 1
            self.max_active_requests = max(
                self.max_active_requests, self.active_requests
            )
            self.paths.append(path)

    def request_finished(self) -> None:
        with self.lock:
            self.active_requests -= 1


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:
        length = min(max(int(self.headers.get("Content-Length", "0")), 0), 2_000_000)
        self.rfile.read(length)
        self.server.request_started(self.path)
        try:
            if self.server.delay_seconds:
                time.sleep(self.server.delay_seconds)
            if self.server.mode == "auth":
                self._send(
                    401,
                    b'{"error":"API key required"}',
                    "application/json",
                )
                return
            if self.server.mode == "oom":
                self._send(
                    500,
                    b"CUDA out of memory token=fixture-secret-value",
                    "text/plain; charset=utf-8",
                )
                return
            if self.server.mode == "malformed":
                self._send(200, b"not-json\n{broken\n", "application/x-ndjson")
                return
            if self.server.mode == "oversized":
                self._send(200, b"x" * (11 * 1024 * 1024), "application/x-ndjson")
                return
            if self.path == "/api/generate":
                payloads = [
                    {"response": "fixture-secret-response", "done": False},
                    {
                        "response": "",
                        "done": True,
                        "prompt_eval_count": 512,
                        "prompt_eval_duration": 500_000_000,
                        "eval_count": 20,
                        "eval_duration": 1_000_000_000,
                    },
                ]
                body = b"".join(
                    json.dumps(item, separators=(",", ":")).encode("utf-8") + b"\n"
                    for item in payloads
                )
                self._send(200, body, "application/x-ndjson")
                return
            payloads = [
                {"choices": [{"text": "fixture-secret-response"}]},
                {
                    "choices": [{"text": ""}],
                    "usage": {"prompt_tokens": 512, "completion_tokens": 20},
                },
            ]
            body = b"".join(
                b"data: "
                + json.dumps(item, separators=(",", ":")).encode("utf-8")
                + b"\n\n"
                for item in payloads
            ) + b"data: [DONE]\n\n"
            self._send(200, body, "text/event-stream")
        finally:
            self.server.request_finished()


class FakeModelRuntime:
    def __init__(self, mode: str = "normal", delay_seconds: float = 0.01):
        self._server = _Server(mode, delay_seconds)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vsg-fake-model-runtime",
            daemon=True,
        )

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def request_count(self) -> int:
        with self._server.lock:
            return self._server.request_count

    @property
    def max_active_requests(self) -> int:
        with self._server.lock:
            return self._server.max_active_requests

    @property
    def paths(self) -> list[str]:
        with self._server.lock:
            return list(self._server.paths)

    def __enter__(self) -> "FakeModelRuntime":
        self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)
