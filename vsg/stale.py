from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .config import AppConfig
from .models import RiskAssessment, ServiceRecord


DEV_RUNTIMES = {"Node.js", "Python", "Java", ".NET", "PHP", "Ruby", "Go", "Rust", "PowerShell"}
MODEL_SERVER_RUNTIMES = {
    "Ollama",
    "llama.cpp",
    "vLLM",
    "SGLang",
    "MLX-LM",
    "LM Studio",
    "KTransformers",
    "KoboldCpp",
    "Hugging Face TGI",
    "ComfyUI",
    "TensorRT-LLM",
    "Text Generation WebUI",
    "TabbyAPI",
}
STOPPABLE_RUNTIMES = DEV_RUNTIMES | (MODEL_SERVER_RUNTIMES - {"LM Studio"})
OPENABLE_RUNTIMES = STOPPABLE_RUNTIMES | {"LM Studio"}


def _duplicate_keys(services: Iterable[ServiceRecord]) -> Counter[tuple[str, str, str]]:
    keys: list[tuple[str, str, str]] = []
    for service in services:
        if service.source not in {"host", "windows_service"} or service.metadata.get(
            "agent_managed_child"
        ):
            continue
        project = (service.project.path or "").lower()
        command_head = " ".join(service.process.cmdline[:2]).lower()
        if project and command_head:
            keys.append((project, service.runtime, command_head))
    return Counter(keys)


def assess_service(
    service: ServiceRecord,
    config: AppConfig,
    now: float | None = None,
    history: dict[str, Any] | None = None,
    duplicate_count: int = 1,
) -> RiskAssessment:
    current = now or time.time()
    if service.expected:
        return RiskAssessment(score=0, level="expected", reasons=["已由用户标记为预期服务"])
    if service.source in {"docker", "wsl"}:
        return RiskAssessment(
            score=0,
            level="not_scored",
            reasons=["Docker/WSL 使用独立生命周期模型，第一版仅展示，不自动判定遗留"],
            scored=False,
        )
    if service.source == "agent":
        return RiskAssessment(
            score=0,
            level="not_scored",
            reasons=["Agent 本体只展示运行状态和项目/会话证据，不按开发服务规则判定遗留"],
            scored=False,
        )
    if service.metadata.get("agent_managed_child"):
        return RiskAssessment(
            score=0,
            level="not_scored",
            reasons=["该进程由可见的 Agent/IDE 父进程拉起，继承其归属并保持停止保护"],
            scored=False,
        )
    if service.windows_services:
        return RiskAssessment(
            score=0,
            level="not_scored",
            reasons=["Windows 服务由服务控制管理器托管，第一版不判定遗留"],
            scored=False,
        )

    score = 0
    reasons: list[str] = []
    if service.metadata.get("historical_lifecycle_label") == "safe_cleanup":
        # A user-confirmed historical label is strong review evidence, but it
        # never makes a stop action automatic or bypasses process protection.
        score += config.review_score
        reasons.append("同路径和工作目录曾由用户标记为可安全清理；仍需重新确认后才能停止")
    started = service.process.create_time
    age_hours = (current - started) / 3600 if started else 0
    if age_hours >= 72:
        score += 20
        reasons.append("已连续运行超过 72 小时")
    elif age_hours >= 24:
        score += 12
        reasons.append("已连续运行超过 24 小时")
    elif age_hours >= config.stale_after_hours:
        score += 6
        reasons.append(f"已超过设定的 {config.stale_after_hours} 小时关注阈值")

    if service.project.path:
        if not Path(service.project.path).exists():
            score += 45
            reasons.append("关联项目目录已不存在")
    elif service.runtime in DEV_RUNTIMES:
        score += 8
        reasons.append("开发运行时未能归入任何已配置项目")

    if age_hours >= 4 and service.process.cpu_percent <= 0.1 and service.established_connections == 0:
        score += 8
        reasons.append("长时间运行且当前低负载、无已建立连接")

    history = history or {}
    historical_agent = history.get("last_agent_provider")
    if historical_agent and not service.agent.provider:
        score += 28
        reasons.append(f"历史上由 {historical_agent} 关联启动，但当前 Agent 链已消失")
    elif service.agent.provider and not service.agent.active:
        score += 24
        reasons.append(f"关联的 {service.agent.provider} 会话当前不活跃")

    if duplicate_count > 1:
        score += min(20, 10 + (duplicate_count - 2) * 5)
        reasons.append(f"同项目检测到 {duplicate_count} 个相似服务实例")

    if not reasons:
        reasons.append("未命中疑似遗留规则")
    score = min(score, 100)
    if score >= config.likely_stale_score:
        level = "likely_stale"
    elif score >= config.review_score:
        level = "review"
    else:
        level = "normal"
    return RiskAssessment(score=score, level=level, reasons=reasons)


def assess_all(
    services: list[ServiceRecord],
    config: AppConfig,
    histories: dict[str, dict[str, Any]] | None = None,
    now: float | None = None,
) -> None:
    duplicates = _duplicate_keys(services)
    histories = histories or {}
    for service in services:
        key = (
            (service.project.path or "").lower(),
            service.runtime,
            " ".join(service.process.cmdline[:2]).lower(),
        )
        service.risk = assess_service(
            service,
            config,
            now=now,
            history=histories.get(service.fingerprint),
            duplicate_count=duplicates.get(key, 1),
        )
