from __future__ import annotations

import hashlib
import re
import secrets
import time
from pathlib import Path
from typing import Any

from .diagnostics import DiagnosticError, redact_text, validate_diagnostic_file
from .storage import Storage


MAX_LOG_BYTES = 100 * 1024 * 1024
MAX_READ_BYTES = 256 * 1024
INITIAL_TAIL_BYTES = 64 * 1024
MAX_ACTIVE_WATCHES = 32


class LogMonitorError(RuntimeError):
    pass


# Ordered from specific to generic so a CUDA OOM is not reduced to an ordinary
# error.  These adapters are intentionally deterministic and offline: no log
# line is sent to a model or an external service.
EVENT_RULES: tuple[tuple[str, str, str, str, re.Pattern[str]], ...] = (
    (
        "accelerator",
        "critical",
        "CUDA_OOM",
        "CUDA/显存不足",
        re.compile(r"(?i)(cuda.*out of memory|cublas.*alloc|CUDNN_STATUS_ALLOC_FAILED)"),
    ),
    (
        "capacity",
        "critical",
        "MEMORY_OOM",
        "内存不足",
        re.compile(r"(?i)(out of memory|cannot allocate memory|memoryerror|killed process.*oom)"),
    ),
    (
        "accelerator",
        "high",
        "CUDA_ERROR",
        "CUDA/NCCL 错误",
        re.compile(r"(?i)(cuda error|cuda failure|cublas|cudnn|nccl.*(?:error|failed|timeout))"),
    ),
    (
        "accelerator",
        "high",
        "ROCM_HIP_ERROR",
        "ROCm/HIP 错误",
        re.compile(r"(?i)(hiperror|hip error|rocm.*(?:error|failed)|miopen.*(?:error|failed))"),
    ),
    (
        "accelerator",
        "high",
        "METAL_ERROR",
        "Metal/MPS 错误",
        re.compile(r"(?i)((?:metal|mps).*(?:error|failed|timeout))"),
    ),
    (
        "accelerator",
        "high",
        "VULKAN_ERROR",
        "Vulkan 错误",
        re.compile(r"(?i)(vulkan.*(?:error|failed)|VK_ERROR_[A-Z_]+)"),
    ),
    (
        "stability",
        "critical",
        "PROCESS_CRASH",
        "进程崩溃证据",
        re.compile(r"(?i)(segmentation fault|access violation|panic:|\bfatal\b|core dumped)"),
    ),
    (
        "performance",
        "medium",
        "REQUEST_TIMEOUT",
        "请求或推理超时",
        re.compile(r"(?i)(request timed out|inference timeout|deadline exceeded|gateway timeout)"),
    ),
    (
        "capacity",
        "medium",
        "CONTEXT_LIMIT",
        "上下文或 KV 缓存受限",
        re.compile(r"(?i)(context.*(?:too long|exceed|overflow)|kv cache.*(?:full|insufficient|failed))"),
    ),
    (
        "security",
        "high",
        "AUTH_FAILURE",
        "认证失败",
        re.compile(r"(?i)(unauthorized|forbidden|invalid api key|authentication failed|401\b|403\b)"),
    ),
    (
        "configuration",
        "medium",
        "CPU_FALLBACK",
        "加速后端回退到 CPU",
        re.compile(r"(?i)(fall(?:ing)? back to cpu|gpu offload.*(?:0 layers|disabled)|no gpu acceleration)"),
    ),
    (
        "capability",
        "medium",
        "TOOL_TEMPLATE_ERROR",
        "工具调用或模板配置错误",
        re.compile(r"(?i)((?:tool call|chat template|jinja).*(?:error|invalid|unsupported|failed))"),
    ),
    (
        "lifecycle",
        "info",
        "MODEL_LOADED",
        "模型已加载",
        re.compile(r"(?i)(model (?:loaded|ready)|loaded model|server is ready|startup complete)"),
    ),
    (
        "lifecycle",
        "info",
        "MODEL_LOADING",
        "模型正在加载",
        re.compile(r"(?i)(loading model|load_model|warming up|initializing model)"),
    ),
    (
        "stability",
        "high",
        "RUNTIME_ERROR",
        "运行时错误",
        re.compile(r"(?i)(traceback \(most recent call last\)|\bexception\b|failed to load|\berror:)"),
    ),
)


