from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import psutil


GIB = 1024**3

PCI_GPU_VENDORS = {
    "10de": "NVIDIA",
    "1002": "AMD",
    "1022": "AMD",
    "8086": "Intel",
    "106b": "Apple",
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], float], CommandResult]


def _run_command(args: Sequence[str], timeout: float = 5.0) -> CommandResult:
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(127, "", type(exc).__name__)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


GPU_PROFILES: list[tuple[re.Pattern[str], float, float, str]] = [
    (re.compile(r"RTX\s*5090", re.I), 32, 1792, "cuda"),
    (re.compile(r"RTX\s*5080", re.I), 16, 960, "cuda"),
    (re.compile(r"RTX\s*4090", re.I), 24, 1008, "cuda"),
    (re.compile(r"RTX\s*4080\s*SUPER", re.I), 16, 736, "cuda"),
    (re.compile(r"RTX\s*4080", re.I), 16, 717, "cuda"),
    (re.compile(r"RTX\s*4070\s*Ti\s*SUPER", re.I), 16, 672, "cuda"),
    (re.compile(r"RTX\s*3090", re.I), 24, 936, "cuda"),
    (re.compile(r"RTX\s*3080\s*Ti", re.I), 12, 912, "cuda"),
    (re.compile(r"RTX\s*3060(?!\s*Ti)", re.I), 12, 360, "cuda"),
    (re.compile(r"RTX\s*PRO\s*6000.*Blackwell", re.I), 96, 1792, "cuda"),
    (re.compile(r"RTX\s*PRO\s*5000.*Blackwell", re.I), 72, 1344, "cuda"),
    (re.compile(r"\bH100\b", re.I), 80, 3350, "cuda"),
    (re.compile(r"\bA100\b.*80", re.I), 80, 2039, "cuda"),
    (re.compile(r"\bA100\b", re.I), 40, 1555, "cuda"),
    (re.compile(r"RX\s*9070\s*XT", re.I), 16, 640, "vulkan"),
    (re.compile(r"RX\s*9070(?!\s*XT)", re.I), 16, 640, "vulkan"),
    (re.compile(r"RX\s*7900\s*XTX", re.I), 24, 960, "vulkan"),
    (re.compile(r"RX\s*7900\s*XT", re.I), 20, 800, "vulkan"),
    (re.compile(r"RX\s*7800\s*XT", re.I), 16, 624, "vulkan"),
    (re.compile(r"RX\s*7700\s*XT", re.I), 12, 432, "vulkan"),
    (re.compile(r"RX\s*7600\s*XT", re.I), 16, 288, "vulkan"),
]


APPLE_BANDWIDTH: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"M4\s*Ultra", re.I), 819),
    (re.compile(r"M4\s*Max", re.I), 546),
    (re.compile(r"M4\s*Pro", re.I), 273),
    (re.compile(r"M4", re.I), 120),
    (re.compile(r"M3\s*Ultra", re.I), 800),
    (re.compile(r"M3\s*Max", re.I), 400),
    (re.compile(r"M3\s*Pro", re.I), 150),
    (re.compile(r"M3", re.I), 100),
    (re.compile(r"M2\s*Ultra", re.I), 800),
    (re.compile(r"M2\s*Max", re.I), 400),
    (re.compile(r"M2\s*Pro", re.I), 200),
    (re.compile(r"M2", re.I), 100),
    (re.compile(r"M1\s*Ultra", re.I), 800),
    (re.compile(r"M1\s*Max", re.I), 400),
    (re.compile(r"M1\s*Pro", re.I), 200),
    (re.compile(r"M1", re.I), 68),
]


def _known_gpu_profile(name: str) -> tuple[float | None, float | None, str | None]:
    for pattern, memory_gib, bandwidth_gbps, backend in GPU_PROFILES:
        if pattern.search(name):
            return memory_gib, bandwidth_gbps, backend
    return None, None, None


def _apple_bandwidth(name: str) -> float | None:
    for pattern, bandwidth in APPLE_BANDWIDTH:
        if pattern.search(name):
            return bandwidth
    return None


