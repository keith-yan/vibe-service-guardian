from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .models import ServiceRecord


MAX_MANIFEST_BYTES = 64 * 1024
CONTROL_RE = re.compile(r"[\x00-\x1f]")


class AttributionRuleError(ValueError):
    pass


def _within_roots(path: Path, roots: Iterable[str]) -> bool:
    resolved = path.expanduser().resolve(strict=False)
    for root_value in roots:
        root = Path(root_value).expanduser().resolve(strict=False)
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _text(value: Any, field: str, limit: int = 160) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    if not result:
        return None
    if len(result) > limit or CONTROL_RE.search(result):
        raise AttributionRuleError(f"{field} 无效")
    return result


def validate_rule_payload(raw: dict[str, Any], project_roots: Iterable[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AttributionRuleError("归属规则必须是对象")
    match_raw = raw.get("match") or {}
    override_raw = raw.get("override") or {}
    if not isinstance(match_raw, dict) or not isinstance(override_raw, dict):
        raise AttributionRuleError("match 和 override 必须是对象")
    match: dict[str, Any] = {}
    for key in (
        "fingerprint",
        "ownership_signature",
        "exe_contains",
        "cwd_prefix",
        "command_contains",
        "runtime",
    ):
        value = _text(match_raw.get(key), f"match.{key}")
        if value:
            match[key] = value
    port = match_raw.get("port")
    if port not in (None, ""):
        try:
            port_value = int(port)
        except (TypeError, ValueError) as exc:
            raise AttributionRuleError("match.port 必须是端口") from exc
        if not 1 <= port_value <= 65535:
            raise AttributionRuleError("match.port 必须在 1 到 65535 之间")
        match["port"] = port_value
    if not match:
        raise AttributionRuleError("归属规则至少需要一个匹配条件")

    override: dict[str, Any] = {}
    for key in ("project_name", "service_name", "agent_provider", "note"):
        value = _text(override_raw.get(key), f"override.{key}", 300 if key == "note" else 160)
        if value:
            override[key] = value
    project_path = _text(override_raw.get("project_path"), "override.project_path", 1024)
    if project_path:
        path = Path(project_path).expanduser()
        if not path.is_absolute() or not _within_roots(path, project_roots):
            raise AttributionRuleError("项目路径必须位于设置中的项目根目录")
        override["project_path"] = str(path.resolve(strict=False))
    for key in ("expected", "protected"):
        if key in override_raw:
            if not isinstance(override_raw[key], bool):
                raise AttributionRuleError(f"override.{key} 必须是布尔值")
            override[key] = override_raw[key]
    lifecycle_label = _text(override_raw.get("lifecycle_label"), "override.lifecycle_label", 40)
    if lifecycle_label:
        if lifecycle_label not in {"expected", "safe_cleanup"}:
            raise AttributionRuleError("override.lifecycle_label 必须是 expected 或 safe_cleanup")
        override["lifecycle_label"] = lifecycle_label
    if not override:
        raise AttributionRuleError("归属规则至少需要一个覆盖结果")
    return {
        "name": _text(raw.get("name"), "name") or "本地归属规则",
        "priority": max(0, min(int(raw.get("priority") or 100), 1000)),
        "enabled": bool(raw.get("enabled", True)),
        "match": match,
        "override": override,
    }


def rule_matches(service: ServiceRecord, rule: dict[str, Any]) -> bool:
    if not rule.get("enabled", True):
        return False
    match = rule.get("match") or {}
    process = service.process
    command = process.command.lower()
    endpoints = service.endpoints
    checks = {
        "fingerprint": service.fingerprint.lower(),
        "ownership_signature": str(service.metadata.get("ownership_signature") or "").lower(),
        "exe_contains": (process.exe or process.name).lower(),
        "cwd_prefix": (process.cwd or "").lower(),
        "command_contains": command,
        "runtime": service.runtime.lower(),
    }
    for key, actual in checks.items():
        expected = match.get(key)
        if expected is None:
            continue
        expected_text = str(expected).lower()
        if key in {"fingerprint", "ownership_signature", "runtime"}:
            if actual != expected_text:
                return False
        elif key == "cwd_prefix":
            if not actual.startswith(expected_text):
                return False
        elif expected_text not in actual:
            return False
    if match.get("port") is not None and not any(
        endpoint.port == int(match["port"]) for endpoint in endpoints
    ):
        return False
    return True


def apply_rules(service: ServiceRecord, rules: Iterable[dict[str, Any]]) -> list[str]:
    matched: list[str] = []
    for rule in sorted(rules, key=lambda item: int(item.get("priority") or 0), reverse=True):
        if not rule_matches(service, rule):
            continue
        override = rule.get("override") or {}
        if override.get("project_path"):
            service.project.path = str(override["project_path"])
            service.project.confidence = 100
        if override.get("project_name"):
            service.project.name = str(override["project_name"])
            service.project.confidence = 100
        if override.get("service_name"):
            service.display_name = str(override["service_name"])
        if override.get("agent_provider"):
            service.agent.provider = str(override["agent_provider"])
            service.agent.kind = "manual"
            service.agent.confidence = 100
            service.agent.active = True
        if "expected" in override:
            service.expected = bool(override["expected"])
        if "protected" in override:
            # Protection is monotonic.  A local attribution rule may add a
            # guard, but it must never downgrade a platform/default guard.
            service.protected = service.protected or bool(override["protected"])
        rule_name = str(rule.get("name") or rule.get("id") or "local-rule")
        service.project.evidence.append(f"本地规则：{rule_name}")
        service.metadata["attribution_source"] = "local_rule"
        service.metadata["attribution_rule_note"] = override.get("note")
        lifecycle_label = override.get("lifecycle_label")
        if lifecycle_label:
            service.metadata["historical_lifecycle_label"] = lifecycle_label
            service.metadata["historical_label_inherited"] = bool(
                (rule.get("match") or {}).get("ownership_signature")
            )
            if "user_history_mark" not in service.tags:
                service.tags.append("user_history_mark")
            if lifecycle_label == "expected":
                service.expected = True
        matched.append(str(rule.get("id") or rule_name))
        break
    return matched


def _scalar(value: str) -> Any:
    clean = value.strip()
    if len(clean) >= 2 and clean[0] == clean[-1] and clean[0] in {'"', "'"}:
        clean = clean[1:-1]
    lowered = clean.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if re.fullmatch(r"\d+", clean):
        return int(clean)
    if clean.startswith("[") and clean.endswith("]"):
        return [_scalar(item) for item in clean[1:-1].split(",") if item.strip()]
    return clean


def parse_vsg_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AttributionRuleError(".vsg.yaml 必须是普通文件且不能是符号链接")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise AttributionRuleError(".vsg.yaml 超过 64 KiB")
    content = path.read_text(encoding="utf-8")
    if any(token in content for token in ("!!", "&", "*", "${")):
        raise AttributionRuleError(".vsg.yaml 包含不支持的 YAML 特性")
    stripped = content.lstrip()
    if stripped.startswith("{"):
        value = json.loads(content)
        if not isinstance(value, dict):
            raise AttributionRuleError(".vsg.yaml 根节点必须是对象")
        if int(value.get("version") or 1) != 1:
            raise AttributionRuleError("仅支持 .vsg.yaml version: 1")
        return value

    result: dict[str, Any] = {"services": []}
    current: dict[str, Any] | None = None
    in_services = False
    for number, raw_line in enumerate(content.splitlines(), 1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        line = raw_line.split(" #", 1)[0].rstrip()
        indent = len(line) - len(line.lstrip(" "))
        clean = line.strip()
        if "\t" in raw_line or ":" not in clean:
            raise AttributionRuleError(f".vsg.yaml 第 {number} 行无效")
        if indent == 0:
            key, value = clean.split(":", 1)
            in_services = key.strip() == "services"
            current = None
            if not in_services:
                result[key.strip()] = _scalar(value)
            continue
        if not in_services:
            raise AttributionRuleError(f".vsg.yaml 第 {number} 行缩进无效")
        if clean.startswith("- "):
            current = {}
            result["services"].append(current)
            clean = clean[2:].strip()
        if current is None:
            raise AttributionRuleError(f".vsg.yaml 第 {number} 行缺少服务列表项")
        key, value = clean.split(":", 1)
        current[key.strip()] = _scalar(value)
    if int(result.get("version") or 1) != 1:
        raise AttributionRuleError("仅支持 .vsg.yaml version: 1")
    return result


def apply_project_manifest(service: ServiceRecord) -> bool:
    if not service.project.path:
        return False
    manifest_path = Path(service.project.path) / ".vsg.yaml"
    if not manifest_path.exists():
        return False
    try:
        manifest = parse_vsg_manifest(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError, AttributionRuleError) as exc:
        service.metadata["manifest_status"] = "invalid"
        service.metadata["manifest_error"] = type(exc).__name__
        return False
    try:
        project_name = _text(manifest.get("project_name"), "project_name")
    except AttributionRuleError:
        service.metadata["manifest_status"] = "invalid"
        service.metadata["manifest_error"] = "AttributionRuleError"
        return False
    if project_name:
        service.project.name = project_name
        service.project.evidence.append("项目 .vsg.yaml 名称")
    command = service.process.command.lower()
    ports = {endpoint.port for endpoint in service.endpoints}
    for entry in manifest.get("services") or []:
        if not isinstance(entry, dict):
            continue
        runtime = str(entry.get("runtime") or "").lower()
        if runtime and runtime != service.runtime.lower():
            continue
        entry_ports = entry.get("ports", entry.get("port"))
        if entry_ports not in (None, ""):
            values = entry_ports if isinstance(entry_ports, list) else [entry_ports]
            try:
                wanted_ports = {int(value) for value in values}
            except (TypeError, ValueError):
                continue
            if not ports.intersection(wanted_ports):
                continue
        contains = str(entry.get("command_contains") or "").lower()
        if contains and contains not in command:
            continue
        try:
            name = _text(entry.get("name"), "services.name")
            agent = _text(entry.get("agent"), "services.agent")
        except AttributionRuleError:
            continue
        if name:
            service.display_name = name
        if isinstance(entry.get("expected"), bool):
            service.expected = bool(entry["expected"])
        if isinstance(entry.get("protected"), bool):
            # Project manifests are reusable project metadata, not an escape
            # hatch from host/agent process protection.
            service.protected = service.protected or bool(entry["protected"])
        if agent:
            service.agent.provider = agent
            service.agent.kind = "manifest"
            service.agent.confidence = max(service.agent.confidence, 90)
        service.metadata["manifest_status"] = "matched"
        service.metadata["attribution_source"] = "project_manifest"
        service.project.evidence.append("项目 .vsg.yaml 服务规则")
        return True
    service.metadata["manifest_status"] = "loaded"
    return bool(project_name)
