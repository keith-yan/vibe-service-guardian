from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


MAX_TITLE_CHARS = 120
MAX_MESSAGE_CHARS = 500


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def _windows_command(title: str, message: str) -> list[str] | None:
    executable = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if not executable.is_file():
        return None

    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    # The generated script contains only length-bounded, quote-escaped text.
    # No shell is involved, and the process exits after showing one balloon.
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Warning;"
        "$n.Visible=$true;"
        f"$n.BalloonTipTitle={quote(title)};"
        f"$n.BalloonTipText={quote(message)};"
        "$n.ShowBalloonTip(5000);Start-Sleep -Seconds 6;$n.Dispose()"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [
        str(executable),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
        encoded,
    ]


def _macos_command(title: str, message: str) -> list[str] | None:
    executable = Path("/usr/bin/osascript")
    if not executable.is_file():
        return None

    def quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    return [
        str(executable),
        "-e",
        f'display notification "{quote(message)}" with title "{quote(title)}"',
    ]


def _linux_command(title: str, message: str) -> list[str] | None:
    executable = next(
        (path for path in (Path("/usr/bin/notify-send"), Path("/bin/notify-send")) if path.is_file()),
        None,
    )
    if not executable:
        return None
    return [str(executable), "--app-name=Vibe Service Guardian", title, message]


def send_system_notification(title: str, message: str) -> dict[str, Any]:
    """Best-effort, opt-in local notification with no network or shell use."""

    safe_title = _clean(title, MAX_TITLE_CHARS)
    safe_message = _clean(message, MAX_MESSAGE_CHARS)
    if not safe_title or not safe_message:
        return {"sent": False, "reason": "empty_message"}
    if os.name == "nt":
        command = _windows_command(safe_title, safe_message)
        platform = "windows"
    elif sys.platform == "darwin":
        command = _macos_command(safe_title, safe_message)
        platform = "macos"
    else:
        command = _linux_command(safe_title, safe_message)
        platform = "linux"
    if not command:
        return {"sent": False, "reason": "notifier_unavailable", "platform": platform}
    creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    try:
        # The executable is fixed, shell use is disabled, and text is bounded.
        subprocess.Popen(  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=os.name != "nt",
            creationflags=creation_flags,
        )
    except (OSError, ValueError) as exc:
        return {
            "sent": False,
            "reason": type(exc).__name__,
            "platform": platform,
        }
    return {"sent": True, "platform": platform}
