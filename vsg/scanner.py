from __future__ import annotations

import hashlib
import os
import re
import time
from collections import defaultdict
from typing import Any

import psutil

from .attribution import attribute_agent, attribute_project, detect_runtime, identify_agent_process
from .config import AppConfig
from .containers import scan_docker, scan_wsl
from .models import Endpoint, ProcessSnapshot, ServiceRecord
from .network import collect_connections
from .platforms import platform_info, platform_key
from .project_rules import apply_project_manifest, apply_rules
from .sessions import SessionHint, load_recent_session_hints
from .stale import MODEL_SERVER_RUNTIMES, OPENABLE_RUNTIMES, STOPPABLE_RUNTIMES, assess_all
from .storage import Storage


SENSITIVE_KEY_RE = re.compile(
    r"(?ix)(?:^|[^a-z0-9])(?:"
    r"api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|"
    r"credential|authorization|cookie|session[_-]?key|client[_-]?secret|private[_-]?key|"
    r"database[_-]?url|db[_-]?url|dsn|connection[_-]?string"
    r")(?:$|[^a-z0-9])"
)
URI_USERINFO_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)([^\s/@:]+):([^\s/@]+)@"
)
QUERY_SECRET_RE = re.compile(
    r"(?ix)([?&;](?:"
    r"api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|"
    r"credential|authorization|cookie|session[_-]?key|client[_-]?secret|private[_-]?key"
    r")=)([^&#;\s]+)"
)
OPTION_ASSIGNMENT_RE = re.compile(
    r"^(?P<key>(?:--?|/)?[A-Za-z][A-Za-z0-9_.-]*)=(?P<value>.*)$"
)
INLINE_SECRET_RE = re.compile(
    r"(?ix)(?:"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|npm_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_-]{20,}|"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/-]{8,}"
    r")"
)


def _normalized_identity_path(value: str | None) -> str:
    """Normalize an already-visible local path for a one-way ownership signature."""

    if not value:
        return ""
    try:
        normalized = os.path.realpath(os.path.expanduser(value))
    except (OSError, ValueError):
        normalized = value
    # normcase folds case on Windows but preserves it on case-sensitive POSIX
    # filesystems.  An unconditional casefold would merge distinct Linux paths.
    return os.path.normcase(os.path.normpath(normalized))


def ownership_signature(process: ProcessSnapshot) -> str | None:
    """Return a privacy-preserving identity for historical ownership labels.

    The user-defined contract is exact executable path plus exact working
    directory.  Only the digest is persisted in an attribution rule; paths are
    never copied into the historical-label record.
    """

    executable = _normalized_identity_path(process.exe)
    working_directory = _normalized_identity_path(process.cwd)
    if not executable or not working_directory:
        return None
    material = f"v1|{executable}|{working_directory}"
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def _lifecycle_manager(source: str, windows_services: list[str], ancestors: list[ProcessSnapshot]) -> str | None:
    if windows_services:
        return "Windows Service Control Manager"
    if source == "agent":
        return "Agent/IDE parent"
    names = {item.name.casefold() for item in ancestors}
    if names & {"systemd", "systemd --user"}:
        return "systemd"
    if names & {"launchd"}:
        return "launchd"
    if names & {"pm2", "pm2.exe", "pm2-runtime", "pm2-runtime.exe"}:
        return "PM2"
    if names & {"supervisord", "supervisorctl"}:
        return "supervisord"
    return None


def _redact_inline(value: str) -> str:
    value = URI_USERINFO_RE.sub(r"\1\2:[REDACTED]@", value)
    value = QUERY_SECRET_RE.sub(r"\1[REDACTED]", value)
    return INLINE_SECRET_RE.sub("[REDACTED]", value)


