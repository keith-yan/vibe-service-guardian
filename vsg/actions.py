from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Callable

import psutil

from .network import collect_connections
from .scanner import redacted_command_hash


class ActionError(RuntimeError):
    pass


def _process_create_time(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _listener_snapshot() -> list[dict[str, Any]]:
    endpoint_map, _established, errors, status = collect_connections(include_udp=True)
    if errors and not any(endpoint_map.values()):
        raise OSError(str(status.get("message") or errors[0]))

    listeners: list[dict[str, Any]] = []
    for pid, endpoints in endpoint_map.items():
        owner_pid = int(pid) if int(pid) > 0 else None
        for endpoint in endpoints:
            listeners.append(
                {
                    "protocol": endpoint.protocol,
                    "address": endpoint.address,
                    "port": endpoint.port,
                    "pid": owner_pid,
                }
            )
    return listeners


def _matching_command_processes(expected_hash: str) -> list[dict[str, Any]]:
    """Find local processes matching a redacted command hash.

    This function reads only process metadata already used by the scanner.  It
    returns PID/parent/start evidence and never returns command arguments.
    """

    if not expected_hash:
        return []
    matches: list[dict[str, Any]] = []
    try:
        iterator = psutil.process_iter(
            attrs=["pid", "ppid", "name", "cmdline", "create_time"], ad_value=None
        )
        for process in iterator:
            try:
                info = process.info
                arguments = info.get("cmdline") or []
                if redacted_command_hash(arguments) != expected_hash:
                    continue
                matches.append(
                    {
                        "pid": int(info.get("pid") or 0),
                        "ppid": int(info["ppid"]) if info.get("ppid") is not None else None,
                        "name": str(info.get("name") or "unknown")[:160],
                        "create_time": float(info["create_time"])
                        if info.get("create_time") is not None
                        else None,
                    }
                )
            except (psutil.Error, OSError, TypeError, ValueError):
                continue
    except (psutil.Error, OSError):
        return []
    return matches


def _parent_process_evidence(pid: int, original_parent_pid: int | None) -> dict[str, Any]:
    try:
        process = psutil.Process(pid)
        parent_pid = int(process.ppid())
        parent_name = None
        if parent_pid > 0:
            try:
                parent_name = psutil.Process(parent_pid).name()[:160]
            except (psutil.Error, OSError):
                parent_name = None
    except (psutil.Error, OSError):
        parent_pid = None
        parent_name = None
    return {
        "replacement_parent_pid": parent_pid,
        "replacement_parent_name": parent_name,
        "original_parent_pid": original_parent_pid,
        "parent_changed": bool(
            parent_pid is not None
            and original_parent_pid is not None
            and parent_pid != original_parent_pid
        ),
    }


def verify_post_stop(
    service: dict[str, Any],
    affected_pids: list[int],
    *,
    observation_seconds: float = 4.0,
    poll_interval: float = 0.5,
    process_probe: Callable[[int], float | None] = _process_create_time,
    listener_provider: Callable[[], list[dict[str, Any]]] = _listener_snapshot,
    command_match_provider: Callable[[str], list[dict[str, Any]]] = _matching_command_processes,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    stop_on_restart: bool = False,
) -> dict[str, Any]:
    """Observe a bounded post-stop window without taking a second action.

    A different process re-binding one of the original ports is reported as a
    relaunch.  The verifier never kills that replacement process.
    """

    process = service.get("process") or {}
    original_pid = int(process.get("pid") or 0)
    original_parent_pid = int(process.get("ppid") or 0) or None
    expected_create_time = process.get("create_time")
    expected_command_hash = str((service.get("metadata") or {}).get("command_hash") or "")
    endpoints = [
        {
            "protocol": str(item.get("protocol") or "").upper(),
            "address": str(item.get("address") or ""),
            "port": int(item.get("port") or 0),
        }
        for item in service.get("endpoints") or []
        if int(item.get("port") or 0) > 0
    ]
    target_pids = sorted({int(item) for item in affected_pids if int(item) > 0} | ({original_pid} if original_pid > 0 else set()))
    replacement_pids: set[int] = set()
    surviving_pids: set[int] = set()
    endpoint_seen: dict[tuple[str, int], set[int | None]] = {
        (item["protocol"], item["port"]): set() for item in endpoints
    }
    listener_status = "measured"
    limitations: list[str] = []
    restart_evidence: list[dict[str, Any]] = []
    evidence_keys: set[tuple[str, int | None, int | None]] = set()
    original_absent_observed = False
    cancelled = False
    checks = 0
    started = monotonic()
    deadline = started + max(0.0, float(observation_seconds))

    while True:
        checks += 1
        surviving_pids.clear()
        for pid in target_pids:
            create_time = process_probe(pid)
            if create_time is None:
                continue
            if pid == original_pid and expected_create_time is not None:
                if abs(float(create_time) - float(expected_create_time)) <= 0.01:
                    surviving_pids.add(pid)
                else:
                    replacement_pids.add(pid)
            else:
                surviving_pids.add(pid)
        original_create_time = process_probe(original_pid) if original_pid > 0 else None
        current_original_alive = bool(
            original_create_time is not None
            and (
                expected_create_time is None
                or abs(float(original_create_time) - float(expected_create_time)) <= 0.01
            )
        )
        if not current_original_alive:
            original_absent_observed = True
        try:
            listeners = listener_provider()
        except (psutil.Error, OSError) as exc:
            listeners = []
            listener_status = "unavailable"
            message = f"停止后无法读取端口表：{type(exc).__name__}"
            if message not in limitations:
                limitations.append(message)
        for listener in listeners:
            key = (str(listener.get("protocol") or "").upper(), int(listener.get("port") or 0))
            if key not in endpoint_seen:
                continue
            listener_pid = listener.get("pid")
            normalized_pid = int(listener_pid) if listener_pid is not None else None
            endpoint_seen[key].add(normalized_pid)
            if original_absent_observed and (
                normalized_pid is None or normalized_pid not in target_pids
            ):
                evidence_key = ("port_rebound", normalized_pid, key[1])
                if evidence_key not in evidence_keys:
                    evidence_keys.add(evidence_key)
                    restart_evidence.append(
                        {
                            "type": "port_rebound",
                            "detected_at": time.time(),
                            "protocol": key[0],
                            "port": key[1],
                            "replacement_pid": normalized_pid,
                            **(
                                _parent_process_evidence(normalized_pid, original_parent_pid)
                                if normalized_pid is not None
                                else {
                                    "replacement_parent_pid": None,
                                    "replacement_parent_name": None,
                                    "original_parent_pid": original_parent_pid,
                                    "parent_changed": None,
                                }
                            ),
                        }
                    )
                if normalized_pid is not None and normalized_pid not in target_pids:
                    replacement_pids.add(normalized_pid)
        if original_absent_observed and expected_command_hash:
            try:
                command_matches = command_match_provider(expected_command_hash)
            except (psutil.Error, OSError):
                command_matches = []
                message = "无法在持续观察期间读取同命令哈希进程"
                if message not in limitations:
                    limitations.append(message)
            for match in command_matches:
                match_pid = int(match.get("pid") or 0)
                match_created = match.get("create_time")
                is_original_identity = bool(
                    match_pid == original_pid
                    and expected_create_time is not None
                    and match_created is not None
                    and abs(float(match_created) - float(expected_create_time)) <= 0.01
                )
                if match_pid <= 0 or is_original_identity or match_pid in target_pids:
                    continue
                evidence_key = ("command_hash_reappeared", match_pid, None)
                if evidence_key in evidence_keys:
                    continue
                evidence_keys.add(evidence_key)
                replacement_pids.add(match_pid)
                parent_pid = int(match.get("ppid") or 0) or None
                restart_evidence.append(
                    {
                        "type": "command_hash_reappeared",
                        "detected_at": time.time(),
                        "replacement_pid": match_pid,
                        "replacement_parent_pid": parent_pid,
                        "replacement_parent_name": None,
                        "original_parent_pid": original_parent_pid,
                        "parent_changed": bool(
                            parent_pid is not None
                            and original_parent_pid is not None
                            and parent_pid != original_parent_pid
                        ),
                    }
                )
        now = monotonic()
        if progress_callback:
            try:
                progress_callback(
                    {
                        "checked_at": time.time(),
                        "checks": checks,
                        "remaining_seconds": round(max(0.0, deadline - now), 1),
                        "original_pid_alive": current_original_alive,
                        "original_pid_disappeared": original_absent_observed,
                        "listener_status": listener_status,
                        "restart_detected": bool(restart_evidence),
                        "restart_evidence": restart_evidence[-5:],
                    }
                )
            except Exception:
                pass
        if cancel_event and cancel_event.is_set():
            cancelled = True
            break
        if stop_on_restart and restart_evidence:
            break
        if now >= deadline:
            break
        delay = min(max(0.01, poll_interval), max(0.0, deadline - now))
        if cancel_event is not None:
            if cancel_event.wait(delay):
                cancelled = True
                break
        else:
            sleeper(delay)

    final_listeners: list[dict[str, Any]] = []
    try:
        latest = listener_provider()
    except (psutil.Error, OSError):
        latest = []
        listener_status = "unavailable"
    for endpoint in endpoints:
        matches = [
            item
            for item in latest
            if str(item.get("protocol") or "").upper() == endpoint["protocol"]
            and int(item.get("port") or 0) == endpoint["port"]
        ]
        pids = sorted({int(item["pid"]) for item in matches if item.get("pid") is not None})
        unknown_owner = any(item.get("pid") is None for item in matches)
        if original_absent_observed and matches:
            for pid in pids:
                if pid not in target_pids:
                    replacement_pids.add(pid)
            replacement_pid = next((pid for pid in pids if pid not in target_pids), None)
            if unknown_owner or replacement_pid is not None:
                evidence_key = ("port_rebound", replacement_pid, int(endpoint["port"]))
                if evidence_key not in evidence_keys:
                    evidence_keys.add(evidence_key)
                    restart_evidence.append(
                        {
                            "type": "port_rebound",
                            "detected_at": time.time(),
                            "protocol": endpoint["protocol"],
                            "port": int(endpoint["port"]),
                            "replacement_pid": replacement_pid,
                            **(
                                _parent_process_evidence(
                                    replacement_pid, original_parent_pid
                                )
                                if replacement_pid is not None
                                else {
                                    "replacement_parent_pid": None,
                                    "replacement_parent_name": None,
                                    "original_parent_pid": original_parent_pid,
                                    "parent_changed": None,
                                }
                            ),
                        }
                    )
        final_listeners.append(
            {
                **endpoint,
                "closed": not matches if listener_status == "measured" else None,
                "listener_pids": pids,
                "owner_unknown": unknown_owner,
                "observed_pids_during_window": sorted(
                    pid for pid in endpoint_seen[(endpoint["protocol"], endpoint["port"])] if pid is not None
                ),
            }
        )

    original_create_time = process_probe(original_pid) if original_pid > 0 else None
    original_alive = bool(
        original_create_time is not None
        and (
            expected_create_time is None
            or abs(float(original_create_time) - float(expected_create_time)) <= 0.01
        )
    )
    any_port_open = any(item.get("closed") is False for item in final_listeners)
    unknown_port = any(item.get("closed") is None for item in final_listeners)
    if original_alive or surviving_pids:
        outcome = "stop_incomplete"
    elif restart_evidence:
        outcome = "relaunched"
    elif cancelled:
        outcome = "cancelled"
    elif any_port_open or unknown_port:
        outcome = "verification_partial"
    else:
        outcome = "stopped"

    return {
        "schema_version": "1.1",
        "service_id": service.get("id"),
        "service_fingerprint": service.get("fingerprint"),
        "original_pid": original_pid,
        "expected_create_time": expected_create_time,
        "checked_at": time.time(),
        "observation_window_seconds": round(max(0.0, monotonic() - started), 2),
        "checks": checks,
        "outcome": outcome,
        "cancelled": cancelled,
        "original_pid_disappeared": original_absent_observed,
        "original_pid_alive": original_alive,
        "surviving_pids": sorted(surviving_pids),
        "replacement_pids": sorted(replacement_pids),
        "restart_detected": bool(restart_evidence),
        "restart_evidence": restart_evidence,
        "parent_process_changed": any(item.get("parent_changed") is True for item in restart_evidence),
        "automatic_restart_evidence": "observed" if restart_evidence else "not_observed" if outcome == "stopped" else "unknown",
        "endpoint_verification": final_listeners,
        "listener_status": listener_status,
        "second_stop_attempted": False,
        "limitations": limitations
        + ["只观察有限时间窗；更晚发生的自动重启不会由本次观察作出结论"],
    }


def _current_process_tree() -> set[int]:
    protected = {os.getpid(), os.getppid()}
    try:
        current = psutil.Process(os.getpid())
        for parent in current.parents():
            protected.add(parent.pid)
    except psutil.Error:
        pass
    return protected


def terminate_process_tree(
    pid: int,
    expected_create_time: float | None,
    confirmation: str,
    protected_names: list[str],
    already_protected: bool = False,
    timeout: float = 4.0,
) -> dict[str, Any]:
    if confirmation != f"STOP {pid}":
        raise ActionError(f"确认短语必须是 STOP {pid}")
    if pid <= 0:
        raise ActionError("无效 PID")
    if already_protected or pid in _current_process_tree():
        raise ActionError("目标属于保护进程，拒绝停止")

    try:
        process = psutil.Process(pid)
        actual_create_time = process.create_time()
    except psutil.NoSuchProcess as exc:
        raise ActionError("进程已经退出") from exc
    except psutil.AccessDenied as exc:
        raise ActionError("权限不足，未执行停止操作") from exc

    if expected_create_time is not None and abs(actual_create_time - float(expected_create_time)) > 0.01:
        raise ActionError("PID 已被复用，目标启动时间不匹配")

    protected = {item.lower() for item in protected_names}
    try:
        name = process.name().lower()
    except psutil.AccessDenied as exc:
        raise ActionError("无法验证目标进程名称，未执行停止操作") from exc
    except psutil.NoSuchProcess as exc:
        raise ActionError("进程已经退出") from exc
    if name in protected:
        raise ActionError(f"{name or pid} 位于保护名单，拒绝停止")

    try:
        children = process.children(recursive=True)
    except psutil.AccessDenied as exc:
        raise ActionError("无法完整读取目标进程树，未执行停止操作") from exc
    except psutil.NoSuchProcess as exc:
        raise ActionError("进程已经退出") from exc
    for child in children:
        try:
            if child.name().lower() in protected:
                raise ActionError(f"进程树包含受保护子进程 {child.name()}，未执行任何操作")
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise ActionError("无法验证子进程名称，未执行停止操作") from exc

    targets = children[::-1] + [process]
    requested: list[int] = []
    requested_targets: list[psutil.Process] = []
    errors: list[str] = []
    for target in targets:
        try:
            # psutil.Process retains its original create time and is_running()
            # rejects a PID that was reused between discovery and signalling.
            if not target.is_running():
                continue
            target.terminate()
            requested.append(target.pid)
            requested_targets.append(target)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            errors.append(f"终止 PID {target.pid} 时权限不足")

    try:
        _, alive = psutil.wait_procs(requested_targets, timeout=timeout)
    except psutil.Error as exc:
        alive = list(requested_targets)
        errors.append(f"等待进程退出失败：{type(exc).__name__}")
    forced: list[int] = []
    forced_targets: list[psutil.Process] = []
    for target in alive:
        try:
            if not target.is_running():
                continue
            target.kill()
            forced.append(target.pid)
            forced_targets.append(target)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            errors.append(f"强制结束 PID {target.pid} 时权限不足")
    still_alive: list[int] = []
    if forced_targets:
        try:
            _, remaining = psutil.wait_procs(forced_targets, timeout=2)
            still_alive = [target.pid for target in remaining if target.is_running()]
        except psutil.Error as exc:
            errors.append(f"强制结束后验证失败：{type(exc).__name__}")
            still_alive = [target.pid for target in forced_targets if target.is_running()]
    return {
        "pid": pid,
        "terminated": requested,
        "forced": forced,
        "still_alive": sorted(set(still_alive)),
        "errors": errors,
        "completed": not errors and not still_alive,
        "completed_at": time.time(),
    }


def _launch_project_path(path: Path, platform_name: str | None = None) -> None:
    current_platform = platform_name or sys.platform
    if current_platform.startswith("win") and hasattr(os, "startfile"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    command = "open" if current_platform == "darwin" else "xdg-open"
    executable = shutil.which(command)
    if not executable:
        raise ActionError("当前平台缺少打开目录所需的系统命令")
    try:
        subprocess.Popen(
            [executable, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise ActionError("无法调用系统文件管理器") from exc


def open_project_path(path_value: str, allowed_roots: list[str]) -> None:
    path = Path(path_value).expanduser().resolve(strict=False)
    if not path.exists() or not path.is_dir():
        raise ActionError("项目目录不存在")
    allowed = False
    for root_value in allowed_roots:
        root = Path(root_value).expanduser().resolve(strict=False)
        try:
            if os.path.commonpath([str(path), str(root)]) == str(root):
                allowed = True
                break
        except ValueError:
            continue
    if not allowed:
        raise ActionError("目录不在已配置项目根目录内")
    _launch_project_path(path)


def open_local_url(port: int, path: str = "/") -> str:
    if not 1 <= int(port) <= 65535:
        raise ActionError("端口无效")
    safe_path = path if path.startswith("/") else "/"
    parsed = urllib.parse.urlsplit(safe_path)
    if parsed.scheme or parsed.netloc:
        raise ActionError("只允许打开本机相对路径")
    url = f"http://127.0.0.1:{int(port)}{safe_path}"
    webbrowser.open(url, new=2)
    return url