def _pci_id(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("0x", "")
    match = re.search(r"(?:ven_|vendor[:=_ -]?)([0-9a-f]{4})", text, re.I)
    if match:
        return match.group(1).lower()
    return text[-4:] if re.fullmatch(r"[0-9a-f]{4,8}", text) else None


def _device_id(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("0x", "")
    match = re.search(r"(?:dev_|device[:=_ -]?)([0-9a-f]{4})", text, re.I)
    if match:
        return match.group(1).lower()
    return text[-4:] if re.fullmatch(r"[0-9a-f]{4,8}", text) else None


def _gpu_vendor(*evidence: Any) -> tuple[str, str | None]:
    joined = " ".join(str(value or "") for value in evidence)
    vendor_id = next((candidate for candidate in (_pci_id(value) for value in evidence) if candidate in PCI_GPU_VENDORS), None)
    if vendor_id:
        return PCI_GPU_VENDORS[vendor_id], vendor_id
    lower = joined.lower()
    if any(token in lower for token in ("nvidia", "geforce", "quadro", "tesla")):
        return "NVIDIA", "10de"
    if any(token in lower for token in ("advanced micro devices", "amd", "radeon", "firepro", "instinct")):
        return "AMD", "1002"
    if any(token in lower for token in ("intel", "iris", "arc graphics")):
        return "Intel", "8086"
    if "apple" in lower:
        return "Apple", "106b"
    return "Unknown", vendor_id


def _vram_gib(value: Any) -> float | None:
    text = str(value or "").strip().lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(gb|gib|mb|mib)", text)
    if not match:
        return None
    number = float(match.group(1))
    return round(number if match.group(2) in {"gb", "gib"} else number / 1024, 2)


def parse_nvidia_smi(output: str) -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    for row in csv.reader(line for line in output.splitlines() if line.strip()):
        if len(row) < 4:
            continue
        name = row[0].strip()
        try:
            total_mib = float(row[1].strip())
            free_mib = float(row[2].strip())
        except ValueError:
            continue
        known_memory, bandwidth, _ = _known_gpu_profile(name)
        item: dict[str, Any] = {
            "vendor": "NVIDIA",
            "vendor_id": "10de",
            "name": name,
            "memory_total_gib": round(total_mib / 1024, 2),
            "memory_free_gib": round(free_mib / 1024, 2),
            "memory_source": "nvidia-smi",
            "bandwidth_gbps": bandwidth,
            "backend": "cuda",
            "support_tier": "supported",
            "confidence": "high",
            "driver_version": row[3].strip() or None,
            "detection_source": "nvidia-smi",
            "notes": [],
        }
        if len(row) >= 5 and row[4].strip():
            item["compute_capability"] = row[4].strip()
        if known_memory and abs(known_memory - total_mib / 1024) > 2:
            item["notes"].append("显存以 nvidia-smi 实测值为准，未采用型号表推断值")
        gpus.append(item)
    return gpus


def parse_windows_video_json(raw: str) -> list[dict[str, Any]]:
    try:
        decoded = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    values = decoded if isinstance(decoded, list) else [decoded]
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        name = str(value.get("Name") or "").strip()
        if not name:
            continue
        lower = name.lower()
        if "virtual" in lower or "display adapter" in lower and "radeon" not in lower:
            continue
        pnp_id = str(value.get("PNPDeviceID") or "")
        vendor, vendor_id = _gpu_vendor(
            pnp_id,
            value.get("AdapterCompatibility"),
            value.get("VideoProcessor"),
            name,
        )
        device_id = _device_id(pnp_id)
        known_memory, bandwidth, backend = _known_gpu_profile(name)
        integrated = any(token in lower for token in ("integrated", "uhd", "iris", "apu", "vega")) and not re.search(r"(?:RX|Arc)\s*\w", name, re.I)
        notes: list[str] = []
        memory_source = "unknown"
        confidence = "low"
        if known_memory:
            memory_source = "model_profile"
            confidence = "medium"
            notes.append("Windows CIM 的 AdapterRAM 常被 32 位字段截断，显存采用内置型号表估算")
        elif value.get("AdapterRAM"):
            notes.append("Windows CIM AdapterRAM 不可靠，未将该数值用于容量决策")
        if vendor in {"AMD", "Intel"}:
            notes.append("Windows 非 NVIDIA GPU 推理支持标记为实验性，需用本地基准校准")
        result.append(
            {
                "vendor": vendor,
                "vendor_id": vendor_id,
                "device_id": device_id,
                "name": name,
                "memory_total_gib": None if integrated else known_memory,
                "memory_free_gib": None,
                "memory_source": memory_source,
                "bandwidth_gbps": bandwidth,
                "backend": backend or ("vulkan" if vendor in {"AMD", "Intel"} else "unknown"),
                "support_tier": "experimental" if vendor in {"AMD", "Intel"} else "preview",
                "confidence": confidence,
                "driver_version": str(value.get("DriverVersion") or "") or None,
                "adapter_compatibility": str(value.get("AdapterCompatibility") or "") or None,
                "video_processor": str(value.get("VideoProcessor") or "") or None,
                "detection_source": "Win32_VideoController",
                "integrated": integrated,
                "notes": notes,
            }
        )
    return result


def parse_system_profiler(raw: str, total_memory_gib: float) -> tuple[str | None, list[dict[str, Any]]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, []
    hardware = data.get("SPHardwareDataType", []) if isinstance(data, dict) else []
    chip_name = None
    if hardware and isinstance(hardware[0], dict):
        chip_name = hardware[0].get("chip_type") or hardware[0].get("cpu_type")
    displays = data.get("SPDisplaysDataType", []) if isinstance(data, dict) else []
    gpus: list[dict[str, Any]] = []
    for display in displays:
        if not isinstance(display, dict):
            continue
        name = str(display.get("sppci_model") or display.get("_name") or chip_name or "Apple GPU")
        vendor, vendor_id = _gpu_vendor(
            display.get("spdisplays_vendor-id"),
            display.get("spdisplays_vendor"),
            chip_name,
            name,
        )
        apple_silicon = vendor == "Apple"
        device_id = _device_id(display.get("spdisplays_device-id"))
        reported_vram = _vram_gib(
            display.get("spdisplays_vram")
            or display.get("_spdisplays_vram")
            or display.get("spdisplays_vram_shared")
        )
        gpus.append(
            {
                "vendor": vendor,
                "vendor_id": vendor_id,
                "device_id": device_id,
                "name": name,
                "memory_total_gib": round(total_memory_gib, 2) if apple_silicon else reported_vram,
                "memory_free_gib": None,
                "memory_source": "unified_memory" if apple_silicon else "system_profiler",
                "bandwidth_gbps": _apple_bandwidth(str(chip_name or name)) if apple_silicon else None,
                "backend": "metal",
                "support_tier": "supported" if apple_silicon else "experimental",
                "confidence": "high" if apple_silicon or reported_vram is not None else "low",
                "detection_source": "system_profiler",
                "integrated": apple_silicon,
                "unified_memory": apple_silicon,
                "notes": ["Apple Silicon CPU/GPU 共用统一内存，不能把系统内存和显存相加"] if apple_silicon else ["Intel Mac 独立/集成 GPU 首版仅展示，容量规划默认使用 CPU 路径"],
            }
        )
    return str(chip_name) if chip_name else None, gpus


def _windows_cpu_name() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except (ImportError, OSError):
        return None


def _cpu_bandwidth_estimate(cpu_name: str, memory_total_gib: float) -> tuple[float, str]:
    lower = cpu_name.lower()
    if "apple" in lower:
        value = _apple_bandwidth(cpu_name)
        return (value or 100), "apple_chip_profile" if value else "fallback"
    if any(token in lower for token in ("9950", "9900", "9800", "9700", "7950", "7900")):
        return 75, "desktop_ddr5_estimate"
    if any(token in lower for token in ("threadripper", "xeon", "epyc")):
        return (120 if memory_total_gib >= 128 else 90), "workstation_estimate"
    return (55 if memory_total_gib >= 32 else 35), "generic_estimate"


def _detect_nvidia(runner: Runner) -> list[dict[str, Any]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    query = "name,memory.total,memory.free,driver_version,compute_cap"
    result = runner([executable, f"--query-gpu={query}", "--format=csv,noheader,nounits"], 5)
    if result.returncode != 0:
        query = "name,memory.total,memory.free,driver_version"
        result = runner([executable, f"--query-gpu={query}", "--format=csv,noheader,nounits"], 5)
    return parse_nvidia_smi(result.stdout) if result.returncode == 0 else []


def _detect_windows_gpus(runner: Runner) -> list[dict[str, Any]]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return []
    script = "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,AdapterCompatibility,PNPDeviceID,VideoProcessor | ConvertTo-Json -Compress"
    result = runner([powershell, "-NoProfile", "-NonInteractive", "-Command", script], 8)
    return parse_windows_video_json(result.stdout) if result.returncode == 0 else []


def _detect_macos(runner: Runner, total_memory_gib: float) -> tuple[str | None, list[dict[str, Any]]]:
    profiler = shutil.which("system_profiler") or "/usr/sbin/system_profiler"
    result = runner([profiler, "SPHardwareDataType", "SPDisplaysDataType", "-json"], 15)
    if result.returncode != 0:
        return None, []
    return parse_system_profiler(result.stdout, total_memory_gib)


def _linux_pci_names(output: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(r"^(?P<bus>[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7])\s+[^:]+:\s+(?P<name>.+)$", line.strip(), re.I)
        if not match:
            continue
        name = re.sub(r"\s*\[[0-9a-f]{4}:[0-9a-f]{4}\](?:\s*\(rev [^)]+\))?\s*$", "", match.group("name"), flags=re.I)
        names[match.group("bus").lower()] = name.strip()
    return names


def parse_linux_pci_devices(sysfs_root: Path, lspci_output: str = "") -> list[dict[str, Any]]:
    """Read display controllers from Linux PCI sysfs without a model allowlist."""

    names = _linux_pci_names(lspci_output)
    result: list[dict[str, Any]] = []
    if not sysfs_root.is_dir():
        return result
    for device_path in sorted(sysfs_root.iterdir(), key=lambda item: item.name):
        try:
            class_id = (device_path / "class").read_text(encoding="ascii").strip().lower().replace("0x", "")
            vendor_id = (device_path / "vendor").read_text(encoding="ascii").strip().lower().replace("0x", "")[-4:]
            device_id = (device_path / "device").read_text(encoding="ascii").strip().lower().replace("0x", "")[-4:]
        except (OSError, UnicodeError):
            continue
        if not class_id.startswith("03"):
            continue
        vendor = PCI_GPU_VENDORS.get(vendor_id, "Unknown")
        bus_id = device_path.name.lower()
        name = names.get(bus_id) or f"{vendor} display controller [{vendor_id.upper()}:{device_id.upper()}]"
        known_memory, bandwidth, backend = _known_gpu_profile(name)
        integrated = vendor in {"Intel", "AMD"} and any(token in name.lower() for token in ("integrated", "uhd", "iris", "apu", "vega"))
        result.append(
            {
                "vendor": vendor,
                "vendor_id": vendor_id,
                "device_id": device_id,
                "pci_bus_id": bus_id,
                "pci_class": class_id,
                "name": name,
                "memory_total_gib": None if integrated else known_memory,
                "memory_free_gib": None,
                "memory_source": "model_profile" if known_memory else "unknown",
                "bandwidth_gbps": bandwidth,
                "backend": backend or ("cuda" if vendor == "NVIDIA" else "vulkan" if vendor in {"AMD", "Intel"} else "unknown"),
                "support_tier": "preview" if vendor == "NVIDIA" else "experimental",
                "confidence": "high" if vendor != "Unknown" else "medium",
                "detection_source": "linux_pci_sysfs",
                "integrated": integrated,
                "notes": ["PCI sysfs confirms the adapter identity; usable VRAM requires a vendor telemetry interface"] if not known_memory else ["VRAM is a model-profile estimate until confirmed by a vendor telemetry interface"],
            }
        )
    return result


def _detect_linux_gpus(runner: Runner) -> list[dict[str, Any]]:
    lspci = shutil.which("lspci")
    lspci_output = ""
    if lspci:
        result = runner([lspci, "-D", "-nn"], 5)
        if result.returncode == 0:
            lspci_output = result.stdout
    return parse_linux_pci_devices(Path("/sys/bus/pci/devices"), lspci_output)


def _version_line(executable: str, args: Sequence[str], runner: Runner) -> str | None:
    result = runner([executable, *args], 4)
    text = (result.stdout or result.stderr).strip().splitlines()
    if result.returncode != 0 or not text:
        return None
    return re.sub(r"\s+", " ", text[0])[:180]


def _known_runtime_path(runtime_id: str, system_key: str) -> str | None:
    candidates: list[Path] = []
    if runtime_id == "ollama":
        if system_key == "windows":
            local = os.environ.get("LOCALAPPDATA")
            if local:
                candidates.extend(
                    [
                        Path(local) / "Programs" / "Ollama" / "ollama.exe",
                        Path(local) / "Ollama" / "ollama.exe",
                    ]
                )
        elif system_key == "darwin":
            candidates.append(Path("/Applications/Ollama.app/Contents/Resources/ollama"))
    elif runtime_id == "lm-studio":
        candidates.append(Path.home() / ".lmstudio" / "bin" / ("lms.exe" if system_key == "windows" else "lms"))
    return str(next((item for item in candidates if item.is_file()), "")) or None


def _known_runtime_application(runtime_id: str, system_key: str) -> bool:
    if runtime_id != "lm-studio":
        return False
    if system_key == "windows":
        local = os.environ.get("LOCALAPPDATA")
        return bool(local and (Path(local) / "Programs" / "LM Studio" / "LM Studio.exe").is_file())
    if system_key == "darwin":
        return Path("/Applications/LM Studio.app").is_dir()
    return False


def detect_runtimes(system: str | None = None, runner: Runner = _run_command) -> list[dict[str, Any]]:
    system_key = (system or platform.system()).lower()
    definitions = [
        ("ollama", "Ollama", ["ollama"], ["--version"], "supported"),
        ("llama.cpp", "llama.cpp server", ["llama-server", "llama-server.exe"], ["--version"], "supported"),
        ("llama-bench", "llama.cpp benchmark", ["llama-bench", "llama-bench.exe"], ["--version"], "supported"),
        ("lm-studio", "LM Studio CLI", ["lms", "lms.exe"], ["--version"], "detected_only"),
        ("mlx", "MLX-LM", ["mlx_lm"], ["--help"], "supported" if system_key == "darwin" else "unavailable_platform"),
        ("vllm", "vLLM", ["vllm"], ["--version"], "preview"),
        ("sglang", "SGLang", ["sglang"], ["--version"], "preview"),
    ]
    runtimes: list[dict[str, Any]] = []
    for runtime_id, label, candidates, version_args, support in definitions:
        executable = next((shutil.which(item) for item in candidates if shutil.which(item)), None)
        executable = executable or _known_runtime_path(runtime_id, system_key)
        application_detected = _known_runtime_application(runtime_id, system_key)
        module_detected = False
        if not executable and runtime_id in {"mlx", "vllm", "sglang"}:
            module_name = {"mlx": "mlx_lm", "vllm": "vllm", "sglang": "sglang"}[runtime_id]
            try:
                module_detected = importlib.util.find_spec(module_name) is not None
            except (ImportError, ValueError):
                module_detected = False
        installed = bool(executable or module_detected or application_detected)
        runtimes.append(
            {
                "id": runtime_id,
                "label": label,
                "installed": installed,
                "version": _version_line(executable, version_args, runner) if executable else None,
                "detection": "executable" if executable else "python_module" if module_detected else "application" if application_detected else "not_found",
                "support_tier": support,
            }
        )

    if system_key == "windows":
        wsl = shutil.which("wsl.exe")
        runtimes.append(
            {
                "id": "wsl-runtime",
                "label": "WSL 推理运行时桥接",
                "installed": bool(wsl),
                "version": None,
                "detection": "wsl_executable" if wsl else "not_found",
                "support_tier": "preview",
                "notes": "首版只确认 WSL 桥接可用，不读取发行版内部包或用户配置。",
            }
        )
    return runtimes


def _runtime_capabilities(gpus: list[dict[str, Any]], system_key: str) -> list[dict[str, str]]:
    capabilities: list[dict[str, str]] = [{"backend": "cpu", "support_tier": "supported"}]
    backends = {str(item.get("backend")) for item in gpus}
    if "cuda" in backends:
        capabilities.append({"backend": "cuda", "support_tier": "supported"})
    if "metal" in backends or system_key == "darwin":
        capabilities.append({"backend": "metal", "support_tier": "supported"})
    if "vulkan" in backends:
        capabilities.append({"backend": "vulkan", "support_tier": "experimental"})
    return capabilities


def collect_hardware(
    system: str | None = None,
    machine: str | None = None,
    runner: Runner = _run_command,
) -> dict[str, Any]:
    system_name = system or platform.system()
    system_key = system_name.lower()
    architecture = machine or platform.machine() or "unknown"
    memory = psutil.virtual_memory()
    total_memory_gib = memory.total / GIB
    available_memory_gib = memory.available / GIB
    cpu_name = (_windows_cpu_name() if system_key == "windows" else None) or platform.processor().strip() or "未知 CPU"
    gpus: list[dict[str, Any]] = []
    warnings: list[str] = []
    apple_chip = None

    if system_key == "windows":
        gpus = _detect_nvidia(runner)
        known_names = {item["name"].lower() for item in gpus}
        for item in _detect_windows_gpus(runner):
            if item["name"].lower() not in known_names:
                gpus.append(item)
    elif system_key == "darwin":
        apple_chip, gpus = _detect_macos(runner, total_memory_gib)
        if apple_chip:
            cpu_name = apple_chip
    elif system_key == "linux":
        nvidia_gpus = _detect_nvidia(runner)
        pci_gpus = _detect_linux_gpus(runner)
        gpus = [*nvidia_gpus]
        nvidia_measured = bool(nvidia_gpus)
        for item in pci_gpus:
            if nvidia_measured and item.get("vendor") == "NVIDIA":
                continue
            gpus.append(item)
        if any(item.get("vendor") == "AMD" for item in gpus) and not shutil.which("amd-smi"):
            warnings.append("AMD 适配器已由 PCI sysfs 识别；未检测到 AMD SMI，VRAM、温度、风扇和功耗可能不可用")
    else:
        warnings.append("当前操作系统未实现 GPU 枚举适配器")

    if not gpus:
        warnings.append("未获得可用于容量计算的 GPU；将使用 CPU/系统内存路径")
    if any(item.get("support_tier") == "experimental" for item in gpus):
        warnings.append("检测到实验性 GPU 路径；性能结论必须用本机基准校准")

    cpu_bandwidth, cpu_bandwidth_source = _cpu_bandwidth_estimate(cpu_name, total_memory_gib)
    disk_path = Path(os.environ.get("SystemDrive", "C:") + "\\") if system_key == "windows" else Path("/")
    try:
        disk = psutil.disk_usage(str(disk_path))
        disk_info = {
            "total_gib": round(disk.total / GIB, 2),
            "free_gib": round(disk.free / GIB, 2),
            "scope": "system_volume",
        }
    except OSError:
        disk_info = {"total_gib": None, "free_gib": None, "scope": "unavailable"}

    profile: dict[str, Any] = {
        "schema_version": "1.0",
        "captured_at": time.time(),
        "platform": {
            "key": "windows" if system_key == "windows" else "macos" if system_key == "darwin" else system_key,
            "system": system_name,
            "release": platform.release(),
            "architecture": architecture,
        },
        "cpu": {
            "name": cpu_name,
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "max_frequency_mhz": round(psutil.cpu_freq().max, 0) if psutil.cpu_freq() else None,
            "memory_bandwidth_gbps_estimate": cpu_bandwidth,
            "bandwidth_source": cpu_bandwidth_source,
        },
        "memory": {
            "total_gib": round(total_memory_gib, 2),
            "available_gib": round(available_memory_gib, 2),
            "used_percent": round(float(memory.percent), 1),
            "unified": bool(apple_chip and "Apple" in apple_chip),
        },
        "gpus": gpus,
        "disk": disk_info,
        "backend_capabilities": _runtime_capabilities(gpus, system_key),
        "warnings": warnings,
        "privacy": "不采集序列号、MAC 地址、主机名、用户名、模型文件或环境变量",
    }
    fingerprint_payload = {
        "platform": profile["platform"],
        "cpu": {"name": cpu_name, "cores": profile["cpu"]["logical_cores"]},
        "memory_total_gib": profile["memory"]["total_gib"],
        "gpus": [{"name": item.get("name"), "memory": item.get("memory_total_gib")} for item in gpus],
    }
    profile["hardware_fingerprint"] = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    return profile
