from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "tools" / "codex_thread_todo.py"
THREAD_ID = "22222222-2222-4222-8222-222222222222"


def _write_session_index(codex_root: Path) -> None:
    codex_root.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": THREAD_ID,
        "thread_name": "Forward-looking todo test",
        "updated_at": "2026-07-27T12:00:00Z",
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


def _activate(codex_root: Path, backup_root: Path) -> None:
    result = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "activate",
        "--latest-thread",
    )
    assert result.returncode == 0, result.stderr


def _todo_path(backup_root: Path) -> Path:
    return backup_root / "thread_todos" / THREAD_ID / "todo.md"


def test_usage_contract_declares_forward_looking_lifecycle(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    result = _run_tool(
        codex_root,
        backup_root,
        "--format",
        "json",
        "usage",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "forward-looking" in payload["purpose"]
    assert payload["docs"] == [
        "AGENTS.md",
        "docs/instructions/agent_workflow/README.md",
    ]
    complete = next(verb for verb in payload["verbs"] if verb["name"] == "complete")
    assert "remove the item from active state" in complete["summary"]


def test_complete_returns_receipt_and_removes_item(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)

    first = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "add",
        "--latest-thread",
        "--text",
        "Finish the active correction.",
    )
    second = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "add",
        "--latest-thread",
        "--section",
        "next",
        "--text",
        "Continue with remaining work.",
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr

    completed = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "complete",
        "--latest-thread",
        "--id",
        "T001",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["removed_from_active"] is True
    assert payload["item"] == {
        "section": "now",
        "checked": True,
        "id": "T001",
        "text": "Finish the active correction.",
    }

    readback = _run_tool(codex_root, backup_root, "read", "--latest-thread")
    assert readback.returncode == 0, readback.stderr
    assert "T001" not in readback.stdout
    assert "T002" in readback.stdout
    assert "## Done" not in readback.stdout
    assert "## Completed" not in readback.stdout

    reopen_removed = _run_tool(
        codex_root,
        backup_root,
        "reopen",
        "--latest-thread",
        "--id",
        "T001",
    )
    assert reopen_removed.returncode != 0
    assert "no to-do item T001 exists" in reopen_removed.stderr


def test_legacy_completed_content_is_hidden_and_purged_on_write(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)

    legacy_body = """# Legacy To-Do

## Now

- [ ] T001: Current action.
- [x] T002: Checked action from old state.

## Completed

- [x] T900: Legacy completed action.
- [x] T901: Another legacy completed action.

## Parked

- [ ] T010: Important parked work.
"""
    _todo_path(backup_root).write_text(legacy_body, encoding="utf-8")

    checked = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "check",
        "--latest-thread",
        "--max-chars",
        "800",
    )
    assert checked.returncode == 0, checked.stderr
    excerpt = json.loads(checked.stdout)["excerpt"]
    assert "Current action." in excerpt
    assert "Important parked work." in excerpt
    assert "Checked action from old state." not in excerpt
    assert "Legacy completed action." not in excerpt
    assert "## Completed" not in excerpt

    reopened = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "reopen",
        "--latest-thread",
        "--id",
        "T900",
        "--section",
        "next",
    )
    assert reopened.returncode == 0, reopened.stderr
    assert json.loads(reopened.stdout)["item"]["section"] == "next"

    stored = _todo_path(backup_root).read_text(encoding="utf-8")
    assert "T900" in stored
    assert "T901" not in stored
    assert "T002" not in stored
    assert "## Done" not in stored
    assert "## Completed" not in stored

    _todo_path(backup_root).write_text(
        f"{stored.rstrip()}\n\n## Done\n\n- [x] T902: Reintroduced legacy history.\n",
        encoding="utf-8",
    )
    appended = _run_tool(
        codex_root,
        backup_root,
        "append",
        "--latest-thread",
        "--text",
        "Current note.",
    )
    assert appended.returncode == 0, appended.stderr
    appended_body = _todo_path(backup_root).read_text(encoding="utf-8")
    assert "Current note." in appended_body
    assert "T902" not in appended_body
    assert "## Done" not in appended_body

    rejected_note = _run_tool(
        codex_root,
        backup_root,
        "append",
        "--latest-thread",
        "--text",
        "## Completed\n\n- [x] T903: Invalid appended history.",
    )
    assert rejected_note.returncode != 0
    assert "append note contains completed action history" in rejected_note.stderr
    assert "T903" not in _todo_path(backup_root).read_text(encoding="utf-8")

    rejected_bare_note = _run_tool(
        codex_root,
        backup_root,
        "append",
        "--latest-thread",
        "--text",
        "- [x] Bare completed note item.",
    )
    assert rejected_bare_note.returncode != 0
    assert "append note contains completed action history" in rejected_bare_note.stderr
    assert "Bare completed note item." not in _todo_path(backup_root).read_text(
        encoding="utf-8"
    )

    fenced_note = _run_tool(
        codex_root,
        backup_root,
        "append",
        "--latest-thread",
        "--text",
        "```markdown\n## Done\n- [x] Illustrative only.\n```",
    )
    assert fenced_note.returncode == 0, fenced_note.stderr
    fenced_body = _todo_path(backup_root).read_text(encoding="utf-8")
    assert "## Done" in fenced_body
    assert "Illustrative only." in fenced_body


