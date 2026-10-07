"""Optional, explicit private-human clipboard handoff.

This is not the browser worker transport. Values never leave this process via
stdout, files, argv or environment. Linux delivery owns a foreground clipboard
selection process; cleanup terminates only that owner, so a replacement clipboard
is preserved even when compare-and-clear is unavailable on the display protocol.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hmac
import os
import shutil
import subprocess
import sys
import time
from typing import Any


class ClipboardError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__("The private clipboard operation could not be completed.")


class WindowsClipboard:
    """CF_UNICODETEXT; compare and clear share one exclusive OpenClipboard lock."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ClipboardError("clipboard_backend_unavailable")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("OpenClipboard", [wintypes.HWND], wintypes.BOOL),
            ("CloseClipboard", [], wintypes.BOOL),
            ("EmptyClipboard", [], wintypes.BOOL),
            ("SetClipboardData", [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE),
            ("GetClipboardData", [wintypes.UINT], wintypes.HANDLE),
            ("CreateWindowExW", [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                  wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HWND),
            ("DestroyWindow", [wintypes.HWND], wintypes.BOOL),
        ):
            function = getattr(self._user32, name)
            function.argtypes, function.restype = args, result
        for name, args, result in (
            ("GlobalAlloc", [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL),
            ("GlobalLock", [wintypes.HGLOBAL], ctypes.c_void_p),
            ("GlobalUnlock", [wintypes.HGLOBAL], wintypes.BOOL),
            ("GlobalFree", [wintypes.HGLOBAL], wintypes.HGLOBAL),
            ("GlobalSize", [wintypes.HGLOBAL], ctypes.c_size_t),
        ):
            function = getattr(self._kernel32, name)
            function.argtypes, function.restype = args, result

    def _open(self) -> int:
        # EmptyClipboard needs a real owner for the subsequent SetClipboardData.
        # A message-only STATIC window needs no visible UI or custom window proc.
        # Each operation owns its HWND on its calling thread and destroys it after
        # closing the clipboard. Text is materialized, never delayed-rendered.
        hwnd = self._user32.CreateWindowExW(
            0, "STATIC", None, 0, 0, 0, 0, 0, wintypes.HWND(-3), None, None, None,
        )
        if not hwnd:
            raise ClipboardError("clipboard_unavailable")
        if not self._user32.OpenClipboard(hwnd):
            self._user32.DestroyWindow(hwnd)
            raise ClipboardError("clipboard_unavailable")
        return hwnd

    def _close(self, hwnd: int) -> None:
        try:
            if not self._user32.CloseClipboard():
                raise ClipboardError("clipboard_close_failed")
        finally:
            if not self._user32.DestroyWindow(hwnd):
                raise ClipboardError("clipboard_owner_cleanup_failed")

    def _read_open(self) -> str | None:
        handle = self._user32.GetClipboardData(13)
        if not handle:
            return None
        size = self._kernel32.GlobalSize(handle)
        if not size or size > 131074 or size % 2:
            raise ClipboardError("clipboard_input_invalid")
        pointer = self._kernel32.GlobalLock(handle)
        if not pointer:
            raise ClipboardError("clipboard_read_failed")
        try:
            try:
                text = ctypes.string_at(pointer, size).decode("utf-16-le", errors="strict")
            except UnicodeError:
                raise ClipboardError("clipboard_input_invalid") from None
            if "\0" not in text:
                raise ClipboardError("clipboard_input_invalid")
            return text.split("\0", 1)[0]
        finally:
            self._kernel32.GlobalUnlock(handle)

    def get_text(self) -> str | None:
        hwnd = self._open()
        try:
            return self._read_open()
        finally:
            self._close(hwnd)

    def set_text(self, value: str) -> None:
        _check_value(value)
        encoded = (value + "\0").encode("utf-16-le")
        handle = self._kernel32.GlobalAlloc(0x0002, len(encoded))
        if not handle:
            raise ClipboardError("clipboard_write_failed")
        transferred = False
        try:
            pointer = self._kernel32.GlobalLock(handle)
            if not pointer:
                raise ClipboardError("clipboard_write_failed")
            try:
                ctypes.memmove(pointer, encoded, len(encoded))
            finally:
                self._kernel32.GlobalUnlock(handle)
            hwnd = self._open()
            try:
                if not self._user32.EmptyClipboard() or not self._user32.SetClipboardData(13, handle):
                    raise ClipboardError("clipboard_write_failed")
                transferred = True
            finally:
                self._close(hwnd)
        finally:
            if not transferred:
                self._kernel32.GlobalFree(handle)

    def cleanup(self, expected: str) -> str:
        hwnd = self._open()
        try:
            current = self._read_open()
            if current is None or not hmac.compare_digest(current.encode("utf-8"), expected.encode("utf-8")):
                return "preserved_changed"
            if not self._user32.EmptyClipboard():
                raise ClipboardError("clipboard_cleanup_failed")
            return "cleared"
        finally:
            self._close(hwnd)


class LinuxClipboard:
    """X11/Wayland clipboard using explicitly selected, owned processes.

    There is no safe generic read-then-clear operation for an unowned Linux
    clipboard here. Intake preserves it; delivery can release its own selection.
    """

    def __init__(self, backend: str):
        self._owner: subprocess.Popen[bytes] | None = None
        if sys.platform != "linux" or backend not in {"wayland", "x11"}:
            raise ClipboardError("clipboard_backend_unavailable")
        if backend == "wayland":
            if not os.environ.get("WAYLAND_DISPLAY"):
                raise ClipboardError("clipboard_session_unavailable")
            copy, paste = shutil.which("wl-copy"), shutil.which("wl-paste")
            self._copy = [copy, "--foreground", "--type", "text/plain;charset=utf-8"]
            self._paste = [paste, "--no-newline", "--type", "text/plain;charset=utf-8"]
        else:
            if not os.environ.get("DISPLAY"):
                raise ClipboardError("clipboard_session_unavailable")
            copy = paste = shutil.which("xclip")
            self._copy = [copy, "-selection", "clipboard", "-in", "-quiet"]
            self._paste = [paste, "-selection", "clipboard", "-out"]
        if not copy or not paste:
            raise ClipboardError("clipboard_backend_unavailable")

    def get_text(self) -> str | None:
        try:
            result = subprocess.run(self._paste, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=5, check=False)
            if result.returncode:
                raise ClipboardError("clipboard_read_failed")
            if len(result.stdout) > 65536:
                raise ClipboardError("clipboard_input_invalid")
            return result.stdout.decode("utf-8", errors="strict")
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            raise ClipboardError("clipboard_read_failed") from None

    def set_text(self, value: str) -> None:
        _check_value(value)
        if self._owner is not None:
            raise ClipboardError("clipboard_owner_already_active")
        try:
            self._owner = subprocess.Popen(self._copy, stdin=subprocess.PIPE,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            assert self._owner.stdin is not None
            self._owner.stdin.write(value.encode("utf-8"))
            self._owner.stdin.close()
            # Wait only for the actual selection to contain this value. A CLI
            # process starting successfully is not proof that it owns a selection.
            for _ in range(20):
                if self._owner.poll() is not None:
                    raise ClipboardError("clipboard_write_failed")
                try:
                    current = self.get_text()
                except ClipboardError:
                    current = None
                if current is not None and hmac.compare_digest(current.encode("utf-8"), value.encode("utf-8")):
                    return
                time.sleep(0.05)
            raise ClipboardError("clipboard_write_failed")
        except (OSError, BrokenPipeError):
            raise ClipboardError("clipboard_write_failed") from None
        finally:
            if sys.exc_info()[0] is not None:
                self.cleanup(value)

    def cleanup(self, expected: str) -> str:
        # Never invoke wl-copy --clear or xclip with empty input after a separate
        # read: another writer could replace the clipboard between those calls.
        if self._owner is None:
            return "preserved_unowned"
        owner, self._owner = self._owner, None
        if owner.poll() is not None:
            return "preserved_changed"
        try:
            owner.terminate()
            try:
                owner.wait(timeout=2)
            except subprocess.TimeoutExpired:
                owner.kill()
                owner.wait(timeout=2)
            return "released_owned_selection"
        except (OSError, subprocess.TimeoutExpired):
            raise ClipboardError("clipboard_cleanup_failed") from None


def _check_value(value: Any) -> None:
    if not isinstance(value, str) or not value or "\0" in value or len(value.encode("utf-8")) > 65536:
        raise ClipboardError("clipboard_input_invalid")


def open_clipboard(backend: str):
    """No automatic fallback between distinct display/clipboard sessions."""
    if backend == "windows":
        return WindowsClipboard()
    return LinuxClipboard(backend)


def manual_handoff(selection: Any, *, backend: str) -> dict[str, Any]:
    """Interactive human paste; keep the value private through conditional cleanup."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ClipboardError("interactive_console_required")
    from . import custody
    selection = custody.refresh(selection)
    if not selection.origin or selection.credential not in {"password", "security_answer", "totp_code", "totp_enrollment_code"}:
        raise ClipboardError("clipboard_selection_invalid")
    clipboard = open_clipboard(backend)
    if selection.credential in {"totp_code", "totp_enrollment_code"}:
        from . import totp
        value = totp.operate(selection.credential, selection.account_id, selection.origin,
                             config=selection.config, expected=selection)
    else:
        value = custody.read_value(selection)
    outcome = "failed"
    try:
        clipboard.set_text(value)
        input("Paste into the authorized field now, then press Enter here to finish: ")
    finally:
        outcome = clipboard.cleanup(value)
    return {"delivery_state": "copied_for_private_human_paste", "clipboard_cleanup": outcome,
            "provider_authentication_verified": False, "credential_value_emitted": False}


def intake(selection: Any, *, backend: str) -> dict[str, Any]:
    """Explicit clipboard-source intake into absent canonical custody only."""
    from . import custody
    selection = custody.refresh(selection)
    if not selection.origin or selection.credential not in {"password", "security_answer"}:
        raise ClipboardError("clipboard_selection_invalid")
    # Resolve/check storage and absence before touching the user's clipboard.
    with custody.locked(custody.lock_path(selection)), custody.store_for(selection) as store:
        if custody.read_payload(selection, store) is not None:
            custody.fail("credentials_already_provisioned")
        clipboard = open_clipboard(backend)
        value = clipboard.get_text()
        custody.text(value)
        custody.refresh(selection)
        custody.write_payload(selection, value, store)
        readback = custody.read_payload(selection, store)
        if not isinstance(readback, str) or not hmac.compare_digest(readback.encode("utf-8"), value.encode("utf-8")):
            custody.fail("credential_readback_mismatch")
        try:
            cleanup = clipboard.cleanup(value)
        except ClipboardError:
            cleanup = "failed"
    return {"status": "stored_unverified", "input_source": "clipboard", "credential_readback_verified": True,
            "clipboard_cleanup": cleanup, "credential_value_emitted": False,
            "provider_authentication_verified": False}
