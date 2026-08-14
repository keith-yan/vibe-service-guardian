from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlsplit


def _private_addresses(host: str, port: int) -> list[str]:
    addresses: list[str] = []
    for value in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        address = str(value[4][0]).split("%", 1)[0]
        ip = ipaddress.ip_address(address)
        allowed = ip.is_loopback or ip.is_link_local or (ip.is_private and not ip.is_reserved)
        if not allowed or ip.is_unspecified or ip.is_multicast:
            raise ValueError("节点解析到非私网地址，已拒绝连接")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError("节点没有可用地址")
    return addresses


def _probe(url: str) -> dict[str, Any]:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port = int(parsed.port or 0)
    started = time.perf_counter()
    base: dict[str, Any] = {
        "url": url,
        "host": host,
        "port": port,
        "status": "unreachable",
        "health_path": None,
        "http_status": None,
        "latency_ms": None,
        "addresses": [],
        "mode": "manual_trusted_node",
    }
    try:
        addresses = _private_addresses(host, port)
        base["addresses"] = addresses
    except (OSError, ValueError) as exc:
        base["error"] = str(exc)[:160]
        return base
    for path in ("/healthz", "/health"):
        for address in addresses:
            # Connect to the already validated literal address.  Reusing the
            # hostname here would perform a second DNS lookup and reopen a
            # DNS-rebinding window between validation and connection.
            connection = http.client.HTTPConnection(address, port, timeout=1.2)
            try:
                connection.request("GET", path, headers={"Host": f"{host}:{port}", "Accept": "application/json,text/plain"})
                response = connection.getresponse()
                response.read(64 * 1024)
                base["http_status"] = int(response.status)
                base["health_path"] = path
                base["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
                if 200 <= response.status < 300:
                    base["status"] = "ready"
                    return base
                if response.status in {401, 403}:
                    base["status"] = "reachable_auth_required"
                    return base
            except (OSError, http.client.HTTPException):
                pass
            finally:
                connection.close()
    return base


class TrustedNodeCollector:
    """Probe only addresses the user explicitly added; never enumerate a LAN."""

    def __init__(self, cache_seconds: float = 12.0):
        self.cache_seconds = cache_seconds
        self._cache_at = 0.0
        self._cache_urls: tuple[str, ...] = ()
        self._cache: list[dict[str, Any]] = []

    def collect(self, urls: list[str]) -> dict[str, Any]:
        key = tuple(urls)
        now = time.time()
        if key == self._cache_urls and now - self._cache_at < self.cache_seconds:
            return {"status": "measured" if urls else "not_configured", "nodes": json.loads(json.dumps(self._cache)), "captured_at": self._cache_at, "scan_performed": False}
        results: list[dict[str, Any]] = []
        if urls:
            with ThreadPoolExecutor(max_workers=min(4, len(urls)), thread_name_prefix="vsg-node") as pool:
                futures = {pool.submit(_probe, url): url for url in urls}
                for future in as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        results.append({"url": futures[future], "status": "probe_error", "error": type(exc).__name__})
        results.sort(key=lambda item: str(item.get("url")))
        self._cache = results
        self._cache_urls = key
        self._cache_at = time.time()
        return {
            "status": "measured" if urls else "not_configured",
            "nodes": json.loads(json.dumps(results)),
            "captured_at": self._cache_at,
            "scan_performed": False,
            "privacy": "只连接设置中明确添加的私网/回环节点，不扫描网段，不携带凭据",
        }
