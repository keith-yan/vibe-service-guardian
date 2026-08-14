from __future__ import annotations

import os
import shutil
import socket
import subprocess
from collections import defaultdict
from typing import Any, Iterable

import psutil

from .models import Endpoint
from .platforms import executable_search_path, platform_key


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def endpoint_exposure(address: str) -> str:
    lowered = address.lower().strip("[]")
    if lowered in {"127.0.0.1", "::1", "localhost"}:
        return "loopback"
    # These literals classify observed bindings; this function never binds a socket.
    if lowered in {"0.0.0.0", "::", "*", ""}:  # nosec B104
        return "all_interfaces"
    return "lan"


def _split_lsof_address(value: str) -> tuple[str, int] | None:
    local = value.split("->", 1)[0].strip()
    if local.startswith("[") and "]:" in local:
        address, port_value = local[1:].rsplit("]:", 1)
    elif ":" in local:
        address, port_value = local.rsplit(":", 1)
    else:
        return None
    try:
        port = int(port_value)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return address or "*", port


def parse_lsof_fields(
    output: str,
    include_udp: bool = True,
) -> tuple[dict[int, list[Endpoint]], dict[int, int]]:
    endpoints: dict[int, list[Endpoint]] = defaultdict(list)
    established: dict[int, int] = defaultdict(int)
    seen: set[tuple[int, str, str, int, str]] = set()
    current_pid: int | None = None
    record: dict[str, str] = {}

    def flush() -> None:
        nonlocal record
        if current_pid is None:
            record = {}
            return
        protocol = record.get("protocol", "").upper()
        address_value = record.get("address")
        state = record.get("state", "").upper()
        if protocol == "TCP" and state == "ESTABLISHED":
            established[current_pid] += 1
        parsed = _split_lsof_address(address_value or "")
        if not parsed:
            record = {}
            return
        address, port = parsed
        is_tcp_listener = protocol == "TCP" and state == "LISTEN"
        is_udp_binding = protocol == "UDP" and include_udp
        if is_tcp_listener or is_udp_binding:
            endpoint_state = "LISTEN" if is_tcp_listener else "BOUND"
            key = (current_pid, protocol, address, port, endpoint_state)
            if key not in seen:
                seen.add(key)
                endpoints[current_pid].append(
                    Endpoint(
                        protocol=protocol,
                        address=address,
                        port=port,
                        state=endpoint_state,
                        exposure=endpoint_exposure(address),
                    )
                )
        record = {}

    for raw_line in output.splitlines():
        if not raw_line:
            continue
        field, value = raw_line[0], raw_line[1:]
        if field == "p":
            flush()
            try:
                current_pid = int(value)
            except ValueError:
                current_pid = None
        elif field == "f":
            flush()
        elif field == "P":
            record["protocol"] = value
        elif field == "n":
            record["address"] = value
        elif field == "T" and value.startswith("ST="):
            record["state"] = value[3:]
    flush()
    return endpoints, established


def _merge_endpoint_maps(
    target: dict[int, list[Endpoint]],
    source: dict[int, Iterable[Endpoint]],
) -> None:
    existing = {
        (pid, item.protocol, item.address, item.port, item.state)
        for pid, items in target.items()
        for item in items
    }
    for pid, items in source.items():
        for item in items:
            key = (pid, item.protocol, item.address, item.port, item.state)
            if key not in existing:
                target[pid].append(item)
                existing.add(key)


def _macos_connections(
    include_udp: bool,
) -> tuple[dict[int, list[Endpoint]], dict[int, int], list[str], dict[str, Any]]:
    executable = shutil.which("lsof") or executable_search_path("lsof")
    if not executable:
        message = "未找到系统 lsof，无法在 macOS 上读取端口归属"
        return defaultdict(list), defaultdict(int), [message], {
            "status": "error",
            "message": message,
            "method": "lsof",
            "visibility": "none",
        }

    endpoints: dict[int, list[Endpoint]] = defaultdict(list)
    established: dict[int, int] = defaultdict(int)
    errors: list[str] = []
    commands = [[executable, "-nP", "-iTCP", "-FpcPnT"]]
    if include_udp:
        commands.append([executable, "-nP", "-iUDP", "-FpcPn"])
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"lsof 采集失败：{type(exc).__name__}")
            continue
        # lsof uses exit code 1 when a filter has no matches; empty output is valid.
        if completed.returncode not in {0, 1}:
            errors.append(f"lsof 返回状态 {completed.returncode}")
            continue
        parsed_endpoints, parsed_established = parse_lsof_fields(completed.stdout, include_udp)
        _merge_endpoint_maps(endpoints, parsed_endpoints)
        for pid, count in parsed_established.items():
            established[pid] += count

    status = "partial" if not errors else "error"
    message = "通过 lsof 读取当前用户可见端口；macOS 非提权模式不保证覆盖其他用户进程"
    if errors:
        message = "；".join(errors)
    return endpoints, established, errors, {
        "status": status,
        "message": message,
        "method": "lsof",
        "visibility": "current_user",
    }


def _psutil_connections(
    include_udp: bool,
) -> tuple[dict[int, list[Endpoint]], dict[int, int], list[str], dict[str, Any]]:
    errors: list[str] = []
    endpoints: dict[int, list[Endpoint]] = defaultdict(list)
    established: dict[int, int] = defaultdict(int)
    unknown_counter = -1
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError) as exc:
        message = f"无法读取完整端口表：{type(exc).__name__}"
        return endpoints, established, [message], {
            "status": "partial",
            "message": message,
            "method": "psutil",
            "visibility": "partial",
        }

    for connection in connections:
        pid = connection.pid
        if connection.status == psutil.CONN_ESTABLISHED and pid:
            established[int(pid)] += 1
        is_tcp_listener = connection.type == socket.SOCK_STREAM and connection.status == psutil.CONN_LISTEN
        is_udp_binding = connection.type == socket.SOCK_DGRAM and include_udp
        if not (is_tcp_listener or is_udp_binding) or not connection.laddr:
            continue
        address = str(connection.laddr.ip)
        port = int(connection.laddr.port)
        if pid is None:
            pid = unknown_counter
            unknown_counter -= 1
        endpoints[int(pid)].append(
            Endpoint(
                protocol="TCP" if connection.type == socket.SOCK_STREAM else "UDP",
                address=address,
                port=port,
                state="LISTEN" if is_tcp_listener else "BOUND",
                exposure=endpoint_exposure(address),
            )
        )
    return endpoints, established, errors, {
        "status": "ok",
        "message": "通过 psutil 读取系统端口表",
        "method": "psutil",
        "visibility": "system",
    }


def collect_connections(
    include_udp: bool,
    system_name: str | None = None,
) -> tuple[dict[int, list[Endpoint]], dict[int, int], list[str], dict[str, Any]]:
    if platform_key(system_name) == "macos":
        return _macos_connections(include_udp)
    return _psutil_connections(include_udp)
