from __future__ import annotations

import ctypes
import logging
import os
import threading
from collections.abc import Callable
from typing import Any


LOGGER = logging.getLogger("vsg")
UnreadProvider = Callable[[], int]
Action = Callable[[], None]


class _WindowsTrayBackend:
    WM_APP_TRAY = 0x8000 + 41
    WM_CLOSE = 0x0010

    def __init__(
        self,
        open_dashboard: Action,
        open_focus: Action,
        exit_application: Action,
        unread_provider: UnreadProvider,
        hotkey: str,
    ):
        self.open_dashboard = open_dashboard
        self.open_focus = open_focus
        self.exit_application = exit_application
        self.unread_provider = unread_provider
        self.hotkey = hotkey
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._hwnd: int | None = None
        self._error: str | None = None
        self._wndproc: Any = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="vsg-windows-tray",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=3)
        return bool(self._hwnd and self._thread.is_alive())

    def stop(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            try:
                from ctypes import wintypes

                post_message = ctypes.windll.user32.PostMessageW
                post_message.argtypes = [
                    wintypes.HWND,
                    wintypes.UINT,
                    wintypes.WPARAM,
                    wintypes.LPARAM,
                ]
                post_message.restype = wintypes.BOOL
                post_message(wintypes.HWND(hwnd), self.WM_CLOSE, 0, 0)
            except (AttributeError, OSError):
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive() and self._hwnd),
            "hotkey": self.hotkey,
            "error": self._error,
        }

    def _tip(self) -> str:
        try:
            unread = max(0, int(self.unread_provider()))
        except Exception:
            unread = 0
        return (
            f"Vibe Service Guardian · {unread} 条未读提醒"
            if unread
            else "Vibe Service Guardian · 本机服务守望"
        )[:127]

    def _run(self) -> None:
        if os.name != "nt":
            self._error = "windows_only"
            self._ready.set()
            return
        try:
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32
            wndproc_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", wndproc_type),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            class NOTIFYICONDATAW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256),
                    ("uTimeoutOrVersion", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", ctypes.c_byte * 16),
                    ("hBalloonIcon", wintypes.HICON),
                ]

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            # ctypes defaults C functions to a 32-bit integer return value.  A
            # tray window runs in a 64-bit portable build, so every API that
            # returns or accepts a handle needs an explicit prototype to avoid
            # truncating HWND/HMENU/HICON values.
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            user32.RegisterClassW.restype = wintypes.WORD
            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                wintypes.HINSTANCE,
                wintypes.LPVOID,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
            user32.LoadIconW.restype = wintypes.HICON
            user32.CreatePopupMenu.argtypes = []
            user32.CreatePopupMenu.restype = wintypes.HMENU
            user32.AppendMenuW.argtypes = [
                wintypes.HMENU,
                wintypes.UINT,
                ctypes.c_size_t,
                wintypes.LPCWSTR,
            ]
            user32.AppendMenuW.restype = wintypes.BOOL
            user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
            user32.GetCursorPos.restype = wintypes.BOOL
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.TrackPopupMenu.argtypes = [
                wintypes.HMENU,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.LPVOID,
            ]
            user32.TrackPopupMenu.restype = wintypes.UINT
            user32.DestroyMenu.argtypes = [wintypes.HMENU]
            user32.DestroyMenu.restype = wintypes.BOOL
            user32.DestroyWindow.argtypes = [wintypes.HWND]
            user32.DestroyWindow.restype = wintypes.BOOL
            user32.DefWindowProcW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.DefWindowProcW.restype = ctypes.c_ssize_t
            user32.RegisterHotKey.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.RegisterHotKey.restype = wintypes.BOOL
            user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.UnregisterHotKey.restype = wintypes.BOOL
            user32.GetMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.GetMessageW.restype = ctypes.c_int
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.TranslateMessage.restype = wintypes.BOOL
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.restype = ctypes.c_ssize_t
            user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
            user32.UnregisterClassW.restype = wintypes.BOOL
            shell32.Shell_NotifyIconW.argtypes = [
                wintypes.DWORD,
                ctypes.POINTER(NOTIFYICONDATAW),
            ]
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL

            command_open = 1001
            command_focus = 1002
            command_exit = 1003
            wm_lbutton_up = 0x0202
            wm_rbutton_up = 0x0205
            wm_hotkey = 0x0312
            wm_destroy = 0x0002

            icon_data: NOTIFYICONDATAW | None = None

            def refresh_icon() -> None:
                if icon_data is None:
                    return
                icon_data.szTip = self._tip()
                shell32.Shell_NotifyIconW(1, ctypes.byref(icon_data))

            def show_menu(hwnd: int) -> None:
                refresh_icon()
                menu = user32.CreatePopupMenu()
                if not menu:
                    return
                try:
                    unread = max(0, int(self.unread_provider()))
                    user32.AppendMenuW(menu, 0, command_open, "打开控制台")
                    user32.AppendMenuW(
                        menu,
                        0,
                        command_focus,
                        f"今日关注与提醒（未读 {unread}）",
                    )
                    user32.AppendMenuW(menu, 0x0800, 0, None)
                    user32.AppendMenuW(menu, 0, command_exit, "退出 VSG（中止监控）")
                    point = POINT()
                    user32.GetCursorPos(ctypes.byref(point))
                    user32.SetForegroundWindow(hwnd)
                    selected = user32.TrackPopupMenu(
                        menu,
                        0x0100 | 0x0002,
                        point.x,
                        point.y,
                        0,
                        hwnd,
                        None,
                    )
                    if selected == command_open:
                        self.open_dashboard()
                    elif selected == command_focus:
                        self.open_focus()
                    elif selected == command_exit:
                        self.exit_application()
                finally:
                    user32.DestroyMenu(menu)

            @wndproc_type
            def window_proc(hwnd, message, wparam, lparam):  # type: ignore[no-untyped-def]
                if message == self.WM_APP_TRAY:
                    if int(lparam) == wm_lbutton_up:
                        self.open_focus()
                    elif int(lparam) == wm_rbutton_up:
                        show_menu(hwnd)
                    return 0
                if message == wm_hotkey:
                    self.open_focus()
                    return 0
                if message == self.WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == wm_destroy:
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, message, wparam, lparam)

            self._wndproc = window_proc
            instance = kernel32.GetModuleHandleW(None)
            class_name = f"VSGTrayWindow-{os.getpid()}"
            window_class = WNDCLASSW()
            window_class.lpfnWndProc = window_proc
            window_class.hInstance = instance
            window_class.lpszClassName = class_name
            atom = user32.RegisterClassW(ctypes.byref(window_class))
            if not atom:
                raise OSError("RegisterClassW failed")
            hwnd = user32.CreateWindowExW(
                0,
                class_name,
                "Vibe Service Guardian",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                instance,
                None,
            )
            if not hwnd:
                raise OSError("CreateWindowExW failed")
            self._hwnd = int(hwnd)
            icon_resource = ctypes.cast(ctypes.c_void_p(32512), wintypes.LPCWSTR)
            icon = user32.LoadIconW(None, icon_resource)
            icon_data = NOTIFYICONDATAW()
            icon_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            icon_data.hWnd = hwnd
            icon_data.uID = 1
            icon_data.uFlags = 0x1 | 0x2 | 0x4
            icon_data.uCallbackMessage = self.WM_APP_TRAY
            icon_data.hIcon = icon
            icon_data.szTip = self._tip()
            if not shell32.Shell_NotifyIconW(0, ctypes.byref(icon_data)):
                raise OSError("Shell_NotifyIconW failed")
            hotkey_registered = False
            if self.hotkey == "ctrl_alt_g":
                hotkey_registered = bool(
                    user32.RegisterHotKey(hwnd, 1, 0x0002 | 0x0001 | 0x4000, ord("G"))
                )
                if not hotkey_registered:
                    self._error = "hotkey_registration_failed"
            self._ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            if hotkey_registered:
                user32.UnregisterHotKey(hwnd, 1)
            shell32.Shell_NotifyIconW(2, ctypes.byref(icon_data))
            self._hwnd = None
            user32.UnregisterClassW(class_name, instance)
        except Exception as exc:
            self._error = type(exc).__name__
            LOGGER.warning("Windows tray unavailable", exc_info=True)
            self._hwnd = None
            self._ready.set()


