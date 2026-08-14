from __future__ import annotations

import hashlib
from typing import Any


PROXY_RUNTIMES = {"nginx", "caddy", "traefik", "haproxy", "apache", "httpd"}


def _node_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:14]
    return f"{prefix}:{digest}"


def build_network_topology(
    services: list[dict[str, Any]], telemetry: dict[str, Any]
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "host", "kind": "host", "label": "Local host", "group": "host"}
    ]
    edges: list[dict[str, Any]] = []
    service_by_pid: dict[int, str] = {}
    exposure_counts = {"loopback": 0, "lan": 0, "all_interfaces": 0, "unknown": 0}
    for service in services:
        service_id = _node_id("service", str(service.get("id") or service.get("fingerprint") or "unknown"))
        process = service.get("process") or {}
        pid = int(process.get("pid") or 0)
        service_by_pid[pid] = service_id
        runtime = str(service.get("runtime") or "unknown").lower()
        source = str(service.get("source") or "host")
        group = "docker" if source == "docker" else "wsl" if source == "wsl" else "reverse_proxy" if runtime in PROXY_RUNTIMES else "host"
        endpoints = service.get("endpoints") or []
        nodes.append(
            {
                "id": service_id,
                "kind": "service",
                "label": service.get("display_name") or process.get("name") or "service",
                "group": group,
                "source": source,
                "runtime": runtime,
                "pid": pid,
                "project": (service.get("project") or {}).get("name"),
                "agent": (service.get("agent") or {}).get("provider"),
                "risk": (service.get("risk") or {}).get("level"),
            }
        )
        edges.append({"from": "host", "to": service_id, "kind": "runs"})
        for endpoint in endpoints:
            exposure = str(endpoint.get("exposure") or "unknown")
            exposure_counts[exposure if exposure in exposure_counts else "unknown"] += 1
            listener_value = f"{endpoint.get('protocol')}|{endpoint.get('address')}|{endpoint.get('port')}"
            endpoint_id = _node_id("listener", listener_value)
            nodes.append(
                {
                    "id": endpoint_id,
                    "kind": "listener",
                    "label": f"{endpoint.get('address')}:{endpoint.get('port')}",
                    "group": exposure,
                    "protocol": endpoint.get("protocol"),
                    "address": endpoint.get("address"),
                    "port": endpoint.get("port"),
                    "exposure": exposure,
                }
            )
            edges.append({"from": service_id, "to": endpoint_id, "kind": "listens"})

    public_connections = 0
    for flow in (telemetry.get("network") or {}).get("model_remote_connections") or []:
        remote = str(flow.get("remote_address") or "unknown")
        remote_port = int(flow.get("remote_port") or 0)
        remote_id = _node_id("remote", f"{remote}|{remote_port}")
        scope = str(flow.get("scope") or "unknown")
        public_connections += int(scope == "public")
        nodes.append(
            {
                "id": remote_id,
                "kind": "remote",
                "label": f"{remote}:{remote_port}",
                "group": scope,
                "scope": scope,
                "live_only": True,
            }
        )
        service_id = service_by_pid.get(int(flow.get("pid") or 0), "host")
        edges.append(
            {
                "from": service_id,
                "to": remote_id,
                "kind": "connected",
                "state": flow.get("state"),
                "protocol": flow.get("protocol"),
            }
        )

    unique_nodes = {item["id"]: item for item in nodes}
    unique_edges = {
        (item["from"], item["to"], item["kind"], item.get("protocol")): item
        for item in edges
    }
    return {
        "nodes": list(unique_nodes.values()),
        "edges": list(unique_edges.values()),
        "summary": {
            "services": sum(item.get("kind") == "service" for item in unique_nodes.values()),
            "listeners": sum(item.get("kind") == "listener" for item in unique_nodes.values()),
            "remote_connections": sum(item.get("kind") == "remote" for item in unique_nodes.values()),
            "public_connections": public_connections,
            "exposures": exposure_counts,
        },
        "privacy": "远端 IP 只存在于当前内存快照；历史时间线仅保留端点哈希和范围",
    }
