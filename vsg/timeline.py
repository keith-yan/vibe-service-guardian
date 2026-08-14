from __future__ import annotations

import hashlib
import time
from typing import Any

from .storage import Storage


SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _service_state(service: dict[str, Any]) -> dict[str, Any]:
    process = service.get("process") or {}
    endpoints = tuple(
        sorted(
            (
                str(item.get("protocol") or ""),
                str(item.get("address") or ""),
                int(item.get("port") or 0),
                str(item.get("exposure") or "unknown"),
            )
            for item in service.get("endpoints") or []
        )
    )
    return {
        "id": service.get("id"),
        "fingerprint": service.get("fingerprint"),
        "name": service.get("display_name"),
        "pid": int(process.get("pid") or 0),
        "create_time": process.get("create_time"),
        "project_name": (service.get("project") or {}).get("name"),
        "project_path": (service.get("project") or {}).get("path"),
        "agent_provider": (service.get("agent") or {}).get("provider"),
        "endpoints": endpoints,
        "model_runtime": bool((service.get("metadata") or {}).get("model_runtime")),
        "risk_level": (service.get("risk") or {}).get("level"),
    }


def _service_key(service: dict[str, Any]) -> str:
    return str(service.get("fingerprint") or service.get("id") or "unknown")


def _flow_key(flow: dict[str, Any]) -> str:
    value = "|".join(
        [
            str(flow.get("pid") or 0),
            str(flow.get("remote_address") or ""),
            str(flow.get("remote_port") or 0),
            str(flow.get("protocol") or ""),
        ]
    )
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:20]


