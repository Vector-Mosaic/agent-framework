"""Actual guarded curl prompt on an isolated Linux tmux socket, synthetic data.

The reserved .invalid hostname cannot be a real credential destination. This
tests the Linux consumer, not a live SSH installation or provider login.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "repo_platform/credentials/terminal_remote.py"


def main():
    if sys.platform != "linux" or not shutil.which("tmux") or not shutil.which("curl"):
        raise SystemExit("Linux, tmux and curl required")
    with tempfile.TemporaryDirectory(prefix="agent-framework-tmux-") as folder:
        environment = dict(os.environ, TMUX_TMPDIR=folder)
        environment.pop("TMUX", None)
        # TMUX_TMPDIR creates a new default socket namespace; preserve any
        # user's unrelated default tmux server and never connect to it.
        def tmux(*args):
            result = subprocess.run(["tmux", *args], env=environment, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=8, check=False)
            if result.returncode:
                raise RuntimeError("isolated tmux command failed")
            return result.stdout.decode("utf-8", errors="strict")
        def consumer(mode, identity="-", value=b"", prompt=""):
            return subprocess.run([sys.executable, str(HELPER), "handoff:0.0", prompt, "curl",
                                   "https://example.invalid", mode, identity], input=value,
                                  env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=15, check=False)
        command = shlex.join(["curl", "--disable", "--proto", "=https", "--proto-redir", "=https",
                              "--proxy", "", "--noproxy", "*", "--max-redirs", "0", "--user", "example",
                              "--connect-timeout", "1", "--max-time", "2", "https://example.invalid/data"])
        try:
            tmux("new-session", "-d", "-s", "handoff", "-x", "140", "-y", "30")
            tmux("send-keys", "-t", "handoff:0.0", "-l", command)
            tmux("send-keys", "-t", "handoff:0.0", "Enter")
            identity = None
            prompt = ""
            for _ in range(50):
                lines = tmux("capture-pane", "-p", "-t", "handoff:0.0").splitlines()
                prompt = next((line for line in reversed(lines) if line), "")
                if "password" in prompt.lower():
                    probe = consumer("probe", prompt=prompt)
                    match = re.fullmatch(rb"READY:(%[0-9]+:[0-9]+:[0-9]+:[a-f0-9]{64})\n", probe.stdout)
                    if probe.returncode == 0 and match:
                        identity = match.group(1).decode("ascii")
                        break
                time.sleep(0.05)
            if identity is None:
                raise RuntimeError("actual guarded echo-off curl prompt not verified")
            # Wrong process identity must reject before consuming/pasting input.
            mismatch = consumer("deliver", identity="%999:0:0:" + "0" * 64,
                                value=b"synthetic-must-not-paste", prompt=prompt)
            assert mismatch.returncode == 2 and mismatch.stdout == b"FAILED\n"
            result = consumer("deliver", identity=identity, value=b"synthetic-test-password", prompt=prompt)
            assert result.returncode == 0 and result.stdout == b"DELIVERED\n"
            time.sleep(0.1)
            visible = tmux("capture-pane", "-p", "-t", "handoff:0.0")
            assert "synthetic-test-password" not in visible and "synthetic-must-not-paste" not in visible
            buffers = tmux("list-buffers", "-F", "#{buffer_name}")
            assert "agent-framework-" not in buffers
            changed = consumer("deliver", identity=identity, value=b"synthetic-never-retry", prompt=prompt)
            assert changed.returncode == 2 and changed.stdout == b"FAILED\n"
        finally:
            subprocess.run(["tmux", "kill-server"], env=environment, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=8, check=False)
    print("PASS: actual Linux guarded prompt, identity refusal, private delivery, cleanup and stale-target refusal")


if __name__ == "__main__":
    main()
