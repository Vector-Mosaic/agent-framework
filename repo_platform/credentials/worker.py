"""Private browser worker; values leave only through inherited fd 3.

The executable and parent browser runtime are trusted local processes.
Stdout contains fixed status codes only; stderr carries no custody diagnostics.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import socket
import stat
import sys

# Absolute invocation by Node works regardless of the calling directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from repo_platform.credentials.custody import CustodyError, read_value, resolve_pair

MAXIMUM_BYTES = 16384
MAXIMUM_REQUEST_BYTES = 16384


def private_pipe() -> None:
    """Accept only Node's inherited local pipe or Unix socketpair endpoint."""
    try:
        if sys.platform == "win32":
            import msvcrt
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetFileType.argtypes = [ctypes.c_void_p]
            kernel.GetFileType.restype = ctypes.c_uint32
            if kernel.GetFileType(msvcrt.get_osfhandle(3)) != 3:  # FILE_TYPE_PIPE
                raise ValueError()
            msvcrt.setmode(3, os.O_BINARY)
        else:
            mode = os.fstat(3).st_mode
            if stat.S_ISSOCK(mode):
                # Detect the actual family. fromfd(..., AF_UNIX, ...) alone
                # would not verify that the underlying socket is Unix-local.
                with socket.socket(fileno=os.dup(3)) as channel:
                    if channel.family != socket.AF_UNIX or channel.type != socket.SOCK_STREAM:
                        raise ValueError()
                    channel.getpeername()
            elif not stat.S_ISFIFO(mode):
                raise ValueError()
        os.set_inheritable(3, False)
    except Exception:
        raise CustodyError("private_pipe_required") from None


def main() -> int:
    value = None
    payload = None
    request = None
    try:
        # Refuse terminal, regular file or network output before secret access.
        private_pipe()
        raw = sys.stdin.buffer.read(MAXIMUM_REQUEST_BYTES + 1)
        if len(raw) > MAXIMUM_REQUEST_BYTES:
            raise CustodyError("request_invalid")
        request = json.loads(raw)
        raw = None
        if not isinstance(request, dict):
            raise CustodyError("request_invalid")
        config_value = request.pop("config", None)
        if config_value is not None and (
            not isinstance(config_value, str) or "\0" in config_value
            or not Path(config_value).is_absolute()
        ):
            raise CustodyError("request_invalid")
        config = Path(config_value) if config_value is not None else None
        operation = request.get("operation", "fill")
        if operation == "fill":
            if set(request) != {"account", "credential", "origin", "tab_origin"}:
                raise CustodyError("request_invalid")
            selection = resolve_pair(request["account"], request["credential"],
                                     request["tab_origin"], request["origin"], config=config)
            if request["credential"] == "totp_code":
                from repo_platform.credentials.totp import operate
                value = operate("totp_code", request["account"], request["origin"],
                                config=config, expected=selection)
            else:
                value = read_value(selection)
        elif operation in (
            "totp_status", "capture_totp", "activate_totp",
        ):
            from repo_platform.credentials.totp import operate
            expected = {"operation", "account", "origin"}
            if operation == "capture_totp":
                expected |= {"enrollment", "options"}
            if set(request) != expected and not (
                operation == "capture_totp" and set(request) == expected | {"replace_pending"}
            ):
                raise CustodyError("request_invalid")
            result = operate(operation, request["account"], request["origin"], config=config,
                             value=request.get("enrollment"), options=request.get("options"),
                             replace_pending=request.get("replace_pending", False))
            value = json.dumps(result)
        else:
            raise CustodyError("request_invalid")
        if not isinstance(value, str):
            raise CustodyError("credential_invalid")
        payload = bytearray(value, "utf-8")
        if not payload or len(payload) > MAXIMUM_BYTES:
            raise CustodyError("credential_invalid")
        with os.fdopen(3, "wb", closefd=True) as destination:
            destination.write(payload)
        print(json.dumps({"status": "ok"}))
        return 0
    except CustodyError as error:
        print(json.dumps({"status": "error", "code": error.code}))
        return 2
    except Exception:
        # Never stringify browser/custody failures or emit a traceback.
        print(json.dumps({"status": "error", "code": "custody_failed"}))
        return 2
    finally:
        if payload is not None:
            payload[:] = b"\0" * len(payload)
        value = None
        request = None


if __name__ == "__main__":
    raise SystemExit(main())
