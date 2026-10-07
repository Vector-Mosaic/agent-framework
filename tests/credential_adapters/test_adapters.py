"""Synthetic safeguards: no OS clipboard, real account, network or SSH use."""
from contextlib import contextmanager
import base64
import ctypes
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from repo_platform.credentials import clipboard, custody, terminal, terminal_remote
from scripts.tools import credential_adapters


def curl_args():
    return ["curl", "--disable", "--proto", "=https", "--proto-redir", "=https", "--proxy", "",
            "--noproxy", "*", "--max-redirs", "0", "--user", "example", "--output", "result.dat",
            "https://example.test/data"]


def wget_args():
    return ["wget", "--no-config", "--no-netrc", "--no-proxy", "--max-redirect=0", "--ask-password",
            "--user=example", "--output-document=result.dat", "https://example.test/data"]


@pytest.mark.parametrize("process,arguments", [("curl", curl_args()), ("wget", wget_args())])
def test_guarded_downloaders(process, arguments):
    terminal_remote.downloader_arguments(arguments, process, "https://example.test")


def test_proc_command_preserves_empty_proxy_argument():
    command = b"\0".join(arg.encode() for arg in curl_args()) + b"\0"
    parsed = terminal_remote.command_arguments(command)
    assert parsed == curl_args()
    terminal_remote.downloader_arguments(parsed, "curl", "https://example.test")


@pytest.mark.parametrize("option", ["--insecure", "-k", "--location", "--config=other", "--netrc",
                                   "--header=Authorization: example", "--trace=-", "--resolve=example.test:443:127.0.0.1"])
def test_curl_escape_routes_refused(option):
    with pytest.raises(RuntimeError):
        terminal_remote.downloader_arguments(curl_args() + [option], "curl", "https://example.test")


@pytest.mark.parametrize("option", ["--server-response", "--no-check-certificate", "--use-askpass=other", "--config=other"])
def test_wget_escape_routes_refused(option):
    with pytest.raises(RuntimeError):
        terminal_remote.downloader_arguments(wget_args() + [option], "wget", "https://example.test")


def test_first_config_guard_origin_and_duplicate_override():
    for args in [curl_args()[0:1] + curl_args()[2:], curl_args() + ["https://other.test/data"],
                 curl_args() + ["--proto=all"], curl_args()[:-1] + ["https://user:pass@example.test/data"]]:
        with pytest.raises(RuntimeError):
            terminal_remote.downloader_arguments(args, "curl", "https://example.test")


def test_target_pin_and_no_ambient_ssh_configuration(tmp_path):
    identity = tmp_path / "test_identity"
    identity.write_text("synthetic placeholder, not a private key")
    key_type = b"ssh-ed25519"
    wire = len(key_type).to_bytes(4, "big") + key_type + (32).to_bytes(4, "big") + bytes(32)
    target = {"host": "worker.example.test", "port": 22, "username": "example", "identity_file": str(identity),
              "host_key": "ssh-ed25519 " + base64.b64encode(wire).decode()}
    config = tmp_path / "targets.json"
    config.write_text(json.dumps({"schema_version": 1, "targets": {"fixture": target}}))
    assert terminal.load_target(config, "fixture") == target
    with patch.object(terminal.shutil, "which", return_value="ssh"), terminal.pinned_ssh(target) as command:
        assert command[1:3] == ["-F", "none"]
        assert "StrictHostKeyChecking=yes" in command
        assert "IdentityAgent=none" in command
        assert "GlobalKnownHostsFile=none" in command
        pin_path = Path(shlex.split(next(arg.split("=", 1)[1] for arg in command if arg.startswith("UserKnownHostsFile=")))[0])
        assert target["host_key"] in pin_path.read_text()
    assert not pin_path.exists()
    target["host"] = "-oProxyCommand=bad"
    config.write_text(json.dumps({"schema_version": 1, "targets": {"fixture": target}}))
    with pytest.raises(terminal.TerminalError):
        terminal.load_target(config, "fixture")


@pytest.mark.skipif(shutil.which("ssh") is None, reason="OpenSSH client unavailable")
def test_ssh_configuration_path_with_spaces_without_network(tmp_path):
    root = tmp_path / "Example User"
    root.mkdir()
    original = tempfile.TemporaryDirectory
    target = {"host": "worker.example.test", "port": 22, "username": "example",
              "identity_file": str(root / "synthetic_identity"), "host_key": "ssh-ed25519 synthetic"}
    def with_spaces(**kwargs):
        return original(dir=root, **kwargs)
    with patch.object(terminal.tempfile, "TemporaryDirectory", with_spaces), terminal.pinned_ssh(target) as command:
        result = subprocess.run(command[:-1] + ["-G", command[-1]], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False, timeout=10)
        assert result.returncode == 0
        line = next(line for line in result.stdout.decode().splitlines() if line.startswith("userknownhostsfile "))
        # ssh -G renders paths without shell quoting, so shlex-splitting its
        # output would manufacture multiple files even for a parsed single path.
        rendered_path = line.partition(" ")[2]
        configured = next(arg.split("=", 1)[1] for arg in command if arg.startswith("UserKnownHostsFile="))
        assert configured.startswith('"') and configured.endswith('"')
        assert rendered_path == shlex.split(configured)[0]
        assert "Example User" in rendered_path and Path(rendered_path).is_file()