def test_full_body_write_cannot_reintroduce_completion_history(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)

    proposed_body = """# Current Work

## Now
    - [x] Indented code example.
    continuation inside indented code.

- [ ] Active item without a generated id.
- [x] Completed item without a generated id.
  - [x] Nested completed item.
  Continuation belonging to the completed item.
* [x] Completed star item.
Lazy continuation belonging to the completed star item.
  + [ ] Nested unfinished child of completed star item.
- [x] Completed item before nested heading.
### Current Nested Detail
    - [x] Indented code after nested heading.
    continuation inside nested-heading code.

This current nested detail is forward narrative.
- [x] Completed item before block quote.
> Current quoted forward narrative.
+ [ ] Active plus item.

```markdown
## Done
* [x] Illustrative completed item.
```

## Done

- [x] T777: Historical item.

# Forward Narrative

This forward narrative follows the retired completion section.

## Parked

- [ ] Parked item without a generated id.
"""
    written = _run_tool(
        codex_root,
        backup_root,
        "write",
        "--latest-thread",
        "--stdin",
        input_text=proposed_body,
    )
    assert written.returncode == 0, written.stderr

    stored = _todo_path(backup_root).read_text(encoding="utf-8")
    assert "Active item without a generated id." in stored
    assert "Parked item without a generated id." in stored
    assert "Completed item without a generated id." not in stored
    assert "Nested completed item." not in stored
    assert "Continuation belonging to the completed item." not in stored
    assert "Completed star item." not in stored
    assert "Lazy continuation belonging to the completed star item." not in stored
    assert "Nested unfinished child of completed star item." not in stored
    assert "Completed item before nested heading." not in stored
    assert "### Current Nested Detail" in stored
    assert "Indented code after nested heading." in stored
    assert "continuation inside nested-heading code." in stored
    assert "This current nested detail is forward narrative." in stored
    assert "Completed item before block quote." not in stored
    assert "> Current quoted forward narrative." in stored
    assert "Indented code example." in stored
    assert "continuation inside indented code." in stored
    assert "Active plus item." in stored
    assert "Illustrative completed item." in stored
    assert "Historical item." not in stored
    assert "## Completed" not in stored
    assert "# Forward Narrative" in stored
    assert "This forward narrative follows" in stored

    added = _run_tool(
        codex_root,
        backup_root,
        "add",
        "--latest-thread",
        "--text",
        "Generated active item.",
    )
    assert added.returncode == 0, added.stderr
    rewritten = _todo_path(backup_root).read_text(encoding="utf-8")
    assert "Illustrative completed item." in rewritten
    assert "Generated active item." in rewritten
    assert "### Current Nested Detail" in rewritten
    assert "Indented code after nested heading." in rewritten
    assert "continuation inside nested-heading code." in rewritten
    assert "This current nested detail is forward narrative." in rewritten
    assert "> Current quoted forward narrative." in rewritten
    assert "# Forward Narrative" in rewritten
    assert "This forward narrative follows" in rewritten
    assert rewritten.count("## Done") == 1


