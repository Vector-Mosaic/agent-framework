from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "tools" / "codex_thread_sketchpad.py"
THREAD_ID = "11111111-1111-4111-8111-111111111111"


def _write_session_index(codex_root: Path) -> None:
    codex_root.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": THREAD_ID,
        "thread_name": "Sketchpad check window test",
        "updated_at": "2026-08-19T12:00:00Z",
    }
    (codex_root / "session_index.jsonl").write_text(
        json.dumps(entry) + "\n",
        encoding="utf-8",
    )


def _run_tool(
    codex_root: Path,
    backup_root: Path,
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for key in ("CODEX_THREAD_ID", "CODEX_THREAD_UUID", "OPENAI_CODEX_THREAD_ID"):
        env.pop(key, None)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--codex-root",
            str(codex_root),
            "--backup-root",
            str(backup_root),
            *args,
        ],
        cwd=REPO_ROOT,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _run_returned_command(command: list[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for key in ("CODEX_THREAD_ID", "CODEX_THREAD_UUID", "OPENAI_CODEX_THREAD_ID"):
        env.pop(key, None)
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _observed_basis(codex_root: Path, backup_root: Path) -> list[str]:
    # Serial fixture actions explicitly observe before preparing their next edit.
    # Stale/competing behavior is tested with retained tokens separately.
    result = _run_tool(codex_root, backup_root, "status", "--thread-id", THREAD_ID, "--json")
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    return ["--expected", status["mutation_token"]] if status["exists"] else ["--expect-absent"]


def _activate(codex_root: Path, backup_root: Path, body: str) -> Path:
    activated = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "activate",
        "--thread-id",
        THREAD_ID,
        "--text",
        body, "--expect-absent",
    )
    assert activated.returncode == 0, activated.stderr
    return backup_root / "thread_sketchpads" / THREAD_ID / "sketchpad.md"


def test_activate_empty_body_stays_empty_and_pinned(tmp_path: Path) -> None:
    for input_mode in ("default", "text", "stdin", "file"):
        _check_empty_activation(tmp_path / input_mode, input_mode)


def _check_empty_activation(tmp_path: Path, input_mode: str) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    empty_file = tmp_path / "empty.md"
    empty_file.write_text("", encoding="utf-8")
    body_args = {
        "default": (),
        "text": ("--text", ""),
        "stdin": ("--stdin",),
        "file": ("--body-file", str(empty_file)),
    }[input_mode]

    activated = _run_tool(codex_root, backup_root, "activate", "--thread-id", THREAD_ID,
                          "--expect-absent", "--json", *body_args, input_text="")
    assert activated.returncode == 0, activated.stderr
    sketchpad_path = backup_root / "thread_sketchpads" / THREAD_ID / "sketchpad.md"
    assert sketchpad_path.read_bytes() == b""

    for verb, content_key in (("read", "body"), ("check", "excerpt")):
        result = _run_tool(codex_root, backup_root, verb, "--thread-id", THREAD_ID, "--json")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["active"] is True
        assert payload["pinned"] is True
        assert payload["consistency"] == "consistent"
        assert payload["source"]["bytes"] == 0
        assert payload[content_key] == ""


def test_activate_without_ideas_preserves_existing_body(tmp_path: Path) -> None:
    for index, body_args in enumerate(((), ("--text", ""))):
        _check_preserving_activation(tmp_path / str(index), body_args)


def _check_preserving_activation(tmp_path: Path, body_args: tuple[str, ...]) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    body = "An unresolved idea the user asked to retain.\n"
    sketchpad_path = _activate(codex_root, backup_root, body)
    original_bytes = sketchpad_path.read_bytes()
    unpinned = _run_tool(codex_root, backup_root, "unpin", "--thread-id", THREAD_ID,
                         *_observed_basis(codex_root, backup_root))
    assert unpinned.returncode == 0, unpinned.stderr

    activated = _run_tool(codex_root, backup_root, "activate", "--thread-id", THREAD_ID,
                          *_observed_basis(codex_root, backup_root), "--json", *body_args)
    assert activated.returncode == 0, activated.stderr
    assert json.loads(activated.stdout)["metadata"]["pinned"] is True
    assert sketchpad_path.read_bytes() == original_bytes


def test_explicit_ideas_replace_body_and_write_can_clear_it(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    sketchpad_path = _activate(codex_root, backup_root, "Initial idea.\n")
    replacement = "# Open idea\n\nKeep both alternatives unresolved.\n"
    activated = _run_tool(codex_root, backup_root, "activate", "--thread-id", THREAD_ID,
                          *_observed_basis(codex_root, backup_root), "--text", replacement)
    assert activated.returncode == 0, activated.stderr
    assert sketchpad_path.read_text(encoding="utf-8") == replacement

    cleared = _run_tool(codex_root, backup_root, "write", "--thread-id", THREAD_ID,
                        *_observed_basis(codex_root, backup_root), "--text", "", "--json")
    assert cleared.returncode == 0, cleared.stderr
    assert sketchpad_path.read_bytes() == b""
    assert json.loads(cleared.stdout)["metadata"]["pinned"] is True


def test_legacy_options_keep_only_supplied_ideas(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    activated = _run_tool(codex_root, backup_root, "activate", "--thread-id", THREAD_ID,
                          "--expect-absent", "--main-goal", "A selected idea.",
                          "--non-goal", "Its unresolved boundary.")
    assert activated.returncode == 0, activated.stderr
    sketchpad_path = backup_root / "thread_sketchpads" / THREAD_ID / "sketchpad.md"
    assert sketchpad_path.read_text(encoding="utf-8") == "A selected idea.\n\nIts unresolved boundary.\n"


def test_check_reports_head_offset_tail_and_full_read(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "Framework State"
    _write_session_index(codex_root)
    body = "alpha-😀-beta\nsecond line\nfinal line\n"
    sketchpad_path = _activate(codex_root, backup_root, body)
    original_bytes = sketchpad_path.read_bytes()

    head = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "check",
        "--thread-id",
        THREAD_ID,
        "--max-chars",
        "8",
    )
    assert head.returncode == 0, head.stderr
    payload = json.loads(head.stdout)
    assert payload["excerpt"] == body[:8]
    commands = payload["excerpt_meta"]["commands"]
    for command in (commands["next"], commands["tail"], commands["full"]):
        assert command[:2] == [sys.executable, str(SCRIPT)]
        assert command[command.index("--codex-root") + 1] == str(codex_root)
        assert command[command.index("--backup-root") + 1] == str(backup_root)
    assert payload["excerpt_meta"] == {
        "commands": commands,
        "mode": "head",
        "next_offset": 8,
        "range": {
            "end_char_exclusive": 8,
            "returned_chars": 8,
            "start_char": 0,
        },
        "total_chars": len(body),
        "total_lines": len(body.splitlines()),
        "truncated": True,
        "truncated_after": True,
        "truncated_before": False,
    }

    returned_next = _run_returned_command(commands["next"], cwd=tmp_path)
    assert returned_next.returncode == 0, returned_next.stderr
    assert json.loads(returned_next.stdout)["excerpt"] == body[8:16]

    returned_full = _run_returned_command(commands["full"], cwd=tmp_path)
    assert returned_full.returncode == 0, returned_full.stderr
    assert json.loads(returned_full.stdout)["body"] == body

    offset = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "check",
        "--thread-id",
        THREAD_ID,
        "--max-chars",
        "8",
        "--offset",
        "8",
    )
    assert offset.returncode == 0, offset.stderr
    offset_payload = json.loads(offset.stdout)
    assert offset_payload["excerpt"] == body[8:16]
    assert offset_payload["excerpt_meta"]["mode"] == "offset"
    assert offset_payload["excerpt_meta"]["range"]["start_char"] == 8
    assert offset_payload["excerpt_meta"]["truncated_before"] is True

    tail = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "check",
        "--thread-id",
        THREAD_ID,
        "--max-chars",
        "8",
        "--tail",
    )
    assert tail.returncode == 0, tail.stderr
    tail_payload = json.loads(tail.stdout)
    assert tail_payload["excerpt"] == body[-8:]
    assert tail_payload["excerpt_meta"]["mode"] == "tail"
    assert tail_payload["excerpt_meta"]["next_offset"] is None
    assert tail_payload["excerpt_meta"]["commands"]["tail"] is None

    full = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "read",
        "--thread-id",
        THREAD_ID,
    )
    assert full.returncode == 0, full.stderr
    assert json.loads(full.stdout)["body"] == body
    assert sketchpad_path.read_bytes() == original_bytes


