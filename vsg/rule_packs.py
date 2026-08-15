from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any, Iterable

from .project_rules import AttributionRuleError, validate_rule_payload


RULE_PACK_KIND = "vsg-attribution-rule-pack"
RULE_PACK_VERSION = 1
MAX_RULE_PACK_BYTES = 256 * 1024
MAX_RULE_PACK_RULES = 500
CONTROL_RE = re.compile(r"[\x00-\x1f]")
PORTABLE_MATCH_KEYS = {
    "fingerprint",
    "ownership_signature",
    "redacted_command_hash",
    "runtime",
    "port",
}
PORTABLE_OVERRIDE_KEYS = {
    "project_name",
    "service_name",
    "agent_provider",
    "expected",
    "protected",
    "lifecycle_label",
}


class RulePackError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded_text(value: Any, field: str, limit: int = 300) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit or CONTROL_RE.search(text):
        raise RulePackError(f"{field} 无效")
    return text


def _portable_rule(rule: dict[str, Any]) -> dict[str, Any]:
    match = rule.get("match") or {}
    override = rule.get("override") or {}
    stripped_match = sorted(set(match) - PORTABLE_MATCH_KEYS)
    stripped_override = sorted(set(override) - PORTABLE_OVERRIDE_KEYS)
    portable_match = {key: match[key] for key in PORTABLE_MATCH_KEYS if key in match}
    portable_override = {key: override[key] for key in PORTABLE_OVERRIDE_KEYS if key in override}
    return {
        "name": str(rule.get("name") or "Imported attribution rule")[:160],
        "priority": max(0, min(int(rule.get("priority") or 100), 1000)),
        "enabled": bool(rule.get("enabled", True)),
        "scope": str(rule.get("scope") or "legacy")[:20],
        "match": portable_match,
        "override": portable_override,
        "portability": {
            "requires_explicit_rebind": True,
            "stripped_match_fields": stripped_match,
            "stripped_override_fields": stripped_override,
            "full_command_persisted": False,
            "environment_persisted": False,
        },
    }


def build_rule_pack(rules: Iterable[dict[str, Any]], application_version: str) -> dict[str, Any]:
    portable = [_portable_rule(rule) for rule in rules]
    if not portable:
        raise RulePackError("没有可导出的归属规则")
    if len(portable) > MAX_RULE_PACK_RULES:
        raise RulePackError(f"规则包最多包含 {MAX_RULE_PACK_RULES} 条规则")
    payload = {
        "kind": RULE_PACK_KIND,
        "schema_version": RULE_PACK_VERSION,
        "application_version": str(application_version)[:40],
        "exported_at": time.time(),
        "privacy": {
            "local_only": True,
            "contains_full_command": False,
            "contains_environment": False,
            "cross_machine_rebind_required": True,
        },
        "rules": portable,
    }
    envelope = {**payload, "integrity": {"canonical_payload_sha256": _digest(payload)}}
    if len(_canonical(envelope).encode("utf-8")) > MAX_RULE_PACK_BYTES:
        raise RulePackError("规则包超过 256 KiB 上限")
    return envelope


