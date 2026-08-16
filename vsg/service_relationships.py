from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import shlex
import socket
import subprocess
from collections import defaultdict
from typing import Any, Callable, Iterable

import psutil


ConnectionProvider = Callable[[], Iterable[Any]]
ProcessNameProvider = Callable[[int], str]
LocalAddressProvider = Callable[[], Iterable[str]]
CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{12,64}$", re.IGNORECASE)


def _display_command(arguments: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def recommended_operations(service: dict[str, Any]) -> list[dict[str, Any]]:
    """Return display-only lifecycle guidance; VSG never executes these items."""

    source = str(service.get("source") or "")
    metadata = service.get("metadata") or {}
    process = service.get("process") or {}
    agent = service.get("agent") or {}
    pid = int(process.get("pid") or 0)
    items: list[dict[str, Any]] = []

    def add(
        kind: str,
        title: str,
        instruction: str,
        arguments: list[str] | None = None,
    ) -> None:
        items.append(
            {
                "kind": kind,
                "title": title,
                "instruction": instruction,
                "argv": arguments,
                "copy_text": _display_command(arguments) if arguments else None,
                "will_execute": False,
                "requires_manual_review": True,
            }
        )

    if metadata.get("vsg_instance"):
        if metadata.get("vsg_current_instance"):
            add(
                "vsg_current_instance",
                "保留当前 VSG 控制台实例",
                "这是当前控制台自身。请使用界面的退出入口或 Stop-VSG 脚本结束，不要从服务清单直接停止。",
            )
        else:
            add(
                "vsg_previous_instance",
                "复核其他 VSG 实例及其数据目录意图",
                "检测到另一个受保护的 VSG 进程。先核对 PID、端口、启动时间及是否显式使用独立数据目录；确认属于非预期旧实例后，再使用对应版本的 Stop-VSG 脚本或原终端结束。",
            )
    elif source == "docker":
        compose_project = str(metadata.get("compose_project") or "")
        compose_service = str(metadata.get("compose_service") or "")
        if compose_project and compose_service:
            arguments = [
                "docker",
                "compose",
                "--project-name",
                compose_project,
                "stop",
                compose_service,
            ]
            add(
                "docker_compose",
                "通过 Docker Compose 停止服务",
                "先在 Compose 项目目录复核服务名与依赖，再复制命令执行。",
                arguments,
            )
        container_id = str(metadata.get("container_id") or "")
        if CONTAINER_ID_RE.fullmatch(container_id):
            add(
                "docker",
                "通过 Docker Engine 停止容器",
                "该命令只作为建议展示；请先检查重启策略。",
                ["docker", "stop", container_id],
            )
    elif source == "wsl":
        distro = str(metadata.get("distribution") or "")[:128]
        linux_pid = int(metadata.get("linux_pid") or 0)
        if distro and linux_pid > 0:
            add(
                "wsl",
                "在对应 WSL 发行版中停止进程",
                "请先在发行版内复核 PID 与进程身份；Windows PID 不能替代该 Linux PID。",
                ["wsl.exe", "-d", distro, "--", "kill", "-TERM", str(linux_pid)],
            )
    elif source == "windows_service" or service.get("windows_services"):
        names = [str(item)[:160] for item in service.get("windows_services") or []]
        add(
            "windows_service",
            "通过 Windows 服务管理器停止",
            f"打开 services.msc，定位“{names[0] if names else service.get('display_name')}”，复核依赖后停止。",
            ["services.msc"],
        )
    elif source == "agent" or agent.get("provider"):
        provider = str(agent.get("provider") or service.get("display_name") or "Agent/IDE")
        add(
            "agent_session",
            f"在 {provider} 内结束对应会话",
            "先保存未提交工作，再从原 Agent/IDE 会话终止任务或关闭对应终端。",
        )

    manager = str(metadata.get("lifecycle_manager") or "")
    if manager == "systemd" and pid > 0:
        add(
            "systemd",
            "先定位 systemd 托管单元",
            "运行只读状态命令确认 unit 名称，再人工执行 systemctl stop <unit>。",
            ["systemctl", "status", str(pid)],
        )
    elif manager == "launchd" and pid > 0:
        add(
            "launchd",
            "先定位 launchd 标签",
            "运行只读命令确认 domain/label，再人工使用 launchctl bootout。",
            ["launchctl", "procinfo", str(pid)],
        )
    elif manager == "PM2":
        add(
            "pm2",
            "通过 PM2 复核并停止应用",
            "先运行 pm2 list 确认应用名，再人工执行 pm2 stop <app-name>。",
            ["pm2", "list"],
        )
    elif manager == "supervisord":
        add(
            "supervisord",
            "通过 Supervisor 复核托管任务",
            "先查看状态确认任务名，再人工执行 supervisorctl stop <name>。",
            ["supervisorctl", "status"],
        )

    if not items and service.get("protected"):
        parent_names = [
            str(item.get("name") or "unknown")[:80]
            for item in (service.get("ancestor_chain") or [])[:3]
        ]
        parent_hint = f" 可见父进程：{' → '.join(parent_names)}。" if parent_names else ""
        add(
            "manual_review",
            "从原启动入口结束服务",
            "根据父进程证据回到原项目终端、IDE 或生命周期管理器人工停止。" + parent_hint,
        )
    return items


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _address(value: Any) -> tuple[str, int] | None:
    if not value:
        return None
    ip = getattr(value, "ip", None)
    port = getattr(value, "port", None)
    if ip is None and isinstance(value, (tuple, list)) and len(value) >= 2:
        ip, port = value[0], value[1]
    try:
        return str(ip), int(port)
    except (TypeError, ValueError):
        return None


def _normalized_ip(address: str) -> str:
    try:
        value = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return address
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped:
        return str(value.ipv4_mapped)
    return str(value)


def _is_local_destination(
    address: str,
    endpoint_address: str,
    local_addresses: set[str],
) -> bool:
    destination = _normalized_ip(address)
    endpoint = _normalized_ip(endpoint_address)
    try:
        destination_ip = ipaddress.ip_address(destination)
    except ValueError:
        return destination == endpoint
    # These literals compare an observed listener; this function never binds a socket.
    if endpoint_address in {"0.0.0.0", "::", "*", ""}:  # nosec B104
        return destination_ip.is_loopback or destination in local_addresses
    return destination == endpoint


def _default_connections() -> Iterable[Any]:
    return psutil.net_connections(kind="inet")


def _default_local_addresses() -> Iterable[str]:
    values = {"127.0.0.1", "::1"}
    try:
        for addresses in psutil.net_if_addrs().values():
            for item in addresses:
                if item.family in {socket.AF_INET, socket.AF_INET6}:
                    values.add(_normalized_ip(str(item.address)))
    except (psutil.Error, OSError):
        pass
    return values


def _default_process_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, OSError):
        return "受限或已退出进程"


