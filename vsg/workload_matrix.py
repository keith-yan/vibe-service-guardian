from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from typing import Any, Callable

from .diagnostics import redact_text
from .service_benchmark import (
    MAX_CALIBRATION_REQUESTS,
    MAX_CONTEXT_TOKENS,
    run_service_benchmark,
)
from .storage import Storage


SnapshotProvider = Callable[[], dict[str, Any]]
HardwareProvider = Callable[[], dict[str, Any]]
PredictionProvider = Callable[[str, str, dict[str, Any]], dict[str, Any] | None]
LowDiskProvider = Callable[[], float]
LOGGER = logging.getLogger("vsg")
MAX_PREVIEW_HISTORY = 128
MAX_JOB_HISTORY = 100
TERMINAL_JOB_STATES = {
    "completed",
    "cancelled",
    "failed",
    "guard_stopped",
    "identity_changed",
}


class WorkloadMatrixError(ValueError):
    pass


def resource_guard(
    snapshot: dict[str, Any],
    low_disk_free_gib: float,
    *,
    require_memory_evidence: bool = False,
) -> dict[str, Any]:
    telemetry = snapshot.get("telemetry") or {}
    blockers: list[str] = []
    evidence: list[str] = []
    memory_percent = (telemetry.get("memory") or {}).get("used_percent")
    if memory_percent is not None:
        evidence.append(f"RAM {float(memory_percent):.1f}%")
        if float(memory_percent) >= 85:
            blockers.append("系统内存使用率已达到 85%，拒绝启动新的基准负载")
    elif require_memory_evidence:
        blockers.append("未获得系统内存占用证据，拒绝启动 60 秒本机校准")
    gpu_items = telemetry.get("gpus") or []
    measured_gpu_memory = False
    discrete_gpu_reported = False
    for gpu in gpu_items:
        name = str(gpu.get("name") or "GPU")
        memory_util = gpu.get("memory_util_percent")
        temperature = gpu.get("temperature_c")
        if gpu.get("memory_total_gib") is not None and not gpu.get("unified_memory"):
            discrete_gpu_reported = True
        if memory_util is not None:
            measured_gpu_memory = True
            evidence.append(f"{name} VRAM {float(memory_util):.1f}%")
            if float(memory_util) >= 85:
                blockers.append(f"{name} 显存占用已达到 85%，拒绝启动新的基准负载")
        if temperature is not None:
            evidence.append(f"{name} {float(temperature):.1f}°C")
            if float(temperature) >= 88:
                blockers.append(f"{name} 温度已达到 88°C，拒绝启动新的基准负载")
    if require_memory_evidence and discrete_gpu_reported and not measured_gpu_memory:
        blockers.append("检测到独立 GPU，但未获得 VRAM 占用证据，拒绝启动 60 秒本机校准")
    for sensor in (telemetry.get("sensors") or {}).get("temperatures") or []:
        current = sensor.get("current_c")
        if current is not None and float(current) >= 90:
            blockers.append(f"传感器 {sensor.get('label') or sensor.get('group') or 'temperature'} 已达到 90°C")
    for disk in telemetry.get("disks") or []:
        free = disk.get("free_gib")
        if free is None:
            continue
        evidence.append(f"{disk.get('root') or 'disk'} free {float(free):.1f} GiB")
        if float(free) < float(low_disk_free_gib):
            blockers.append(
                f"{disk.get('root') or '磁盘'} 剩余 {float(free):.1f} GiB，低于 {float(low_disk_free_gib):.1f} GiB 护栏"
            )
    return {
        "allowed": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "evidence": evidence[:20],
        "thresholds": {
            "memory_used_percent": 85,
            "gpu_memory_used_percent": 85,
            "gpu_temperature_c": 88,
            "system_temperature_c": 90,
            "disk_free_gib": float(low_disk_free_gib),
        },
    }


def _probe_for(snapshot: dict[str, Any], service_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in snapshot.get("runtime_probes") or []
            if item.get("service_id") == service_id
        ),
        None,
    )


def _service_for(snapshot: dict[str, Any], service_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in snapshot.get("services") or [] if item.get("id") == service_id),
        None,
    )


