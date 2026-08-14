from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .privacy import atomic_write_private_text, ensure_private_directory, harden_private_file


LOG_SUFFIXES = {".log", ".txt", ".out", ".jsonl"}
CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".conf", ".cfg", ".modelfile"}
BLOCKED_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", "credentials.json", "secrets.json"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12"}
MAX_SNAPSHOT_FILE_HASH_BYTES = 512 * 1024 * 1024
MAX_SNAPSHOT_TOTAL_HASH_BYTES = 1024 * 1024 * 1024
SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|pwd|authorization|cookie|credential|private[_-]?key|client[_-]?secret|database[_-]?url|dsn)"
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)(?P<prefix>[\"']?(?:api[_-]?key|token|secret|password|passwd|pwd|authorization|cookie|credential|client[_-]?secret)[\"']?\s*[:=]\s*)(?P<quote>[\"']?)(?P<value>[^\s,}\]\"']+)(?P=quote)"
)
INLINE_SECRET_RE = re.compile(
    r"(?ix)(?:sk-[A-Za-z0-9_-]{8,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|(?:bearer|basic)\s+[A-Za-z0-9._~+/-]{8,})"
)
ERROR_PATTERNS: dict[str, re.Pattern[str]] = {
    "oom": re.compile(r"(?i)out of memory|\boom\b|cannot allocate memory|memoryerror"),
    "cuda": re.compile(r"(?i)cuda(?: error| failure| out of memory)|cublas|cudnn|nccl"),
    "rocm_hip": re.compile(r"(?i)rocm|hip error|hiperror|miopen"),
    "metal": re.compile(r"(?i)metal.*(?:error|failed)|mps.*(?:error|failed)"),
    "fatal": re.compile(r"(?i)\bfatal\b|panic:|segmentation fault|access violation"),
    "traceback": re.compile(r"Traceback \(most recent call last\)"),
    "generic_error": re.compile(r"(?i)\berror\b|exception|failed to load"),
    "model_loaded": re.compile(r"(?i)model (?:loaded|ready)|loaded model|server is ready|startup complete"),
    "model_loading": re.compile(r"(?i)loading model|load_model|warming up|initializing model"),
}


class DiagnosticError(RuntimeError):
    pass


def redact_text(text: str) -> str:
    text = SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group('prefix')}[REDACTED]", text)
    text = INLINE_SECRET_RE.sub("[REDACTED]", text)
    home = str(Path.home())
    if home:
        text = re.sub(re.escape(home), "~", text, flags=re.IGNORECASE)
    return text


# Kept as a compatibility alias for the existing one-shot inspectors.  New
# continuous log monitoring imports the public helper so every path uses the
# same secret and home-directory redaction rules.
_redact = redact_text


def _validate_file(path_value: str, mode: str) -> Path:
    if not isinstance(path_value, str) or not path_value.strip():
        raise DiagnosticError("文件路径不能为空")
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise DiagnosticError("只接受绝对文件路径")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DiagnosticError("文件不存在或不可访问") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise DiagnosticError("只允许读取普通文件，不跟随符号链接")
    if resolved.name.lower() in BLOCKED_NAMES or resolved.suffix.lower() in SENSITIVE_SUFFIXES:
        raise DiagnosticError("拒绝读取密钥、凭据或 .env 文件")
    allowed = LOG_SUFFIXES if mode == "log" else CONFIG_SUFFIXES
    if resolved.suffix.lower() not in allowed and resolved.name.lower() != "modelfile":
        raise DiagnosticError(f"{mode} 模式不支持该文件类型")
    return resolved


def validate_diagnostic_file(path_value: str, mode: str) -> Path:
    """Validate an explicitly selected local diagnostic file.

    This wrapper intentionally exposes validation without exposing the private
    implementation details used by the one-shot inspectors.
    """

    if mode not in {"log", "config"}:
        raise DiagnosticError("检查模式无效")
    return _validate_file(path_value, mode)


def inspect_log(path_value: str, confirmation: str, pid: int) -> dict[str, Any]:
    if confirmation.strip() != f"INSPECT {pid}":
        raise DiagnosticError(f"确认短语必须是 INSPECT {pid}")
    path = _validate_file(path_value, "log")
    size = path.stat().st_size
    if size > 100 * 1024 * 1024:
        raise DiagnosticError("日志超过 100 MiB；请先轮转或导出较小片段")
    with path.open("rb") as stream:
        stream.seek(max(0, size - 1024 * 1024))
        raw = stream.read(1024 * 1024)
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()[-2000:]
    counts = {name: 0 for name in ERROR_PATTERNS}
    snippets: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=max(1, len(text.splitlines()) - len(lines) + 1)):
        matched = []
        for name, pattern in ERROR_PATTERNS.items():
            if pattern.search(line):
                counts[name] += 1
                matched.append(name)
        if matched and len(snippets) < 30:
            snippets.append(
                {
                    "line": line_number,
                    "categories": matched,
                    "text": _redact(line)[:500],
                }
            )
    load_state = "unknown"
    if counts["model_loaded"]:
        load_state = "loaded_evidence"
    elif counts["model_loading"]:
        load_state = "loading_evidence"
    return {
        "mode": "log",
        "file_name": path.name,
        "file_size_bytes": size,
        "tail_bytes_read": len(raw),
        "lines_examined": len(lines),
        "counts": counts,
        "snippets": snippets,
        "model_load_evidence": load_state,
        "persisted": False,
        "privacy": "结果已脱敏；原始日志和绝对路径不写入数据库",
    }