def _message_hash(code: str, message: str) -> str:
    normalized = re.sub(r"\b(?:0x[0-9a-f]+|\d+(?:\.\d+)?)\b", "#", message.lower())
    return hashlib.sha256(f"{code}\0{normalized}".encode("utf-8", errors="replace")).hexdigest()


def _file_identity(path: Path) -> str:
    stat = path.stat()
    # ctime_ns catches same-inode atomic replacements on filesystems where an
    # inode alone is not a stable generation identifier.
    return f"{int(stat.st_dev)}:{int(stat.st_ino)}:{int(stat.st_ctime_ns)}"


def parse_log_line(line: str, runtime: str = "unknown", observed_at: float | None = None) -> dict[str, Any] | None:
    clean = redact_text(line.strip())[:500]
    if not clean:
        return None
    for category, severity, code, title, pattern in EVENT_RULES:
        if pattern.search(clean):
            return {
                "runtime": str(runtime or "unknown")[:80],
                "observed_at": float(observed_at or time.time()),
                "severity": severity,
                "category": category,
                "code": code,
                "title": title,
                "message": clean,
                "message_hash": _message_hash(code, clean),
                "confidence": "high",
                "source": "redacted_local_log",
            }
    return None


def _service_identity_matches(watch: dict[str, Any], service: dict[str, Any]) -> bool:
    if str(service.get("fingerprint") or "") != str(watch.get("service_fingerprint") or ""):
        return False
    process = service.get("process") or {}
    if int(process.get("pid") or 0) != int(watch.get("pid") or 0):
        return False
    expected = watch.get("process_create_time")
    actual = process.get("create_time")
    if expected is None:
        return True
    if actual is None:
        return False
    try:
        return abs(float(expected) - float(actual)) <= 0.5
    except (TypeError, ValueError):
        return False