def _resource_sample(snapshot: dict[str, Any]) -> dict[str, Any]:
    telemetry = snapshot.get("telemetry") or {}
    gpu_memory = [
        float(item["memory_util_percent"])
        for item in telemetry.get("gpus") or []
        if item.get("memory_util_percent") is not None
    ]
    gpu_temperature = [
        float(item["temperature_c"])
        for item in telemetry.get("gpus") or []
        if item.get("temperature_c") is not None
    ]
    disk_free = [
        float(item["free_gib"])
        for item in telemetry.get("disks") or []
        if item.get("free_gib") is not None
    ]
    return {
        "at": snapshot.get("generated_at") or time.time(),
        "memory_used_percent": (telemetry.get("memory") or {}).get("used_percent"),
        "gpu_memory_used_percent": max(gpu_memory) if gpu_memory else None,
        "gpu_temperature_c": max(gpu_temperature) if gpu_temperature else None,
        "disk_free_gib": min(disk_free) if disk_free else None,
    }


def _resource_peaks(samples: list[dict[str, Any]]) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        return [float(item[key]) for item in samples if item.get(key) is not None]

    ram = values("memory_used_percent")
    vram = values("gpu_memory_used_percent")
    temperature = values("gpu_temperature_c")
    disk = values("disk_free_gib")
    return {
        "samples": len(samples),
        "peak_ram_used_percent": round(max(ram), 2) if ram else None,
        "peak_vram_used_percent": round(max(vram), 2) if vram else None,
        "peak_gpu_temperature_c": round(max(temperature), 2) if temperature else None,
        "minimum_disk_free_gib": round(min(disk), 2) if disk else None,
        "source": "collector snapshots sampled during the workload step",
        "limitation": "采样周期可能遗漏短于控制台刷新间隔的瞬时峰值",
    }


def _error_percent(actual: Any, predicted: Any) -> dict[str, float] | None:
    try:
        actual_value = float(actual)
        predicted_value = float(predicted)
    except (TypeError, ValueError):
        return None
    if predicted_value <= 0:
        return None
    signed = (actual_value - predicted_value) / predicted_value * 100
    return {
        "predicted": round(predicted_value, 3),
        "measured": round(actual_value, 3),
        "signed_percent": round(signed, 2),
        "absolute_percent": round(abs(signed), 2),
    }


def _hardware_vram_total_gib(hardware: dict[str, Any]) -> float | None:
    values = [
        float(item["memory_total_gib"])
        for item in hardware.get("gpus") or []
        if item.get("memory_total_gib") is not None
    ]
    return round(sum(values), 2) if values else None