class TimelineTracker:
    """Correlates passive snapshots without persisting raw remote addresses."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self._services: dict[str, dict[str, Any]] | None = None
        self._flows: dict[str, dict[str, Any]] | None = None
        self._last_sample_at = 0.0

    def _add(
        self,
        code: str,
        severity: str,
        title_zh: str,
        title_en: str,
        *,
        category: str,
        dedup_key: str,
        service: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        dedup_seconds: float = 60.0,
        observed_at: float | None = None,
    ) -> None:
        item = service or {}
        self.storage.add_timeline_event(
            {
                "observed_at": observed_at or time.time(),
                "category": category,
                "code": code,
                "severity": severity,
                "service_fingerprint": item.get("fingerprint"),
                "service_id": item.get("id"),
                "project_name": item.get("project_name"),
                "agent_provider": item.get("agent_provider"),
                "title_zh": title_zh,
                "title_en": title_en,
                "details": details or {},
                "dedup_key": dedup_key,
            },
            dedup_seconds=dedup_seconds,
        )

    def observe(self, snapshot: dict[str, Any], telemetry: dict[str, Any]) -> None:
        now = float(snapshot.get("generated_at") or time.time())
        current_services = {
            _service_key(service): _service_state(service)
            for service in snapshot.get("services") or []
        }
        raw_flows = (telemetry.get("network") or {}).get("model_remote_connections") or []
        current_flows = {_flow_key(flow): dict(flow) for flow in raw_flows}

        if self._services is not None:
            previous_keys = set(self._services)
            current_keys = set(current_services)
            for key in sorted(current_keys - previous_keys):
                service = current_services[key]
                self._add(
                    "SERVICE_STARTED",
                    "info",
                    f"服务已出现：{service.get('name') or key}",
                    f"Service appeared: {service.get('name') or key}",
                    category="lifecycle",
                    dedup_key=f"service-start:{key}:{service.get('create_time')}",
                    service=service,
                    details={"pid": service.get("pid"), "endpoints": service.get("endpoints")},
                    observed_at=now,
                )
            for key in sorted(previous_keys - current_keys):
                service = self._services[key]
                severity = "warning" if service.get("model_runtime") else "info"
                self._add(
                    "SERVICE_STOPPED",
                    severity,
                    f"服务已消失：{service.get('name') or key}",
                    f"Service disappeared: {service.get('name') or key}",
                    category="lifecycle",
                    dedup_key=f"service-stop:{key}:{service.get('create_time')}",
                    service=service,
                    details={"pid": service.get("pid")},
                    observed_at=now,
                )
            for key in sorted(previous_keys & current_keys):
                before = self._services[key]
                after = current_services[key]
                if before.get("create_time") != after.get("create_time"):
                    severity = "warning" if after.get("model_runtime") else "info"
                    self._add(
                        "SERVICE_RESTARTED",
                        severity,
                        f"服务疑似重启：{after.get('name') or key}",
                        f"Service likely restarted: {after.get('name') or key}",
                        category="lifecycle",
                        dedup_key=f"service-restart:{key}:{after.get('create_time')}",
                        service=after,
                        details={"previous_pid": before.get("pid"), "pid": after.get("pid")},
                        observed_at=now,
                    )
                if before.get("endpoints") != after.get("endpoints"):
                    severity = (
                        "critical"
                        if any(endpoint[3] == "all_interfaces" for endpoint in after["endpoints"])
                        else "warning"
                    )
                    self._add(
                        "EXPOSURE_CHANGED",
                        severity,
                        f"监听范围已变化：{after.get('name') or key}",
                        f"Listener exposure changed: {after.get('name') or key}",
                        category="security",
                        dedup_key="exposure:"
                        + key
                        + ":"
                        + hashlib.sha256(repr(after.get("endpoints")).encode()).hexdigest()[:16],
                        service=after,
                        details={"before": before.get("endpoints"), "after": after.get("endpoints")},
                        observed_at=now,
                    )
                before_owner = (
                    before.get("project_name"),
                    before.get("project_path"),
                    before.get("agent_provider"),
                )
                after_owner = (
                    after.get("project_name"),
                    after.get("project_path"),
                    after.get("agent_provider"),
                )
                if before_owner != after_owner:
                    self._add(
                        "ATTRIBUTION_CHANGED",
                        "info",
                        f"服务归属已变化：{after.get('name') or key}",
                        f"Service attribution changed: {after.get('name') or key}",
                        category="attribution",
                        dedup_key="owner:"
                        + key
                        + ":"
                        + hashlib.sha256(repr(after_owner).encode()).hexdigest()[:16],
                        service=after,
                        details={
                            "previous_project": before.get("project_name"),
                            "project": after.get("project_name"),
                            "previous_agent": before.get("agent_provider"),
                            "agent": after.get("agent_provider"),
                        },
                        observed_at=now,
                    )

        if self._flows is not None:
            previous_flows = set(self._flows)
            current_flow_keys = set(current_flows)
            services_by_pid = {item.get("pid"): item for item in current_services.values()}
            for key in sorted(current_flow_keys - previous_flows):
                flow = current_flows[key]
                service = services_by_pid.get(int(flow.get("pid") or 0))
                scope = str(flow.get("scope") or "unknown")
                severity = "critical" if scope == "public" else "info"
                self._add(
                    "MODEL_CONNECTION_OPENED",
                    severity,
                    "模型服务建立了公网连接" if scope == "public" else "模型服务建立了网络连接",
                    "Model service opened a public connection" if scope == "public" else "Model service opened a network connection",
                    category="network",
                    dedup_key=f"flow-open:{key}",
                    service=service,
                    details={
                        "endpoint_hash": key,
                        "remote_port": int(flow.get("remote_port") or 0),
                        "scope": scope,
                        "protocol": flow.get("protocol"),
                    },
                    observed_at=now,
                )
            for key in sorted(previous_flows - current_flow_keys):
                flow = self._flows[key]
                service = services_by_pid.get(int(flow.get("pid") or 0))
                self._add(
                    "MODEL_CONNECTION_CLOSED",
                    "info",
                    "模型服务网络连接已关闭",
                    "Model service network connection closed",
                    category="network",
                    dedup_key=f"flow-close:{key}",
                    service=service,
                    details={
                        "endpoint_hash": key,
                        "remote_port": int(flow.get("remote_port") or 0),
                        "scope": flow.get("scope"),
                        "protocol": flow.get("protocol"),
                    },
                    observed_at=now,
                )

        self._observe_thresholds(telemetry, now)
        if now - self._last_sample_at >= 15:
            self._sample(telemetry, now)
            self._last_sample_at = now
        self._services = current_services
        self._flows = current_flows

    def _observe_thresholds(self, telemetry: dict[str, Any], now: float) -> None:
        memory_percent = (telemetry.get("memory") or {}).get("used_percent")
        if memory_percent is not None and float(memory_percent) >= 90:
            self._add(
                "MEMORY_HIGH",
                "critical",
                "系统内存占用超过 90%",
                "System memory usage exceeded 90%",
                category="resource",
                dedup_key="threshold:memory-high",
                details={"percent": float(memory_percent)},
                dedup_seconds=600,
                observed_at=now,
            )
        for gpu in telemetry.get("gpus") or []:
            index = int(gpu.get("index") or 0)
            memory_used = gpu.get("memory_util_percent")
            temperature = gpu.get("temperature_c")
            if memory_used is not None and float(memory_used) >= 90:
                self._add(
                    "GPU_VRAM_HIGH",
                    "critical",
                    f"GPU {index} 显存占用超过 90%",
                    f"GPU {index} VRAM usage exceeded 90%",
                    category="resource",
                    dedup_key=f"threshold:gpu-vram:{index}",
                    details={"gpu_index": index, "percent": float(memory_used)},
                    dedup_seconds=600,
                    observed_at=now,
                )
            if temperature is not None and float(temperature) >= 85:
                self._add(
                    "GPU_TEMPERATURE_HIGH",
                    "critical",
                    f"GPU {index} 温度达到 85°C 或更高",
                    f"GPU {index} temperature reached 85°C or higher",
                    category="resource",
                    dedup_key=f"threshold:gpu-temperature:{index}",
                    details={"gpu_index": index, "temperature_c": float(temperature)},
                    dedup_seconds=600,
                    observed_at=now,
                )
        for disk in telemetry.get("disks") or []:
            free = disk.get("free_gib")
            threshold = disk.get("low_free_threshold_gib")
            if free is not None and threshold is not None and float(free) < float(threshold):
                disk_key = hashlib.sha256(
                    str(disk.get("root") or disk.get("mount") or disk.get("device") or "disk").encode()
                ).hexdigest()[:12]
                self._add(
                    "DISK_SPACE_LOW",
                    "warning",
                    "模型存储磁盘剩余空间不足",
                    "Model storage disk is low on free space",
                    category="resource",
                    dedup_key=f"threshold:disk:{disk_key}",
                    details={"free_gib": float(free), "threshold_gib": float(threshold)},
                    dedup_seconds=600,
                    observed_at=now,
                )

    def _sample(self, telemetry: dict[str, Any], now: float) -> None:
        gpu_values = [
            float(item["memory_util_percent"])
            for item in telemetry.get("gpus") or []
            if item.get("memory_util_percent") is not None
        ]
        temperatures = [
            float(item["temperature_c"])
            for item in telemetry.get("gpus") or []
            if item.get("temperature_c") is not None
        ]
        free_values = [
            float(item["free_gib"])
            for item in telemetry.get("disks") or []
            if item.get("free_gib") is not None
        ]
        self.storage.add_telemetry_sample(
            {
                "observed_at": now,
                "cpu_percent": (telemetry.get("cpu") or {}).get("percent"),
                "memory_percent": (telemetry.get("memory") or {}).get("used_percent"),
                "gpu_memory_percent": max(gpu_values) if gpu_values else None,
                "gpu_temperature_c": max(temperatures) if temperatures else None,
                "disk_free_gib": min(free_values) if free_values else None,
                "public_connections": (telemetry.get("network") or {}).get("public_remote_connections") or 0,
            }
        )


def build_incident_view(storage: Storage, since_hours: int = 24) -> dict[str, Any]:
    hours = max(1, min(int(since_hours), 24 * 30))
    since = time.time() - hours * 3600
    timeline = storage.recent_timeline_events(500, since=since)
    logs = storage.recent_log_events(500, since=since)
    combined: list[dict[str, Any]] = []
    for item in timeline:
        combined.append({"source": "timeline", **item})
    for item in logs:
        raw_severity = str(item.get("severity") or "info")
        severity = "critical" if raw_severity in {"critical", "high"} else "warning" if raw_severity == "medium" else "info"
        combined.append(
            {
                "source": "log",
                "last_seen": item.get("last_seen"),
                "first_seen": item.get("first_seen"),
                "severity": severity,
                "category": item.get("category"),
                "code": item.get("code"),
                "service_fingerprint": item.get("service_fingerprint"),
                "title_zh": item.get("message"),
                "title_en": item.get("message"),
                "occurrences": item.get("occurrences"),
                "details": {"watch_id": item.get("watch_id"), "runtime": item.get("runtime")},
            }
        )
    combined.sort(key=lambda item: float(item.get("last_seen") or 0), reverse=True)
    counts = {"critical": 0, "warning": 0, "info": 0}
    for item in combined:
        severity = str(item.get("severity") or "info")
        counts[severity if severity in counts else "info"] += 1
    highest = max(counts, key=lambda key: (counts[key] > 0, SEVERITY_ORDER[key])) if combined else "info"
    if counts["critical"]:
        health = "critical"
    elif counts["warning"]:
        health = "warning"
    else:
        health = "healthy"
    return {
        "since": since,
        "hours": hours,
        "health": health,
        "highest_severity": highest,
        "counts": counts,
        "items": combined[:500],
        "samples": storage.telemetry_samples(since, 2000),
        "privacy": "历史网络事件仅保存不可逆端点哈希、范围和端口，不保存远端 IP 或流量内容",
    }
