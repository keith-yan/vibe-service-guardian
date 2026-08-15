from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .models import Endpoint, ProcessSnapshot, ProjectAttribution, ServiceRecord


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _run(command: list[str], timeout: float = 3.0) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", type(exc).__name__
    stdout = _decode(completed.stdout)
    stderr = _decode(completed.stderr)
    return completed.returncode, stdout, stderr


def _decode(value: bytes) -> str:
    if not value:
        return ""
    if value.count(b"\x00") > len(value) // 8:
        return value.decode("utf-16le", errors="replace").lstrip("\ufeff")
    for encoding in ("utf-8-sig", "utf-8", "mbcs"):
        try:
            return value.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return value.decode("utf-8", errors="replace")


def _exposure(address: str) -> str:
    lowered = address.lower().strip("[]")
    if lowered in {"127.0.0.1", "::1", "localhost"}:
        return "loopback"
    # These literals classify observed bindings; this function never binds a socket.
    if lowered in {"0.0.0.0", "::", "*", ""}:  # nosec B104
        return "all_interfaces"
    return "lan"


DOCKER_PORT_RE = re.compile(
    r"(?P<address>\d{1,3}(?:\.\d{1,3}){3}|\[::\]):(?P<host>\d+)->(?P<container>\d+)/(?:tcp|udp)",
    re.IGNORECASE,
)
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"
COMPOSE_CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"
DOCKER_INSPECT_FIELDS = (
    "{{json .Id}}",
    "{{json .State.Pid}}",
    "{{json .State.Restarting}}",
    "{{json .RestartCount}}",
    "{{json .HostConfig.RestartPolicy.Name}}",
    f'{{{{json (index .Config.Labels "{COMPOSE_PROJECT_LABEL}")}}}}',
    f'{{{{json (index .Config.Labels "{COMPOSE_SERVICE_LABEL}")}}}}',
    f'{{{{json (index .Config.Labels "{COMPOSE_WORKING_DIR_LABEL}")}}}}',
    f'{{{{json (index .Config.Labels "{COMPOSE_CONFIG_FILES_LABEL}")}}}}',
)
DOCKER_INSPECT_FORMAT = "\t".join(DOCKER_INSPECT_FIELDS)


def _safe_label(labels: dict[str, Any], key: str, limit: int) -> str | None:
    value = labels.get(key)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit or any(ord(character) < 32 for character in cleaned):
        return None
    return cleaned


def _safe_compose_name(labels: dict[str, Any], key: str) -> str | None:
    value = _safe_label(labels, key, 128)
    if value and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        return value
    return None


def _safe_absolute_path(labels: dict[str, Any], key: str) -> str | None:
    value = _safe_label(labels, key, 1024)
    if not value:
        return None
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    selected = posix if posix.is_absolute() else windows if windows.is_absolute() else None
    if selected is None or ".." in selected.parts:
        return None
    return value


def _compose_config_names(labels: dict[str, Any]) -> list[str]:
    value = _safe_label(labels, COMPOSE_CONFIG_FILES_LABEL, 2048)
    if not value:
        return []
    result: list[str] = []
    for item in value.split(",")[:8]:
        normalized = item.strip().replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        if name and len(name) <= 180 and name not in {".", ".."}:
            result.append(name)
    return list(dict.fromkeys(result))


def _model_runtime(value: str) -> str | None:
    lowered = value.lower()
    checks = (
        (("ollama",), "Ollama"),
        (("llama-server", "llama.cpp", "llama-cpp", "llamacpp"), "llama.cpp"),
        (("vllm",), "vLLM"),
        (("sglang",), "SGLang"),
        (("text-generation-inference", "text-generation-launcher", "tgi"), "Hugging Face TGI"),
        (("comfyui",), "ComfyUI"),
        (("koboldcpp",), "KoboldCpp"),
        (("ktransformers",), "KTransformers"),
        (("tensorrt-llm", "tensorrt_llm", "trtllm"), "TensorRT-LLM"),
        (("text-generation-webui",), "Text Generation WebUI"),
        (("tabbyapi", "exllamav2"), "TabbyAPI"),
    )
    for tokens, runtime in checks:
        if any(token in lowered for token in tokens):
            return runtime
    return None