def redact_arguments(arguments: list[str] | tuple[str, ...] | None) -> list[str]:
    if not arguments:
        return []
    result: list[str] = []
    redact_next = False
    for raw in arguments:
        value = str(raw)
        if redact_next:
            result.append("[REDACTED]")
            redact_next = False
            continue
        assignment = OPTION_ASSIGNMENT_RE.match(value)
        if assignment:
            key = assignment.group("key")
            if SENSITIVE_KEY_RE.search(key):
                result.append(f"{key}=[REDACTED]")
                continue
        if value.startswith("-") and SENSITIVE_KEY_RE.search(value):
            result.append(value)
            redact_next = True
            continue
        result.append(_redact_inline(value))
    return result


def redacted_command_hash(arguments: list[str] | tuple[str, ...] | None) -> str:
    """Hash the same redacted command representation used by service records."""

    command = " ".join(redact_arguments(arguments)).strip()
    # An unavailable command line is not identity evidence. Hashing the empty
    # string would make every access-denied process look identical and could
    # create a false relaunch conclusion during post-stop observation.
    if not command:
        return ""
    return hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()


class Scanner:
    def __init__(self, config: AppConfig, storage: Storage | None = None):
        self.config = config
        self.storage = storage
        self._process_objects: dict[tuple[int, int], psutil.Process] = {}
        self._session_hints: list[SessionHint] = []
        self._session_hints_at = 0.0
        self._external_cache: dict[str, tuple[float, list[ServiceRecord], dict[str, Any]]] = {}

    def update_config(self, config: AppConfig) -> None:
        self.config = config
        self._external_cache.clear()

    def _sessions(self) -> list[SessionHint]:
        if time.time() - self._session_hints_at > 30:
            self._session_hints = load_recent_session_hints()
            self._session_hints_at = time.time()
        return self._session_hints

    def _external_services(
        self,
        name: str,
        enabled: bool,
        loader: Any,
        ttl: float = 30.0,
    ) -> tuple[list[ServiceRecord], dict[str, Any]]:
        if not enabled:
            return [], {"status": "disabled", "message": "已在设置中关闭"}
        cached = self._external_cache.get(name)
        if cached and time.time() - cached[0] < ttl:
            return cached[1], cached[2]
        services, status = loader()
        self._external_cache[name] = (time.time(), services, status)
        return services, status

    def _processes(self, required_pids: set[int]) -> dict[int, ProcessSnapshot]:
        table: dict[int, ProcessSnapshot] = {}
        live_keys: set[tuple[int, int]] = set()
        attrs = ["pid", "ppid", "name", "exe", "cmdline", "cwd", "create_time", "memory_percent", "status"]
        queue = list(required_pids)
        visited: set[int] = set()
        while queue and len(visited) < 512:
            pid = int(queue.pop())
            if pid <= 0 or pid in visited:
                continue
            visited.add(pid)
            try:
                process = psutil.Process(pid)
                info = process.as_dict(attrs=attrs, ad_value=None)
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, OSError):
                info = {"pid": pid, "ppid": None, "name": "受限进程"}
            created = info.get("create_time")
            key = (pid, int(float(created or 0) * 1000))
            live_keys.add(key)
            cached = self._process_objects.get(key) or process
            self._process_objects[key] = cached
            try:
                cpu = float(cached.cpu_percent(interval=None))
            except (psutil.Error, OSError):
                cpu = 0.0
            accessible = bool(info.get("exe") or info.get("cwd") or info.get("cmdline"))
            table[pid] = ProcessSnapshot(
                pid=pid,
                ppid=int(info["ppid"]) if info.get("ppid") is not None else None,
                name=str(info.get("name") or "unknown"),
                exe=str(info["exe"]) if info.get("exe") else None,
                cmdline=redact_arguments(info.get("cmdline")),
                cwd=str(info["cwd"]) if info.get("cwd") else None,
                create_time=float(created) if created else None,
                cpu_percent=cpu,
                memory_percent=float(info.get("memory_percent") or 0.0),
                status=str(info.get("status") or "unknown"),
                accessible=accessible,
            )
            parent_id = table[pid].ppid
            if parent_id and parent_id not in visited:
                queue.append(parent_id)
        self._process_objects = {key: value for key, value in self._process_objects.items() if key in live_keys}
        return table

    def _agent_pids(self) -> set[int]:
        candidates: dict[int, tuple[int | None, str]] = {}
        attrs = ["pid", "ppid", "name", "exe", "cmdline"]
        try:
            iterator = psutil.process_iter(attrs=attrs, ad_value=None)
            for process in iterator:
                try:
                    info = process.info
                    snapshot = ProcessSnapshot(
                        pid=int(info.get("pid") or 0),
                        name=str(info.get("name") or "unknown"),
                        exe=str(info["exe"]) if info.get("exe") else None,
                        cmdline=redact_arguments(info.get("cmdline")),
                    )
                    signature = identify_agent_process(snapshot)
                    if signature and signature.kind == "agent" and snapshot.pid > 0:
                        parent = int(info["ppid"]) if info.get("ppid") is not None else None
                        candidates[snapshot.pid] = (parent, signature.provider)
                except (psutil.Error, OSError, ValueError):
                    continue
        except (psutil.Error, OSError):
            return set(candidates)

        roots: set[int] = set(candidates)
        for pid, (parent, provider) in candidates.items():
            seen: set[int] = set()
            while parent and parent not in seen:
                seen.add(parent)
                parent_candidate = candidates.get(parent)
                if not parent_candidate:
                    break
                if parent_candidate[1] == provider:
                    roots.discard(pid)
                    break
                parent = parent_candidate[0]
        return roots

    def _connections(
        self,
    ) -> tuple[dict[int, list[Endpoint]], dict[int, int], list[str], dict[str, Any]]:
        return collect_connections(self.config.include_udp)

    @staticmethod
    def _ancestors(process: ProcessSnapshot, table: dict[int, ProcessSnapshot], limit: int = 12) -> list[ProcessSnapshot]:
        chain: list[ProcessSnapshot] = []
        seen = {process.pid}
        parent_id = process.ppid
        while parent_id and parent_id not in seen and len(chain) < limit:
            parent = table.get(parent_id)
            if not parent:
                break
            chain.append(parent)
            seen.add(parent.pid)
            parent_id = parent.ppid
        return chain

    def _windows_service_map(self) -> dict[int, list[str]]:
        result: dict[int, list[str]] = defaultdict(list)
        if os.name != "nt" or not self.config.include_windows_services or not hasattr(psutil, "win_service_iter"):
            return result
        try:
            for service in psutil.win_service_iter():
                try:
                    info = service.as_dict()
                except psutil.Error:
                    continue
                pid = int(info.get("pid") or 0)
                if pid > 0 and str(info.get("status") or "").lower() == "running":
                    result[pid].append(str(info.get("display_name") or info.get("name") or "Windows service"))
        except (psutil.Error, OSError):
            return result
        return result

    def _host_services(
        self,
        processes: dict[int, ProcessSnapshot],
        endpoints_by_pid: dict[int, list[Endpoint]],
        established_by_pid: dict[int, int],
        agent_pids: set[int],
    ) -> list[ServiceRecord]:
        service_map = self._windows_service_map()
        session_hints = self._sessions()
        marks = self.storage.marks() if self.storage else {}
        services: list[ServiceRecord] = []
        visible_pids = set(endpoints_by_pid) | agent_pids
        for pid in visible_pids:
            endpoints = endpoints_by_pid.get(pid, [])
            process = processes.get(pid)
            if process is None:
                process = ProcessSnapshot(pid=max(pid, 0), name="受限或已退出进程", accessible=False)
            ancestors = self._ancestors(process, processes)
            project = attribute_project(process, ancestors, self.config.project_roots)
            agent = attribute_agent(process, ancestors, project.path, session_hints)
            runtime = detect_runtime(process)
            windows_services = service_map.get(pid, [])
            source = "windows_service" if windows_services else "agent" if pid in agent_pids else "host"
            command_hash = redacted_command_hash(process.cmdline)
            historical_signature = ownership_signature(process)
            fingerprint_material = "|".join(
                [
                    source,
                    (process.exe or process.name).lower(),
                    (process.cwd or "").lower(),
                    command_hash,
                ]
            )
            fingerprint = hashlib.sha256(fingerprint_material.encode("utf-8")).hexdigest()[:20]
            mark = marks.get(fingerprint, {})
            protected = (
                bool(mark.get("protected"))
                or process.name.lower() in set(self.config.protected_names)
                or source == "agent"
            )
            if pid in {os.getpid(), os.getppid()}:
                protected = True
            tags: list[str] = []
            if any(item.exposure == "all_interfaces" for item in endpoints):
                tags.append("all_interfaces")
            elif any(item.exposure == "lan" for item in endpoints):
                tags.append("lan")
            if not process.accessible:
                tags.append("limited_visibility")
            if agent.provider:
                tags.append("agent_attributed")
            if windows_services:
                display_name = windows_services[0]
            elif source == "agent" and agent.provider:
                display_name = agent.provider
            else:
                display_name = project.name or process.name
            created_key = int((process.create_time or 0) * 1000)
            service = ServiceRecord(
                id=f"{source}:{pid}:{created_key}",
                fingerprint=fingerprint,
                source=source,
                display_name=display_name,
                runtime=runtime,
                process=process,
                endpoints=sorted(endpoints, key=lambda item: (item.protocol, item.port, item.address)),
                ancestor_chain=ancestors,
                project=project,
                agent=agent,
                windows_services=windows_services,
                established_connections=established_by_pid.get(pid, 0),
                expected=bool(mark.get("expected")),
                protected=protected,
                tags=tags,
                metadata={
                    "command_hash": command_hash,
                    "ownership_signature": historical_signature,
                    "mark_note": mark.get("note"),
                    "mark_present": bool(mark),
                    "stoppable_candidate": source == "host" and runtime in STOPPABLE_RUNTIMES and not protected,
                    "openable_candidate": runtime in OPENABLE_RUNTIMES,
                    "agent_process": source == "agent",
                    "model_runtime": runtime in MODEL_SERVER_RUNTIMES,
                    "auto_restart": None,
                    "lifecycle_manager": _lifecycle_manager(source, windows_services, ancestors),
                },
            )
            services.append(service)
        return services

    def scan(self) -> dict[str, Any]:
        started = time.time()
        errors: list[str] = []
        collectors: dict[str, Any] = {}
        current_platform = platform_info()
        endpoints_by_pid, established_by_pid, host_errors, connection_status = self._connections()
        listener_pids = {pid for pid in endpoints_by_pid if pid > 0}
        agent_pids = self._agent_pids()
        processes = self._processes(listener_pids | agent_pids)
        services = self._host_services(processes, endpoints_by_pid, established_by_pid, agent_pids)
        errors.extend(host_errors)
        collectors["host"] = {
            **connection_status,
            "message": (
                f"{connection_status.get('message', '')}；读取 {len(listener_pids)} 个监听 PID"
                f"，以及 {max(0, len(processes) - len(listener_pids) - len(agent_pids - listener_pids))} 个祖先进程"
            ),
        }
        collectors["agent"] = {
            "status": "ok",
            "message": f"检测到 {len(agent_pids)} 个受支持 Agent 进程（含无监听端口进程）",
        }
        collectors["platform"] = {
            "status": "ok" if current_platform["supported"] else "partial",
            "message": f"{current_platform['label']} · {current_platform['architecture']}",
        }

        docker_services, docker_status = self._external_services(
            "docker", self.config.include_docker, scan_docker
        )
        services.extend(docker_services)
        collectors["docker"] = docker_status
        if platform_key() == "windows":
            wsl_services, wsl_status = self._external_services("wsl", self.config.include_wsl, scan_wsl)
        else:
            wsl_services, wsl_status = [], {
                "status": "unavailable",
                "message": "WSL 仅适用于 Windows，当前平台不执行扫描",
            }
        services.extend(wsl_services)
        collectors["wsl"] = wsl_status

        # Container/WSL collectors use the same public record shape.  Add a
        # signature when both path components are visible, but never weaken
        # their independent lifecycle protections.
        for service in services:
            service.metadata.setdefault("ownership_signature", ownership_signature(service.process))

        rules = self.storage.attribution_rules(enabled_only=True) if self.storage else []
        current_marks = self.storage.marks() if self.storage else {}
        matched_rules = 0
        manifests_loaded = 0
        for service in services:
            if apply_project_manifest(service):
                manifests_loaded += 1
            matched = apply_rules(service, rules)
            if matched:
                service.metadata["attribution_rule_ids"] = matched
                matched_rules += 1
            # A one-process mark is the most specific local decision and wins
            # over reusable project/rule defaults.
            if service.metadata.get("mark_present"):
                mark = current_marks.get(service.fingerprint, {})
                service.expected = bool(mark.get("expected"))
                service.protected = bool(mark.get("protected")) or service.protected
            service.metadata["stoppable_candidate"] = (
                service.source == "host"
                and service.runtime in STOPPABLE_RUNTIMES
                and not service.protected
            )
            if service.agent.provider and "agent_attributed" not in service.tags:
                service.tags.append("agent_attributed")
        collectors["attribution_rules"] = {
            "status": "ok",
            "message": f"已加载 {len(rules)} 条本地规则，命中 {matched_rules} 个服务；{manifests_loaded} 个项目清单生效",
            "rules": len(rules),
            "matched_services": matched_rules,
            "manifests": manifests_loaded,
        }

        histories = self.storage.histories(item.fingerprint for item in services) if self.storage else {}
        for service in services:
            history = histories.get(service.fingerprint)
            detected_restarts = int(service.metadata.get("restart_count") or 0)
            if history:
                service.first_seen = history.get("first_seen")
                service.last_seen = history.get("last_seen")
                service.metadata["restart_count"] = max(detected_restarts, int(history.get("restart_count") or 0))
            else:
                service.metadata["restart_count"] = detected_restarts
            service.metadata["uptime_seconds"] = (
                max(0, round(started - service.process.create_time, 1))
                if service.process.create_time
                else None
            )
        assess_all(services, self.config, histories=histories, now=started)
        if self.storage:
            self.storage.observe(services, now=started)

        services.sort(
            key=lambda item: (
                0 if item.risk.level == "likely_stale" else 1 if item.risk.level == "review" else 2,
                item.source,
                item.display_name.lower(),
            )
        )
        projects = {item.project.path for item in services if item.project.path}
        agents = {item.agent.provider for item in services if item.agent.provider}
        total_tcp = sum(1 for item in services for endpoint in item.endpoints if endpoint.protocol == "TCP")
        total_udp = sum(1 for item in services for endpoint in item.endpoints if endpoint.protocol == "UDP")
        review_count = sum(item.risk.level in {"review", "likely_stale"} for item in services)
        return {
            "schema_version": "1.1",
            "generated_at": started,
            "duration_ms": round((time.time() - started) * 1000, 1),
            "summary": {
                "services": len(services),
                "tcp_listeners": total_tcp,
                "udp_bindings": total_udp,
                "projects": len(projects),
                "agents": len(agents),
                "review_count": review_count,
                "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
                "memory_percent": round(psutil.virtual_memory().percent, 1),
            },
            "collectors": collectors,
            "platform": current_platform,
            "errors": errors,
            "services": [item.to_dict() for item in services],
        }
