"""Pinned-SSH transport for the fixed, guarded Linux downloader consumer."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Iterator


class TerminalError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__("The protected terminal handoff could not be completed.")


def load_target(path: Path, target_id: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError()
        target = data["targets"][target_id]
        if set(target) != {"host", "port", "username", "identity_file", "host_key"}:
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}", target["host"]):
            raise ValueError()
        if type(target["port"]) is not int or not 1 <= target["port"] <= 65535:
            raise ValueError()
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", target["username"]):
            raise ValueError()
        key_path = Path(target["identity_file"])
        if not key_path.is_absolute() or not key_path.is_file():
            raise ValueError()
        # The full SSH public host key is the pin, not a hostname discovered on
        # first use. No ssh-keyscan trust-on-first-use or private key read occurs.
        algorithm, encoded = target["host_key"].split()
        if algorithm not in {"ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"}:
            raise ValueError()
        wire = base64.b64decode(encoded, validate=True)
        length = int.from_bytes(wire[:4], "big")
        if length <= 0 or wire[4:4 + length].decode("ascii") != algorithm or len(wire) < 4 + length + 8:
            raise ValueError()
        return dict(target)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        raise TerminalError("terminal_target_invalid") from None


@contextmanager
def pinned_ssh(target: dict[str, Any]) -> Iterator[list[str]]:
    executable = shutil.which("ssh")
    if executable is None:
        raise TerminalError("terminal_ssh_unavailable")
    with tempfile.TemporaryDirectory(prefix="agent-framework-host-pin-") as folder:
        known_hosts = Path(folder) / "known_hosts"
        known_hosts.write_text("agent-framework-downloader " + target["host_key"] + "\n", encoding="ascii")
        algorithm = target["host_key"].split()[0]
        if algorithm == "ssh-rsa":
            algorithm = "rsa-sha2-512,rsa-sha2-256"
        # OpenSSH parses the value of -o again as configuration syntax. Passing
        # one subprocess argument alone does not preserve whitespace in a path.
        quoted_hosts = '"' + known_hosts.as_posix().replace('\\', '\\\\').replace('"', '\\"') + '"'
        yield [executable, "-F", "none", "-T", "-p", str(target["port"]), "-l", target["username"],
               "-i", target["identity_file"], "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
               "-o", "IdentityAgent=none", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", "GlobalKnownHostsFile=none",
               "-o", "UserKnownHostsFile=" + quoted_hosts, "-o", "HostKeyAlias=agent-framework-downloader",
               "-o", "HostKeyAlgorithms=" + algorithm, "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR",
               target["host"]]


def remote_command(pane: str, prompt: str, process_name: str, origin: str, mode: str, identity: str = "-") -> str:
    helper = Path(__file__).with_name("terminal_remote.py").read_text(encoding="utf-8")
    return shlex.join(["python3", "-c", helper, pane, prompt, process_name, origin, mode, identity])


def deliver(selection: Any, *, targets_path: Path, target_id: str, pane: str,
            prompt: str, process_name: str) -> dict[str, Any]:
    from . import custody
    selection = custody.refresh(selection)
    if (selection.credential != "password" or not selection.origin
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}:[0-9]{1,4}\.[0-9]{1,4}", pane)
            or not isinstance(prompt, str) or not 1 <= len(prompt) <= 200 or prompt != prompt.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in prompt)
            or process_name not in {"curl", "wget"}):
        raise TerminalError("terminal_target_invalid")
    target = load_target(targets_path, target_id)
    with pinned_ssh(target) as ssh:
        try:
            probe = subprocess.run(ssh + [remote_command(pane, prompt, process_name, selection.origin, "probe")],
                                   input=b"", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=25, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise TerminalError("terminal_prompt_unverified") from None
        match = re.fullmatch(rb"READY:(%[0-9]+:[0-9]+:[0-9]+:[a-f0-9]{64})\n", probe.stdout)
        if probe.returncode or match is None:
            raise TerminalError("terminal_prompt_unverified")
        identity = match.group(1).decode("ascii")
        # No credential decryption until the pinned peer proves the declared
        # live prompt. read_value refreshes the account binding again.
        value = custody.read_value(selection).encode("utf-8")
        if not value or len(value) > 4096 or any(byte < 32 or byte == 127 for byte in value):
            raise TerminalError("terminal_credential_incompatible")
        try:
            result = subprocess.run(ssh + [remote_command(pane, prompt, process_name, selection.origin, "deliver", identity)],
                                    input=value, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise TerminalError("terminal_delivery_uncertain") from None
        if result.returncode or result.stdout != b"DELIVERED\n":
            raise TerminalError("terminal_delivery_uncertain")
    return {"target": target_id, "pane": pane, "origin": selection.origin,
            "foreground_process": process_name, "delivery_state": "delivered_to_terminal",
            "remote_prompt_verified": True, "provider_authentication_verified": False,
            "credential_value_emitted": False, "clipboard_modified": False}