def validate_rule_pack(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RulePackError("规则包根节点必须是对象")
    try:
        encoded_size = len(_canonical(value).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise RulePackError("规则包包含无法序列化或嵌套过深的内容") from exc
    if encoded_size > MAX_RULE_PACK_BYTES:
        raise RulePackError("规则包超过 256 KiB 上限")
    if value.get("kind") != RULE_PACK_KIND or value.get("schema_version") != RULE_PACK_VERSION:
        raise RulePackError("不支持的归属规则包格式或版本")
    integrity = value.get("integrity") or {}
    if not isinstance(integrity, dict):
        raise RulePackError("规则包完整性字段无效")
    payload = {key: item for key, item in value.items() if key != "integrity"}
    expected = str(integrity.get("canonical_payload_sha256") or "")
    try:
        actual = _digest(payload)
    except (TypeError, ValueError, RecursionError) as exc:
        raise RulePackError("规则包有效载荷无法安全规范化") from exc
    if len(expected) != 64 or not secrets_compare(expected, actual):
        raise RulePackError("规则包完整性校验失败")
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RulePackError("规则包至少需要一条规则")
    if len(raw_rules) > MAX_RULE_PACK_RULES:
        raise RulePackError(f"规则包最多包含 {MAX_RULE_PACK_RULES} 条规则")
    rules: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise RulePackError(f"第 {index + 1} 条规则不是对象")
        if not isinstance(raw.get("match"), dict) or not isinstance(raw.get("override"), dict):
            raise RulePackError(f"第 {index + 1} 条规则的 match/override 必须是对象")
        if set(raw.get("match") or {}) - PORTABLE_MATCH_KEYS:
            raise RulePackError(f"第 {index + 1} 条规则包含非便携匹配字段")
        if set(raw.get("override") or {}) - PORTABLE_OVERRIDE_KEYS:
            raise RulePackError(f"第 {index + 1} 条规则包含非便携覆盖字段")
        name = _bounded_text(raw.get("name"), f"rules[{index}].name", 160)
        if not name:
            raise RulePackError(f"第 {index + 1} 条规则缺少名称")
        try:
            validated = validate_rule_payload(
                {
                    "name": name,
                    "priority": raw.get("priority", 100),
                    "enabled": raw.get("enabled", True),
                    "source": "import",
                    "scope": raw.get("scope") or None,
                    "match": raw.get("match") or {},
                    "override": raw.get("override") or {},
                },
                [],
            )
        except (AttributionRuleError, TypeError, ValueError) as exc:
            raise RulePackError(f"第 {index + 1} 条规则无效：{exc}") from exc
        rules.append({**validated, "portability": raw.get("portability") or {}})
    return {"rules": rules, "digest": actual, "payload": payload}


def secrets_compare(left: str, right: str) -> bool:
    # Kept local to avoid importing a larger authentication module into the
    # deterministic file-format layer.
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _service_matches_portable_rule(service: dict[str, Any], rule: dict[str, Any]) -> bool:
    match = rule.get("match") or {}
    metadata = service.get("metadata") or {}
    checks = {
        "fingerprint": service.get("fingerprint"),
        "ownership_signature": metadata.get("ownership_signature"),
        "redacted_command_hash": metadata.get("command_hash"),
        "runtime": service.get("runtime"),
    }
    for key, actual in checks.items():
        expected = match.get(key)
        if expected is not None and str(actual or "").casefold() != str(expected).casefold():
            return False
    if match.get("port") is not None:
        ports = {int(item.get("port") or 0) for item in service.get("endpoints") or []}
        if int(match["port"]) not in ports:
            return False
    return True


def preview_rule_pack(
    pack: dict[str, Any],
    services: Iterable[dict[str, Any]],
    existing_rules: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    validated = validate_rule_pack(pack)
    service_list = list(services)
    existing = list(existing_rules)
    items: list[dict[str, Any]] = []
    for index, rule in enumerate(validated["rules"]):
        candidates = [
            str(service.get("id") or "")
            for service in service_list
            if _service_matches_portable_rule(service, rule)
        ]
        feature_key = _canonical(rule.get("match") or {})
        conflicts = [
            int(item["id"])
            for item in existing
            if _canonical(item.get("match") or {}) == feature_key
        ]
        status = "exact_candidate" if len(candidates) == 1 else "ambiguous" if candidates else "unmatched"
        items.append(
            {
                "index": index,
                "name": rule["name"],
                "scope": rule["scope"],
                "status": status,
                "candidate_service_ids": candidates[:20],
                "existing_conflict_rule_ids": conflicts[:20],
                "requires_explicit_rebind": True,
                "stripped_fields": rule.get("portability") or {},
            }
        )
    return {
        "digest": validated["digest"],
        "count": len(items),
        "confirmation": f"IMPORT RULES {{count}} {validated['digest'][:12]}",
        "items": items,
        "summary": {
            "exact_candidates": sum(item["status"] == "exact_candidate" for item in items),
            "ambiguous": sum(item["status"] == "ambiguous" for item in items),
            "unmatched": sum(item["status"] == "unmatched" for item in items),
            "conflicts": sum(bool(item["existing_conflict_rule_ids"]) for item in items),
        },
    }


def rebind_imported_rule(
    rule: dict[str, Any], service: dict[str, Any], scope: str, project_roots: Iterable[str]
) -> dict[str, Any]:
    metadata = service.get("metadata") or {}
    if scope == "instance":
        match = {"fingerprint": service.get("fingerprint")}
    elif scope == "standard":
        match = {"ownership_signature": metadata.get("ownership_signature")}
    elif scope == "strict":
        match = {
            "ownership_signature": metadata.get("ownership_signature"),
            "redacted_command_hash": metadata.get("command_hash"),
        }
    else:
        raise RulePackError("导入重绑定范围必须是 instance、standard 或 strict")
    if any(value in (None, "") for value in match.values()):
        raise RulePackError(f"当前服务缺少创建 {scope} 规则所需的脱敏证据")
    try:
        return validate_rule_payload(
            {
                "name": rule.get("name"),
                "priority": rule.get("priority", 100),
                "enabled": rule.get("enabled", True),
                "source": "import",
                "scope": scope,
                "match": match,
                "override": rule.get("override") or {},
            },
            project_roots,
        )
    except AttributionRuleError as exc:
        raise RulePackError(str(exc)) from exc