def test_check_format_json_and_text_emit_excerpt_once(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    body = "unique-window-marker\nmore content\n"
    _activate(codex_root, backup_root, body)

    formatted = _run_tool(
        codex_root,
        backup_root,
        "check",
        "--thread-id",
        THREAD_ID,
        "--max-chars",
        str(len(body)),
        "--format",
        "json",
    )
    assert formatted.returncode == 0, formatted.stderr
    assert json.loads(formatted.stdout)["excerpt"] == body

    text_result = _run_tool(
        codex_root,
        backup_root,
        "check",
        "--thread-id",
        THREAD_ID,
        "--max-chars",
        str(len(body)),
    )
    assert text_result.returncode == 0, text_result.stderr
    assert text_result.stdout.count("unique-window-marker") == 1
    assert "excerpt_meta:" in text_result.stdout
    assert "excerpt: unique-window-marker" in text_result.stdout


def test_check_default_remains_first_4000_characters(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    body = ("x" * 4000) + "tail"
    _activate(codex_root, backup_root, body)

    checked = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "check",
        "--thread-id",
        THREAD_ID,
    )
    assert checked.returncode == 0, checked.stderr
    payload = json.loads(checked.stdout)
    assert payload["excerpt"] == body[:4000]
    assert payload["excerpt_meta"]["range"]["returned_chars"] == 4000
    assert payload["excerpt_meta"]["next_offset"] == 4000


def test_check_rejects_invalid_windows_and_hides_inactive_content(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    body = "bounded body"
    sketchpad_path = _activate(codex_root, backup_root, body)
    stored_chars = len(sketchpad_path.read_text(encoding="utf-8"))

    eof = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "check",
        "--thread-id",
        THREAD_ID,
        "--offset",
        str(stored_chars),
    )
    assert eof.returncode == 0, eof.stderr
    eof_payload = json.loads(eof.stdout)
    assert eof_payload["excerpt"] == ""
    assert eof_payload["excerpt_meta"]["range"]["start_char"] == stored_chars
    assert eof_payload["excerpt_meta"]["next_offset"] is None

    for args in (
        ("--max-chars", "0"),
        ("--offset", "-1"),
        ("--offset", str(stored_chars + 1)),
        ("--offset", "0", "--tail"),
    ):
        result = _run_tool(
            codex_root,
            backup_root,
            "check",
            "--thread-id",
            THREAD_ID,
            *args,
        )
        assert result.returncode != 0

    unpinned = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "unpin",
        "--thread-id",
        THREAD_ID, *_observed_basis(codex_root, backup_root),
    )
    assert unpinned.returncode == 0, unpinned.stderr
    inactive = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "check",
        "--thread-id",
        THREAD_ID,
    )
    assert inactive.returncode == 0, inactive.stderr
    payload = json.loads(inactive.stdout)
    assert payload["active"] is False
    assert payload["excerpt"] == ""
    assert payload["excerpt_meta"]["mode"] == "inactive"
    assert payload["excerpt_meta"]["total_chars"] is None
    assert payload["excerpt_meta"]["commands"] == {
        "next": None,
        "tail": None,
        "full": None,
    }
