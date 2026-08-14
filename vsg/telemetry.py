from __future__ import annotations

import csv
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import psutil

from .config import AppConfig
from .hardware import CommandResult, collect_hardware


GIB = 1024**3
MIB = 1024**2
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
Runner = Callable[[Sequence[str], float], CommandResult]


def _run_command(args: Sequence[str], timeout: float = 5.0) -> CommandResult:
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(127, "", type(exc).__name__)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _number(value: str) -> float | None:
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"n/a", "na", "not supported", "[not supported]", "-"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _unit_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group(0)) if match else None


def _flatten_json(value: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            child_prefix = f"{prefix}_{normalized}" if prefix else normalized
            flattened.update(_flatten_json(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            flattened.update(_flatten_json(child, f"{prefix}_{index}" if prefix else str(index)))
    else:
        flattened[prefix] = value
    return flattened


def _amd_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        records = [item for item in value if isinstance(item, dict)]
        if records:
            return records
    if not isinstance(value, dict):
        return []
    for key in ("gpu_data", "gpus", "devices", "gpu_metrics"):
        child = value.get(key)
        if isinstance(child, list):
            return [item for item in child if isinstance(item, dict)]
        if isinstance(child, dict):
            return [dict(item, _vsg_index=index) if isinstance(item, dict) else {} for index, item in child.items()]
    keyed = [
        dict(item, _vsg_index=key)
        for key, item in value.items()
        if isinstance(item, dict) and (str(key).isdigit() or re.fullmatch(r"gpu[_ -]?\d+", str(key), re.I))
    ]
    if keyed:
        return keyed
    flattened = _flatten_json(value)
    markers = ("gfx_activity", "gpu_util", "vram_used", "memory_used", "temperature", "socket_power", "fan_speed")
    return [value] if any(any(marker in key for marker in markers) for key in flattened) else []


def _find_flat(flattened: dict[str, Any], aliases: Sequence[str]) -> tuple[str, Any] | tuple[None, None]:
    for alias in aliases:
        normalized = re.sub(r"[^a-z0-9]+", "_", alias.lower()).strip("_")
        for key, value in flattened.items():
            if key == normalized or key.endswith(f"_{normalized}") or normalized in key:
                return key, value
    return None, None


def _memory_gib(key: str | None, value: Any) -> float | None:
    number = _unit_number(value)
    if number is None:
        return None
    text = str(value or "").lower()
    key_text = str(key or "").lower()
    if "gib" in text or re.search(r"\bgb\b", text):
        return round(number, 3)
    if "kib" in text or re.search(r"\bkb\b", text):
        return round(number / 1024**2, 3)
    if "mib" in text or re.search(r"\bmb\b", text):
        return round(number / 1024, 3)
    if "byte" in key_text or number > 1024**3:
        return round(number / GIB, 3)
    return round(number / 1024, 3)


def parse_amd_smi_json(output: str) -> list[dict[str, Any]]:
    """Parse AMD SMI JSON while tolerating release-specific key nesting."""

    try:
        decoded = json.loads(output or "{}")
    except json.JSONDecodeError:
        return []
    items: list[dict[str, Any]] = []
    for fallback_index, record in enumerate(_amd_records(decoded)):
        if not record:
            continue
        flattened = _flatten_json(record)
        _, index_value = _find_flat(flattened, ("gpu", "gpu_id", "index", "_vsg_index"))
        _, name_value = _find_flat(flattened, ("market_name", "product_name", "asic_name", "device_name", "name"))
        _, util_value = _find_flat(flattened, ("gfx_activity", "gpu_utilization", "gpu_util", "usage_gfx"))
        _, memory_util_value = _find_flat(flattened, ("vram_usage_percent", "memory_utilization", "mem_activity"))
        total_key, total_value = _find_flat(flattened, ("vram_total", "total_vram", "memory_total", "vram_size"))
        used_key, used_value = _find_flat(flattened, ("vram_used", "used_vram", "memory_used"))
        free_key, free_value = _find_flat(flattened, ("vram_free", "free_vram", "memory_free"))
        _, temperature_value = _find_flat(flattened, ("temperature_edge", "edge_temperature", "hotspot_temperature", "temperature"))
        _, fan_value = _find_flat(flattened, ("fan_speed_percent", "fan_speed", "fan"))
        _, power_value = _find_flat(flattened, ("current_socket_power", "socket_power", "average_socket_power", "power"))
        total = _memory_gib(total_key, total_value)
        used = _memory_gib(used_key, used_value)
        free = _memory_gib(free_key, free_value)
        if free is None and total is not None and used is not None:
            free = round(max(0.0, total - used), 3)
        index_number = _unit_number(index_value)
        items.append(
            {
                "index": int(index_number) if index_number is not None else fallback_index,
                "vendor": "AMD",
                "name": str(name_value or "AMD GPU"),
                "telemetry_status": "measured",
                "gpu_util_percent": _unit_number(util_value),
                "memory_util_percent": _unit_number(memory_util_value) if memory_util_value is not None else round(used / total * 100, 1) if used is not None and total else None,
                "memory_total_gib": total,
                "memory_used_gib": used,
                "memory_free_gib": free,
                "temperature_c": _unit_number(temperature_value),
                "fan_percent": _unit_number(fan_value),
                "power_w": _unit_number(power_value),
                "power_limit_w": None,
                "source": "amd-smi metric JSON",
            }
        )
    return items


def parse_nvidia_telemetry(output: str) -> list[dict[str, Any]]:
    """Parse the explicit nvidia-smi CSV query used by the live collector.

    The query intentionally excludes serial numbers and GPU UUIDs.  nvidia-smi
    prints N/A for unsupported sensors; those values stay null instead of being
    estimated.
    """

    fields = (
        "index",
        "name",
        "gpu_util_percent",
        "memory_util_percent",
        "memory_total_mib",
        "memory_used_mib",
        "memory_free_mib",
        "temperature_c",
        "fan_percent",
        "power_w",
        "power_limit_w",
    )
    items: list[dict[str, Any]] = []
    for row in csv.reader(line for line in output.splitlines() if line.strip()):
        if len(row) < len(fields):
            continue
        values = dict(zip(fields, (value.strip() for value in row), strict=False))
        index_value = _number(values["index"])
        total = _number(values["memory_total_mib"])
        used = _number(values["memory_used_mib"])
        free = _number(values["memory_free_mib"])
        items.append(
            {
                "index": int(index_value) if index_value is not None else len(items),
                "vendor": "NVIDIA",
                "name": values["name"] or "NVIDIA GPU",
                "telemetry_status": "measured",
                "gpu_util_percent": _number(values["gpu_util_percent"]),
                "memory_util_percent": _number(values["memory_util_percent"]),
                "memory_total_gib": round(total / 1024, 2) if total is not None else None,
                "memory_used_gib": round(used / 1024, 2) if used is not None else None,
                "memory_free_gib": round(free / 1024, 2) if free is not None else None,
                "temperature_c": _number(values["temperature_c"]),
                "fan_percent": _number(values["fan_percent"]),
                "power_w": _number(values["power_w"]),
                "power_limit_w": _number(values["power_limit_w"]),
                "source": "nvidia-smi explicit CSV query",
            }
        )
    return items


def parse_nvidia_process_memory(output: str) -> dict[int, dict[str, Any]]:
    """Parse NVIDIA per-compute-process VRAM without exposing GPU UUIDs."""

    result: dict[int, dict[str, Any]] = {}
    for row in csv.reader(line for line in output.splitlines() if line.strip()):
        if len(row) < 2:
            continue
        try:
            pid = int(row[0].strip())
        except ValueError:
            continue
        used_mib = _number(row[1])
        if used_mib is None:
            continue
        item = result.setdefault(
            pid,
            {"gpu_memory_used_gib": 0.0, "gpu_process_entries": 0, "source": "nvidia-smi compute-apps"},
        )
        item["gpu_memory_used_gib"] += used_mib / 1024
        item["gpu_process_entries"] += 1
    for item in result.values():
        item["gpu_memory_used_gib"] = round(float(item["gpu_memory_used_gib"]), 3)
    return result


def parse_windows_gpu_counters(output: str) -> list[dict[str, Any]]:
    try:
        decoded = json.loads(output or "[]")
    except json.JSONDecodeError:
        return []
    values = decoded if isinstance(decoded, list) else [decoded]
    items: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        dedicated = value.get("DedicatedBytes")
        shared = value.get("SharedBytes")
        utilization = value.get("UtilizationPercent")
        if not isinstance(dedicated, (int, float)) and not isinstance(shared, (int, float)):
            continue
        items.append(
            {
                "adapter": str(value.get("Adapter") or "")[:80],
                "dedicated_used_gib": round(float(dedicated or 0) / GIB, 3),
                "shared_used_gib": round(float(shared or 0) / GIB, 3),
                "gpu_util_percent": round(float(utilization or 0), 1),
            }
        )
    return sorted(items, key=lambda item: item["dedicated_used_gib"] + item["shared_used_gib"], reverse=True)


def _nvidia_live(runner: Runner) -> list[dict[str, Any]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    query = ",".join(
        (
            "index",
            "name",
            "utilization.gpu",
            "utilization.memory",
            "memory.total",
            "memory.used",
            "memory.free",
            "temperature.gpu",
            "fan.speed",
            "power.draw",
            "power.limit",
        )
    )
    result = runner(
        [executable, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
        5,
    )
    return parse_nvidia_telemetry(result.stdout) if result.returncode == 0 else []


def _nvidia_process_memory(runner: Runner) -> tuple[dict[int, dict[str, Any]], str]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {}, "unavailable"
    result = runner(
        [
            executable,
            "--query-compute-apps=pid,used_gpu_memory,gpu_uuid",
            "--format=csv,noheader,nounits",
        ],
        5,
    )
    if result.returncode != 0:
        return {}, "unavailable"
    return parse_nvidia_process_memory(result.stdout), "measured"


def _amd_live(runner: Runner) -> list[dict[str, Any]]:
    executable = shutil.which("amd-smi")
    if not executable:
        return []
    result = runner(
        [executable, "metric", "--gpu", "all", "--mem-usage", "--usage", "--power", "--temperature", "--fan", "--json"],
        7,
    )
    return parse_amd_smi_json(result.stdout) if result.returncode == 0 else []


def _windows_gpu_counters(runner: Runner) -> list[dict[str, Any]]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if os.name != "nt" or not powershell:
        return []
    script = (
        "$c=Get-Counter -Counter '\\GPU Adapter Memory(*)\\Dedicated Usage','\\GPU Adapter Memory(*)\\Shared Usage','\\GPU Engine(*)\\Utilization Percentage' -MaxSamples 1 -ErrorAction Stop; "
        "$rows=@{}; foreach($s in $c.CounterSamples){ if($s.Path -match 'luid_(0x[0-9a-f]+_0x[0-9a-f]+_phys_[0-9]+)'){ $id=$Matches[1]; "
        "if(-not $rows.ContainsKey($id)){ $rows[$id]=[ordered]@{Adapter=$id;DedicatedBytes=0;SharedBytes=0;UtilizationPercent=0} }; "
        "if($s.Path -like '*gpu adapter memory*dedicated usage'){ $rows[$id].DedicatedBytes=[double]$s.CookedValue } "
        "elseif($s.Path -like '*gpu adapter memory*shared usage'){ $rows[$id].SharedBytes=[double]$s.CookedValue } "
        "elseif($s.Path -like '*gpu engine*' -and [double]$s.CookedValue -gt $rows[$id].UtilizationPercent){ $rows[$id].UtilizationPercent=[double]$s.CookedValue } } }; "
        "@($rows.Values | ForEach-Object {[pscustomobject]$_}) | ConvertTo-Json -Compress"
    )
    result = runner([powershell, "-NoProfile", "-NonInteractive", "-Command", script], 6)
    return parse_windows_gpu_counters(result.stdout) if result.returncode == 0 else []


def _disk_roots(config: AppConfig) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    if os.name == "nt":
        system_root = (os.environ.get("SystemDrive") or "C:") + "\\"
    else:
        system_root = "/"
    values.append((system_root, "system"))
    for configured in config.project_roots:
        path = Path(configured).expanduser()
        root = path.anchor or str(path)
        if root:
            values.append((root, "project"))
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for root, scope in values:
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen:
            seen.add(key)
            unique.append((root, scope))
    return unique


def _collect_disks(config: AppConfig) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for root, scope in _disk_roots(config):
        try:
            usage = psutil.disk_usage(root)
        except (OSError, PermissionError):
            result.append(
                {
                    "root": root,
                    "scope": scope,
                    "status": "unavailable",
                    "total_gib": None,
                    "used_gib": None,
                    "free_gib": None,
                    "used_percent": None,
                    "low_space": None,
                    "low_free_threshold_gib": config.low_disk_free_gib,
                }
            )
            continue
        free_gib = usage.free / GIB
        result.append(
            {
                "root": root,
                "scope": scope,
                "status": "measured",
                "total_gib": round(usage.total / GIB, 2),
                "used_gib": round(usage.used / GIB, 2),
                "free_gib": round(free_gib, 2),
                "used_percent": round(float(usage.percent), 1),
                "low_space": free_gib < config.low_disk_free_gib,
                "low_free_threshold_gib": config.low_disk_free_gib,
            }
        )
    return result


def _collect_sensors() -> dict[str, Any]:
    temperatures: list[dict[str, Any]] = []
    fans: list[dict[str, Any]] = []
    temperature_reader = getattr(psutil, "sensors_temperatures", None)
    if callable(temperature_reader):
        try:
            for group, entries in (temperature_reader(fahrenheit=False) or {}).items():
                for index, entry in enumerate(entries):
                    current = getattr(entry, "current", None)
                    if current is None:
                        continue
                    temperatures.append(
                        {
                            "group": str(group)[:80],
                            "label": str(getattr(entry, "label", "") or f"sensor-{index + 1}")[:80],
                            "current_c": round(float(current), 1),
                            "high_c": getattr(entry, "high", None),
                            "critical_c": getattr(entry, "critical", None),
                            "source": "psutil/native",
                        }
                    )
        except (OSError, RuntimeError, psutil.Error):
            temperatures = []
    fan_reader = getattr(psutil, "sensors_fans", None)
    if callable(fan_reader):
        try:
            for group, entries in (fan_reader() or {}).items():
                for index, entry in enumerate(entries):
                    fans.append(
                        {
                            "group": str(group)[:80],
                            "label": str(getattr(entry, "label", "") or f"fan-{index + 1}")[:80],
                            "rpm": int(getattr(entry, "current", 0) or 0),
                            "source": "psutil/native",
                        }
                    )
        except (OSError, RuntimeError, psutil.Error):
            fans = []
    return {
        "temperatures": temperatures,
        "fans": fans,
        "temperature_status": "measured" if temperatures else "unavailable",
        "fan_status": "measured" if fans else "unavailable",
        "limitations": (
            []
            if temperatures or fans
            else ["操作系统未向当前非提权进程暴露温度或风扇传感器；VSG 未使用估算值"]
        ),
    }


def _cpu_frequencies_mhz() -> tuple[float | None, float | None]:
    """Return current/max CPU frequency only when psutil supports the probe."""

    reader = getattr(psutil, "cpu_freq", None)
    if not callable(reader):
        return None, None
    try:
        frequency = reader()
    except (AttributeError, NotImplementedError, OSError, psutil.Error):
        return None, None
    if frequency is None:
        return None, None

    def measured(name: str) -> float | None:
        try:
            value = float(getattr(frequency, name))
        except (AttributeError, TypeError, ValueError):
            return None
        return round(value, 0) if value > 0 else None

    return measured("current"), measured("max")


def _remote_scope(address: str) -> str:
    try:
        value = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return "unknown"
    if value.is_loopback:
        return "loopback"
    if value.is_private or value.is_link_local:
        return "private"
    return "public"


def _model_network_connections(services: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    pids = {
        int(service.get("process", {}).get("pid") or 0)
        for service in services
        if service.get("metadata", {}).get("model_runtime")
    }
    pids.discard(0)
    if not pids:
        return [], "no_model_runtime"
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return [], "unavailable"
    flows: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()
    for connection in connections:
        if connection.pid not in pids or not connection.raddr:
            continue
        remote_address = str(connection.raddr.ip)
        remote_port = int(connection.raddr.port)
        key = (int(connection.pid), remote_address, remote_port)
        if key in seen:
            continue
        seen.add(key)
        flows.append(
            {
                "pid": int(connection.pid),
                "remote_address": remote_address,
                "remote_port": remote_port,
                "scope": _remote_scope(remote_address),
                "state": str(connection.status),
                "protocol": "TCP" if connection.type == socket.SOCK_STREAM else "UDP",
            }
        )
    return sorted(flows, key=lambda item: (item["scope"], item["pid"], item["remote_address"])), "measured"


class TelemetryCollector:
    """Stateful, passive and read-only host telemetry collector."""

    def __init__(self, config: AppConfig, runner: Runner = _run_command):
        self.config = config
        self.runner = runner
        self._last_network_at: float | None = None
        self._last_network_sent: int | None = None
        self._last_network_recv: int | None = None
        self._last_power_at: float | None = None
        self._energy_wh = 0.0
        self._hardware: dict[str, Any] | None = None
        self._hardware_at = 0.0

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def _static_hardware(self, now: float) -> dict[str, Any]:
        if self._hardware is None or now - self._hardware_at > 300:
            self._hardware = collect_hardware()
            self._hardware_at = now
        return self._hardware

    def _gpus(self, now: float) -> list[dict[str, Any]]:
        hardware = self._static_hardware(now)
        measured = [*_nvidia_live(self.runner), *_amd_live(self.runner)]
        if measured:
            inventory_by_vendor: dict[str, list[dict[str, Any]]] = {}
            for item in hardware.get("gpus", []):
                inventory_by_vendor.setdefault(str(item.get("vendor") or "Unknown"), []).append(item)
            measured_counts: dict[str, int] = {}
            for item in measured:
                vendor = str(item.get("vendor") or "Unknown")
                index = measured_counts.get(vendor, 0)
                measured_counts[vendor] = index + 1
                inventory = (inventory_by_vendor.get(vendor) or [{}])[min(index, len(inventory_by_vendor.get(vendor) or [{}]) - 1)]
                if item.get("name") in {None, "", "AMD GPU"}:
                    item["name"] = inventory.get("name") or item.get("name")
                for key in ("memory_total_gib", "memory_free_gib"):
                    if item.get(key) is None:
                        item[key] = inventory.get(key)
            for vendor, inventory_items in inventory_by_vendor.items():
                already = measured_counts.get(vendor, 0)
                for item in inventory_items[already:]:
                    measured.append(
                        {
                            "index": len(measured),
                            "vendor": vendor,
                            "name": item.get("name"),
                            "telemetry_status": "unavailable",
                            "gpu_util_percent": None,
                            "memory_util_percent": None,
                            "memory_total_gib": item.get("memory_total_gib"),
                            "memory_used_gib": None,
                            "memory_free_gib": item.get("memory_free_gib"),
                            "temperature_c": None,
                            "fan_percent": None,
                            "power_w": None,
                            "power_limit_w": None,
                            "source": item.get("memory_source") or "hardware inventory",
                            "integrated": bool(item.get("integrated")),
                            "limitation": "No usable vendor telemetry interface was detected for this adapter",
                        }
                    )
            return measured
        gpus: list[dict[str, Any]] = []
        for index, item in enumerate(hardware.get("gpus", [])):
            gpus.append(
                {
                    "index": index,
                    "vendor": item.get("vendor"),
                    "name": item.get("name"),
                    "telemetry_status": "unavailable",
                    "gpu_util_percent": None,
                    "memory_util_percent": None,
                    "memory_total_gib": item.get("memory_total_gib"),
                    "memory_used_gib": None,
                    "memory_free_gib": item.get("memory_free_gib"),
                    "temperature_c": None,
                    "fan_percent": None,
                    "power_w": None,
                    "power_limit_w": None,
                    "source": item.get("memory_source") or "hardware inventory",
                    "integrated": bool(item.get("integrated")),
                    "limitation": "未检测到可用的厂商遥测 CLI；仅展示硬件清单，不估算实时负载",
                }
            )
        if os.name == "nt" and gpus:
            counters = _windows_gpu_counters(self.runner)
            # Windows performance counters expose an adapter LUID but not the
            # marketing name.  Pair the busiest adapter to the discrete GPU
            # first and report the heuristic explicitly.
            ordered_gpu_indexes = sorted(
                range(len(gpus)),
                key=lambda index: (
                    bool(gpus[index].get("integrated")),
                    -(float(gpus[index].get("memory_total_gib") or 0)),
                ),
            )
            for gpu_index, counter in zip(ordered_gpu_indexes, counters, strict=False):
                gpu = gpus[gpu_index]
                used = counter["dedicated_used_gib"]
                total = gpu.get("memory_total_gib")
                gpu.update(
                    {
                        "telemetry_status": "measured_partial",
                        "gpu_util_percent": counter["gpu_util_percent"],
                        "memory_used_gib": used,
                        "memory_free_gib": round(max(0.0, float(total) - used), 3) if total is not None else None,
                        "memory_util_percent": round(used / float(total) * 100, 1) if total else None,
                        "shared_memory_used_gib": counter["shared_used_gib"],
                        "source": "Windows GPU performance counters",
                        "mapping_confidence": "heuristic_name_mapping",
                        "limitation": "显存/利用率为 Windows 适配器计数器实测；LUID 到营销型号按独显优先启发式映射，温度/风扇/功耗仍不可用",
                    }
                )
        return gpus

    def _network(self, now: float, services: list[dict[str, Any]]) -> dict[str, Any]:
        counters = psutil.net_io_counters()
        elapsed = now - self._last_network_at if self._last_network_at is not None else None
        sent_rate = None
        recv_rate = None
        if elapsed and elapsed > 0 and self._last_network_sent is not None and self._last_network_recv is not None:
            sent_rate = max(0.0, (counters.bytes_sent - self._last_network_sent) / elapsed)
            recv_rate = max(0.0, (counters.bytes_recv - self._last_network_recv) / elapsed)
        self._last_network_at = now
        self._last_network_sent = counters.bytes_sent
        self._last_network_recv = counters.bytes_recv
        flows, connection_status = _model_network_connections(services)
        return {
            "bytes_sent_total": int(counters.bytes_sent),
            "bytes_recv_total": int(counters.bytes_recv),
            "send_mib_per_second": round(sent_rate / MIB, 3) if sent_rate is not None else None,
            "receive_mib_per_second": round(recv_rate / MIB, 3) if recv_rate is not None else None,
            "rate_status": "measured" if sent_rate is not None else "warming_up",
            "model_connections_status": connection_status,
            "model_remote_connections": flows,
            "public_remote_connections": sum(item["scope"] == "public" for item in flows),
            "privacy": "仅记录模型服务的远端 IP、端口和连接状态；不抓包、不读取 DNS、URL 或内容",
        }

    def _service_resources(self, services: list[dict[str, Any]]) -> dict[str, Any]:
        nvidia_by_pid, gpu_status = _nvidia_process_memory(self.runner)
        items: list[dict[str, Any]] = []
        for service in services:
            if service.get("source") not in {"host", "agent", "windows_service"}:
                continue
            process = service.get("process") or {}
            pid = int(process.get("pid") or 0)
            if pid <= 0:
                continue
            rss_gib = None
            process_status = "measured"
            try:
                rss_gib = round(psutil.Process(pid).memory_info().rss / GIB, 3)
            except (psutil.Error, OSError):
                process_status = "unavailable"
            gpu = nvidia_by_pid.get(pid)
            items.append(
                {
                    "service_id": service.get("id"),
                    "service_fingerprint": service.get("fingerprint"),
                    "display_name": service.get("display_name"),
                    "runtime": service.get("runtime"),
                    "model_runtime": bool((service.get("metadata") or {}).get("model_runtime")),
                    "pid": pid,
                    "cpu_percent": process.get("cpu_percent"),
                    "memory_percent": process.get("memory_percent"),
                    "rss_gib": rss_gib,
                    "process_status": process_status,
                    "gpu_memory_used_gib": gpu.get("gpu_memory_used_gib") if gpu else None,
                    "gpu_process_entries": gpu.get("gpu_process_entries") if gpu else 0,
                    "gpu_status": "measured" if gpu else gpu_status,
                    "gpu_source": gpu.get("source") if gpu else None,
                }
            )
        return {
            "status": "measured" if items else "unavailable",
            "gpu_process_status": gpu_status,
            "items": sorted(
                items,
                key=lambda item: (
                    not item["model_runtime"],
                    -float(item.get("gpu_memory_used_gib") or 0),
                    -float(item.get("rss_gib") or 0),
                ),
            ),
            "limitations": [
                "NVIDIA 进程级显存仅在驱动和 nvidia-smi 暴露 compute-apps 数据时实测",
                "AMD 与 macOS 进程级显存未获普通用户稳定接口时保持未知，不按整卡占用反推到进程",
                "Docker/WSL 的 PID 命名空间与宿主机不同，首版不做不可靠的一对一显存归属",
            ],
        }

    def collect(self, services: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        now = time.time()
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        current_frequency_mhz, max_frequency_mhz = _cpu_frequencies_mhz()
        gpus = self._gpus(now)
        measured_power = sum(float(item["power_w"]) for item in gpus if item.get("power_w") is not None)
        if self._last_power_at is not None and 0 < now - self._last_power_at <= 120 and measured_power:
            self._energy_wh += measured_power * (now - self._last_power_at) / 3600
        self._last_power_at = now
        price = self.config.electricity_price_per_kwh
        service_list = services or []
        return {
            "schema_version": "1.0",
            "captured_at": now,
            "cpu": {
                # A short blocking sample avoids psutil's documented first-call
                # 0.0 sentinel and produces a meaningful live utilization value.
                "percent": round(float(psutil.cpu_percent(interval=0.1)), 1),
                "logical_cores": psutil.cpu_count(logical=True),
                "physical_cores": psutil.cpu_count(logical=False),
                "current_frequency_mhz": current_frequency_mhz,
                "max_frequency_mhz": max_frequency_mhz,
                "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
                "power_w": None,
                "power_status": "unavailable",
            },
            "memory": {
                "total_gib": round(memory.total / GIB, 2),
                "used_gib": round(memory.used / GIB, 2),
                "available_gib": round(memory.available / GIB, 2),
                "used_percent": round(float(memory.percent), 1),
                "swap_used_gib": round(swap.used / GIB, 2),
                "swap_percent": round(float(swap.percent), 1),
            },
            "disks": _collect_disks(self.config),
            "gpus": gpus,
            "sensors": _collect_sensors(),
            "power": {
                "gpu_power_w": round(measured_power, 2) if measured_power else None,
                "system_power_w": None,
                "measurement_scope": "gpu_only" if measured_power else "unavailable",
                "energy_wh_since_start": round(self._energy_wh, 3) if measured_power else None,
                "estimated_cost_since_start": round(self._energy_wh / 1000 * price, 4) if measured_power else None,
                "electricity_price_per_kwh": price,
                "note": "仅按厂商实测 GPU 功耗积分；不把 CPU/整机功耗估算冒充实测",
            },
            "network": self._network(now, service_list),
            "service_resources": self._service_resources(service_list),
            "hardware": {
                "platform": platform.system(),
                "gpu_count": len(gpus),
                "capacity_gpu_count": sum(
                    item.get("memory_total_gib") is not None and not item.get("integrated", False)
                    for item in gpus
                ),
                "multi_gpu": sum(
                    item.get("memory_total_gib") is not None and not item.get("integrated", False)
                    for item in gpus
                ) > 1,
                "source": "local passive collectors",
            },
        }