def _parse_docker_inspect_allowlist(output: str) -> dict[str, dict[str, Any]]:
    """Parse only fields explicitly requested from Docker's format template."""

    result: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != len(DOCKER_INSPECT_FIELDS):
            continue
        values: list[Any] = []
        try:
            for field in fields:
                values.append(json.loads(field))
        except json.JSONDecodeError:
            continue
        container_id = str(values[0] or "")
        if not container_id:
            continue
        labels = {
            key: value
            for key, value in zip(
                (
                    COMPOSE_PROJECT_LABEL,
                    COMPOSE_SERVICE_LABEL,
                    COMPOSE_WORKING_DIR_LABEL,
                    COMPOSE_CONFIG_FILES_LABEL,
                ),
                values[5:9],
                strict=True,
            )
            if isinstance(value, str)
        }
        result[container_id] = {
            "Id": container_id,
            "State": {"Pid": values[1], "Restarting": values[2]},
            "RestartCount": values[3],
            "HostConfig": {"RestartPolicy": {"Name": values[4]}},
            "Config": {"Labels": labels},
        }
    return result


def scan_docker() -> tuple[list[ServiceRecord], dict[str, Any]]:
    executable = shutil.which("docker")
    if not executable:
        return [], {"status": "unavailable", "message": "未检测到 docker 命令"}
    code, stdout, stderr = _run([executable, "ps", "--no-trunc", "--format", "{{json .}}"], timeout=4)
    if code != 0:
        message = "Docker daemon 当前不可用"
        if "permission" in stderr.lower():
            message = "Docker daemon 权限不足"
        return [], {"status": "error", "message": message}

    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    inspect_by_id: dict[str, dict[str, Any]] = {}
    container_ids = [str(item.get("ID")) for item in rows if item.get("ID")]
    if container_ids:
        inspect_code, inspect_stdout, _ = _run(
            [
                executable,
                "inspect",
                "--format",
                DOCKER_INSPECT_FORMAT,
                *container_ids[:64],
            ],
            timeout=8,
        )
        if inspect_code == 0:
            inspect_by_id = _parse_docker_inspect_allowlist(inspect_stdout)

    services: list[ServiceRecord] = []
    for item in rows:
        container_id = str(item.get("ID") or "unknown")
        inspect_item = inspect_by_id.get(container_id, {})
        host_config = inspect_item.get("HostConfig") if isinstance(inspect_item.get("HostConfig"), dict) else {}
        state = inspect_item.get("State") if isinstance(inspect_item.get("State"), dict) else {}
        config = inspect_item.get("Config") if isinstance(inspect_item.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        restart_policy_value = host_config.get("RestartPolicy") if isinstance(host_config.get("RestartPolicy"), dict) else {}
        restart_policy = str(restart_policy_value.get("Name") or "no")
        compose_project = _safe_compose_name(labels, COMPOSE_PROJECT_LABEL)
        compose_service = _safe_compose_name(labels, COMPOSE_SERVICE_LABEL)
        compose_working_dir = _safe_absolute_path(labels, COMPOSE_WORKING_DIR_LABEL)
        compose_config_names = _compose_config_names(labels)
        project = ProjectAttribution()
        if compose_project or compose_working_dir:
            inferred_name = compose_project
            if not inferred_name and compose_working_dir:
                posix_name = PurePosixPath(compose_working_dir).name
                windows_name = PureWindowsPath(compose_working_dir).name
                inferred_name = windows_name if len(windows_name) < len(posix_name) else posix_name
            evidence = []
            if compose_project:
                evidence.append("Docker Compose project label")
            if compose_working_dir:
                evidence.append("Docker Compose working-dir label")
            project = ProjectAttribution(
                name=inferred_name,
                path=compose_working_dir,
                confidence=90 if compose_project and compose_working_dir else 75,
                evidence=evidence,
            )
        runtime = _model_runtime(f"{item.get('Image') or ''} {item.get('Names') or ''}") or "Docker"
        endpoints: list[Endpoint] = []
        for match in DOCKER_PORT_RE.finditer(str(item.get("Ports") or "")):
            protocol = match.group(0).rsplit("/", 1)[-1].upper()
            address = match.group("address").strip("[]")
            endpoints.append(
                Endpoint(
                    protocol=protocol,
                    address=address,
                    port=int(match.group("host")),
                    state="PUBLISHED",
                    exposure=_exposure(address),
                )
            )
        fingerprint = hashlib.sha256(f"docker|{container_id}".encode()).hexdigest()[:20]
        process = ProcessSnapshot(
            pid=0,
            name=str(item.get("Names") or container_id[:12]),
            cmdline=[str(item.get("Image") or "")],
            status="restarting" if state.get("Restarting") else str(item.get("Status") or "running"),
            accessible=True,
        )
        services.append(
            ServiceRecord(
                id=f"docker:{container_id}",
                fingerprint=fingerprint,
                source="docker",
                display_name=str(item.get("Names") or container_id[:12]),
                runtime=runtime,
                process=process,
                endpoints=endpoints,
                project=project,
                protected=True,
                tags=["container", *(("compose",) if compose_project else ())],
                metadata={
                    "container_id": container_id,
                    "image": item.get("Image"),
                    "state": item.get("State"),
                    "ports_raw": item.get("Ports"),
                    "restart_policy": restart_policy,
                    "auto_restart": restart_policy not in {"", "no", "none"},
                    "restart_count": int(inspect_item.get("RestartCount") or 0),
                    "model_runtime": runtime != "Docker",
                    "openable_candidate": runtime != "Docker",
                    "lifecycle_manager": "Docker Compose" if compose_project else "Docker Engine",
                    "compose_project": compose_project,
                    "compose_service": compose_service,
                    "compose_working_dir": compose_working_dir,
                    "compose_config_files": compose_config_names,
                    "attribution_contract": {
                        "version": "1.0",
                        "ownership_namespace": "docker",
                        "process_identity": {
                            "model": "container_id",
                            "container_id": container_id,
                            "engine_reported_pid": int(state.get("Pid") or 0) or None,
                            "host_pid": None,
                            "host_process_mapping": "not_assumed",
                        },
                        "port_identity": {
                            "model": "published_host_port",
                            "mapping_observed": bool(endpoints),
                        },
                        "project_identity": {
                            "source": "compose_labels" if compose_project or compose_working_dir else "unknown",
                            "confidence": project.confidence,
                        },
                        "lifecycle": {
                            "manager": "Docker Compose" if compose_project else "Docker Engine",
                            "restart_policy": restart_policy,
                        },
                    },
                },
            )
        )
    return services, {
        "status": "ok",
        "message": f"检测到 {len(services)} 个运行中容器（仅采集固定白名单元数据）",
        "metadata_policy": "fixed_allowlist_no_environment",
    }


PID_RE = re.compile(r"pid=(\d+)")
NETSTAT_PID_RE = re.compile(r"\b(\d+)/[^\s]+")


def _parse_socket_address(value: str) -> tuple[str, int] | None:
    token = value.strip()
    if token.startswith("[") and "]:" in token:
        address, port_value = token[1:].rsplit("]:", 1)
    elif ":" in token:
        address, port_value = token.rsplit(":", 1)
    else:
        return None
    try:
        port = int(port_value)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return address or "*", port


def _parse_wsl_line(distro: str, line: str) -> ServiceRecord | None:
    fields = line.split()
    if len(fields) < 5:
        return None
    protocol = fields[0].upper()
    if not (protocol.startswith("TCP") or protocol.startswith("UDP")):
        return None
    parsed_local: tuple[str, int] | None = None
    for field in fields[1:]:
        parsed_local = _parse_socket_address(field)
        if parsed_local:
            break
    if not parsed_local:
        return None
    address, port = parsed_local
    pid_match = PID_RE.search(line)
    if not pid_match:
        pid_match = NETSTAT_PID_RE.search(line)
    pid = int(pid_match.group(1)) if pid_match else 0
    name_match = re.search(r'\(\("([^\"]+)"', line)
    if not name_match:
        name_match = re.search(r"\b\d+/([^\s]+)", line)
    name = name_match.group(1) if name_match else "WSL service"
    key = f"wsl|{distro}|{protocol}|{address}|{port}|{pid}|{name}"
    fingerprint = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:20]
    runtime = _model_runtime(name) or "WSL"
    return ServiceRecord(
        id=f"wsl:{distro}:{protocol}:{port}:{pid}",
        fingerprint=fingerprint,
        source="wsl",
        display_name=f"{name} · {distro}",
        runtime=runtime,
        process=ProcessSnapshot(pid=pid, name=name, status="running"),
        endpoints=[
            Endpoint(
                protocol="TCP" if protocol.startswith("TCP") else "UDP",
                address=address,
                port=port,
                state="LISTEN" if protocol.startswith("TCP") else "BOUND",
                exposure=_exposure(address),
            )
        ],
        protected=True,
        tags=["wsl", distro],
        metadata={
            "distribution": distro,
            "linux_pid": pid,
            "raw_visibility": "ss/netstat",
            "model_runtime": runtime != "WSL",
            "openable_candidate": runtime != "WSL",
            "auto_restart": None,
            "lifecycle_manager": "WSL distribution",
            "attribution_contract": {
                "version": "1.0",
                "ownership_namespace": "wsl",
                "process_identity": {
                    "model": "wsl_linux_pid",
                    "distribution": distro,
                    "linux_pid": pid or None,
                    "windows_host_pid": None,
                },
                "port_identity": {
                    "model": "guest_listener",
                    "guest_address": address,
                    "guest_port": port,
                    "windows_forwarding": "not_verified",
                },
                "project_identity": {
                    "source": "unavailable_from_socket_table",
                    "confidence": 0,
                },
                "lifecycle": {
                    "manager": "WSL distribution",
                    "restart_policy": "unknown",
                },
            },
        },
    )