def _local_dependencies(
    services: list[dict[str, Any]],
    connection_provider: ConnectionProvider,
    process_name_provider: ProcessNameProvider,
    local_address_provider: LocalAddressProvider,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    listeners: dict[tuple[str, int], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    service_by_pid: dict[int, dict[str, Any]] = {}
    for service in services:
        pid = int((service.get("process") or {}).get("pid") or 0)
        if pid > 0:
            service_by_pid[pid] = service
        for endpoint in service.get("endpoints") or []:
            protocol = str(endpoint.get("protocol") or "").upper()
            port = int(endpoint.get("port") or 0)
            if protocol == "TCP" and port > 0:
                listeners[(protocol, port)].append((service, endpoint))

    dependencies: list[dict[str, Any]] = []
    limitations: list[str] = []
    try:
        connections = list(connection_provider())
        status = "measured"
    except (psutil.AccessDenied, OSError) as exc:
        connections = []
        status = "unavailable"
        limitations.append(f"无法读取本机已建立连接：{type(exc).__name__}")

    try:
        local_addresses = {_normalized_ip(str(item)) for item in local_address_provider()}
    except (psutil.Error, OSError):
        local_addresses = {"127.0.0.1", "::1"}
        limitations.append("无法完整读取本机网卡地址；通配监听只匹配回环地址")
    seen: set[tuple[int, str, int]] = set()
    for connection in connections:
        if getattr(connection, "type", None) != socket.SOCK_STREAM:
            continue
        connection_status = str(getattr(connection, "status", ""))
        if connection_status not in {str(psutil.CONN_ESTABLISHED), "ESTABLISHED"}:
            continue
        pid = int(getattr(connection, "pid", 0) or 0)
        remote = _address(getattr(connection, "raddr", None))
        if pid <= 0 or remote is None:
            continue
        remote_address, remote_port = remote
        targets = listeners.get(("TCP", remote_port), [])
        for target, endpoint in targets:
            target_pid = int((target.get("process") or {}).get("pid") or 0)
            if pid == target_pid or not _is_local_destination(
                remote_address,
                str(endpoint.get("address") or ""),
                local_addresses,
            ):
                continue
            key = (pid, str(target.get("id") or ""), remote_port)
            if key in seen:
                continue
            seen.add(key)
            source_service = service_by_pid.get(pid)
            dependencies.append(
                {
                    "source_kind": "service" if source_service else "client_process",
                    "source_service_id": source_service.get("id") if source_service else None,
                    "source_pid": pid,
                    "source_name": (
                        source_service.get("display_name")
                        if source_service
                        else process_name_provider(pid)
                    ),
                    "target_service_id": target.get("id"),
                    "target_pid": target_pid,
                    "protocol": "TCP",
                    "port": remote_port,
                    "state": "ESTABLISHED",
                    "evidence": "本机 TCP 已建立连接的目标端口与监听服务一致",
                }
            )
    return dependencies, status, limitations


def build_stop_assessment(
    service: dict[str, Any],
    dependencies: list[dict[str, Any]],
) -> dict[str, Any]:
    process = service.get("process") or {}
    metadata = service.get("metadata") or {}
    agent = service.get("agent") or {}
    project = service.get("project") or {}
    pid = int(process.get("pid") or 0)
    incoming = [item for item in dependencies if item.get("target_service_id") == service.get("id")]
    endpoints = [
        {
            "protocol": item.get("protocol"),
            "address": item.get("address"),
            "port": item.get("port"),
            "exposure": item.get("exposure"),
        }
        for item in service.get("endpoints") or []
    ]

    blockers: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    if service.get("source") != "host":
        blockers.append("该服务由独立生命周期管理器托管，当前版本仅提供只读评估")
    if service.get("protected"):
        blockers.append("该服务或其归属规则已标记为受保护")
    if not metadata.get("stoppable_candidate"):
        blockers.append("该进程未进入普通宿主机开发/模型运行时安全停止白名单")
    if incoming:
        warnings.append(f"当前检测到 {len(incoming)} 个本机客户端依赖，停止可能中断正在进行的请求")
        evidence.extend(
            f"PID {item['source_pid']} {item['source_name']} 正在连接端口 {item['port']}"
            for item in incoming[:12]
        )
    if len(endpoints) > 1:
        warnings.append(f"该进程同时拥有 {len(endpoints)} 个监听端点，停止会一并关闭")
    if metadata.get("auto_restart") is True:
        warnings.append("检测到自动重启策略，停止后服务可能被重新拉起")
    lifecycle_manager = metadata.get("lifecycle_manager")
    if lifecycle_manager:
        warnings.append(f"生命周期归属：{lifecycle_manager}")
    if agent.get("provider"):
        warnings.append(f"服务与 {agent.get('provider')} 进程链相关联，Agent 仍活动时可能再次启动服务")
    if metadata.get("restart_count"):
        evidence.append(f"历史重启计数 {int(metadata.get('restart_count') or 0)}")
    evidence.extend(
        f"监听 {item['protocol']} {item['address']}:{item['port']}"
        for item in endpoints[:12]
    )
    for parent in (service.get("ancestor_chain") or [])[:6]:
        evidence.append(
            f"父进程 PID {int(parent.get('pid') or 0)} {str(parent.get('name') or 'unknown')[:160]}"
        )

    if blockers:
        decision = "blocked"
    elif incoming or metadata.get("auto_restart") is True or lifecycle_manager or agent.get("active"):
        decision = "review"
    else:
        decision = "allowed"

    relaunch_risk = "high" if metadata.get("auto_restart") is True else "medium" if lifecycle_manager or agent.get("active") else "low"
    command = str(process.get("command") or "")[:600]
    working_directory = process.get("cwd") or project.get("path")
    recovery_steps = [
        "先确认项目目录和配置快照仍可用",
        "在原项目终端中复核运行时与脱敏命令后手动重启",
        "重启后确认原端口、绑定地址、认证和模型加载状态",
    ]
    if lifecycle_manager:
        recovery_steps.insert(0, f"优先通过 {lifecycle_manager} 的原生方式恢复，而不是直接重放命令")

    return {
        "schema_version": "1.0",
        "service_id": service.get("id"),
        "service_fingerprint": service.get("fingerprint"),
        "pid": pid,
        "decision": decision,
        "can_request_stop": decision in {"allowed", "review"},
        "requires_confirmation": f"STOP {pid}" if pid > 0 else None,
        "blockers": blockers,
        "warnings": warnings,
        "evidence": evidence,
        "impact": {
            "client_count": len(incoming),
            "clients": incoming[:20],
            "endpoint_count": len(endpoints),
            "endpoints": endpoints,
            "project": project.get("name"),
            "agent": agent.get("provider"),
            "model_runtime": bool(metadata.get("model_runtime")),
        },
        "relaunch": {
            "risk": relaunch_risk,
            "auto_restart": metadata.get("auto_restart"),
            "restart_policy": metadata.get("restart_policy"),
            "lifecycle_manager": lifecycle_manager,
            "historical_restart_count": int(metadata.get("restart_count") or 0),
        },
        "recovery": {
            "working_directory": working_directory,
            "project_path": project.get("path"),
            "runtime": service.get("runtime"),
            "process_name": process.get("name"),
            "redacted_command": command or None,
            "steps": recovery_steps,
            "automatic_restart": False,
        },
        "recommended_operations": recommended_operations(service),
        "limitations": [
            "评估只使用当前本机进程、端口、连接和已识别生命周期证据；无法证明外部客户端或脚本不存在",
            "恢复信息只用于人工复核，VSG 不自动重放命令",
        ],
    }


def build_service_relationships(
    services: list[dict[str, Any]],
    connection_provider: ConnectionProvider = _default_connections,
    process_name_provider: ProcessNameProvider = _default_process_name,
    local_address_provider: LocalAddressProvider = _default_local_addresses,
    *,
    runtime_probes: list[dict[str, Any]] | None = None,
    telemetry: dict[str, Any] | None = None,
    service_benchmarks: list[dict[str, Any]] | None = None,
    calibration_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dependencies, connection_status, limitations = _local_dependencies(
        services, connection_provider, process_name_provider, local_address_provider
    )
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    known_nodes: set[str] = set()

    def add_node(node: dict[str, Any]) -> None:
        node_id = str(node.get("id") or "")
        if node_id and node_id not in known_nodes:
            known_nodes.add(node_id)
            nodes.append(node)

    for service in services:
        service_id = str(service.get("id") or service.get("fingerprint") or "unknown")
        process = service.get("process") or {}
        project = service.get("project") or {}
        agent = service.get("agent") or {}
        add_node(
            {
                "id": service_id,
                "kind": "service",
                "label": service.get("display_name") or process.get("name") or "service",
                "source": service.get("source"),
                "runtime": service.get("runtime"),
                "decision": (service.get("stop_assessment") or {}).get("decision"),
            }
        )
        pid = int(process.get("pid") or 0)
        process_id = (
            f"process:{pid}:{int(float(process.get('create_time') or 0) * 1000)}"
            if pid > 0
            else _stable_id("process", f"unknown|{service_id}")
        )
        add_node(
            {
                "id": process_id,
                "kind": "process",
                "label": process.get("name") or "process",
                "pid": pid,
            }
        )
        edges.append({"from": process_id, "to": service_id, "kind": "provides"})
        if project.get("name") or project.get("path"):
            project_id = _stable_id("project", str(project.get("path") or project.get("name")))
            add_node({"id": project_id, "kind": "project", "label": project.get("name") or "project"})
            edges.append({"from": project_id, "to": service_id, "kind": "owns"})
        if agent.get("provider"):
            agent_identity = agent.get("session_id") or service_id
            agent_id = _stable_id("agent", f"{agent.get('provider')}|{agent_identity}")
            add_node(
                {
                    "id": agent_id,
                    "kind": "agent",
                    "label": agent.get("provider"),
                    "session_known": bool(agent.get("session_id")),
                    "active": bool(agent.get("active")),
                }
            )
            edges.append({"from": agent_id, "to": service_id, "kind": "attributed_start"})
        for endpoint in service.get("endpoints") or []:
            endpoint_id = _stable_id(
                "endpoint",
                f"{endpoint.get('protocol')}|{endpoint.get('address')}|{endpoint.get('port')}",
            )
            add_node(
                {
                    "id": endpoint_id,
                    "kind": "endpoint",
                    "label": f"{endpoint.get('address')}:{endpoint.get('port')}",
                    "protocol": endpoint.get("protocol"),
                    "port": endpoint.get("port"),
                    "exposure": endpoint.get("exposure"),
                }
            )
            edges.append({"from": service_id, "to": endpoint_id, "kind": "listens"})

    for dependency in dependencies:
        source_id = dependency.get("source_service_id")
        if not source_id:
            source_id = f"client:{int(dependency.get('source_pid') or 0)}"
            add_node(
                {
                    "id": source_id,
                    "kind": "client_process",
                    "label": dependency.get("source_name"),
                    "pid": dependency.get("source_pid"),
                    "live_only": True,
                }
            )
        edges.append(
            {
                "from": source_id,
                "to": dependency.get("target_service_id"),
                "kind": "depends_on",
                "port": dependency.get("port"),
                "protocol": dependency.get("protocol"),
                "state": dependency.get("state"),
            }
        )

    assessments = {
        str(service.get("id")): build_stop_assessment(service, dependencies)
        for service in services
    }
    for node in nodes:
        if node.get("kind") == "service":
            node["decision"] = (assessments.get(str(node.get("id"))) or {}).get("decision")
    probes_by_id = {
        str(item.get("service_id")): item for item in (runtime_probes or [])
    }
    resources_by_id = {
        str(item.get("service_id")): item
        for item in ((telemetry or {}).get("service_resources") or {}).get("items") or []
    }
    latest_benchmark_by_fingerprint: dict[str, dict[str, Any]] = {}
    for item in service_benchmarks or []:
        fingerprint = str(item.get("service_fingerprint") or "")
        if fingerprint and fingerprint not in latest_benchmark_by_fingerprint:
            latest_benchmark_by_fingerprint[fingerprint] = item
    valid_profiles_by_fingerprint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in calibration_profiles or []:
        fingerprint = str(item.get("service_fingerprint") or "")
        if (
            fingerprint
            and item.get("validity", "valid") == "valid"
            and item.get("measured_safe")
        ):
            valid_profiles_by_fingerprint[fingerprint].append(item)

    global_memory_percent = ((telemetry or {}).get("memory") or {}).get(
        "used_percent"
    )
    global_gpu_memory = [
        float(item["memory_util_percent"])
        for item in (telemetry or {}).get("gpus") or []
        if item.get("memory_util_percent") is not None
    ]
    resource_guard_triggered = bool(
        (
            global_memory_percent is not None
            and float(global_memory_percent) >= 85
        )
        or any(value >= 85 for value in global_gpu_memory)
    )

    project_runtime_views: list[dict[str, Any]] = []
    for service in services:
        if not (service.get("metadata") or {}).get("model_runtime"):
            continue
        service_id = str(service.get("id") or "")
        fingerprint = str(service.get("fingerprint") or "")
        probe = probes_by_id.get(service_id) or {}
        performance = probe.get("performance") or {}
        loaded_model_names = {
            str(item.get("name") or "").casefold()
            for item in probe.get("models") or []
            if isinstance(item, dict) and item.get("name")
        }
        model_capacity_ambiguous = len(loaded_model_names) > 1
        profile_candidates = [
            item
            for item in valid_profiles_by_fingerprint.get(fingerprint, [])
            if len(loaded_model_names) == 1
            and str(item.get("model_name") or "").casefold()
            in loaded_model_names
        ]
        profile = max(
            profile_candidates,
            key=lambda item: (
                int(item.get("recommended_safe_concurrency") or 0),
                float(item.get("updated_at") or item.get("created_at") or 0),
            ),
            default=None,
        )
        running_requests = performance.get("requests_running")
        measured_limit = int((profile or {}).get("recommended_safe_concurrency") or 0)
        available_concurrency = (
            0
            if profile and resource_guard_triggered
            else max(0, measured_limit - int(running_requests or 0))
            if profile and running_requests is not None
            else None
        )
        project_runtime_views.append(
            {
                "project": service.get("project") or {},
                "agent": service.get("agent") or {},
                "service": {
                    "id": service_id,
                    "fingerprint": fingerprint,
                    "display_name": service.get("display_name"),
                    "runtime": service.get("runtime"),
                    "pid": (service.get("process") or {}).get("pid"),
                    "endpoints": service.get("endpoints") or [],
                },
                "health": probe.get("health") or "unknown",
                "model_load": probe.get("model_load") or {},
                "models": probe.get("models") or [],
                "performance": performance,
                "resources": resources_by_id.get(service_id) or {},
                "risk": service.get("risk") or {},
                "security": probe.get("security") or {},
                "latest_benchmark": latest_benchmark_by_fingerprint.get(fingerprint),
                "calibration_profile": profile,
                "capacity": {
                    "source": "measured_local" if profile else "unavailable",
                    "measured_safe_concurrency": measured_limit if profile else None,
                    "requests_running": running_requests,
                    "available_concurrency": available_concurrency,
                    "resource_guard_triggered": resource_guard_triggered,
                    "evidence_label": (
                        "当前 RAM/VRAM 已触发 85% 护栏，不建议新增并发"
                        if profile and resource_guard_triggered
                        else "基于本机实测档案"
                        if profile
                        else "当前同时加载多个模型，实测档案不能直接推算余量"
                        if model_capacity_ambiguous
                        else "暂无当前模型和硬件均匹配的有效实测档案"
                    ),
                },
            }
        )
    return {
        "schema_version": "1.1",
        "nodes": nodes,
        "edges": edges,
        "dependencies": dependencies,
        "assessments": assessments,
        "project_runtime_views": project_runtime_views,
        "summary": {
            "projects": sum(item.get("kind") == "project" for item in nodes),
            "agents": sum(item.get("kind") == "agent" for item in nodes),
            "services": sum(item.get("kind") == "service" for item in nodes),
            "local_dependencies": len(dependencies),
            "stop_allowed": sum(item.get("decision") == "allowed" for item in assessments.values()),
            "stop_review": sum(item.get("decision") == "review" for item in assessments.values()),
            "stop_blocked": sum(item.get("decision") == "blocked" for item in assessments.values()),
        },
        "collection": {
            "local_connections": connection_status,
            "limitations": limitations,
        },
        "privacy": "关系图只包含本机进程名、PID、归属、本机端口及已脱敏恢复命令；不持久化远端地址、原始命令或客户端内容",
    }
