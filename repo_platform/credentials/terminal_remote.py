"""Fixed Linux tmux downloader consumer, executed remotely by terminal.py.

Only metadata is accepted as argv. A credential is read from stdin after the
observed process, HTTPS origin, echo-off prompt and probe identity are verified.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import urlsplit


def downloader_arguments(arguments: list[str], process_name: str, expected_origin: str) -> None:
    """A closed option grammar avoids override aliases and hidden config inputs."""
    if not arguments or Path(arguments[0]).name != process_name:
        raise RuntimeError("downloader mismatch")
    origins: list[str] = []
    if process_name == "curl":
        if len(arguments) < 2 or arguments[1] != "--disable":
            raise RuntimeError("curl must disable its startup config first")
        switches = {"--disable", "--fail", "--silent", "--show-error", "--remote-name"}
        values = {"--proto", "--proto-redir", "--proxy", "--noproxy", "--max-redirs", "--user",
                  "--output", "--connect-timeout", "--max-time", "--limit-rate", "--retry"}
        required = {"--proto": "=https", "--proto-redir": "=https", "--proxy": "",
                    "--noproxy": "*", "--max-redirs": "0"}
    elif process_name == "wget":
        if len(arguments) < 2 or arguments[1] != "--no-config":
            raise RuntimeError("wget must disable its startup config first")
        switches = {"--no-config", "--no-netrc", "--no-proxy", "--ask-password", "--https-only",
                    "--spider", "--continue", "--no-clobber", "--quiet", "--no-verbose",
                    "--show-progress", "--timestamping"}
        values = {"--max-redirect", "--user", "--output-document", "--directory-prefix", "--tries",
                  "--timeout", "--connect-timeout", "--read-timeout", "--waitretry", "--limit-rate",
                  "--progress", "--quota"}
        required = {"--max-redirect": "0"}
    else:
        raise RuntimeError("unsupported downloader")
    seen: dict[str, str] = {}
    flags: set[str] = set()
    index = 1
    while index < len(arguments):
        token = arguments[index]
        if token in switches:
            flags.add(token)
        elif token.startswith("--"):
            option, separator, value = token.partition("=")
            if option not in values or option in seen:
                raise RuntimeError("unsupported or duplicate option")
            if not separator:
                index += 1
                if index >= len(arguments):
                    raise RuntimeError("option value missing")
                value = arguments[index]
            if option == "--user" and (not value or ":" in value or any(ord(c) < 32 for c in value)):
                raise RuntimeError("username invalid or password already in argv")
            seen[option] = value
        else:
            parsed = urlsplit(token)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise RuntimeError("invalid URL operand")
            origins.append("https://" + parsed.netloc.lower())
        index += 1
    if any(seen.get(option) != value for option, value in required.items()) or "--user" not in seen:
        raise RuntimeError("guarded downloader options absent")
    if process_name == "wget" and not {"--no-netrc", "--no-proxy", "--ask-password"}.issubset(flags):
        raise RuntimeError("wget guards absent")
    if not origins or any(origin != expected_origin for origin in origins):
        raise RuntimeError("downloader origin mismatch")


def checked_tmux(*args: str, input_bytes: bytes | None = None) -> str:
    result = subprocess.run(["tmux", *args], input=input_bytes, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=5, check=False)
    if result.returncode:
        raise RuntimeError("tmux failed")
    return result.stdout.decode("utf-8", errors="strict")


def process_stat(pid: int) -> dict[str, int]:
    text = Path("/proc", str(pid), "stat").read_text(encoding="utf-8")
    fields = text[text.rindex(")") + 2:].split()
    return {"pid": pid, "ppid": int(fields[1]), "pgrp": int(fields[2]), "tty": int(fields[4]),
            "foreground": int(fields[5]), "start": int(fields[19])}


def has_parent(pid: int, ancestor: int, table: dict[int, dict[str, int]]) -> bool:
    for _ in range(64):
        if pid == ancestor:
            return True
        row = table.get(pid)
        if row is None or row["ppid"] in (0, pid):
            return False
        pid = row["ppid"]
    return False


def command_arguments(command: bytes) -> list[str]:
    if not command.endswith(b"\0"):
        raise RuntimeError("process arguments unavailable")
    # /proc cmdline ends with one NUL, but interior empty arguments matter:
    # curl --proxy '' is the explicit proxy-disable guard.
    return [part.decode("utf-8", errors="strict") for part in command[:-1].split(b"\0")]


def verify_foreground(root_pid: int, process_name: str, origin: str) -> str:
    import termios  # Linux consumer only; option validation can run on either client OS.
    root = process_stat(root_pid)
    if root["tty"] == 0 or root["foreground"] <= 0:
        raise RuntimeError("foreground absent")
    table = {root_pid: root}
    for item in os.scandir("/proc"):
        if item.name.isdecimal():
            try:
                pid = int(item.name)
                if pid != root_pid:
                    table[pid] = process_stat(pid)
            except (OSError, ValueError, IndexError):
                continue
    matches = []
    for pid, row in table.items():
        if row["tty"] != root["tty"] or row["pgrp"] != root["foreground"] or not has_parent(pid, root_pid, table):
            continue
        try:
            if Path(os.readlink(f"/proc/{pid}/exe")).name == process_name:
                matches.append(pid)
        except OSError:
            continue
    if len(matches) != 1:
        raise RuntimeError("foreground ambiguous")
    pid = matches[0]
    command = Path(f"/proc/{pid}/cmdline").read_bytes()
    arguments = command_arguments(command)
    downloader_arguments(arguments, process_name, origin)
    # A visible password-looking prompt is insufficient when the target would
    # echo the password back into terminal scrollback.
    terminal = os.readlink(f"/proc/{pid}/fd/0")
    if not re.fullmatch(r"/dev/pts/[0-9]+", terminal):
        raise RuntimeError("stdin is not the expected terminal")
    descriptor = os.open(terminal, os.O_RDONLY | os.O_NOCTTY)
    try:
        if termios.tcgetattr(descriptor)[3] & termios.ECHO:
            raise RuntimeError("terminal echo enabled")
    finally:
        os.close(descriptor)
    return f"{pid}:{table[pid]['start']}:{hashlib.sha256(command).hexdigest()}"


def verify(pane: str, prompt: str, process_name: str, origin: str) -> str:
    session, location = pane.split(":", 1)
    window, pane_index = location.split(".", 1)
    values = checked_tmux("display-message", "-p", "-t", pane,
                          "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_id}\t#{pane_dead}\t#{pane_pid}").strip().split("\t")
    if len(values) != 6 or values[:3] != [session, window, pane_index] or values[4] != "0":
        raise RuntimeError("pane mismatch")
    identity = verify_foreground(int(values[5]), process_name, origin)
    lines = checked_tmux("capture-pane", "-p", "-t", pane).splitlines()
    if next((line for line in reversed(lines) if line), "") != prompt:
        raise RuntimeError("prompt mismatch")
    return values[3] + ":" + identity


def main(argv: list[str] | None = None) -> int:
    try:
        pane, prompt, process_name, origin, mode, expected_id = argv if argv is not None else sys.argv[1:]
        identity = verify(pane, prompt, process_name, origin)
        if mode == "probe":
            sys.stdout.write("READY:" + identity + "\n")
            return 0
        if mode != "deliver" or identity != expected_id:
            raise RuntimeError("probe identity changed")
        secret = sys.stdin.buffer.read(4097)
        if not secret or len(secret) > 4096 or any(byte < 32 or byte == 127 for byte in secret):
            raise RuntimeError("invalid secret input")
        if verify(pane, prompt, process_name, origin) != expected_id:
            raise RuntimeError("target changed")
        buffer_name = "agent-framework-" + secrets.token_hex(12)
        try:
            checked_tmux("load-buffer", "-b", buffer_name, "-", input_bytes=secret)
            checked_tmux("paste-buffer", "-d", "-b", buffer_name, "-t", pane)
            checked_tmux("send-keys", "-t", pane, "Enter")
        finally:
            subprocess.run(["tmux", "delete-buffer", "-b", buffer_name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
        sys.stdout.write("DELIVERED\n")
        return 0
    except BaseException:
        sys.stdout.write("FAILED\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
