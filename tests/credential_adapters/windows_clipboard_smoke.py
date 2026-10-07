"""Native clipboard smoke in an isolated, noninteractive Windows station.

Run this file directly. The parent launches a bounded subprocess; that subprocess
creates its own window station and desktop before any clipboard API is called.
It never switches the input desktop and never reads the interactive clipboard.
All content is synthetic. This is deliberately separate from normal unit tests.
Windows needs an administrator for a unique named station if the current logon's
automatically named station already exists. The script never elevates itself.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def isolated_smoke() -> dict[str, object]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("GetProcessWindowStation", [], wintypes.HANDLE),
        ("CreateWindowStationW", [wintypes.LPCWSTR, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_void_p], wintypes.HANDLE),
        ("SetProcessWindowStation", [wintypes.HANDLE], wintypes.BOOL),
        ("CloseWindowStation", [wintypes.HANDLE], wintypes.BOOL),
        ("GetThreadDesktop", [wintypes.DWORD], wintypes.HANDLE),
        ("CreateDesktopW", [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p], wintypes.HANDLE),
        ("SetThreadDesktop", [wintypes.HANDLE], wintypes.BOOL),
        ("CloseDesktop", [wintypes.HANDLE], wintypes.BOOL),
        ("GetUserObjectInformationW", [wintypes.HANDLE, ctypes.c_int,
                                        ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        ("IsWindow", [wintypes.HWND], wintypes.BOOL),
    ):
        function = getattr(user32, name)
        function.argtypes, function.restype = args, result
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def checked(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def object_name(handle):
        buffer = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        checked(user32.GetUserObjectInformationW(
            handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)))
        return buffer.value

    class UserObjectFlags(ctypes.Structure):
        _fields_ = [("inherit", wintypes.BOOL), ("reserved", wintypes.BOOL),
                    ("flags", wintypes.DWORD)]

    original_station = checked(user32.GetProcessWindowStation())
    original_desktop = checked(user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
    # Windows reserves caller-chosen station names to administrators. Other users
    # ask Windows for a name. CWF_CREATE_ONLY prevents reusing any existing station.
    # Only read attributes, clipboard access and desktop creation are needed.
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.IsUserAnAdmin.argtypes, shell32.IsUserAnAdmin.restype = [], wintypes.BOOL
    requested_name = "agent-framework-clipboard-test-" + uuid.uuid4().hex if shell32.IsUserAnAdmin() else None
    station = checked(user32.CreateWindowStationW(requested_name, 1, 0x000E, None))
    station_name = object_name(station)
    assert station_name != object_name(original_station), "Cannot reuse original station"
    desktop = None
    try:
        checked(user32.SetProcessWindowStation(station))
        # Fail closed if isolation could not be established. No fallback is safe.
        assert object_name(user32.GetProcessWindowStation()) == station_name
        flags, needed = UserObjectFlags(), wintypes.DWORD()
        checked(user32.GetUserObjectInformationW(
            station, 1, ctypes.byref(flags), ctypes.sizeof(flags), ctypes.byref(needed)))
        assert not (flags.flags & 1), "Test station must be noninteractive"
        desktop = checked(user32.CreateDesktopW("fixture", None, None, 0, 0x0083, None))
        checked(user32.SetThreadDesktop(desktop))
        assert object_name(user32.GetThreadDesktop(kernel32.GetCurrentThreadId())) == "fixture"

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from repo_platform.credentials.clipboard import WindowsClipboard

        windows = []

        class ObservedClipboard(WindowsClipboard):
            def _open(self):
                hwnd = super()._open()
                windows.append(hwnd)
                return hwnd

        clipboard = ObservedClipboard()
        first = "synthetic clipboard: caf\u00e9 \U0001f512"
        replacement = "synthetic replacement"
        clipboard.set_text(first)
        assert clipboard.get_text() == first, "Unicode round-trip failed"
        assert clipboard.cleanup(replacement) == "preserved_changed"
        assert clipboard.get_text() == first, "Mismatched cleanup changed content"
        # Another object replaces the content before the first owner cleans up.
        ObservedClipboard().set_text(replacement)
        assert clipboard.cleanup(first) == "preserved_changed"
        assert clipboard.get_text() == replacement, "Replacement was not preserved"
        assert clipboard.cleanup(replacement) == "cleared"
        assert clipboard.get_text() is None, "Exact cleanup did not empty clipboard"
        assert windows and all(not user32.IsWindow(hwnd) for hwnd in windows), "Leaked HWND"
    finally:
        # Restore only this subprocess's station/thread association, never the
        # visible input desktop. Process exit also closes its remaining handles.
        try:
            checked(user32.SetProcessWindowStation(original_station))
            checked(user32.SetThreadDesktop(original_desktop))
        finally:
            try:
                if desktop is not None:
                    checked(user32.CloseDesktop(desktop))
            finally:
                checked(user32.CloseWindowStation(station))
    return {"ok": True, "isolation": "private_noninteractive_window_station",
            "checks": ["unicode_round_trip", "mismatch_preserved", "replacement_preserved",
                       "exact_cleanup", "owner_windows_destroyed", "station_desktop_closed"],
            "interactive_clipboard_accessed": False}


def main() -> int:
    if sys.platform != "win32":
        print(json.dumps({"ok": False, "status": "not_applicable", "platform": sys.platform}))
        return 0
    if sys.argv[1:] == ["--isolated-child"]:
        try:
            print(json.dumps(isolated_smoke()))
            return 0
        except Exception as exc:
            # No clipboard value, credential content or arbitrary exception text.
            print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                              "winerror": getattr(exc, "winerror", None)}))
            return 1
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--isolated-child"],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    print(result.stdout.strip())
    if result.stderr:
        print("Isolated child emitted unexpected diagnostic output.", file=sys.stderr)
        return 1
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
