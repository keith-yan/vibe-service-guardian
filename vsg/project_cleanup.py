from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any


def _group_identity(service: dict[str, Any]) -> tuple[str, str]:
    project = service.get("project") or {}
    project_name = str(project.get("name") or "未归类项目")[:160]
    private_key = str(project.get("path") or project_name)
    group_id = hashlib.sha256(private_key.encode("utf-8", errors="replace")).hexdigest()[:16]
    return group_id, project_name


def _is_recommended(service: dict[str, Any], assessment: dict[str, Any]) -> bool:
    metadata = service.get("metadata") or {}
    risk = service.get("risk") or {}
    return bool(
        assessment.get("can_request_stop")
        and (
            risk.get("level") == "likely_stale"
            or metadata.get("historical_lifecycle_label") == "safe_cleanup"
        )
    )


def build_project_cleanup_plans(
    services: list[dict[str, Any]],
    relationships: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a read-only project cleanup preview.

    The plan deliberately contains no batch execution primitive.  Every
    actionable item carries the existing per-service STOP confirmation and is
    expected to flow through the normal stop-assessment endpoint again.
    """

    assessments = (relationships or {}).get("assessments") or {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    names: dict[str, str] = {}
    for service in services:
        service_id = str(service.get("id") or "")
        if not service_id:
            continue
        assessment = assessments.get(service_id) or service.get("stop_assessment") or {}
        group_id, project_name = _group_identity(service)
        names[group_id] = project_name
        process = service.get("process") or {}
        impact = assessment.get("impact") or {}
        risk = service.get("risk") or {}
        recommended = _is_recommended(service, assessment)
        groups[group_id].append(
            {
                "service_id": service_id,
                "display_name": str(service.get("display_name") or process.get("name") or "service")[:160],
                "pid": int(process.get("pid") or 0) or None,
                "source": service.get("source"),
                "runtime": service.get("runtime"),
                "risk_level": risk.get("level"),
                "risk_score": risk.get("score"),
                "decision": assessment.get("decision") or "blocked",
                "can_request_stop": bool(assessment.get("can_request_stop")),
                "recommended": recommended,
                "requires_confirmation": assessment.get("requires_confirmation"),
                "endpoint_count": int(impact.get("endpoint_count") or 0),
                "client_count": int(impact.get("client_count") or 0),
                "blockers": list(assessment.get("blockers") or [])[:8],
                "warnings": list(assessment.get("warnings") or [])[:8],
                "recommended_operations": list(assessment.get("recommended_operations") or [])[:8],
                "observation_minutes": [5, 15, 30],
            }
        )

    plans: list[dict[str, Any]] = []
    for group_id, items in groups.items():
        items.sort(
            key=lambda item: (
                not item["recommended"],
                not item["can_request_stop"],
                -(int(item.get("risk_score") or 0)),
                str(item["display_name"]).casefold(),
            )
        )
        recommended = [item for item in items if item["recommended"]]
        reviewable = [
            item for item in items if item["can_request_stop"] and not item["recommended"]
        ]
        blocked = [item for item in items if not item["can_request_stop"]]
        plans.append(
            {
                "group_id": group_id,
                "project_name": names[group_id],
                "summary": {
                    "services": len(items),
                    "recommended": len(recommended),
                    "reviewable": len(reviewable),
                    "protected_or_managed": len(blocked),
                    "endpoints": sum(int(item["endpoint_count"]) for item in items),
                    "local_clients": sum(int(item["client_count"]) for item in items),
                },
                "items": items,
            }
        )
    plans.sort(
        key=lambda plan: (
            -int(plan["summary"]["recommended"]),
            -int(plan["summary"]["reviewable"]),
            str(plan["project_name"]).casefold(),
        )
    )
    return {
        "schema_version": "1.0",
        "execution_mode": "individual_confirmation_only",
        "automatic_cleanup": False,
        "requires_fresh_assessment": True,
        "plans": plans,
        "summary": {
            "projects": len(plans),
            "recommended": sum(int(plan["summary"]["recommended"]) for plan in plans),
            "reviewable": sum(int(plan["summary"]["reviewable"]) for plan in plans),
            "protected_or_managed": sum(
                int(plan["summary"]["protected_or_managed"]) for plan in plans
            ),
        },
        "limitations": [
            "计划只基于当前快照，点击单项时仍会重新读取证据并执行 PID 复用防护",
            "受保护或受生命周期管理的服务只展示建议路径，不进入停止确认",
            "VSG 不提供静默批量停止；每个服务都必须单独输入 STOP <PID>",
        ],
    }