def scan_wsl() -> tuple[list[ServiceRecord], dict[str, Any]]:
    executable = shutil.which("wsl") or shutil.which("wsl.exe")
    if not executable:
        return [], {"status": "unavailable", "message": "未检测到 WSL"}
    code, stdout, _ = _run([executable, "--list", "--running", "--quiet"], timeout=3)
    if code != 0:
        return [], {"status": "error", "message": "无法读取 WSL 运行状态"}
    distributions = [line.strip().replace("\x00", "") for line in stdout.splitlines() if line.strip().replace("\x00", "")]
    if not distributions:
        return [], {"status": "ok", "message": "当前没有运行中的 WSL 发行版"}

    services: list[ServiceRecord] = []
    for distro in distributions:
        command = "if command -v ss >/dev/null 2>&1; then ss -H -lntup; elif command -v netstat >/dev/null 2>&1; then netstat -lntup; fi"
        code, output, _ = _run([executable, "-d", distro, "--", "sh", "-lc", command], timeout=4)
        if code != 0:
            continue
        for line in output.splitlines():
            service = _parse_wsl_line(distro, line)
            if service:
                services.append(service)
    return services, {
        "status": "ok",
        "message": f"{len(distributions)} 个运行中发行版，检测到 {len(services)} 个绑定端口",
    }
