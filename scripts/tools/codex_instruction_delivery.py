#!/usr/bin/env python3
"""Render and manage an explicit framework block in the selected Codex home."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
from typing import Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO_ROOT))

from repo_platform.tooling.codex_thread_overlay import default_backup_root, default_codex_root
from repo_platform.tooling.tool_handshake import (
    ToolSelector, ToolVerb, argparse_command_details, emit_payload,
    tool_result_payload, tool_usage_payload,
)

TOOL_ID = "tool.ops.codex_instruction_delivery"
ENTRYPOINT = "python scripts/tools/codex_instruction_delivery.py"
SOURCE_PATH = Path("docs/instructions/framework/README.md")
START = "<!-- codex-home-kernel:start -->"
END = "<!-- codex-home-kernel:end -->"
MANAGED_START = b"<!-- agent-framework:managed:start -->"
MANAGED_END = b"<!-- agent-framework:managed:end -->"
STATE_FILENAME = "agent-framework-install.json"
TOKENS = ("REPO_ROOT", "CODEX_HOME", "BACKUP_ROOT", "PYTHON_COMMAND")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command")
    usage = commands.add_parser("usage", help="Return zero-effect invocation information.")
    usage.add_argument("--format", choices=("text", "json"), default="text")
    for name in ("preview", "install", "update", "remove"):
        command = commands.add_parser(name, help={
            "preview": "Preview the resulting home file without writing anything.",
            "install": "Add one managed block, preserving existing home guidance.",
            "update": "Replace only an unchanged managed block from its authored source.",
            "remove": "Remove only the installed unchanged managed block and its metadata.",
        }[name])
        command.add_argument("--format", choices=("text", "json"), default="text")
        command.add_argument("--source-root", default=str(REPO_ROOT), help="Checkout containing the authored kernel.")
        command.add_argument("--repo-root", help="Absolute maintained framework checkout; defaults to source root.")
        command.add_argument("--codex-root", help="Absolute Codex home; defaults to CODEX_HOME, then ~/.codex.")
        command.add_argument("--backup-root", help="Absolute continuity storage; defaults to AGENT_FRAMEWORK_STATE_HOME or platform user state.")
        if name == "preview":
            command.add_argument("--operation", choices=("install", "update", "remove"), default="install")
    return result


def usage_payload(command_parser: argparse.ArgumentParser) -> dict:
    return tool_usage_payload(
        tool_id=TOOL_ID,
        purpose="Preview, install, update or remove the framework's managed home-guidance block while preserving unrelated guidance and configuration.",
        entrypoint=ENTRYPOINT,
        safe_first_calls=[{"label": "Usage", "cmd": f"{ENTRYPOINT} usage --format json"}, {"label": "Preview", "cmd": f"{ENTRYPOINT} preview --format json"}],
        verbs=[ToolVerb("usage", "none", "Describe invocation without reading installation/configuration state."),
               ToolVerb("preview", "none", "Read source and destination; return a candidate without writing."),
               ToolVerb("install", "writes selected local Codex home", "Append a managed block and installation metadata; preserve unrelated bytes."),
               ToolVerb("update", "writes selected local Codex home", "Update the unchanged managed block; refuse edits inside it."),
               ToolVerb("remove", "writes selected local Codex home", "Remove the unchanged block and its installation metadata; leave other guidance and all continuity data.")],
        selectors=[ToolSelector("--source-root", False, "Source checkout; defaults to this script's checkout."),
                   ToolSelector("--repo-root", False, "Maintained framework checkout used for owner/tool links."),
                   ToolSelector("--codex-root", False, "Selected Codex profile home."),
                   ToolSelector("--backup-root", False, "Selected private continuity storage root."),
                   ToolSelector("--operation", False, "Preview only: install, update, or remove.")],
        output_formats=["text", "json"],
        side_effects={"writes_repo": False, "writes_paths": ["<codex-root>/AGENTS.md", "<codex-root>/agent-framework-install.json", "<codex-root>/.agent-framework-install.lock (temporary)"], "modifies_remote": False, "network_access": "none", "danger_level": "low", "supports_dry_run": True, "idempotent": True},
        examples=[{"label": "Preview selected profile", "cmd": f'{ENTRYPOINT} preview --codex-root "<absolute profile home>" --format json'},
                  {"label": "Install reviewed defaults", "cmd": f"{ENTRYPOINT} install"},
                  {"label": "Preview removal", "cmd": f"{ENTRYPOINT} preview --operation remove"}],
        docs=["docs/setup.md", "docs/ops/codex_instruction_delivery.md"],
        errors=[{"code": "instruction_delivery_failed", "message": "Exit 2: malformed source, unsafe destination, shadowing override, conflicting installation, or edited managed block. Nothing is intentionally overwritten outside the owned block; resolve the reported condition before retrying."}],
        command_details=argparse_command_details(command_parser),
    )


def absolute_binding(raw: str, name: str) -> str:
    if any(character in raw for character in ('\r', '\n', '\x00', '"', '`', '$')):
        raise ValueError(f"{name} must be a single path without control characters, quotes, or Markdown delimiters")
    value = Path(raw).expanduser()
    if not value.is_absolute():
        raise ValueError(f"{name} must be an absolute path on this host")
    return value.resolve().as_posix()


def python_command() -> str:
    # Keep the selected venv launcher path: resolving its symlink would bind
    # the base interpreter and silently lose installed environment dependencies.
    executable = os.path.abspath(sys.executable)
    if os.name == "nt":
        return "& '" + executable.replace("'", "''") + "'"
    return shlex.quote(executable)


def render(source_text: str, bindings: dict[str, str]) -> str:
    if source_text.count(START) != 1 or source_text.count(END) != 1:
        raise ValueError("source must contain exactly one start marker and one end marker")
    start = source_text.index(START) + len(START)
    end = source_text.index(END)
    if end < start:
        raise ValueError("source markers are reversed")
    body = source_text[start:end].strip()
    if not body:
        raise ValueError("authored kernel is empty")
    if set(re.findall(r"\{\{([^{}]+)\}\}", body)) != set(TOKENS):
        raise ValueError("authored kernel must use exactly REPO_ROOT, CODEX_HOME, BACKUP_ROOT and PYTHON_COMMAND placeholders")
    for token in TOKENS:
        body = body.replace("{{" + token + "}}", bindings[token])
    if "{{" in body or "}}" in body:
        raise ValueError("candidate contains an unresolved placeholder")
    return body + "\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_optional(path: Path) -> bytes | None:
    if path.is_symlink():
        raise ValueError(f"refusing a symlink at {path}")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"expected a regular file at {path}")
        return path.read_bytes()
    return None


def build_plan(args: argparse.Namespace) -> dict:
    operation = args.operation if args.command == "preview" else args.command
    home = Path(absolute_binding(args.codex_root or str(default_codex_root()), "--codex-root"))
    target = home / "AGENTS.md"
    state_path = home / STATE_FILENAME
    original = read_optional(target)
    state_bytes = read_optional(state_path)
    (original or b"").decode("utf-8-sig")  # refuse non-UTF-8; retain original bytes
    state = json.loads(state_bytes) if state_bytes is not None else None
    if state is not None and (not isinstance(state, dict) or state.get("version") != 1):
        raise ValueError("unrecognized installation metadata; preserve it and resolve manually")
    existing = original or b""
    block_begin = block_end = 0
    prefix = b""
    if state is not None:
        if existing.count(MANAGED_START) != 1 or existing.count(MANAGED_END) != 1:
            raise ValueError("managed markers are missing or duplicated; refusing to replace user guidance")
        block_begin = existing.index(MANAGED_START)
        block_end = existing.index(MANAGED_END) + len(MANAGED_END)
        if block_end < block_begin:
            raise ValueError("managed markers are reversed")
        block = existing[block_begin:block_end]
        if digest(block) != state.get("block_sha256"):
            raise ValueError("managed block was edited; move desired personal edits outside its markers before retrying")
        prefix = str(state.get("inserted_prefix", "")).encode("utf-8")
    elif MANAGED_START in existing or MANAGED_END in existing:
        raise ValueError("managed markers exist without owned metadata; refusing to adopt or overwrite them")
    if operation != "remove" and (home / "AGENTS.override.md").exists():
        raise ValueError("AGENTS.override.md shadows AGENTS.md in this Codex home; resolve the override deliberately before installing (it is not modified)")
    if operation == "update" and state is None:
        raise ValueError("no managed installation exists; preview/install first")
    body = ""
    source = None
    bindings = None
    source_hash = None
    new_state = None
    if operation != "remove":
        source = Path(args.source_root).expanduser().resolve() / SOURCE_PATH
        source_text = source.read_text(encoding="utf-8-sig")
        bindings = {
            "REPO_ROOT": absolute_binding(args.repo_root or str(Path(args.source_root).expanduser().resolve()), "--repo-root"),
            "CODEX_HOME": home.as_posix(),
            "BACKUP_ROOT": absolute_binding(args.backup_root or str(default_backup_root()), "--backup-root"),
            "PYTHON_COMMAND": python_command(),
        }
        body = render(source_text, bindings)
        source_hash = digest(source_text.encode("utf-8"))
        new_block = MANAGED_START + b"\n" + body.encode("utf-8") + MANAGED_END
        if state is not None:
            if operation == "install" and new_block != existing[block_begin:block_end]:
                raise ValueError("a different managed installation already exists; preview --operation update, then update")
            candidate = existing[:block_begin] + new_block + existing[block_end:]
        else:
            # The inserted prefix and block have no trailing bytes. Removing
            # them restores the original file exactly, including missing EOF LF.
            prefix = b"\n\n" if existing else b""
            candidate = existing + prefix + new_block
        new_state = {"version": 1, "block_sha256": digest(new_block), "inserted_prefix": prefix.decode(), "target_existed": state.get("target_existed", True) if state else original is not None,
                     "source": source.as_posix(), "source_sha256": source_hash, "bindings": bindings}
    elif state is None:
        candidate = existing
    else:
        start = block_begin
        # Preserve additions outside the block. Remove only the exact separator
        # we inserted if it is still immediately adjacent.
        if prefix and existing[max(0, start-len(prefix)):start] == prefix:
            start -= len(prefix)
        candidate = existing[:start] + existing[block_end:]
    delete_target = operation == "remove" and state is not None and not state.get("target_existed", True) and not candidate
    return {"operation": operation, "home": home, "target": target, "state_path": state_path,
            "original": original, "state_bytes": state_bytes, "candidate": candidate,
            "new_state": new_state, "delete_target": delete_target, "source": source,
            "source_sha256": source_hash, "bindings": bindings, "body": body,
            "changed": candidate != existing or delete_target or ((new_state is None) != (state is None))}


def atomic_write(path: Path, data: bytes) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def installation_lock(home: Path) -> Iterator[None]:
    home.mkdir(parents=True, exist_ok=True)
    lock = home / ".agent-framework-install.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"installation lock exists at {lock}; another install may be running. If a prior process crashed, verify it stopped before removing that exact lock") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def apply_plan(plan: dict) -> None:
    target, state_path = plan["target"], plan["state_path"]
    if read_optional(target) != plan["original"] or read_optional(state_path) != plan["state_bytes"]:
        raise ValueError("home guidance changed during preparation; nothing changed, preview again")
    if plan["delete_target"]:
        target.unlink()
    elif plan["candidate"] != (plan["original"] or b""):
        atomic_write(target, plan["candidate"])
    try:
        if plan["new_state"] is None:
            state_path.unlink(missing_ok=True)
        else:
            atomic_write(state_path, (json.dumps(plan["new_state"], indent=2) + "\n").encode("utf-8"))
    except OSError:
        # Recover ordinary metadata-write failures without disturbing a newer edit.
        if read_optional(target) in (plan["candidate"], None):
            if plan["original"] is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write(target, plan["original"])
        raise


def result_payload(args: argparse.Namespace, plan: dict) -> dict:
    is_preview = args.command == "preview"
    return tool_result_payload(
        tool_id=TOOL_ID, verb=args.command,
        summary=("Preview only; no files changed." if is_preview else f"Home-guidance {plan['operation']} complete; unrelated guidance and configuration preserved."),
        data={"operation": plan["operation"], "intended_home_file": plan["target"].as_posix(),
              "source": plan["source"].as_posix() if plan["source"] else None,
              "source_sha256": plan["source_sha256"], "bindings": plan["bindings"],
              "candidate_utf8_bytes": len(plan["candidate"]), "candidate_sha256": digest(plan["candidate"]),
              "candidate_text": plan["candidate"].decode("utf-8-sig") if is_preview else None,
              "changed": plan["changed"], "installed": not is_preview and plan["operation"] != "remove"},
        warnings=["File installation does not prove the selected client loaded or followed the guidance. Start a fresh task and check actual instruction discovery."],
    )


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    command_parser = parser()
    args = command_parser.parse_args(argv)
    output_format = getattr(args, "format", "text")
    if args.command in (None, "usage"):
        emit_payload(usage_payload(command_parser), output_format=output_format, stream=sys.stdout)
        return 0
    try:
        plan = build_plan(args)
        if args.command != "preview":
            with installation_lock(plan["home"]):
                apply_plan(plan)
        payload = result_payload(args, plan)
    except (OSError, UnicodeError, ValueError) as exc:
        payload = tool_result_payload(tool_id=TOOL_ID, verb=args.command, summary="Instruction delivery could not complete.", status="error", errors=[{"code": "instruction_delivery_failed", "message": str(exc)}])
        emit_payload(payload, output_format=output_format, stream=sys.stdout)
        return 2
    emit_payload(payload, output_format=output_format, stream=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