@contextmanager
def fake_ssh(_target):
    yield ["ssh", "configured-host"]


def run_delivery(results, secret="synthetic-password"):
    selected = SimpleNamespace(credential="password", origin="https://example.test")
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return results.pop(0)
    read = Mock(return_value=secret)
    with patch.object(custody, "refresh", return_value=selected), patch.object(custody, "read_value", read), \
         patch.object(terminal, "load_target", return_value={}), patch.object(terminal, "pinned_ssh", fake_ssh), \
         patch.object(terminal.subprocess, "run", run):
        try:
            result = terminal.deliver(selected, targets_path=Path("unused"), target_id="fixture", pane="download:0.0",
                                       prompt="Password:", process_name="wget")
        except terminal.TerminalError as error:
            result = {"error": error.code}
    return result, read, calls


def test_failed_probe_never_reads_custody():
    result, read, calls = run_delivery([subprocess.CompletedProcess([], 2, b"FAILED\n")])
    assert result == {"error": "terminal_prompt_unverified"}
    read.assert_not_called()
    assert len(calls) == 1 and calls[0][1]["input"] == b""


def test_secret_only_enters_stdin_no_automatic_retry():
    ready = b"READY:%1:345:1234:" + b"a" * 64 + b"\n"
    result, read, calls = run_delivery([subprocess.CompletedProcess([], 0, ready),
                                      subprocess.CompletedProcess([], 2, b"FAILED\n")])
    assert result == {"error": "terminal_delivery_uncertain"}
    assert len(calls) == 2
    assert calls[1][1]["input"] == b"synthetic-password"
    assert "synthetic-password" not in str(calls[1][0])
    read.assert_called_once()


@pytest.mark.parametrize("control", ["\x03", "\x1b", "\x7f"])
def test_terminal_control_characters_never_reach_consumer(control):
    ready = b"READY:%1:345:1234:" + b"a" * 64 + b"\n"
    result, read, calls = run_delivery([subprocess.CompletedProcess([], 0, ready)], "synthetic" + control)
    assert result == {"error": "terminal_credential_incompatible"}
    assert len(calls) == 1


def test_linux_cleanup_never_overwrites_a_replacement():
    backend = object.__new__(clipboard.LinuxClipboard)
    backend._owner = Mock()
    backend._owner.poll.return_value = None
    owner = backend._owner
    assert backend.cleanup("synthetic") == "released_owned_selection"
    owner.terminate.assert_called_once()
    assert backend._owner is None
    assert backend.cleanup("synthetic") == "preserved_unowned"


def test_windows_clipboard_read_is_bounded_before_native_access():
    backend = object.__new__(clipboard.WindowsClipboard)
    backend._user32 = Mock()
    backend._kernel32 = Mock()
    backend._user32.GetClipboardData.return_value = 1
    backend._kernel32.GlobalSize.return_value = 131076
    with pytest.raises(clipboard.ClipboardError):
        backend._read_open()
    backend._kernel32.GlobalLock.assert_not_called()
    raw = "synthetic\0".encode("utf-16-le")
    buffer = ctypes.create_string_buffer(raw)
    backend._kernel32.GlobalSize.return_value = len(raw)
    backend._kernel32.GlobalLock.return_value = ctypes.addressof(buffer)
    assert backend._read_open() == "synthetic"


def test_clipboard_intake_refuses_existing_before_read():
    selected = SimpleNamespace(origin="https://example.test", credential="password")
    @contextmanager
    def store(_selection):
        yield object()
    @contextmanager
    def lock(_path):
        yield
    with patch.object(custody, "refresh", return_value=selected), patch.object(custody, "lock_path", return_value=Path("unused")), \
         patch.object(custody, "locked", lock), patch.object(custody, "store_for", store), \
         patch.object(custody, "read_payload", return_value="existing"), patch.object(clipboard, "open_clipboard") as backend:
        with pytest.raises(custody.CustodyError, match="private credential") as error:
            clipboard.intake(selected, backend="x11")
    assert error.value.code == "credentials_already_provisioned"
    backend.assert_not_called()


def test_usage_is_offline_and_schema_shaped():
    args = credential_adapters.parser().parse_args(["usage", "--format", "json"])
    with patch.object(custody, "resolve_selection", side_effect=AssertionError("must stay offline")):
        result = credential_adapters.perform(args)
    assert result["schema_version"] == "tool_usage.v1"
    assert {row["name"] for row in result["command_details"]} == set(credential_adapters.USAGE["commands"])
