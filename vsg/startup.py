from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "VibeServiceGuardian"


class StartupConfigurationError(ValueError):
    pass


def _portable_command() -> str | None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    return subprocess.list2cmdline([sys.executable, "--open"])


def windows_startup_status() -> dict[str, Any]:
    expected = _portable_command()
    result: dict[str, Any] = {
        "platform": "windows" if os.name == "nt" else "unsupported",
        "available": bool(expected),
        "enabled": False,
        "managed_by_current_executable": False,
        "scope": "current_user",
        "default_enabled": False,
        "requires_explicit_confirmation": True,
    }
    if os.name != "nt":
        result["reason"] = "windows_only"
        return result
    if not expected:
        result["reason"] = "portable_executable_required"
        return result
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            current, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return result
    except OSError as exc:
        result["reason"] = type(exc).__name__
        return result
    result["enabled"] = True
    result["managed_by_current_executable"] = str(current) == expected
    if not result["managed_by_current_executable"]:
        result["reason"] = "existing_entry_differs"
    return result


def configure_windows_startup(enabled: bool, confirmation: str) -> dict[str, Any]:
    required = "ENABLE STARTUP" if enabled else "DISABLE STARTUP"
    if confirmation.strip() != required:
        raise StartupConfigurationError(f"确认短语必须是 {required}")
    if os.name != "nt":
        raise StartupConfigurationError("开机启动配置仅支持 Windows")
    expected = _portable_command()
    if not expected:
        raise StartupConfigurationError("仅便携 EXE 支持配置当前用户开机启动")

    import winreg

    def assert_current_target(current: object | None) -> None:
        if current is not None and str(current) != expected:
            raise StartupConfigurationError(
                "检测到同名但目标不同的开机启动项；为避免覆盖，VSG 已拒绝修改"
            )

    try:
        access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
        if enabled:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                RUN_KEY,
                0,
                access,
            ) as key:
                try:
                    current, _ = winreg.QueryValueEx(key, VALUE_NAME)
                except FileNotFoundError:
                    current = None
                assert_current_target(current)
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, expected)
        else:
            try:
                key_context = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    RUN_KEY,
                    0,
                    access,
                )
            except FileNotFoundError:
                return windows_startup_status()
            with key_context as key:
                try:
                    current, _ = winreg.QueryValueEx(key, VALUE_NAME)
                except FileNotFoundError:
                    return windows_startup_status()
                assert_current_target(current)
                winreg.DeleteValue(key, VALUE_NAME)
    except OSError as exc:
        raise StartupConfigurationError(
            f"无法修改当前用户开机启动项：{type(exc).__name__}"
        ) from exc
    return windows_startup_status()
