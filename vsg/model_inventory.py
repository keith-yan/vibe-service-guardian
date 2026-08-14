from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
from pathlib import Path
from typing import Any


MAX_FILES = 5000
MAX_DEPTH = 6
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_SCAN_SECONDS = 120
MAX_GGUF_VALUE_DEPTH = 16
WEIGHT_SUFFIXES = {".gguf", ".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".onnx"}
CONFIG_NAMES = {"config.json", "generation_config.json", "tokenizer_config.json", "modelfile"}
QUANT_RE = re.compile(
    r"(?i)(q\d(?:_[a-z0-9]+)*|iq\d(?:_[a-z0-9]+)*|fp(?:8|16|32)|bf16|int(?:4|8)|f16|f32)"
)


class ModelInventoryError(ValueError):
    pass


def _metadata_text(value: Any, limit: int = 200) -> str | None:
    if not isinstance(value, (str, int, float, bool)):
        return None
    text = str(value).strip()
    return text[:limit] or None


def _metadata_int(value: Any, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    integer = int(value)
    return integer if 0 <= integer <= maximum else None


def _model_reference(value: Any) -> str | None:
    text = _metadata_text(value, 500)
    if not text:
        return None
    # Modelfiles may point at an absolute GGUF path.  Inventory APIs retain
    # only the final reference component, never the user's directory path.
    return re.split(r"[/\\]", text)[-1][:200] or None


GGML_FILE_TYPES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    9: "Q8_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "IQ3_XXS",
    22: "IQ1_S",
    23: "IQ4_NL",
    24: "IQ3_S",
    25: "IQ2_S",
    26: "IQ4_XS",
    27: "I8",
    28: "I16",
    29: "I32",
    30: "I64",
    31: "F64",
    32: "IQ1_M",
    36: "BF16",
}


class _Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read(self, count: int) -> bytes:
        if count < 0 or self.offset + count > len(self.data):
            raise ModelInventoryError("GGUF 元数据超过安全读取上限或文件已截断")
        value = self.data[self.offset : self.offset + count]
        self.offset += count
        return value

    def unpack(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        values = struct.unpack(fmt, self.read(size))
        return values[0] if len(values) == 1 else values

    def string(self) -> str:
        length = int(self.unpack("<Q"))
        if length > MAX_METADATA_BYTES:
            raise ModelInventoryError("GGUF 字符串长度异常")
        return self.read(length).decode("utf-8", errors="replace")


def _gguf_value(cursor: _Cursor, value_type: int, *, keep: bool, depth: int = 0) -> Any:
    if depth > MAX_GGUF_VALUE_DEPTH:
        raise ModelInventoryError("GGUF 数组嵌套层级异常")
    formats = {
        0: "<B",
        1: "<b",
        2: "<H",
        3: "<h",
        4: "<I",
        5: "<i",
        6: "<f",
        7: "<?",
        10: "<Q",
        11: "<q",
        12: "<d",
    }
    if value_type in formats:
        value = cursor.unpack(formats[value_type])
        return value if keep else None
    if value_type == 8:
        value = cursor.string()
        return value if keep else None
    if value_type == 9:
        element_type = int(cursor.unpack("<I"))
        length = int(cursor.unpack("<Q"))
        if length > 1_000_000:
            raise ModelInventoryError("GGUF 数组长度异常")
        if keep and length <= 256:
            return [
                _gguf_value(cursor, element_type, keep=True, depth=depth + 1)
                for _ in range(length)
            ]
        for _ in range(length):
            _gguf_value(cursor, element_type, keep=False, depth=depth + 1)
        return None
    raise ModelInventoryError(f"不支持的 GGUF 元数据类型：{value_type}")


def parse_gguf(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        data = stream.read(MAX_METADATA_BYTES)
    cursor = _Cursor(data)
    if cursor.read(4) != b"GGUF":
        raise ModelInventoryError("GGUF 魔数无效")
    version = int(cursor.unpack("<I"))
    if version not in {1, 2, 3}:
        raise ModelInventoryError(f"不支持的 GGUF 版本：{version}")
    tensor_count = int(cursor.unpack("<Q"))
    kv_count = int(cursor.unpack("<Q"))
    if kv_count > 100_000:
        raise ModelInventoryError("GGUF 元数据项目数量异常")
    wanted = {
        "general.name",
        "general.architecture",
        "general.size_label",
        "general.file_type",
        "general.license",
        "general.quantization_version",
    }
    values: dict[str, Any] = {}
    context_length: int | None = None
    expert_count: int | None = None
    for _ in range(kv_count):
        key = cursor.string()
        value_type = int(cursor.unpack("<I"))
        keep = key in wanted or key.endswith(".context_length") or key.endswith(".expert_count") or key.endswith(".expert_used_count")
        value = _gguf_value(cursor, value_type, keep=keep)
        if key in wanted:
            values[key] = value
        elif key.endswith(".context_length") and isinstance(value, (int, float)):
            context_length = int(value)
        elif key.endswith(".expert_count") and isinstance(value, (int, float)):
            expert_count = int(value)
    file_type = values.get("general.file_type")
    quantization = GGML_FILE_TYPES.get(int(file_type), f"GGML_TYPE_{file_type}") if isinstance(file_type, int) else None
    architecture = _metadata_text(values.get("general.architecture"), 120)
    return {
        "format": "gguf",
        "gguf_version": version,
        "tensor_count": tensor_count,
        "name": _metadata_text(values.get("general.name")),
        "architecture": architecture,
        "size_label": _metadata_text(values.get("general.size_label"), 80),
        "quantization": quantization,
        "quantization_version": values.get("general.quantization_version"),
        "license": _metadata_text(values.get("general.license"), 160),
        "context_length": context_length,
        "expert_count": expert_count,
        "model_type": "moe" if expert_count and expert_count > 1 else "dense_or_undetermined",
        "metadata_source": "GGUF header",
    }


DTYPE_BYTES = {
    "BOOL": 1,
    "U8": 1,
    "I8": 1,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "I16": 2,
    "U16": 2,
    "F16": 2,
    "BF16": 2,
    "I32": 4,
    "U32": 4,
    "F32": 4,
    "I64": 8,
    "U64": 8,
    "F64": 8,
}


def parse_safetensors(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw_length = stream.read(8)
        if len(raw_length) != 8:
            raise ModelInventoryError("Safetensors 文件头已截断")
        header_length = int.from_bytes(raw_length, "little")
        if not 2 <= header_length <= MAX_METADATA_BYTES:
            raise ModelInventoryError("Safetensors 元数据长度异常")
        header = json.loads(stream.read(header_length).decode("utf-8"))
    if not isinstance(header, dict):
        raise ModelInventoryError("Safetensors 元数据根节点无效")
    tensor_count = 0
    parameter_count = 0
    dtypes: dict[str, int] = {}
    calculated_bytes = 0
    if len(header) > 200_000:
        raise ModelInventoryError("Safetensors 张量项目数量异常")
    for name, item in header.items():
        if name == "__metadata__" or not isinstance(item, dict):
            continue
        shape = item.get("shape")
        dtype = str(item.get("dtype") or "unknown")[:32]
        if (
            not isinstance(shape, list)
            or len(shape) > 32
            or not all(
                isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**31
                for value in shape
            )
        ):
            continue
        count = 1
        for dimension in shape:
            count *= dimension
            if count > 10**18:
                break
        if count > 10**18:
            continue
        parameter_count += count
        calculated_bytes += count * DTYPE_BYTES.get(dtype, 0)
        tensor_count += 1
        dtypes[dtype] = dtypes.get(dtype, 0) + 1
    metadata = header.get("__metadata__") if isinstance(header.get("__metadata__"), dict) else {}
    return {
        "format": "safetensors",
        "tensor_count": tensor_count,
        "parameter_count": parameter_count,
        "estimated_parameters_billion": round(parameter_count / 1_000_000_000, 3),
        "dtypes": dtypes,
        "calculated_tensor_bytes": calculated_bytes,
        "name": _metadata_text(metadata.get("name") or metadata.get("model_name")),
        "architecture": _metadata_text(
            metadata.get("architecture") or metadata.get("model_type"), 120
        ),
        "quantization": _metadata_text(metadata.get("quantization"), 80)
        or _quantization_from_name(path.name),
        "metadata_source": "Safetensors header",
    }


def parse_model_config(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ModelInventoryError("模型配置超过 2 MiB 安全上限")
    if path.name.lower() == "modelfile":
        text = path.read_text(encoding="utf-8", errors="replace")
        from_value = next(
            (line.split(None, 1)[1].strip() for line in text.splitlines() if line.strip().lower().startswith("from ")),
            None,
        )
        return {
            "format": "ollama_modelfile",
            "base_model": _model_reference(from_value),
            "metadata_source": "Modelfile",
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ModelInventoryError("模型配置根节点无效")
    raw_architectures = value.get("architectures") if isinstance(value.get("architectures"), list) else []
    architectures = [
        text
        for text in (_metadata_text(item, 120) for item in raw_architectures[:20])
        if text
    ]
    expert_count = _metadata_int(
        value.get("num_local_experts", value.get("num_experts")), 1_000_000
    )
    active_experts = _metadata_int(
        value.get("num_experts_per_tok", value.get("num_selected_experts")),
        1_000_000,
    )
    raw_quantization = (
        value.get("quantization_config")
        if isinstance(value.get("quantization_config"), dict)
        else {}
    )
    quantization_config = {
        key: child
        for key in (
            "quant_method",
            "bits",
            "group_size",
            "load_in_4bit",
            "load_in_8bit",
            "bnb_4bit_quant_type",
        )
        if (child := raw_quantization.get(key)) is not None
        and isinstance(child, (str, int, float, bool))
        and len(str(child)) <= 120
    }
    return {
        "format": "model_config",
        "architecture": (architectures[0] if architectures else None)
        or _metadata_text(value.get("model_type"), 120),
        "architectures": architectures,
        "context_length": _metadata_int(
            value.get("max_position_embeddings") or value.get("model_max_length"),
            10_000_000,
        ),
        "hidden_layers": _metadata_int(value.get("num_hidden_layers"), 1_000_000),
        "expert_count": expert_count,
        "active_experts": active_experts,
        "model_type": "moe" if isinstance(expert_count, int) and expert_count > 1 else "dense_or_undetermined",
        "quantization_config": quantization_config or None,
        "metadata_source": path.name,
    }


def parse_ollama_manifest(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ModelInventoryError("Ollama manifest 超过 2 MiB 安全上限")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("layers"), list):
        raise ModelInventoryError("Ollama manifest 结构无效")
    layers = [item for item in value.get("layers") or [] if isinstance(item, dict)]
    total = sum(int(item.get("size") or 0) for item in layers)
    model_layers = [item for item in layers if "model" in str(item.get("mediaType") or "").lower()]
    return {
        "format": "ollama_manifest",
        "layer_count": len(layers),
        "model_layer_count": len(model_layers),
        "declared_size_bytes": total,
        "config_digest": str((value.get("config") or {}).get("digest") or "")[:120],
        "metadata_source": "Ollama manifest",
    }


def _quantization_from_name(name: str) -> str | None:
    match = QUANT_RE.search(name)
    return match.group(1).upper() if match else None


def _quick_fingerprint(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as stream:
        digest.update(stream.read(64 * 1024))
        if size > 64 * 1024:
            stream.seek(max(0, size - 64 * 1024))
            digest.update(stream.read(64 * 1024))
    return digest.hexdigest()[:24]


def _is_ollama_manifest(path: Path, relative: Path) -> bool:
    return "manifests" in {part.lower() for part in relative.parts} and path.stat().st_size <= MAX_CONFIG_BYTES


def _group_models(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for item in assets:
        if item.get("format") == "model_config":
            parent = str(Path(str(item.get("relative_path") or "")).parent).replace("\\", "/")
            configs[parent] = item
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in assets:
        if item.get("kind") not in {"weight", "manifest"}:
            continue
        relative = str(item.get("relative_path") or item.get("file_name") or "model")
        parent = str(Path(relative).parent).replace("\\", "/")
        if item.get("format") == "safetensors":
            key = f"safetensors:{parent}"
        else:
            key = f"{item.get('format')}:{relative}"
        grouped.setdefault(key, []).append(item)
    models: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        first = rows[0]
        relative = str(first.get("relative_path") or first.get("file_name") or "model")
        parent = str(Path(relative).parent).replace("\\", "/")
        config = configs.get(parent, {})
        declared_size = sum(
            int(item.get("declared_size_bytes") or item.get("size_bytes") or 0) for item in rows
        )
        parameter_count = sum(int(item.get("parameter_count") or 0) for item in rows) or None
        architecture = first.get("architecture") or config.get("architecture")
        expert_count = first.get("expert_count") or config.get("expert_count")
        model_type = (
            "moe"
            if isinstance(expert_count, int) and expert_count > 1
            else first.get("model_type") or config.get("model_type") or "unknown"
        )
        label = (
            first.get("name")
            or (Path(parent).name if first.get("format") == "safetensors" and parent not in {"", "."} else first.get("file_name"))
            or "model"
        )
        models.append(
            {
                "id": hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:16],
                "name": str(label)[:200],
                "format": first.get("format"),
                "relative_location": parent if first.get("format") == "safetensors" else relative,
                "files": len(rows),
                "weight_bytes": declared_size,
                "weight_gib": round(declared_size / (1024**3), 3),
                "architecture": architecture,
                "model_type": model_type,
                "expert_count": expert_count,
                "active_experts": config.get("active_experts"),
                "context_length": first.get("context_length") or config.get("context_length"),
                "quantization": first.get("quantization") or (
                    str(config.get("quantization_config", {}).get("quant_method") or "") or None
                    if isinstance(config.get("quantization_config"), dict)
                    else None
                ),
                "estimated_parameters_billion": round(parameter_count / 1_000_000_000, 3) if parameter_count else None,
                "metadata_status": "measured_from_files",
            }
        )
    return sorted(models, key=lambda item: (-int(item.get("weight_bytes") or 0), str(item.get("name"))))


def add_capacity_hints(result: dict[str, Any], hardware: dict[str, Any]) -> dict[str, Any]:
    memory = hardware.get("memory") or {}
    system_available = memory.get("available_gib")
    accelerators = [
        item
        for item in hardware.get("gpus") or []
        if not item.get("integrated") and item.get("memory_free_gib") is not None
    ]
    maximum_free_vram = max((float(item.get("memory_free_gib") or 0) for item in accelerators), default=None)
    for model in result.get("models") or []:
        weight_gib = float(model.get("weight_gib") or 0)
        minimum_with_workspace = round(weight_gib * 1.1 + 0.5, 3)
        model["capacity_hint"] = {
            "minimum_weight_workspace_gib": minimum_with_workspace,
            "single_accelerator_weight_fit": (
                None if maximum_free_vram is None else minimum_with_workspace <= maximum_free_vram
            ),
            "system_memory_weight_fit": (
                None if system_available is None else minimum_with_workspace <= float(system_available)
            ),
            "maximum_free_accelerator_gib": maximum_free_vram,
            "system_available_gib": system_available,
            "complete": False,
            "reason": "仅核对当前权重与保守工作区；KV 缓存、上下文、并发、后端副本和性能仍需容量规划与本机实测",
        }
        model["advisor_seed"] = {
            "model_format": "ollama" if model.get("format") == "ollama_manifest" else model.get("format"),
            "architecture": model.get("architecture"),
            "quantization": model.get("quantization"),
        }
    result["hardware_fingerprint"] = hardware.get("hardware_fingerprint")
    return result


def _asset(path: Path, root: Path) -> dict[str, Any] | None:
    relative = path.relative_to(root)
    suffix = path.suffix.lower()
    name_lower = path.name.lower()
    is_manifest = _is_ollama_manifest(path, relative)
    if suffix not in WEIGHT_SUFFIXES and name_lower not in CONFIG_NAMES and not is_manifest:
        return None
    size = path.stat().st_size
    metadata: dict[str, Any]
    kind = "weight" if suffix in WEIGHT_SUFFIXES else "config"
    try:
        if suffix == ".gguf":
            metadata = parse_gguf(path)
        elif suffix == ".safetensors":
            metadata = parse_safetensors(path)
        elif is_manifest:
            metadata = parse_ollama_manifest(path)
            kind = "manifest"
        elif name_lower in CONFIG_NAMES:
            metadata = parse_model_config(path)
        else:
            metadata = {
                "format": suffix.lstrip("."),
                "quantization": _quantization_from_name(path.name),
                "metadata_source": "file name only",
            }
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ModelInventoryError,
        RecursionError,
        struct.error,
    ) as exc:
        metadata = {
            "format": suffix.lstrip(".") or "unknown",
            "quantization": _quantization_from_name(path.name),
            "metadata_status": "unavailable",
            "metadata_error": type(exc).__name__,
        }
    fingerprint = _quick_fingerprint(path, size) if kind == "weight" else None
    return {
        "file_name": path.name,
        "relative_path": relative.as_posix(),
        "kind": kind,
        "size_bytes": size,
        "size_gib": round(size / (1024**3), 4),
        "modified_at": path.stat().st_mtime,
        "quick_fingerprint": fingerprint,
        **metadata,
    }


def scan_model_directory(root_value: str, confirmation: str) -> dict[str, Any]:
    if confirmation != "SCAN MODELS":
        raise ModelInventoryError("确认短语必须精确输入 SCAN MODELS")
    if not isinstance(root_value, str) or not root_value.strip():
        raise ModelInventoryError("请选择明确的模型目录")
    requested_root = Path(root_value).expanduser()
    if not requested_root.is_absolute():
        raise ModelInventoryError("模型目录必须是绝对路径")
    is_junction = getattr(requested_root, "is_junction", lambda: False)
    if requested_root.is_symlink() or is_junction():
        raise ModelInventoryError("模型目录必须是普通目录且不能是符号链接")
    try:
        root = requested_root.resolve(strict=True)
    except OSError as exc:
        raise ModelInventoryError("模型目录不存在或当前用户无法读取") from exc
    if not root.is_dir():
        raise ModelInventoryError("模型目录必须是普通目录且不能是符号链接")
    home = Path.home().resolve(strict=False)
    if root.parent == root or root == home:
        raise ModelInventoryError("不允许扫描磁盘根目录或整个用户主目录；请选择具体模型目录")

    created_at = time.time()
    root_hash = hashlib.sha256(str(root).encode("utf-8", errors="replace")).hexdigest()[:20]
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []
    examined = 0
    truncated = False
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        if time.time() - created_at > MAX_SCAN_SECONDS:
            truncated = True
            warnings.append(f"扫描超过 {MAX_SCAN_SECONDS} 秒，已安全停止")
            break
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts)
        except ValueError:
            continue
        directories[:] = [
            name
            for name in sorted(directories)
            if depth < MAX_DEPTH
            and not (current_path / name).is_symlink()
            and not getattr(current_path / name, "is_junction", lambda: False)()
        ]
        for name in sorted(files):
            if time.time() - created_at > MAX_SCAN_SECONDS:
                truncated = True
                warnings.append(f"扫描超过 {MAX_SCAN_SECONDS} 秒，已安全停止")
                break
            examined += 1
            if examined > MAX_FILES:
                truncated = True
                warnings.append(f"文件数量超过 {MAX_FILES}，扫描已安全停止")
                break
            path = current_path / name
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                item = _asset(path, root)
                if item:
                    assets.append(item)
            except (OSError, PermissionError) as exc:
                warnings.append(f"无法读取：{path.relative_to(root).as_posix()}（{type(exc).__name__}）")
        if truncated:
            break

    duplicate_groups: dict[tuple[str, int], list[str]] = {}
    for item in assets:
        fingerprint = item.get("quick_fingerprint")
        if fingerprint:
            duplicate_groups.setdefault((str(fingerprint), int(item.get("size_bytes") or 0)), []).append(
                str(item.get("relative_path") or item.get("file_name"))
            )
    duplicates = [
        {"quick_fingerprint": key[0], "size_bytes": key[1], "files": values}
        for key, values in duplicate_groups.items()
        if len(values) > 1
    ]
    format_counts: dict[str, int] = {}
    quantization_counts: dict[str, int] = {}
    for item in assets:
        format_name = str(item.get("format") or "unknown")
        format_counts[format_name] = format_counts.get(format_name, 0) + 1
        quantization = item.get("quantization")
        if quantization:
            quantization_counts[str(quantization)] = quantization_counts.get(str(quantization), 0) + 1
    weight_assets = [item for item in assets if item.get("kind") == "weight"]
    models = _group_models(assets)
    result = {
        "created_at": created_at,
        "root_name": root.name,
        "root_hash": root_hash,
        "summary": {
            "examined_files": examined,
            "assets": len(assets),
            "weight_files": len(weight_assets),
            "total_weight_bytes": sum(int(item.get("size_bytes") or 0) for item in weight_assets),
            "total_weight_gib": round(sum(int(item.get("size_bytes") or 0) for item in weight_assets) / (1024**3), 3),
            "formats": format_counts,
            "quantizations": quantization_counts,
            "duplicate_groups": len(duplicates),
            "models": len(models),
        },
        "assets": assets,
        "models": models,
        "duplicates": duplicates,
        "warnings": warnings[:100],
        "truncated": truncated,
        "privacy": "结果仅含所选目录内的相对路径、文件元数据和快速指纹；不保存绝对目录，也不上传任何内容",
        "limitations": [
            "快速指纹只用于候选重复文件识别，不是完整文件完整性校验",
            "未在权重头或配置中声明的信息保持未知，不根据文件大小臆测模型参数",
            "扫描不下载、不删除、不修改模型文件",
        ],
    }
    return result
