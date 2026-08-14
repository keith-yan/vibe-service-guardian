from __future__ import annotations

import json
from importlib import resources
from typing import Any


CATALOG_FILENAME = "models-2026-08-11.json"


class CatalogError(ValueError):
    """Raised when the bundled offline model catalog is invalid."""


def _catalog_resource():
    return resources.files("vsg").joinpath("catalog", CATALOG_FILENAME)


def validate_catalog(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CatalogError("模型目录根节点必须是对象")
    models = raw.get("models")
    if not isinstance(models, list) or not models:
        raise CatalogError("模型目录必须包含非空 models 列表")

    required = {
        "id",
        "name",
        "publisher",
        "architecture",
        "total_params_b",
        "active_params_b",
        "native_context_tokens",
        "license",
        "source_url",
        "kv_cache_kib_per_token_fp16",
        "kv_estimate_confidence",
        "runtimes",
    }
    seen: set[str] = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            raise CatalogError(f"models[{index}] 必须是对象")
        missing = sorted(required - set(model))
        if missing:
            raise CatalogError(f"models[{index}] 缺少字段：{', '.join(missing)}")
        model_id = model["id"]
        if not isinstance(model_id, str) or not model_id or model_id in seen:
            raise CatalogError(f"models[{index}].id 无效或重复")
        seen.add(model_id)
        if model["architecture"] not in {"dense", "moe", "hybrid"}:
            raise CatalogError(f"{model_id} 的 architecture 无效")
        total = float(model["total_params_b"])
        active = float(model["active_params_b"])
        if total <= 0 or active <= 0 or active > total:
            raise CatalogError(f"{model_id} 的参数量无效")
        if int(model["native_context_tokens"]) < 512:
            raise CatalogError(f"{model_id} 的上下文长度无效")
        if float(model["kv_cache_kib_per_token_fp16"]) <= 0:
            raise CatalogError(f"{model_id} 的 KV 估算系数无效")
        if model["kv_estimate_confidence"] not in {"low", "medium", "high"}:
            raise CatalogError(f"{model_id} 的 KV 置信度无效")
        if not isinstance(model["runtimes"], list):
            raise CatalogError(f"{model_id} 的 runtimes 必须是列表")

    return raw


def load_catalog() -> dict[str, Any]:
    try:
        raw = json.loads(_catalog_resource().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"无法读取内置模型目录：{type(exc).__name__}") from exc
    return validate_catalog(raw)


def catalog_summary(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "catalog_version": catalog.get("catalog_version"),
        "snapshot_date": catalog.get("snapshot_date"),
        "model_count": len(catalog.get("models", [])),
        "offline": True,
        "non_exhaustive": True,
        "methodology": catalog.get("methodology"),
        "sources": catalog.get("sources", []),
    }


def model_by_id(catalog: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    return next((item for item in catalog.get("models", []) if item.get("id") == model_id), None)