class TrayController:
    """Optional Windows tray; disabled by default and never changes startup state."""

    def __init__(
        self,
        open_dashboard: Action,
        open_focus: Action,
        exit_application: Action,
        unread_provider: UnreadProvider,
    ):
        self._actions = (open_dashboard, open_focus, exit_application, unread_provider)
        self._backend: _WindowsTrayBackend | None = None
        self._enabled = False
        self._hotkey = "disabled"

    def configure(self, enabled: bool, hotkey: str = "disabled") -> dict[str, Any]:
        normalized_hotkey = hotkey if hotkey in {"disabled", "ctrl_alt_g"} else "disabled"
        if self._backend and (
            not enabled or normalized_hotkey != self._hotkey
        ):
            self._backend.stop()
            self._backend = None
        self._enabled = bool(enabled)
        self._hotkey = normalized_hotkey
        if self._enabled and os.name == "nt" and not self._backend:
            self._backend = _WindowsTrayBackend(*self._actions, normalized_hotkey)
            self._backend.start()
        return self.status()

    def status(self) -> dict[str, Any]:
        backend = self._backend.status() if self._backend else {}
        return {
            "available": os.name == "nt",
            "enabled": self._enabled,
            "running": bool(backend.get("running")),
            "hotkey": self._hotkey,
            "hotkey_default": "disabled",
            "error": backend.get("error"),
            "network_used": False,
        }

    def close(self) -> None:
        if self._backend:
            self._backend.stop()
            self._backend = None
