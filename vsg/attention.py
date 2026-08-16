from __future__ import annotations

from typing import Any


_SEVERITY_ORDER = {"critical": 0, "high": 1, "warning": 2, "info": 3}


def _service_id(service: dict[str, Any]) -> str:
    return str(service.get("id") or "")


def _is_system_noise(service: dict[str, Any]) -> bool:
    """Return whether a service is useful evidence but poor default-page material."""

    if service.get("source") == "windows_service":
        return True
    if service.get("project", {}).get("path") or service.get("agent", {}).get("provider"):
        return False
    if service.get("metadata", {}).get("model_runtime"):
        return False
    endpoints = service.get("endpoints") or []
    return bool(endpoints) and all(str(item.get("protocol") or "").upper() == "UDP" for item in endpoints)


def _action_for(service: dict[str, Any]) -> str:
    assessment = service.get("stop_assessment") or {}
    if assessment.get("can_request_stop"):
        return "打开关停评估，确认影响后再决定是否停止"
    operations = assessment.get("recommended_operations") or []
    if operations:
        return str(operations[0].get("title") or "查看建议路径")
    return "查看归属、父进程和监听端口证据"


def build_attention_summary(
    services: list[dict[str, Any]],
    relationships: dict[str, Any] | None = None,
    posture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a small action-oriented home view from the complete local snapshot.

    This layer never changes the underlying assessment.  It only reduces
    default-page noise while keeping the complete inventory one click away.
    """

    relationships = relationships or {}
    posture = posture or {}
    focus_ids: set[str] = set()
    items: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()

    def add_item(
        item_id: str,
        kind: str,
        severity: str,
        title: str,
        summary: str,
        service_ids: list[str],
        evidence: list[str],
        action: str,
    ) -> None:
        if item_id in seen_item_ids:
            return
        seen_item_ids.add(item_id)
        valid_service_ids = [value for value in service_ids if value]
        focus_ids.update(valid_service_ids)
        items.append(
            {
                "id": item_id,
                "kind": kind,
                "severity": severity,
                "title": title,
                "summary": summary,
                "service_ids": valid_service_ids,
                "evidence": evidence[:8],
                "action": action,
            }
        )

    vsg_instances = [item for item in services if item.get("metadata", {}).get("vsg_instance")]
    if len(vsg_instances) > 1:
        pids = [int(item.get("process", {}).get("pid") or 0) for item in vsg_instances]
        ports = sorted(
            {
                int(endpoint.get("port") or 0)
                for service in vsg_instances
                for endpoint in service.get("endpoints") or []
                if int(endpoint.get("port") or 0) > 0
            }
        )
        add_item(
            "vsg-duplicate-instances",
            "duplicate_instance",
            "high",
            f"检测到 {len(vsg_instances)} 个 VSG 实例",
            "可能是旧版本、重复启动或显式隔离数据目录；当前实例不会自动停止其他实例。",
            [_service_id(item) for item in vsg_instances],
            [f"可见 PID：{', '.join(str(value) for value in pids if value)}", f"监听端口：{', '.join(str(value) for value in ports) or '未识别'}"],
            "逐个核对 PID、端口、启动时间和数据目录意图，再人工处理非预期实例",
        )

    for service in services:
        service_id = _service_id(service)
        metadata = service.get("metadata") or {}
        project = service.get("project") or {}
        agent = service.get("agent") or {}
        risk = service.get("risk") or {}
        endpoints = service.get("endpoints") or []

        if (
            project.get("path")
            or agent.get("provider")
            or metadata.get("model_runtime")
            or metadata.get("stoppable_candidate")
            or service.get("source") in {"docker", "wsl"}
        ) and not _is_system_noise(service):
            focus_ids.add(service_id)

        if risk.get("level") in {"review", "likely_stale"} and not (
            metadata.get("vsg_instance") and len(vsg_instances) > 1
        ):
            stale = risk.get("level") == "likely_stale"
            add_item(
                f"risk:{service_id}",
                "stale_candidate",
                "high" if stale else "warning",
                f"{service.get('display_name') or '服务'}：{'疑似遗留' if stale else '建议复核'}",
                str((risk.get("reasons") or ["当前证据需要人工复核"])[0]),
                [service_id],
                [str(value) for value in risk.get("reasons") or []],
                _action_for(service),
            )

        exposed = [item for item in endpoints if item.get("exposure") == "all_interfaces"]
        if exposed and (project.get("path") or metadata.get("model_runtime")):
            ports = ", ".join(f":{item.get('port')}" for item in exposed[:8])
            add_item(
                f"exposure:{service_id}",
                "network_exposure",
                "high",
                f"{service.get('display_name') or '服务'} 监听所有网卡",
                f"端口 {ports} 可能被局域网访问；是否可达公网仍取决于防火墙、路由与反向代理。",
                [service_id],
                [f"{item.get('protocol')} {item.get('address')}:{item.get('port')}" for item in exposed],
                "查看安全证据并复核绑定地址、认证与防火墙",
            )

        probe = service.get("runtime_probe") or {}
        if metadata.get("model_runtime") and probe.get("health") in {
            "loading",
            "unhealthy",
            "unreachable",
            "probe_error",
        }:
            add_item(
                f"runtime:{service_id}",
                "runtime_health",
                "warning",
                f"{service.get('display_name') or service.get('runtime')} 运行状态异常",
                f"只读适配器报告：{probe.get('health')} / 模型状态 {probe.get('model_load') or 'unknown'}",
                [service_id],
                [str(value) for value in probe.get("limitations") or []],
                "查看运行时、资源与日志证据",
            )

    for index, finding in enumerate(posture.get("findings") or []):
        severity = str(finding.get("severity") or "info").lower()
        if severity not in {"critical", "high", "warning"}:
            continue
        add_item(
            f"posture:{index}:{finding.get('title')}",
            "machine_health",
            severity,
            str(finding.get("title") or "本机健康状态需要处理"),
            str(finding.get("evidence") or "当前采集证据触发健康告警"),
            [],
            [str(finding.get("evidence") or "")],
            str(finding.get("action") or "打开 AI 运行体检查看证据"),
        )

    items.sort(key=lambda item: (_SEVERITY_ORDER.get(item["severity"], 9), item["title"]))
    items = items[:12]
    visible_ids = {service_id for item in items for service_id in item["service_ids"]}
    focus_ids.update(visible_ids)

    assessments = relationships.get("assessments") or {}
    can_stop = sum(bool(value.get("can_request_stop")) for value in assessments.values())
    managed_guidance = sum(
        value.get("decision") == "blocked" and bool(value.get("recommended_operations"))
        for value in assessments.values()
    )
    system_noise = sum(_is_system_noise(service) for service in services)
    model_runtimes = sum(bool(service.get("metadata", {}).get("model_runtime")) for service in services)
    return {
        "schema_version": "1.0",
        "items": items,
        "focus_service_ids": sorted(value for value in focus_ids if value),
        "summary": {
            "focus": len(focus_ids),
            "needs_action": len(items),
            "can_stop": can_stop,
            "managed_guidance": managed_guidance,
            "model_runtimes": model_runtimes,
            "system_noise": system_noise,
        },
        "limitations": [
            "今日关注只做视图降噪，不改变原始服务清单、风险评分或停止保护",
            "未显示在今日关注中的服务仍可在全部清单中查看",
        ],
    }
