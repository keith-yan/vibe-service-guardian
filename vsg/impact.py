from __future__ import annotations

import hashlib
import json
import time
from typing import Any


REPORT_SCHEMA_VERSION = "1.0"
FEEDBACK_OUTCOMES = {"confirmed_stale", "not_stale", "uncertain"}
SOURCE_GROUPS = ("host", "agent", "windows_service", "docker", "wsl", "unknown")
PREDICTION_METRICS = (
    "per_user_generation_tps",
    "aggregate_generation_tps",
    "ttft_seconds",
)


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _current_counts(snapshot: dict[str, Any]) -> dict[str, Any]:
    services = snapshot.get("services")
    if not isinstance(services, list):
        services = []
    sources = {key: 0 for key in SOURCE_GROUPS}
    result: dict[str, Any] = {
        "services": len(services),
        "listening_endpoints": 0,
        "project_attributed_services": 0,
        "agent_attributed_services": 0,
        "review_candidates": 0,
        "likely_stale_candidates": 0,
        "model_runtimes": 0,
        "non_loopback_endpoints": 0,
        "stoppable_candidates": 0,
        "source_groups": sources,
    }
    for raw_service in services:
        if not isinstance(raw_service, dict):
            continue
        source = str(raw_service.get("source") or "unknown")
        sources[source if source in sources else "unknown"] += 1
        project = raw_service.get("project") or {}
        agent = raw_service.get("agent") or {}
        risk = raw_service.get("risk") or {}
        metadata = raw_service.get("metadata") or {}
        endpoints = raw_service.get("endpoints") or []
        if isinstance(project, dict) and (project.get("path") or project.get("name")):
            result["project_attributed_services"] += 1
        if isinstance(agent, dict) and agent.get("provider"):
            result["agent_attributed_services"] += 1
        risk_level = str(risk.get("level") or "unknown") if isinstance(risk, dict) else "unknown"
        if risk_level == "review":
            result["review_candidates"] += 1
        elif risk_level == "likely_stale":
            result["likely_stale_candidates"] += 1
        if isinstance(metadata, dict) and metadata.get("model_runtime"):
            result["model_runtimes"] += 1
        if isinstance(metadata, dict) and metadata.get("stoppable_candidate"):
            result["stoppable_candidates"] += 1
        if isinstance(endpoints, list):
            result["listening_endpoints"] += sum(isinstance(item, dict) for item in endpoints)
            result["non_loopback_endpoints"] += sum(
                isinstance(item, dict)
                and str(item.get("exposure") or "unknown") != "loopback"
                for item in endpoints
            )
    return result


def _prediction_error_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, list[float]] = {key: [] for key in PREDICTION_METRICS}
    runs_with_prediction = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        details = item.get("details") or {}
        matrix = details.get("matrix") or {} if isinstance(details, dict) else {}
        errors = matrix.get("prediction_error") if isinstance(matrix, dict) else None
        if not isinstance(errors, dict):
            continue
        found = False
        for metric in PREDICTION_METRICS:
            error = errors.get(metric)
            if not isinstance(error, dict):
                continue
            absolute = _finite_number(error.get("absolute_percent"))
            if absolute is None or absolute < 0:
                continue
            values[metric].append(absolute)
            found = True
        if found:
            runs_with_prediction += 1
    metrics: dict[str, Any] = {}
    for metric, samples in values.items():
        metrics[metric] = {
            "samples": len(samples),
            "mean_absolute_error_percent": round(sum(samples) / len(samples), 2)
            if samples
            else None,
            "maximum_absolute_error_percent": round(max(samples), 2) if samples else None,
        }
    return {
        "runs_with_prediction": runs_with_prediction,
        "metric_samples": sum(len(samples) for samples in values.values()),
        "metrics": metrics,
    }


def build_impact_report(
    storage: Any,
    snapshot: dict[str, Any],
    platform: dict[str, Any],
    version: str,
    *,
    generated_at: float | None = None,
) -> dict[str, Any]:
    """Build an aggregate-only report for manual local review and export."""

    timestamp = float(generated_at if generated_at is not None else time.time())
    historical = storage.impact_statistics()
    benchmarks = storage.recent_service_benchmarks(200)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": timestamp,
        "application": {"name": "Vibe Service Guardian", "version": str(version)},
        "platform": {
            "key": str(platform.get("key") or "unknown"),
            "architecture": str(platform.get("architecture") or "unknown"),
        },
        "scope": {
            "kind": "single_local_instance",
            "retention_bounded": True,
            "automatic_upload": False,
            "external_adoption_verified": False,
        },
        "current_snapshot": _current_counts(snapshot),
        "retained_local_evidence": {
            **historical,
            "prediction_error": _prediction_error_summary(benchmarks),
        },
        "evidence_quality": {
            "level": "maintainer_or_user_self_reported",
            "human_outcomes_are_subjective": True,
            "feedback_is_deduplicated_by_service_fingerprint": True,
            "not_evidence_of": [
                "independent_user_count",
                "public_repository_usage",
                "downloads_or_installations",
                "external_project_adoption",
            ],
        },
        "privacy": {
            "contains_pid": False,
            "contains_paths": False,
            "contains_ip_addresses": False,
            "contains_commands": False,
            "contains_session_ids": False,
            "contains_log_content": False,
            "contains_model_response_content": False,
        },
        "limitations": [
            "Counts cover only the retained history of this local VSG instance.",
            "A user confirmation records a human judgment, not independently audited ground truth.",
            "Missing permissions or sensors can reduce service, dependency, and resource visibility.",
            "This export must be reviewed before it is shared outside the machine.",
        ],
    }


def build_export_envelope(report: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "export_type": "vsg_redacted_impact_report",
        "report": report,
        "integrity": {
            "algorithm": "sha256",
            "canonical_report_sha256": digest,
        },
    }