def test_check_windows_use_sanitized_forward_looking_view(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)

    raw_body = """# Windowed To-Do

## Now

- [ ] T001: Alpha 😀 action.
- [x] T002: Completed action that must stay hidden.

## Done

- [x] T900: Historical action that must stay hidden.

## Parked

- [ ] T010: Final parked action.
"""
    todo_path = _todo_path(backup_root)
    todo_path.write_text(raw_body, encoding="utf-8")
    original_bytes = todo_path.read_bytes()

    full = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "read",
        "--thread-id",
        THREAD_ID,
    )
    assert full.returncode == 0, full.stderr
    effective_body = json.loads(full.stdout)["body"]
    assert "Completed action" not in effective_body
    assert "Historical action" not in effective_body

    head = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "check",
        "--thread-id",
        THREAD_ID,
        "--max-chars",
        "17",
    )
    assert head.returncode == 0, head.stderr
    payload = json.loads(head.stdout)
    assert payload["excerpt"] == effective_body[:17]
    assert payload["excerpt_meta"]["total_chars"] == len(effective_body)
    assert payload["excerpt_meta"]["total_lines"] == len(effective_body.splitlines())
    assert payload["excerpt_meta"]["range"] == {
        "start_char": 0,
        "end_char_exclusive": 17,
        "returned_chars": 17,
    }
    assert payload["excerpt_meta"]["next_offset"] == 17
    next_command = payload["excerpt_meta"]["commands"]["next"]
    assert next_command[next_command.index("--thread-id") + 1] == THREAD_ID
    assert "--latest-thread" not in next_command

    offset = subprocess.run(
        next_command,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert offset.returncode == 0, offset.stderr
    assert json.loads(offset.stdout)["excerpt"] == effective_body[17:34]

    returned_full = subprocess.run(
        payload["excerpt_meta"]["commands"]["full"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert returned_full.returncode == 0, returned_full.stderr
    assert json.loads(returned_full.stdout)["body"] == effective_body

    tail = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "check",
        "--thread-id",
        THREAD_ID,
        "--max-chars",
        "17",
        "--tail",
    )
    assert tail.returncode == 0, tail.stderr
    tail_payload = json.loads(tail.stdout)
    assert tail_payload["excerpt"] == effective_body[-17:]
    assert tail_payload["excerpt_meta"]["truncated_before"] is True
    assert tail_payload["excerpt_meta"]["truncated_after"] is False
    assert todo_path.read_bytes() == original_bytes


def test_check_validates_windows_and_emits_text_once(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "FrameworkState"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)
    body = "unique-todo-window\n"
    _todo_path(backup_root).write_text(body, encoding="utf-8")

    formatted = _run_tool(
        codex_root,
        backup_root,
        "check",
        "--thread-id",
        THREAD_ID,
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
    )
    assert text_result.returncode == 0, text_result.stderr
    assert text_result.stdout.count("unique-todo-window") == 1
    assert "excerpt: unique-todo-window" in text_result.stdout

    for args in (
        ("--max-chars", "0"),
        ("--offset", "-1"),
        ("--offset", str(len(body) + 1)),
        ("--offset", "0", "--tail"),
    ):
        rejected = _run_tool(
            codex_root,
            backup_root,
            "check",
            "--thread-id",
            THREAD_ID,
            *args,
        )
        assert rejected.returncode != 0
