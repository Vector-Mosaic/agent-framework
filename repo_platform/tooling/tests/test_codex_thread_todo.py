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


def _observed_basis(codex_root: Path, backup_root: Path) -> list[str]:
    # Serial fixture actions explicitly observe before preparing their next edit.
    # Stale/competing behavior is tested with retained tokens separately.
    result = _run_tool(codex_root, backup_root, "status", "--thread-id", THREAD_ID, "--json")
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    return ["--expected", status["mutation_token"]] if status["exists"] else ["--expect-absent"]


def _activate(codex_root: Path, backup_root: Path) -> None:
    result = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "activate",
        "--latest-thread", "--expect-absent",
    )
    assert result.returncode == 0, result.stderr


def _todo_path(backup_root: Path) -> Path:
    return backup_root / "thread_todos" / THREAD_ID / "todo.md"


def test_usage_contract_declares_forward_looking_lifecycle(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "CodexStateBackups"
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
    assert "append" not in {verb["name"] for verb in payload["verbs"]}
    assert "append" not in {command["name"] for command in payload["command_details"]}


def test_default_and_clear_have_no_body_bookkeeping(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)
    expected = "# Thread To-Do List\n\n## Now\n\n## Next\n\n## Parked\n"
    assert _todo_path(backup_root).read_text() == expected
    added = _run_tool(codex_root, backup_root, "add", "--latest-thread", "--text", "Pending discussion.",
                      *_observed_basis(codex_root, backup_root))
    assert added.returncode == 0, added.stderr
    cleared = _run_tool(codex_root, backup_root, "clear", "--latest-thread",
                        *_observed_basis(codex_root, backup_root))
    assert cleared.returncode == 0, cleared.stderr
    assert _todo_path(backup_root).read_text() == expected
    metadata = json.loads((_todo_path(backup_root).parent / "metadata.json").read_text())
    assert metadata["thread_id"] == THREAD_ID
    assert metadata["pinned"] is True
    assert metadata["body_revision"].startswith("sha256:")
    assert metadata["created_at"]


def test_empty_activation_preserves_existing_body_and_pins(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    body = "# Work\n\n## Now\n\nDiscussion still needed.\n\n## Next\n\n## Parked\n"
    created = _run_tool(codex_root, backup_root, "activate", "--latest-thread", "--expect-absent",
                        "--no-pin", "--text", body)
    assert created.returncode == 0, created.stderr
    for arguments in ([], ["--text", ""], ["--text", "   "]):
        activated = _run_tool(codex_root, backup_root, "activate", "--latest-thread",
                              *arguments, *_observed_basis(codex_root, backup_root))
        assert activated.returncode == 0, activated.stderr
        assert _todo_path(backup_root).read_text() == body
    status = _run_tool(codex_root, backup_root, "status", "--latest-thread", "--json")
    assert json.loads(status.stdout)["pinned"] is True


def test_item_updates_preserve_order_nesting_and_discussion(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    body = """# Work

## Now

Discuss the deployment date: 2026-11-15.

- [ ] T001: Parent action.
  ## Notes for this unfinished action
  Attached parent detail.

  - [ ] T002: Nested action.
    Nested detail.
    ```markdown
    ## Notes
    - [x] T888: Example inside a nested fence.
    ```
  - [ ] T003: Remaining child.
    Keep this child detail.
- [ ] T004: Next sibling.
  Keep this sibling detail.

### Open decision

Discuss this separately after the work above.

## Next

Keep this next discussion.

## Parked
"""
    written = _run_tool(codex_root, backup_root, "write", "--latest-thread", "--expect-absent", "--text", body)
    assert written.returncode == 0, written.stderr
    completed = _run_tool(codex_root, backup_root, "complete", "--latest-thread", "--id", "T002",
                          *_observed_basis(codex_root, backup_root))
    assert completed.returncode == 0, completed.stderr
    expected = body.replace("""  - [ ] T002: Nested action.
    Nested detail.
    ```markdown
    ## Notes
    - [x] T888: Example inside a nested fence.
    ```
""", "")
    assert _todo_path(backup_root).read_text() == expected
    added = _run_tool(codex_root, backup_root, "add", "--latest-thread", "--text", "New action.",
                      *_observed_basis(codex_root, backup_root))
    assert added.returncode == 0, added.stderr
    current = _todo_path(backup_root).read_text()
    assert current.index("Parent action") < current.index("Remaining child") < current.index("Next sibling")
    assert "  - [ ] T003: Remaining child.\n    Keep this child detail." in current
    assert "  ## Notes for this unfinished action\n  Attached parent detail." in current
    assert "Discuss the deployment date: 2026-11-15." in current
    assert current.index("New action.") < current.index("### Open decision")
    refused = _run_tool(codex_root, backup_root, "complete", "--latest-thread", "--id", "T001",
                        *_observed_basis(codex_root, backup_root))
    assert refused.returncode == 1
    assert "todo_reconciliation_required" in refused.stderr
    assert _todo_path(backup_root).read_text() == current
    moved_child = _run_tool(codex_root, backup_root, "reopen", "--latest-thread", "--id", "T003", "--section", "next",
                            *_observed_basis(codex_root, backup_root))
    assert moved_child.returncode == 0, moved_child.stderr
    completed_parent = _run_tool(codex_root, backup_root, "complete", "--latest-thread", "--id", "T001",
                                 *_observed_basis(codex_root, backup_root))
    assert completed_parent.returncode == 0, completed_parent.stderr
    current = _todo_path(backup_root).read_text()
    assert "Parent action" not in current
    assert "Attached parent detail" not in current
    assert "Notes for this unfinished action" not in current
    assert "Remaining child" in current
    assert "Keep this child detail" in current
    assert current.index("Remaining child") > current.index("## Next")
    assert "Next sibling.\n  Keep this sibling detail." in current
    assert "Discuss this separately" in current


def test_reopen_moves_the_whole_item_and_preserves_literal_ids(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    body = """## Now

- [ ] T001: Parent.
  - [ ] T002: Move this child.
    Attached detail.
    - [ ] T003: Descendant.
  - [ ] T004: Keep this child.

```markdown
- [ ] T002: Literal duplicate must stay untouched.
```

## Next

## Parked
"""
    written = _run_tool(codex_root, backup_root, "write", "--latest-thread", "--expect-absent", "--text", body)
    assert written.returncode == 0, written.stderr
    moved = _run_tool(codex_root, backup_root, "reopen", "--latest-thread", "--id", "T002", "--section", "parked",
                      *_observed_basis(codex_root, backup_root))
    assert moved.returncode == 0, moved.stderr
    current = _todo_path(backup_root).read_text()
    assert "- [ ] T001: Parent.\n  - [ ] T004: Keep this child." in current
    assert "```markdown\n- [ ] T002: Literal duplicate must stay untouched.\n```" in current
    assert "## Parked\n\n- [ ] T002: Move this child.\n  Attached detail.\n  - [ ] T003: Descendant.\n" in current


def test_generated_legacy_bookkeeping_is_removed_but_substantive_sections_require_write(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)
    body = """# Old list

## Now

- [ ] T001: Keep this action.

## Next

- (none yet)

## Parked

- (none yet)

## Notes

- Keep only current action order here; route completion history to its owning report, checkpoint, or receipt.

## Metadata

- thread_id: `22222222-2222-4222-8222-222222222222`
- thread_title: Old list
- created_at: 2026-11-01T00:00:00Z
- updated_at: 2026-11-02T00:00:00Z
- storage: local-only configured continuity root / thread_todos
- authority: thread-local action overlay, not repo truth
"""
    # Emulate existing, pre-revision legacy storage without going through write.
    path = _todo_path(backup_root)
    path.write_text(body)
    metadata_path = path.parent / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata.pop("body_revision")
    metadata_path.write_text(json.dumps(metadata))
    added = _run_tool(codex_root, backup_root, "add", "--latest-thread", "--text", "New work.",
                      *_observed_basis(codex_root, backup_root))
    assert added.returncode == 0, added.stderr
    current = path.read_text()
    assert "Keep this action." in current
    assert "New work." in current
    for removed in ("## Notes", "## Metadata", "none yet", "created_at", "updated_at"):
        assert removed not in current

    for heading in ("Notes", "Metadata", "Note 2026-11-01T00:00:00Z", "Unresolved choices"):
        legacy = current + f"\n## {heading}\n\nImportant unfinished discussion.\n"
        path.write_text(legacy)
        metadata = json.loads(metadata_path.read_text())
        metadata.pop("body_revision", None)
        metadata_path.write_text(json.dumps(metadata))
        basis = _observed_basis(codex_root, backup_root)
        readback = _run_tool(codex_root, backup_root, "read", "--latest-thread")
        assert "Important unfinished discussion." in readback.stdout
        rejected = _run_tool(codex_root, backup_root, "add", "--latest-thread", "--text", "New action.", *basis)
        assert rejected.returncode == 1
        assert "todo_reconciliation_required" in rejected.stderr
        assert path.read_text() == legacy
        reconciled = current.replace("## Parked", "## Parked\n\nImportant unfinished discussion.")
        written = _run_tool(codex_root, backup_root, "write", "--latest-thread", "--text", reconciled, *basis)
        assert written.returncode == 0, written.stderr
        assert path.read_text() == reconciled


def test_append_is_not_an_available_operation(tmp_path: Path) -> None:
    result = _run_tool(tmp_path / ".codex", tmp_path / "CodexStateBackups", "append", "--text", "old note")
    assert result.returncode != 0
    assert "invalid choice: 'append'" in result.stderr


def test_repeated_item_cycles_leave_no_separator_or_completion_remnants(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)
    original = _todo_path(backup_root).read_text()
    for section in ("now", "next", "parked", "now"):
        added = _run_tool(codex_root, backup_root, "add", "--latest-thread", "--section", section,
                          "--text", "Temporary unfinished work.", *_observed_basis(codex_root, backup_root))
        assert added.returncode == 0, added.stderr
        completed = _run_tool(codex_root, backup_root, "complete", "--latest-thread", "--id", "T001",
                              *_observed_basis(codex_root, backup_root))
        assert completed.returncode == 0, completed.stderr
        assert _todo_path(backup_root).read_text() == original


def test_complete_leaves_sibling_lazy_and_indented_discussion_in_place(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    body = """## Now

- [ ] T001: Complete this.
Lazy detail belonging to the completed item.
  More completed detail.

- [ ] T002: Keep this.
Lazy detail belonging to the unfinished sibling.
  More unfinished detail.

  ```text
  Preserve the empty lines below.


  Keep this line too.
  ```

## Next

## Parked
"""
    written = _run_tool(codex_root, backup_root, "write", "--latest-thread", "--expect-absent", "--text", body)
    assert written.returncode == 0, written.stderr
    completed = _run_tool(codex_root, backup_root, "complete", "--latest-thread", "--id", "T001",
                          *_observed_basis(codex_root, backup_root))
    assert completed.returncode == 0, completed.stderr
    expected = body.replace("""- [ ] T001: Complete this.
Lazy detail belonging to the completed item.
  More completed detail.

""", "")
    assert _todo_path(backup_root).read_text() == expected


def test_legacy_completed_parent_with_unfinished_children_requires_reconciliation(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)
    for section in ("Now", "Completed"):
        legacy = f"""## {section}

- [x] T001: Ambiguous completed parent.
  Attached context needed for reconciliation.
  - [ ] Unfinished child without an id.
  - [ ] T002: Unfinished child with an id.
    Preserve this child detail.

## Next

- [ ] T003: Independent remaining work.

## Parked
"""
        path = _todo_path(backup_root)
        path.write_text(legacy)
        metadata_path = path.parent / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata.pop("body_revision", None)
        metadata_path.write_text(json.dumps(metadata))
        basis = _observed_basis(codex_root, backup_root)
        readback = _run_tool(codex_root, backup_root, "read", "--latest-thread", "--json")
        assert readback.returncode == 0, readback.stderr
        assert json.loads(readback.stdout)["body"] == legacy
        for args in (("add", "--text", "Another action."), ("write", "--text", legacy)):
            rejected = _run_tool(codex_root, backup_root, *args, "--latest-thread", *basis)
            assert rejected.returncode == 1
            assert "todo_reconciliation_required" in rejected.stderr
            assert path.read_text() == legacy
        reconciled = """## Now

- [ ] Unfinished child without an id.
- [ ] T002: Unfinished child with an id.
  Preserve this child detail.

## Next

- [ ] T003: Independent remaining work.

## Parked
"""
        written = _run_tool(codex_root, backup_root, "write", "--latest-thread", "--text", reconciled, *basis)
        assert written.returncode == 0, written.stderr
        assert path.read_text() == reconciled


def test_complete_ignores_unfinished_checkboxes_inside_literal_examples(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    body = """## Now

- [ ] T001: Parent with an example.
  ```markdown
  - [ ] Literal unfinished checkbox.
  ```

## Next

## Parked
"""
    written = _run_tool(codex_root, backup_root, "write", "--latest-thread", "--expect-absent", "--text", body)
    assert written.returncode == 0, written.stderr
    completed = _run_tool(codex_root, backup_root, "complete", "--latest-thread", "--id", "T001",
                          *_observed_basis(codex_root, backup_root))
    assert completed.returncode == 0, completed.stderr
    assert _todo_path(backup_root).read_text() == "## Now\n\n## Next\n\n## Parked\n"


def test_complete_returns_result_and_removes_item(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)

    first = _run_tool(
        codex_root,
        backup_root,
        "--json",
        "add",
        "--latest-thread",
        "--text",
        "Finish the active correction.", *_observed_basis(codex_root, backup_root),
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
        "Continue with remaining work.", *_observed_basis(codex_root, backup_root),
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
        "T001", *_observed_basis(codex_root, backup_root),
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
        "T001", *_observed_basis(codex_root, backup_root),
    )
    assert reopen_removed.returncode != 0
    assert "no to-do item T001 exists" in reopen_removed.stderr


def test_legacy_completed_content_is_hidden_and_purged_on_write(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "CodexStateBackups"
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
    metadata_path = _todo_path(backup_root).parent / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata.pop("body_revision")  # This fixture represents pre-revision legacy state.
    metadata_path.write_text(json.dumps(metadata))

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
        "next", *_observed_basis(codex_root, backup_root),
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
    repaired = _run_tool(codex_root, backup_root, "repair", "--thread-id", THREAD_ID,
                         *_observed_basis(codex_root, backup_root))
    assert repaired.returncode == 0, repaired.stderr
    added = _run_tool(
        codex_root,
        backup_root,
        "add",
        "--latest-thread",
        "--text",
        "Current work.", *_observed_basis(codex_root, backup_root),
    )
    assert added.returncode == 0, added.stderr
    updated_body = _todo_path(backup_root).read_text(encoding="utf-8")
    assert "Current work." in updated_body
    assert "T902" not in updated_body
    assert "## Done" not in updated_body


def test_full_body_write_cannot_reintroduce_completion_history(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "CodexStateBackups"
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
  + [x] Nested completed child of completed star item.
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

## Next

### Forward Narrative

This forward narrative follows the retired completion section.

## Parked

- [ ] Parked item without a generated id.
"""
    written = _run_tool(
        codex_root,
        backup_root,
        "write",
        "--latest-thread",
        "--stdin", *_observed_basis(codex_root, backup_root),
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
    assert "Nested completed child of completed star item." not in stored
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
    assert "### Forward Narrative" in stored
    assert "This forward narrative follows" in stored

    added = _run_tool(
        codex_root,
        backup_root,
        "add",
        "--latest-thread",
        "--text",
        "Generated active item.", *_observed_basis(codex_root, backup_root),
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
    assert "### Forward Narrative" in rewritten
    assert "This forward narrative follows" in rewritten
    assert rewritten.count("## Done") == 1


def test_check_windows_use_sanitized_forward_looking_view(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    backup_root = tmp_path / "CodexStateBackups"
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
    backup_root = tmp_path / "CodexStateBackups"
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


def test_legacy_completed_section_preserves_direct_unfinished_items(tmp_path: Path) -> None:
    codex_root, backup_root = tmp_path / ".codex", tmp_path / "CodexStateBackups"
    _write_session_index(codex_root)
    _activate(codex_root, backup_root)
    path = _todo_path(backup_root)
    metadata_path = path.parent / "metadata.json"
    for heading in ("Done", "Completed"):
        legacy = f"""## Now

## {heading}

- [ ] T001: Still unfinished despite the historical heading.
  Preserve the dependency detail.

## Next

## Parked
"""
        path.write_text(legacy, encoding="utf-8")
        metadata = json.loads(metadata_path.read_text())
        metadata.pop("body_revision", None)
        metadata_path.write_text(json.dumps(metadata))
        basis = _observed_basis(codex_root, backup_root)
        for verb, field in (("read", "body"), ("check", "excerpt")):
            readback = _run_tool(codex_root, backup_root, verb, "--thread-id", THREAD_ID, "--json")
            assert readback.returncode == 0, readback.stderr
            assert json.loads(readback.stdout)[field] == legacy
        for args in (("add", "--text", "New work."), ("write", "--text", legacy)):
            refused = _run_tool(codex_root, backup_root, *args, "--thread-id", THREAD_ID, *basis)
            assert refused.returncode == 1
            assert "todo_reconciliation_required" in refused.stderr
            assert path.read_text(encoding="utf-8") == legacy
        moved = _run_tool(codex_root, backup_root, "reopen", "--thread-id", THREAD_ID,
                          "--id", "T001", "--section", "next", *basis)
        assert moved.returncode == 0, moved.stderr
        current = path.read_text(encoding="utf-8")
        assert "## Done" not in current and "## Completed" not in current
        assert "Still unfinished despite the historical heading." in current
        assert "  Preserve the dependency detail." in current
        assert current.index("## Next") < current.index("Still unfinished") < current.index("## Parked")
