from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any


WINDOWS_PROTECTED_NAMES = {
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "winlogon.exe",
    "dwm.exe",
    "explorer.exe",
}

MACOS_PROTECTED_NAMES = {
    "launchd",
    "kernel_task",
    "windowserver",
    "loginwindow",
    "finder",
    "dock",
    "systemuiserver",
}

LINUX_PROTECTED_NAMES = {
    "systemd",
    "init",
    "kthreadd",
    "dbus-daemon",
    "networkmanager",
    "systemd-logind",
    "systemd-resolved",
    "gnome-shell",
    "kwin_wayland",
    "xorg",
    "xwayland",
}

AGENT_PROTECTED_NAMES = {
    "chatgpt.exe",
    "codex.exe",
    "codex",
    "claude.exe",
    "claude",
    "cursor.exe",
    "cursor",
    "windsurf.exe",
    "windsurf",
    "code.exe",
    "code",
    "workbuddy.exe",
    "workbuddy",
    "codebuddy.exe",
    "codebuddy",
    "hermes.exe",
    "hermes",
    "opencode.exe",
    "opencode",
    "aider.exe",
    "aider",
    "gemini.exe",
    "gemini",
    "goose.exe",
    "goose",
    "goosed.exe",
    "goosed",
    "vibeserviceguardian.exe",
    "vibeserviceguardian",
}

DEFAULT_PROTECTED_NAMES = sorted(
    WINDOWS_PROTECTED_NAMES | MACOS_PROTECTED_NAMES | LINUX_PROTECTED_NAMES | AGENT_PROTECTED_NAMES
)


def platform_key(system_name: str | None = None) -> str:
    system = (system_name or platform.system()).strip().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return "unknown"


def normalized_architecture(machine: str | None = None) -> str:
    value = (machine or platform.machine() or "unknown").strip().lower()
    if value in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    return value or "unknown"


def platform_info(
    system_name: str | None = None,
    machine: str | None = None,
) -> dict[str, Any]:
    key = platform_key(system_name)
    labels = {
        "windows": "Windows",
        "macos": "macOS",
        "linux": "Linux",
        "unknown": "未知平台",
    }
    return {
        "key": key,
        "label": labels[key],
        "architecture": normalized_architecture(machine),
        "supported": key in {"windows", "macos", "linux"},
        "capabilities": {
            "windows_services": key == "windows",
            "wsl": key == "windows",
            "macos_lsof": key == "macos",
            "linux_procfs": key == "linux",
            "desktop_launcher": key in {"windows", "macos", "linux"},
            "docker": key in {"windows", "macos", "linux"},
            "open_project_path": key in {"windows", "macos", "linux"},
            "native_tray": key == "windows",
            "current_user_startup": key == "windows",
            "global_hotkey": key == "windows",
        },
    }


def default_project_roots(
    system_name: str | None = None,
    home: Path | None = None,
    cwd: Path | None = None,
) -> list[str]:
    key = platform_key(system_name)
    current_home = (home or Path.home()).expanduser()
    # Retain cwd as an API parameter for callers that provide a deterministic
    # test environment; project-root defaults intentionally do not trust the
    # launch directory as a scan root.
    _ = cwd

    if key == "windows":
        candidates = [
            Path(r"E:\vibe coding"),
            current_home / "source" / "repos",
            current_home / "Documents" / "GitHub",
            current_home / "Projects",
            current_home / "Developer",
        ]
        existing = [str(path.resolve(strict=False)) for path in candidates if path.is_dir()]
        return existing or [str((current_home / "Projects").resolve(strict=False))]

    if key == "macos":
        candidates = [current_home / "Developer", current_home / "Projects"]
        existing = [str(path.resolve(strict=False)) for path in candidates if path.is_dir()]
        return existing or [str((current_home / "Projects").resolve(strict=False))]

    candidates = [
        current_home / "Projects",
        current_home / "Developer",
        current_home / "src",
        current_home / "workspace",
    ]
    existing = [str(path.resolve(strict=False)) for path in candidates if path.is_dir()]
    return existing or [str((current_home / "Projects").resolve(strict=False))]


def default_windows_features(system_name: str | None = None) -> bool:
    return platform_key(system_name) == "windows"


def executable_search_path(name: str) -> str | None:
    """Return a deterministic platform path when a system binary is not on PATH."""
    if platform_key() == "macos" and name == "lsof":
        candidate = Path("/usr/sbin/lsof")
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