class LogMonitor:
    def __init__(self, storage: Storage):
        self.storage = storage

    @staticmethod
    def _public_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fields = (
            "id",
            "watch_id",
            "runtime",
            "first_seen",
            "last_seen",
            "severity",
            "category",
            "code",
            "message",
            "occurrences",
        )
        return [{key: item.get(key) for key in fields} for item in events]

    def start_watch(
        self, service: dict[str, Any], path_value: str, confirmation: str
    ) -> dict[str, Any]:
        if not service.get("metadata", {}).get("model_runtime"):
            raise LogMonitorError("只允许为已识别的本机模型推理服务启用日志监控")
        process = service.get("process") or {}
        pid = int(process.get("pid") or 0)
        if pid <= 0:
            raise LogMonitorError("该服务没有可验证的宿主机 PID")
        if confirmation.strip() != f"WATCH {pid}":
            raise LogMonitorError(f"确认短语必须是 WATCH {pid}")
        try:
            path = validate_diagnostic_file(path_value, "log")
        except DiagnosticError as exc:
            raise LogMonitorError(str(exc)) from exc
        size = path.stat().st_size
        if size > MAX_LOG_BYTES:
            raise LogMonitorError("日志超过 100 MiB；请先轮转或选择较小的日志文件")
        active_watches = self.storage.log_watches(enabled_only=True, public=False)
        for current in active_watches:
            if (
                current.get("service_fingerprint") == service.get("fingerprint")
                and Path(str(current.get("path"))).resolve(strict=False) == path
            ):
                raise LogMonitorError("该服务与日志文件已处于监控状态")
        if len(active_watches) >= MAX_ACTIVE_WATCHES:
            raise LogMonitorError(f"最多同时监控 {MAX_ACTIVE_WATCHES} 个日志文件")
        watch_id = f"watch_{secrets.token_hex(12)}"
        self.storage.add_log_watch(
            {
                "id": watch_id,
                "service_id": service.get("id"),
                "service_fingerprint": service.get("fingerprint"),
                "pid": pid,
                "process_create_time": process.get("create_time"),
                "runtime": service.get("runtime") or "unknown",
                "path": str(path),
                "file_name": path.name,
                "file_identity": _file_identity(path),
                "byte_offset": max(0, size - INITIAL_TAIL_BYTES),
                "last_size": size,
            }
        )
        return self.storage.log_watch(watch_id, public=True) or {"id": watch_id}

    def stop_watch(self, watch_id: str, confirmation: str) -> dict[str, Any]:
        watch = self.storage.log_watch(watch_id, public=False)
        if not watch:
            raise LogMonitorError("日志监控记录不存在")
        if confirmation.strip() != f"WATCH {int(watch['pid'])}":
            raise LogMonitorError(f"确认短语必须是 WATCH {int(watch['pid'])}")
        self.storage.update_log_watch(
            watch_id, enabled=False, status="stopped", last_error=""
        )
        return self.storage.log_watch(watch_id, public=True) or {"id": watch_id}

    def _system_event(
        self,
        watch: dict[str, Any],
        code: str,
        severity: str,
        category: str,
        message: str,
    ) -> None:
        clean = redact_text(message)[:500]
        self.storage.add_log_event(
            {
                "watch_id": watch["id"],
                "service_fingerprint": watch["service_fingerprint"],
                "runtime": watch["runtime"],
                "observed_at": time.time(),
                "severity": severity,
                "category": category,
                "code": code,
                "message": clean,
                "message_hash": _message_hash(code, clean),
            }
        )

    def poll(self, services: list[dict[str, Any]]) -> dict[str, Any]:
        for watch in self.storage.log_watches(enabled_only=True, public=False):
            service = next(
                (item for item in services if _service_identity_matches(watch, item)), None
            )
            if service is None:
                self._system_event(
                    watch,
                    "SERVICE_EXITED",
                    "critical",
                    "stability",
                    "被监控的模型服务已退出或 PID 已被复用；监控已停止",
                )
                self.storage.update_log_watch(
                    watch["id"], enabled=False, status="service_gone", last_error="服务身份校验失败"
                )
                continue
            path = Path(str(watch["path"]))
            try:
                if not path.is_file() or path.is_symlink():
                    raise OSError("日志文件不可用或已变为符号链接")
                size = path.stat().st_size
                identity = _file_identity(path)
                offset = max(0, int(watch.get("byte_offset") or 0))
                identity_changed = bool(watch.get("file_identity")) and identity != watch.get("file_identity")
                if identity_changed or size < offset:
                    self._system_event(
                        watch, "LOG_ROTATED", "info", "lifecycle", "检测到日志轮转或截断，已从新文件开头继续"
                    )
                    offset = 0
                if size == offset:
                    self.storage.update_log_watch(
                        watch["id"], last_size=size, file_identity=identity, status="watching", last_error=""
                    )
                    continue
                with path.open("rb") as stream:
                    stream.seek(offset)
                    raw = stream.read(MAX_READ_BYTES)
                if not raw:
                    continue
                consumed = len(raw)
                if len(raw) == MAX_READ_BYTES and b"\n" in raw:
                    last_newline = raw.rfind(b"\n") + 1
                    raw = raw[:last_newline]
                    consumed = last_newline
                observed_at = time.time()
                for line in raw.decode("utf-8", errors="replace").splitlines():
                    event = parse_log_line(line, str(watch["runtime"]), observed_at)
                    if event:
                        event.update(
                            {
                                "watch_id": watch["id"],
                                "service_fingerprint": watch["service_fingerprint"],
                            }
                        )
                        self.storage.add_log_event(event)
                self.storage.update_log_watch(
                    watch["id"],
                    byte_offset=offset + consumed,
                    last_size=size,
                    file_identity=identity,
                    status="watching",
                    last_error="",
                )
            except OSError as exc:
                # The API intentionally never exposes the private absolute log
                # path.  Persist only a generic class and bounded diagnosis.
                self.storage.update_log_watch(
                    watch["id"],
                    status="file_error",
                    last_error=f"{type(exc).__name__}: 日志文件不可访问",
                )
        watches = self.storage.log_watches(public=True)
        events = self._public_events(self.storage.recent_log_events(100))
        return {
            "watches": watches,
            "events": events,
            "active_count": sum(bool(item.get("enabled")) for item in watches),
            "event_count": len(events),
            "privacy": "仅保存脱敏事件和本机私有监控游标；接口不返回日志绝对路径或原始日志",
        }

    def status(self) -> dict[str, Any]:
        watches = self.storage.log_watches(public=True)
        events = self._public_events(self.storage.recent_log_events(100))
        return {
            "watches": watches,
            "events": events,
            "active_count": sum(bool(item.get("enabled")) for item in watches),
            "event_count": len(events),
            "requires_confirmation": "WATCH <PID>",
            "privacy": "原始日志不入库；脱敏事件保存在本机私有 SQLite",
        }
