from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from typing import Any, Callable

from .actions import verify_post_stop
from .notifications import send_system_notification
from .storage import Storage


LOGGER = logging.getLogger("vsg")
OBSERVATION_MINUTES = {5, 15, 30}
DEFAULT_POLL_SECONDS = 10
MAX_ACTIVE_OBSERVATIONS = 8
MAX_JOB_HISTORY = 100
Verifier = Callable[..., dict[str, Any]]
Notifier = Callable[[str, str], dict[str, Any]]


class StopObservationError(ValueError):
    pass


class StopObservationManager:
    """Manage bounded, cancellable post-stop evidence windows."""

    def __init__(
        self,
        storage: Storage,
        *,
        notifications_enabled: Callable[[], bool],
        verifier: Verifier = verify_post_stop,
        notifier: Notifier = send_system_notification,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
    ):
        self.storage = storage
        self.notifications_enabled = notifications_enabled
        self.verifier = verifier
        self.notifier = notifier
        self.poll_seconds = max(10, min(int(poll_seconds), 15))
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._restore_interrupted_jobs()

    def _restore_interrupted_jobs(self) -> None:
        stored = self.storage.recent_stop_observations(200)
        for job in stored:
            if job.get("status") not in {"observing", "cancel_requested"}:
                continue
            job["status"] = "interrupted"
            job["updated_at"] = time.time()
            job["remaining_seconds"] = 0
            job["conclusion"] = "evidence_insufficient"
            job["limitations"] = list(job.get("limitations") or []) + [
                "VSG 在观察窗口内退出；未把中断后的状态推断为成功或复活"
            ]
            self.storage.upsert_stop_observation(job)

    def _prune_locked(self) -> None:
        if len(self._jobs) <= MAX_JOB_HISTORY:
            return
        removable = sorted(
            (
                job_id
                for job_id, job in self._jobs.items()
                if job.get("status")
                not in {"observing", "cancel_requested"}
                and not self._threads.get(job_id, threading.current_thread()).is_alive()
            ),
            key=lambda job_id: float(self._jobs[job_id].get("created_at") or 0),
        )
        for job_id in removable:
            if len(self._jobs) <= MAX_JOB_HISTORY:
                break
            self._jobs.pop(job_id, None)
            self._events.pop(job_id, None)
            self._threads.pop(job_id, None)

    @staticmethod
    def _copy(value: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(value, ensure_ascii=False))

    def _persist(self, job: dict[str, Any]) -> None:
        try:
            self.storage.upsert_stop_observation(job)
        except Exception:
            LOGGER.warning("stop observation persistence failed", exc_info=True)

    def _timeline(
        self,
        job: dict[str, Any],
        code: str,
        severity: str,
        title_zh: str,
        title_en: str,
        details: dict[str, Any],
    ) -> None:
        try:
            self.storage.add_timeline_event(
                {
                    "category": "lifecycle",
                    "code": code,
                    "severity": severity,
                    "service_fingerprint": job.get("service_fingerprint"),
                    "service_id": job.get("service_id"),
                    "project_name": job.get("project_name"),
                    "title_zh": title_zh,
                    "title_en": title_en,
                    "details": details,
                    "dedup_key": f"{code}:{job.get('job_id')}",
                },
                dedup_seconds=0,
            )
        except Exception:
            LOGGER.warning("stop observation timeline write failed", exc_info=True)

    def _audit(
        self,
        action: str,
        target: str,
        result: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.storage.add_audit(action, target, result, details)
        except Exception:
            LOGGER.warning("stop observation audit write failed", exc_info=True)

    @staticmethod
    def _port_state(report: dict[str, Any]) -> str:
        if any(
            item.get("type") == "port_rebound"
            for item in report.get("restart_evidence") or []
        ):
            return "reopened"
        endpoints = report.get("endpoint_verification") or []
        if any(item.get("closed") is False for item in endpoints):
            return "reopened"
        if endpoints and all(item.get("closed") is True for item in endpoints):
            return "closed"
        return "unknown"

    def start(
        self,
        service: dict[str, Any],
        affected_pids: list[int],
        observation_minutes: int,
    ) -> dict[str, Any]:
        try:
            minutes = int(observation_minutes)
        except (TypeError, ValueError) as exc:
            raise StopObservationError("观察时长必须是 5、15 或 30 分钟") from exc
        if minutes not in OBSERVATION_MINUTES:
            raise StopObservationError("观察时长必须是 5、15 或 30 分钟")
        process = service.get("process") or {}
        original_pid = int(process.get("pid") or 0)
        fingerprint = str(service.get("fingerprint") or "")
        if original_pid <= 0 or not fingerprint:
            raise StopObservationError("服务身份不完整，不能启动持续观察")

        with self._lock:
            self._prune_locked()
            active = [
                item
                for item in self._jobs.values()
                if item.get("status") in {"observing", "cancel_requested"}
            ]
            if len(active) >= MAX_ACTIVE_OBSERVATIONS:
                raise StopObservationError("已有过多持续观察任务，请等待或取消后重试")
            if any(item.get("service_fingerprint") == fingerprint for item in active):
                raise StopObservationError("该服务已有持续观察任务")
            now = time.time()
            job_id = secrets.token_urlsafe(16)
            job = {
                "schema_version": "1.0",
                "job_id": job_id,
                "created_at": now,
                "updated_at": now,
                "deadline_at": now + minutes * 60,
                "remaining_seconds": minutes * 60,
                "status": "observing",
                "conclusion": None,
                "service_id": service.get("id"),
                "service_fingerprint": fingerprint,
                "display_name": str(service.get("display_name") or "service")[:160],
                "project_name": str((service.get("project") or {}).get("name") or "")[:160]
                or None,
                "original_pid": original_pid,
                "original_parent_pid": int(process.get("ppid") or 0) or None,
                "observation_minutes": minutes,
                "poll_seconds": self.poll_seconds,
                "checks": 0,
                "port_state": "unknown",
                "restart_detected": False,
                "attention_required": False,
                "event_sequence": 0,
                "progress": {},
                "report": None,
                "limitations": [
                    "仅观察当前用户可见的进程树、端口、启动时间和脱敏命令哈希",
                    "不会自动停止观察期间重新出现的进程",
                ],
            }
            cancel_event = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(job_id, self._copy(service), list(affected_pids), cancel_event),
                name=f"vsg-stop-observe-{job_id[:8]}",
                daemon=True,
            )
            self._jobs[job_id] = job
            self._events[job_id] = cancel_event
            self._threads[job_id] = thread
            self._persist(job)
            try:
                thread.start()
            except Exception as exc:
                job["status"] = "failed"
                job["updated_at"] = time.time()
                job["limitations"].append(f"观察线程启动失败：{type(exc).__name__}")
                self._persist(job)
                raise StopObservationError("无法启动持续观察线程") from exc
        self._audit(
            "stop_observation.start",
            str(service.get("id") or fingerprint),
            "success",
            {"job_id": job_id, "minutes": minutes, "poll_seconds": self.poll_seconds},
        )
        self._timeline(
            job,
            "STOP_OBSERVATION_STARTED",
            "info",
            "已开始停止后持续观察",
            "Post-stop observation started",
            {"original_pid": original_pid, "minutes": minutes},
        )
        return self._copy(job)

    def _progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["updated_at"] = time.time()
            job["remaining_seconds"] = float(progress.get("remaining_seconds") or 0)
            job["checks"] = int(progress.get("checks") or 0)
            job["restart_detected"] = bool(progress.get("restart_detected"))
            evidence = progress.get("restart_evidence") or []
            job["port_state"] = (
                "reopened"
                if any(item.get("type") == "port_rebound" for item in evidence)
                else "closed"
                if progress.get("original_pid_disappeared")
                and progress.get("listener_status") == "measured"
                else "unknown"
            )
            job["progress"] = progress
            self._persist(job)

    def _run(
        self,
        job_id: str,
        service: dict[str, Any],
        affected_pids: list[int],
        cancel_event: threading.Event,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            observation_seconds = int(job["observation_minutes"]) * 60
        try:
            report = self.verifier(
                service,
                affected_pids,
                observation_seconds=observation_seconds,
                poll_interval=self.poll_seconds,
                cancel_event=cancel_event,
                progress_callback=lambda progress: self._progress(job_id, progress),
                stop_on_restart=True,
            )
            report["phase"] = "observation"
            report["observation_job_id"] = job_id
            try:
                report["id"] = self.storage.add_stop_verification(report)
            except Exception as exc:
                # Observation is already complete. A local history write
                # failure must not erase a measured relaunch conclusion.
                report["id"] = None
                report["limitations"] = list(report.get("limitations") or []) + [
                    f"停止验证未能写入本机历史：{type(exc).__name__}"
                ]
                LOGGER.warning(
                    "stop observation verification persistence failed",
                    exc_info=True,
                )
            with self._lock:
                job = self._jobs[job_id]
                if report.get("outcome") == "relaunched":
                    status = "relaunched"
                    conclusion = (
                        "higher_level_relaunch"
                        if report.get("parent_process_changed")
                        or (service.get("metadata") or {}).get("lifecycle_manager")
                        else "relaunched"
                    )
                elif report.get("outcome") == "stopped":
                    status, conclusion = "completed", "successfully_disappeared"
                elif report.get("outcome") == "cancelled":
                    status, conclusion = "cancelled", "cancelled"
                else:
                    status, conclusion = "evidence_insufficient", "evidence_insufficient"
                job.update(
                    {
                        "updated_at": time.time(),
                        "remaining_seconds": 0,
                        "status": status,
                        "conclusion": conclusion,
                        "restart_detected": bool(report.get("restart_detected")),
                        "attention_required": status == "relaunched",
                        "event_sequence": int(job.get("event_sequence") or 0) + 1,
                        "port_state": self._port_state(report),
                        "report": report,
                    }
                )
                if status == "relaunched" and self.notifications_enabled():
                    try:
                        job["system_notification"] = self.notifier(
                            "VSG：服务已复活，需人工介入",
                            f"{job.get('display_name')} · 原 PID {job.get('original_pid')}",
                        )
                    except Exception as exc:
                        job["system_notification"] = {
                            "sent": False,
                            "reason": type(exc).__name__,
                        }
                else:
                    job["system_notification"] = {
                        "sent": False,
                        "reason": "disabled" if not self.notifications_enabled() else "not_required",
                    }
                self._persist(job)
            if status == "relaunched":
                self._timeline(
                    job,
                    "STOP_OBSERVATION_RELAUNCHED",
                    "critical",
                    "停止后的服务重新出现，需人工介入",
                    "Stopped service relaunched; manual action required",
                    {
                        "original_pid": job.get("original_pid"),
                        "replacement_pids": report.get("replacement_pids") or [],
                        "parent_process_changed": report.get("parent_process_changed"),
                        "restart_evidence": report.get("restart_evidence") or [],
                    },
                )
            else:
                self._timeline(
                    job,
                    "STOP_OBSERVATION_FINISHED",
                    "info" if status == "completed" else "warning",
                    "停止后持续观察已结束",
                    "Post-stop observation finished",
                    {"outcome": report.get("outcome"), "conclusion": conclusion},
                )
            self._audit(
                "stop_observation.finish",
                str(job.get("service_id") or job.get("service_fingerprint")),
                status,
                {
                    "job_id": job_id,
                    "outcome": report.get("outcome"),
                    "restart_detected": report.get("restart_detected"),
                },
            )
        except Exception as exc:
            LOGGER.exception("stop observation failed")
            with self._lock:
                job = self._jobs[job_id]
                job.update(
                    {
                        "updated_at": time.time(),
                        "remaining_seconds": 0,
                        "status": "failed",
                        "conclusion": "evidence_insufficient",
                        "event_sequence": int(job.get("event_sequence") or 0) + 1,
                    }
                )
                job["limitations"] = list(job.get("limitations") or []) + [
                    f"观察失败：{type(exc).__name__}"
                ]
                self._persist(job)

    def status(self, job_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            if job_id:
                job = self._jobs.get(job_id)
                if not job:
                    job = next(
                        (
                            item
                            for item in self.storage.recent_stop_observations(200)
                            if item.get("job_id") == job_id
                        ),
                        None,
                    )
                return {"job": self._copy(job) if job else None}
            live = [self._copy(item) for item in self._jobs.values()]
        stored = self.storage.recent_stop_observations(50)
        by_id = {str(item.get("job_id")): item for item in stored}
        for item in live:
            by_id[str(item.get("job_id"))] = item
        items = sorted(
            by_id.values(),
            key=lambda item: float(item.get("updated_at") or 0),
            reverse=True,
        )[:50]
        return {
            "items": items,
            "active": [
                item
                for item in items
                if item.get("status") in {"observing", "cancel_requested"}
            ],
        }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            event = self._events.get(job_id)
            if not job or not event:
                raise StopObservationError("持续观察任务不存在或已结束")
            if job.get("status") not in {"observing", "cancel_requested"}:
                return self._copy(job)
            event.set()
            job["status"] = "cancel_requested"
            job["updated_at"] = time.time()
            self._persist(job)
            return self._copy(job)

    def close(self) -> bool:
        with self._lock:
            events = list(self._events.values())
            threads = list(self._threads.values())
        for event in events:
            event.set()
        deadline = time.monotonic() + 12
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in threads)