def _sanitize_json(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[DEPTH_LIMIT]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in list(value.items())[:500]:
            clean_key = str(key)[:160]
            if SECRET_KEY_RE.search(clean_key):
                result[clean_key] = "[REDACTED]"
            else:
                result[clean_key] = _sanitize_json(child, depth + 1)
        return result
    if isinstance(value, list):
        return [_sanitize_json(child, depth + 1) for child in value[:100]]
    if isinstance(value, str):
        return _redact(value)[:1000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]


def inspect_config(path_value: str, confirmation: str, pid: int) -> dict[str, Any]:
    if confirmation.strip() != f"INSPECT {pid}":
        raise DiagnosticError(f"确认短语必须是 INSPECT {pid}")
    path = _validate_file(path_value, "config")
    size = path.stat().st_size
    if size > 2 * 1024 * 1024:
        raise DiagnosticError("配置文件超过 2 MiB，拒绝在控制台解析")
    text = path.read_text(encoding="utf-8", errors="replace")
    syntax = "text"
    sanitized: Any
    syntax_error = None
    if path.suffix.lower() == ".json":
        try:
            sanitized = _sanitize_json(json.loads(text))
            syntax = "valid_json"
        except json.JSONDecodeError as exc:
            sanitized = _redact(text[:20_000])
            syntax = "invalid_json"
            syntax_error = f"line {exc.lineno}, column {exc.colno}"
    else:
        sanitized = _redact(text[:20_000])
    lowered = text.lower()
    secret_keys_present = sorted({match.group(0).lower() for match in SECRET_KEY_RE.finditer(text)})[:30]
    checks = {
        "model_reference_present": bool(re.search(r"(?i)\bmodel(?:_name|_path|_id)?\b", text)),
        "host_binding_present": bool(re.search(r"(?i)\b(?:host|listen|bind|address)\b", text)),
        "context_setting_present": "context" in lowered or "n_ctx" in lowered or "max_model_len" in lowered,
        "concurrency_setting_present": "parallel" in lowered or "concurrency" in lowered or "max_num_seqs" in lowered,
        "authentication_setting_present": bool(secret_keys_present),
    }
    return {
        "mode": "config",
        "file_name": path.name,
        "file_size_bytes": size,
        "syntax": syntax,
        "syntax_error": syntax_error,
        "checks": checks,
        "sensitive_key_names_present": secret_keys_present,
        "sanitized_content": sanitized,
        "persisted": False,
        "privacy": "只返回脱敏结构；密钥值和绝对路径不写入数据库",
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_snapshot_manifest(
    paths: list[str], data_dir: Path, confirmation: str
) -> dict[str, Any]:
    if confirmation.strip() != "SNAPSHOT":
        raise DiagnosticError("确认短语必须是 SNAPSHOT")
    if not isinstance(paths, list) or not 1 <= len(paths) <= 100:
        raise DiagnosticError("请选择 1 到 100 个文件")
    resolved: list[Path] = []
    seen: set[str] = set()
    for value in paths:
        if not isinstance(value, str) or not value.strip():
            raise DiagnosticError("清单路径不能为空")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise DiagnosticError("清单只接受绝对路径")
        try:
            path = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise DiagnosticError(f"文件不存在：{Path(value).name}") from exc
        if not path.is_file() or path.is_symlink():
            raise DiagnosticError("首版清单只支持普通文件，不递归目录或跟随符号链接")
        identity = os.path.normcase(str(path))
        if identity in seen:
            continue
        seen.add(identity)
        resolved.append(path)
    snapshot_id = time.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha256(os.urandom(24)).hexdigest()[:8]
    root = data_dir / "snapshots" / snapshot_id
    if root.exists():
        raise DiagnosticError("快照 ID 冲突，请重试")
    root = ensure_private_directory(root)
    try:
        copies = ensure_private_directory(root / "configs")
        items: list[dict[str, Any]] = []
        total_hashed = 0
        for index, path in enumerate(resolved):
            stat = path.stat()
            suffix = path.suffix.lower()
            sensitive = path.name.lower() in BLOCKED_NAMES or suffix in SENSITIVE_SUFFIXES
            if stat.st_size > MAX_SNAPSHOT_FILE_HASH_BYTES:
                hash_status = "skipped_large_file"
            elif total_hashed + stat.st_size > MAX_SNAPSHOT_TOTAL_HASH_BYTES:
                hash_status = "skipped_total_budget"
            else:
                hash_status = "measured"
            item: dict[str, Any] = {
                "index": index,
                "file_name": path.name,
                "original_path": str(path),
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "sha256": None,
                "sha256_status": hash_status,
                "sensitive_file": sensitive,
                "saved_copy": None,
                "rollback_available": False,
            }
            if hash_status == "measured":
                item["sha256"] = _sha256(path)
                total_hashed += stat.st_size
            if (
                not sensitive
                and (suffix in CONFIG_SUFFIXES or path.name.lower() == "modelfile")
                and stat.st_size <= 2 * 1024 * 1024
            ):
                copy_name = f"{index:03d}-{path.name}"
                target = copies / copy_name
                shutil.copy2(path, target)
                harden_private_file(target)
                item["saved_copy"] = f"configs/{copy_name}"
                item["rollback_available"] = True
            items.append(item)
        manifest = {
            "schema_version": "1.1",
            "snapshot_id": snapshot_id,
            "created_at": time.time(),
            "items": items,
            "policy": (
                "大模型文件只建清单不复制；单文件超过 512 MiB 或本次累计超过 1 GiB "
                "不自动计算完整 SHA-256；小型非敏感显式配置保存本地私有副本"
            ),
        }
        atomic_write_private_text(
            root / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        return manifest
    except Exception:
        # The directory is generated exclusively for this call.  A failed
        # snapshot must not leave a half-valid rollback source behind.
        shutil.rmtree(root, ignore_errors=True)
        raise


def list_snapshot_manifests(data_dir: Path, limit: int = 30) -> list[dict[str, Any]]:
    root = data_dir / "snapshots"
    if not root.is_dir():
        return []
    values: list[dict[str, Any]] = []
    for manifest_path in sorted(root.glob("*/manifest.json"), reverse=True)[: max(1, min(limit, 100))]:
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            # The UI needs basenames and rollback state, not historic absolute paths.
            values.append(
                {
                    "snapshot_id": value.get("snapshot_id"),
                    "created_at": value.get("created_at"),
                    "items": [
                        {
                            "index": item.get("index"),
                            "file_name": item.get("file_name"),
                            "size_bytes": item.get("size_bytes"),
                            "sha256": item.get("sha256"),
                            "sha256_status": item.get("sha256_status"),
                            "sensitive_file": bool(item.get("sensitive_file")),
                            "rollback_available": item.get("rollback_available"),
                        }
                        for item in value.get("items", [])
                        if isinstance(item, dict)
                    ],
                }
            )
    return values


def restore_config_snapshot(
    data_dir: Path, snapshot_id: str, item_index: int, confirmation: str
) -> dict[str, Any]:
    if not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{8}", snapshot_id or ""):
        raise DiagnosticError("快照 ID 无效")
    root = (data_dir / "snapshots" / snapshot_id).resolve(strict=False)
    allowed_root = (data_dir / "snapshots").resolve(strict=False)
    try:
        root.relative_to(allowed_root)
    except ValueError as exc:
        raise DiagnosticError("快照路径越界") from exc
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticError("快照清单不存在或已损坏") from exc
    item = next((value for value in manifest.get("items", []) if value.get("index") == item_index), None)
    if not isinstance(item, dict) or not item.get("rollback_available") or not item.get("saved_copy"):
        raise DiagnosticError("该清单项没有可回滚的配置副本")
    target = Path(str(item.get("original_path") or ""))
    if confirmation.strip() != f"RESTORE {target.name}":
        raise DiagnosticError(f"确认短语必须是 RESTORE {target.name}")
    source = (root / str(item["saved_copy"])).resolve(strict=True)
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise DiagnosticError("配置副本路径越界") from exc
    if source.is_symlink() or not source.is_file() or not target.is_absolute():
        raise DiagnosticError("配置副本或目标无效")
    try:
        current_target = target.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise DiagnosticError("目标路径无法安全解析") from exc
    if current_target != target:
        raise DiagnosticError("目标路径已被重定向，拒绝回滚")
    target = current_target
    if target.exists() and (not target.is_file() or target.is_symlink()):
        raise DiagnosticError("目标不是普通文件")
    pre_restore = (
        ensure_private_directory(root / "pre-restore")
        / f"{int(time.time())}-{secrets.token_hex(4)}-{target.name}"
    )
    if target.exists():
        shutil.copy2(target, pre_restore)
        harden_private_file(pre_restore)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".vsg-restore-", dir=str(target.parent))
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return {
        "snapshot_id": snapshot_id,
        "file_name": target.name,
        "restored": True,
        "pre_restore_backup_created": pre_restore.exists(),
    }