class WorkloadMatrixManager:
    def __init__(
        self,
        storage: Storage,
        snapshot_provider: SnapshotProvider,
        hardware_provider: HardwareProvider,
        prediction_provider: PredictionProvider,
        low_disk_provider: LowDiskProvider,
    ):
        self.storage = storage
        self.snapshot_provider = snapshot_provider
        self.hardware_provider = hardware_provider
        self.prediction_provider = prediction_provider
        self.low_disk_provider = low_disk_provider
        self._lock = threading.RLock()
        self._previews: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._active_job_id: str | None = None

    def _safe_audit(
        self, action: str, target: str, result: str, details: dict[str, Any] | None = None
    ) -> None:
        """Keep observability failures from corrupting job lifecycle state."""

        try:
            self.storage.add_audit(action, target, result, details)
        except Exception:
            LOGGER.warning("workload matrix audit failed: %s", action, exc_info=True)

    def _prune_locked(self, now: float | None = None) -> None:
        current = float(now or time.time())
        self._previews = {
            key: value
            for key, value in self._previews.items()
            if float(value.get("expires_at") or 0) >= current
        }
        if len(self._previews) > MAX_PREVIEW_HISTORY:
            retained = {
                key
                for key, _value in sorted(
                    self._previews.items(),
                    key=lambda item: float(item[1].get("created_at") or 0),
                    reverse=True,
                )[:MAX_PREVIEW_HISTORY]
            }
            self._previews = {
                key: value for key, value in self._previews.items() if key in retained
            }

        if len(self._jobs) <= MAX_JOB_HISTORY:
            return
        removable = sorted(
            (
                key
                for key, value in self._jobs.items()
                if key != self._active_job_id
                and value.get("status") in TERMINAL_JOB_STATES
                and not self._threads.get(key, threading.current_thread()).is_alive()
            ),
            key=lambda key: float(self._jobs[key].get("created_at") or 0),
        )
        for key in removable:
            if len(self._jobs) <= MAX_JOB_HISTORY:
                break
            self._jobs.pop(key, None)
            self._events.pop(key, None)
            self._threads.pop(key, None)

    def preview(
        self,
        service: dict[str, Any],
        probe: dict[str, Any],
        *,
        catalog_model_id: str | None,
        quantization: str | None,
        mode: str = "matrix",
        model_name: str | None = None,
        concurrency: int | None = None,
        duration_seconds: int | None = None,
    ) -> dict[str, Any]:
        if probe.get("health") != "ready":
            raise WorkloadMatrixError("模型服务尚未就绪，不能生成主动负载计划")
        if (probe.get("security") or {}).get("auth_posture") == "required":
            raise WorkloadMatrixError("该模型服务需要认证；VSG 不读取或代填 API Key")
        if service.get("runtime") == "ComfyUI":
            raise WorkloadMatrixError("ComfyUI 工作流不属于文本补全负载矩阵")
        port = int(probe.get("port") or 0)
        if not 1 <= port <= 65535:
            raise WorkloadMatrixError("模型服务端口无效")
        capacity = probe.get("capacity") or {}
        configuration = probe.get("configuration") or {}
        reported_context = int(capacity.get("context_tokens") or configuration.get("context_tokens") or MAX_CONTEXT_TOKENS)
        context_cap = max(128, min(MAX_CONTEXT_TOKENS, reported_context))
        models = probe.get("models") or []
        known_models = [
            str(item.get("name") or "")
            for item in models
            if isinstance(item, dict) and item.get("name")
        ]
        selected_model = str(model_name or "").strip() or (known_models[0] if known_models else "")
        if not selected_model:
            raise WorkloadMatrixError("运行时未报告已加载模型，无法锁定负载目标")
        if known_models and selected_model not in set(known_models):
            raise WorkloadMatrixError("校准目标不在运行时报告的已加载模型清单中")

        if mode not in {"matrix", "calibration"}:
            raise WorkloadMatrixError("负载模式无效")
        calibration_mode = mode == "calibration"
        if calibration_mode:
            selected_concurrency = int(concurrency or 1)
            selected_duration = int(duration_seconds or 60)
            if selected_concurrency not in {1, 2}:
                raise WorkloadMatrixError("60 秒校准只允许单并发或双并发")
            if selected_duration != 60:
                raise WorkloadMatrixError("首版本机校准时长固定为 60 秒")
            fixed_steps = [
                (
                    f"calibration-c{selected_concurrency}",
                    f"{selected_concurrency} 并发 · 60 秒本机校准",
                    selected_concurrency,
                    min(1024, context_cap),
                    32,
                    MAX_CALIBRATION_REQUESTS,
                )
            ]
        else:
            selected_concurrency = 4
            selected_duration = None
            fixed_steps = [
                ("baseline", "单请求基线", 1, min(512, context_cap), 32, 5),
                ("interactive", "双并发交互", 2, min(1024, context_cap), 32, 10),
                ("concurrency", "四并发持续", 4, min(2048, context_cap), 64, 20),
            ]
        steps: list[dict[str, Any]] = []
        prediction_limitations: list[str] = []
        for step_id, label, concurrency, context_tokens, output_tokens, request_count in fixed_steps:
            workload = {
                "concurrency": concurrency,
                "context_tokens": max(128, context_tokens),
                "prompt_tokens": max(128, context_tokens),
                "output_tokens": output_tokens,
            }
            prediction = None
            if catalog_model_id and quantization:
                try:
                    prediction = self.prediction_provider(
                        catalog_model_id, str(quantization), workload
                    )
                except Exception as exc:
                    prediction_limitations.append(
                        f"{step_id}: 容量预测不可用（{type(exc).__name__}），该步骤仍可只测实绩"
                    )
            steps.append(
                {
                    "id": step_id,
                    "label": label,
                    "concurrency": concurrency,
                    "context_tokens": workload["context_tokens"],
                    "output_tokens": output_tokens,
                    "request_count": request_count,
                    "waves": (request_count + concurrency - 1) // concurrency,
                    "duration_seconds": selected_duration if calibration_mode else None,
                    "prediction": prediction,
                }
            )
        snapshot = self.snapshot_provider()
        guard = resource_guard(
            snapshot,
            self.low_disk_provider(),
            require_memory_evidence=calibration_mode,
        )
        plan_id = secrets.token_urlsafe(16)
        created_at = time.time()
        plan = {
            "schema_version": "1.1",
            "plan_id": plan_id,
            "created_at": created_at,
            "expires_at": created_at + 300,
            "service_id": service.get("id"),
            "service_fingerprint": service.get("fingerprint"),
            "pid": int((service.get("process") or {}).get("pid") or 0),
            "process_create_time": (service.get("process") or {}).get("create_time"),
            "runtime": service.get("runtime"),
            "port": port,
            "model_name": selected_model,
            "mode": mode,
            "catalog_model_id": catalog_model_id,
            "quantization": quantization,
            "capacity_calibration": bool(catalog_model_id and quantization),
            "prediction_limitations": prediction_limitations,
            "steps": steps,
            "total_requests": sum(int(item[5]) for item in fixed_steps),
            "maximum_concurrency": selected_concurrency if calibration_mode else 4,
            "guard": guard,
            "confirmation": f"BENCHMARK {port}" if calibration_mode else f"BENCHMARK PLAN {port}",
            "fixed_policy": {
                "automatic_expansion": False,
                "deliberate_oom": False,
                "one_active_plan": True,
                "cancel_between_waves": True,
                "inflight_request_may_finish": True,
                "duration_seconds": selected_duration,
                "resource_utilization_stop_percent": 85,
            },
        }
        with self._lock:
            self._previews[plan_id] = plan
            self._prune_locked(created_at)
        return json.loads(json.dumps(plan, ensure_ascii=False))

    def start(self, plan_id: str, confirmation: str) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            plan = self._previews.get(plan_id)
            if not plan:
                raise WorkloadMatrixError("负载计划不存在或已经失效，请重新预览")
            if time.time() > float(plan.get("expires_at") or 0):
                self._previews.pop(plan_id, None)
                raise WorkloadMatrixError("负载计划已超过 5 分钟有效期，请重新预览")
            if confirmation != plan.get("confirmation"):
                raise WorkloadMatrixError(f"确认短语必须是 {plan.get('confirmation')}")
            if self._active_job_id:
                active = self._jobs.get(self._active_job_id) or {}
                if active.get("status") in {"queued", "running", "cancelling"}:
                    raise WorkloadMatrixError("已有一个工作负载矩阵正在运行；单机首版不允许叠加主动负载")
            snapshot = self.snapshot_provider()
            current = _service_for(snapshot, str(plan.get("service_id") or ""))
            probe = _probe_for(snapshot, str(plan.get("service_id") or ""))
            if not current or not probe:
                raise WorkloadMatrixError("服务身份或运行时探测结果已经变化，请重新预览")
            if current.get("fingerprint") != plan.get("service_fingerprint"):
                raise WorkloadMatrixError("服务指纹已经变化，请重新预览")
            calibration_mode = plan.get("mode") == "calibration"
            guard = resource_guard(
                snapshot,
                self.low_disk_provider(),
                require_memory_evidence=calibration_mode,
            )
            if not guard["allowed"]:
                raise WorkloadMatrixError("；".join(guard["blockers"]))
            job_id = secrets.token_urlsafe(16)
            job = {
                "schema_version": "1.0",
                "job_id": job_id,
                "plan_id": plan_id,
                "created_at": time.time(),
                "started_at": None,
                "completed_at": None,
                "status": "queued",
                "service_id": plan.get("service_id"),
                "service_fingerprint": plan.get("service_fingerprint"),
                "port": plan.get("port"),
                "model_name": plan.get("model_name"),
                "mode": plan.get("mode") or "matrix",
                "catalog_model_id": plan.get("catalog_model_id"),
                "quantization": plan.get("quantization"),
                "step_count": len(plan.get("steps") or []),
                "completed_steps": 0,
                "current_step": None,
                "results": [],
                "error": None,
                "cancel_requested": False,
            }
            event = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(job_id, json.loads(json.dumps(plan)), event),
                name=f"vsg-workload-{job_id[:8]}",
                daemon=True,
            )
            self._jobs[job_id] = job
            self._events[job_id] = event
            self._threads[job_id] = thread
            self._active_job_id = job_id
            try:
                thread.start()
            except Exception as exc:
                self._jobs.pop(job_id, None)
                self._events.pop(job_id, None)
                self._threads.pop(job_id, None)
                if self._active_job_id == job_id:
                    self._active_job_id = None
                raise WorkloadMatrixError("无法启动工作负载线程；计划仍可重新确认") from exc
            self._previews.pop(plan_id, None)
            return json.loads(json.dumps(job, ensure_ascii=False))

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(values)

    def _progress(self, job_id: str, completed: int, total: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or not isinstance(job.get("current_step"), dict):
                return
            job["current_step"]["completed_requests"] = completed
            job["current_step"]["request_count"] = total

    def _resource_progress(
        self,
        job_id: str,
        samples: list[dict[str, Any]],
        guard: dict[str, Any],
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or not isinstance(job.get("current_step"), dict):
                return
            job["current_step"]["resource_samples"] = samples[-180:]
            job["current_step"]["resource_guard"] = guard

    def _run(self, job_id: str, plan: dict[str, Any], cancel_event: threading.Event) -> None:
        self._update(job_id, status="running", started_at=time.time())
        self._safe_audit(
            "benchmark_matrix.start",
            str(plan.get("service_id") or "service"),
            "success",
            {"job_id": job_id, "plan_id": plan.get("plan_id"), "steps": len(plan.get("steps") or [])},
        )
        terminal_status = "completed"
        terminal_error: str | None = None
        try:
            for step_index, step in enumerate(plan.get("steps") or []):
                if cancel_event.is_set():
                    terminal_status = "cancelled"
                    break
                snapshot = self.snapshot_provider()
                service = _service_for(snapshot, str(plan.get("service_id") or ""))
                probe = _probe_for(snapshot, str(plan.get("service_id") or ""))
                if not service or not probe or service.get("fingerprint") != plan.get("service_fingerprint"):
                    terminal_status = "identity_changed"
                    terminal_error = "服务 PID、指纹或运行时探测身份已经变化，剩余步骤未执行"
                    break
                calibration_mode = plan.get("mode") == "calibration"
                guard = resource_guard(
                    snapshot,
                    self.low_disk_provider(),
                    require_memory_evidence=calibration_mode,
                )
                if not guard["allowed"]:
                    terminal_status = "guard_stopped"
                    terminal_error = "；".join(guard["blockers"])
                    break
                current_step = {
                    **step,
                    "index": step_index + 1,
                    "status": "running",
                    "completed_requests": 0,
                    "guard": guard,
                }
                self._update(job_id, current_step=current_step)
                samples: list[dict[str, Any]] = []
                sampler_stop = threading.Event()
                guard_triggered: dict[str, Any] = {"triggered": False, "blockers": []}

                def sample(
                    target_samples: list[dict[str, Any]],
                    stop_event: threading.Event,
                    guard_state: dict[str, Any],
                    memory_evidence_required: bool,
                ) -> None:
                    while not stop_event.is_set():
                        current_snapshot = self.snapshot_provider()
                        target_samples.append(_resource_sample(current_snapshot))
                        current_guard = resource_guard(
                            current_snapshot,
                            self.low_disk_provider(),
                            require_memory_evidence=memory_evidence_required,
                        )
                        self._resource_progress(
                            job_id, target_samples, current_guard
                        )
                        if not current_guard["allowed"]:
                            guard_state["triggered"] = True
                            guard_state["blockers"] = current_guard["blockers"]
                            cancel_event.set()
                            break
                        stop_event.wait(0.35)
                    final_snapshot = self.snapshot_provider()
                    target_samples.append(_resource_sample(final_snapshot))
                    self._resource_progress(
                        job_id,
                        target_samples,
                        resource_guard(
                            final_snapshot,
                            self.low_disk_provider(),
                            require_memory_evidence=memory_evidence_required,
                        ),
                    )

                sampler = threading.Thread(
                    target=sample,
                    args=(samples, sampler_stop, guard_triggered, calibration_mode),
                    name="vsg-workload-sampler",
                    daemon=True,
                )
                sampler.start()
                try:
                    result = run_service_benchmark(
                        service,
                        probe,
                        {
                            "model": plan.get("model_name"),
                            "concurrency": step.get("concurrency"),
                            "context_tokens": step.get("context_tokens"),
                            "output_tokens": step.get("output_tokens"),
                            "confirmation": f"BENCHMARK {plan.get('port')}",
                        },
                        float((snapshot.get("telemetry") or {}).get("memory", {}).get("used_percent") or 0),
                        request_count=int(step.get("request_count") or step.get("concurrency") or 1),
                        cancel_event=cancel_event,
                        progress_callback=lambda completed, total: self._progress(job_id, completed, total),
                        duration_seconds=(
                            int(step["duration_seconds"])
                            if step.get("duration_seconds") is not None
                            else None
                        ),
                    )
                finally:
                    sampler_stop.set()
                    sampler.join(timeout=2)
                prediction = step.get("prediction") or {}
                per_user_prediction = (prediction.get("per_user_generation_tps") or {}).get("expected")
                aggregate_prediction = prediction.get("aggregate_generation_tps")
                ttft_prediction = (prediction.get("ttft_seconds") or {}).get("expected")
                hardware: dict[str, Any] = {}
                hardware_fingerprint = None
                try:
                    hardware = self.hardware_provider()
                    hardware_fingerprint = hardware.get("hardware_fingerprint")
                except Exception:
                    LOGGER.warning(
                        "workload matrix hardware fingerprint unavailable",
                        exc_info=True,
                    )
                result.update(
                    {
                        "matrix_id": job_id,
                        "matrix_step_id": step.get("id"),
                        "hardware_fingerprint": hardware_fingerprint,
                        "catalog_model_id": plan.get("catalog_model_id"),
                        "quantization": plan.get("quantization"),
                        "resource_peaks": _resource_peaks(samples),
                        "prediction": prediction or None,
                        "prediction_error": {
                            "per_user_generation_tps": _error_percent(result.get("generation_tps"), per_user_prediction),
                            "aggregate_generation_tps": _error_percent(result.get("aggregate_generation_tps"), aggregate_prediction),
                            "ttft_seconds": _error_percent(result.get("ttft_seconds"), ttft_prediction),
                        }
                        if prediction
                        else None,
                    }
                )
                result.setdefault("details", {})["matrix"] = {
                    "matrix_id": job_id,
                    "matrix_step_id": step.get("id"),
                    "prediction": prediction or None,
                    "prediction_error": result.get("prediction_error"),
                    "resource_peaks": result.get("resource_peaks"),
                    "mode": plan.get("mode"),
                    "project": {
                        "name": (service.get("project") or {}).get("name"),
                        "confidence": (service.get("project") or {}).get("confidence"),
                    },
                }
                result["id"] = self.storage.add_service_benchmark(result)
                if plan.get("mode") == "calibration":
                    peaks = result.get("resource_peaks") or {}
                    peak_ram = peaks.get("peak_ram_used_percent")
                    peak_vram = peaks.get("peak_vram_used_percent")
                    within_guard = not result.get("oom_observed") and all(
                        value is None or float(value) < 85
                        for value in (peak_ram, peak_vram)
                    )
                    measured_safe = bool(
                        within_guard
                        and not result.get("cancelled")
                        and int(result.get("successful_requests") or 0) > 0
                    )
                    profile_id = secrets.token_urlsafe(16)
                    profile = {
                        "schema_version": "1.0",
                        "profile_id": profile_id,
                        "created_at": time.time(),
                        "updated_at": time.time(),
                        "status": "active",
                        "source": "measured_local",
                        "hardware_fingerprint": str(hardware_fingerprint or ""),
                        "vram_total_gib": _hardware_vram_total_gib(hardware),
                        "service_fingerprint": str(plan.get("service_fingerprint") or ""),
                        "service_id": plan.get("service_id"),
                        "project_name": (service.get("project") or {}).get("name"),
                        "runtime": plan.get("runtime"),
                        "port": int(plan.get("port") or 0),
                        "model_name": plan.get("model_name"),
                        "catalog_model_id": plan.get("catalog_model_id"),
                        "quantization": plan.get("quantization"),
                        "concurrency": int(step.get("concurrency") or 1),
                        "duration_seconds": int(step.get("duration_seconds") or 60),
                        "context_tokens": int(step.get("context_tokens") or 0),
                        "output_tokens": int(step.get("output_tokens") or 0),
                        "measured_safe": measured_safe,
                        "recommended_safe_concurrency": (
                            int(step.get("concurrency") or 1) if measured_safe else 0
                        ),
                        "measurement": {
                            "ttft_seconds": result.get("ttft_seconds"),
                            "ttft_p95_seconds": result.get("ttft_p95_seconds"),
                            "generation_tps": result.get("generation_tps"),
                            "aggregate_generation_tps": result.get(
                                "aggregate_generation_tps"
                            ),
                            "successful_requests": result.get("successful_requests"),
                            "failed_requests": result.get("failed_requests"),
                            "wall_seconds": result.get("wall_seconds"),
                            "resource_peaks": peaks,
                            "oom_observed": result.get("oom_observed"),
                        },
                        "theoretical_capacity": step.get("prediction"),
                        "evidence_label": "基于本机短时实测，非理论估算",
                        "limitations": [
                            "60 秒短时负载不能证明长时间稳定性",
                            "达到 85% 资源阈值后只停止发起新请求，不强杀在途推理",
                            "未故意制造 OOM，因此理论容量上限不是实验证实的物理极限",
                        ],
                    }
                    self.storage.add_calibration_profile(profile)
                    result["calibration_profile"] = {
                        "profile_id": profile_id,
                        "measured_safe": measured_safe,
                        "recommended_safe_concurrency": profile[
                            "recommended_safe_concurrency"
                        ],
                    }
                public_result = {key: value for key, value in result.items() if key != "details"}
                with self._lock:
                    job = self._jobs[job_id]
                    job["results"].append(public_result)
                    job["completed_steps"] = len(job["results"])
                    job["current_step"] = {**current_step, "status": "completed"}
                if guard_triggered["triggered"]:
                    terminal_status = "guard_stopped"
                    terminal_error = "；".join(guard_triggered["blockers"])
                    break
                if result.get("cancelled") or cancel_event.is_set():
                    terminal_status = "cancelled"
                    break
                if result.get("oom_observed"):
                    terminal_status = "guard_stopped"
                    terminal_error = "日志或请求错误中出现 OOM 证据，剩余步骤未执行"
                    break
                if int(result.get("successful_requests") or 0) <= 0:
                    terminal_status = "failed"
                    terminal_error = "当前步骤没有成功请求，剩余步骤未执行"
                    break
        except Exception as exc:
            terminal_status = "failed"
            terminal_error = redact_text(f"{type(exc).__name__}: {str(exc)}")[:400]
        finally:
            completed_at = time.time()
            self._update(
                job_id,
                status=terminal_status,
                error=terminal_error,
                current_step=None,
                completed_at=completed_at,
            )
            with self._lock:
                completed_steps = (self._jobs.get(job_id) or {}).get("completed_steps")
                if self._active_job_id == job_id:
                    self._active_job_id = None
            self._safe_audit(
                "benchmark_matrix.finish",
                str(plan.get("service_id") or "service"),
                terminal_status,
                {
                    "job_id": job_id,
                    "completed_steps": completed_steps,
                    "error": terminal_error,
                },
            )

    def status(self, job_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            selected = job_id or self._active_job_id
            if not selected and self._jobs:
                selected = max(self._jobs, key=lambda key: float(self._jobs[key].get("created_at") or 0))
            job = self._jobs.get(selected or "")
            return {
                "active_job_id": self._active_job_id,
                "job": json.loads(json.dumps(job, ensure_ascii=False)) if job else None,
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            job = self._jobs.get(job_id)
            if not job:
                raise WorkloadMatrixError("工作负载任务不存在")
            if job.get("status") not in {"queued", "running", "cancelling"}:
                return json.loads(json.dumps(job, ensure_ascii=False))
            event = self._events.get(job_id)
            if not event:
                raise WorkloadMatrixError("工作负载任务状态不完整，无法中止")
            event.set()
            job["cancel_requested"] = True
            job["status"] = "cancelling"
            result = json.loads(json.dumps(job, ensure_ascii=False))
        self._safe_audit(
            "benchmark_matrix.cancel",
            str(job.get("service_id") or "service"),
            "requested",
            {"job_id": job_id},
        )
        return result

    def close(self) -> bool:
        with self._lock:
            events = list(self._events.values())
            threads = list(self._threads.values())
        for event in events:
            event.set()
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=8)
        return not any(thread.is_alive() for thread in threads)
