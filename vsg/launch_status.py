from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .notifications import send_system_notification
from .privacy import atomic_write_private_text


def record_version_conflict(
    data_dir: Path,
    *,
    requested_version: str,
    running_version: str,
    running_pid: int | None,
    running_port: int,
) -> Path:
    path = data_dir / "last-launch.json"
    atomic_write_private_text(
        path,
        json.dumps(
            {
                "status": "existing_instance_version_mismatch",
                "observed_at": time.time(),
                "requested_version": requested_version,
                "running_version": running_version,
                "running_pid": running_pid,
                "running_port": running_port,
                "action_taken": "opened_existing_instance",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return path


def notify_version_conflict(requested_version: str, running_version: str) -> dict[str, Any]:
    title = "Vibe Service Guardian 版本提示"
    message = (
        f"当前仍在运行 VSG {running_version}，刚启动的是 {requested_version}。"
        "已打开旧实例；请先在控制台安全退出旧实例，再启动新版本。"
    )
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
            return {"sent": True, "platform": "windows", "channel": "message_box"}
        except (AttributeError, OSError):
            pass
    if sys.platform == "darwin" or os.name != "nt":
        return send_system_notification(title, message)
    return {"sent": False, "platform": "windows", "reason": "notifier_unavailable"}
