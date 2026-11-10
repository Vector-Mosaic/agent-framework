#!/usr/bin/env python3
"""Local-only forward-looking per-thread action overlays for Codex."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(REPO_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_FOR_IMPORTS))

from repo_platform.tooling.codex_thread_overlay import (  # noqa: E402
    ThreadOverlayError,
    ThreadOverlayPaths,
    ThreadTarget,
    add_common,
    add_selector,
    bounded_check_excerpt,
    body_path,
    emit_kv,
    load_metadata,
    nonnegative_int,
    pinned_registry_status as overlay_pinned_registry_status,
    positive_int,
    read_body_arg,
    resolve_paths as resolve_overlay_paths,
    resolve_thread_target,
    mutate_overlay, mutation_options, add_mutation_options,
    capture_overlay, overlay_view, navigate_overlay, add_navigation_options,
    overlay_status,
    utc_now,
)
from repo_platform.tooling.tool_handshake import (  # noqa: E402
    ToolSelector,
    ToolVerb,
    argparse_command_details,
    emit_payload,
    normalize_format,
    tool_result_payload,
    tool_usage_payload,
)


TOOL_ID = "tool.ops.codex_thread_todo"
ENTRYPOINT = "python scripts/tools/codex_thread_todo.py"
ENTRYPOINT_ARGV = (sys.executable, str(Path(__file__).resolve()))
DEFAULT_MAX_CHECK_CHARS = 4000
OVERLAY_ROOT_NAME = "thread_todos"
BODY_FILENAME = "todo.md"
PATH_FIELD = "todo_path"
ROOT_FIELD = "todo_root"
SECTION_CHOICES = ("now", "next", "parked")
ACTIVE_SECTIONS = SECTION_CHOICES
PARSED_SECTIONS = (*ACTIVE_SECTIONS, "done")
SECTION_TITLES = {
    "now": "Now",
    "next": "Next",
    "done": "Done",
    "parked": "Parked",
}
ITEM_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-+*])\s+\[(?P<state>[ xX])\]\s+"
    r"(?P<item_id>T(?P<number>\d{3,})):\s*(?P<text>.*)$"
)
CHECKLIST_ITEM_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-+*])\s+\[(?P<state>[ xX])\]\s+"
)
LIST_ITEM_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-+*]|\d+[.)])\s+"
)
FENCE_RE = re.compile(
    r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<rest>.*)$"
)
HEADING_RE = re.compile(
    r"^ {0,3}##(?!#)[ \t]+(?P<title>.*?)(?:[ \t]+#+)?[ \t]*$"
)
H1_RE = re.compile(
    r"^ {0,3}#(?!#)[ \t]+(?P<title>.*?)(?:[ \t]+#+)?[ \t]*$"
)
ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?!#)(?:[ \t]+|$)")
INTERRUPTING_BLOCK_RE = re.compile(
    r"^ {0,3}(?:>|<|(?:\*[ \t]*){3,}$|(?:-[ \t]*){3,}$|(?:_[ \t]*){3,}$)"
)


class TodoError(ThreadOverlayError):
    """Raised when a thread to-do operation cannot complete safely."""


@dataclass
class TodoItem:
    section: str
    checked: bool
    item_id: str
    text: str

    def to_line(self) -> str:
        state = "x" if self.checked else " "
        return f"- [{state}] {self.item_id}: {self.text}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "checked": self.checked,
            "id": self.item_id,
            "text": self.text,
        }


@dataclass
class TodoDocument:
    title: str
    items: list[TodoItem]
    extras: dict[str, list[str]]
    notes_lines: list[str]


def resolve_paths(args: argparse.Namespace) -> ThreadOverlayPaths:
    return resolve_overlay_paths(args, OVERLAY_ROOT_NAME)


def todo_path(paths: ThreadOverlayPaths, thread_id: str) -> Path:
    return body_path(paths, thread_id, BODY_FILENAME)


def set_pinned(paths: ThreadOverlayPaths, target: ThreadTarget, pinned: bool, **precondition: Any) -> dict[str, Any]:
    return mutate_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id),
                          path_field=PATH_FIELD, action="pin" if pinned else "unpin", pin=pinned, **precondition)


def section_key(raw_title: str) -> str:
    normalized = raw_title.strip().lower()
    if normalized == "completed":
        return "done"
    for key, title in SECTION_TITLES.items():
        if normalized == title.lower():
            return key
    if normalized == "notes":
        return "notes"
    if normalized == "metadata":
        return "metadata"
    return "notes"


def leading_indent_width(line: str) -> int:
    prefix = line[: len(line) - len(line.lstrip(" \t"))]
    return len(prefix.expandtabs(4))


def fence_marker(line: str) -> tuple[str, int] | None:
    match = FENCE_RE.match(line)
    if not match:
        return None
    marker = match.group("marker")
    return marker[0], len(marker)


def closes_fence(line: str, marker_char: str, minimum_length: int) -> bool:
    match = FENCE_RE.match(line)
    if not match:
        return False
    marker = match.group("marker")
    return (
        marker[0] == marker_char
        and len(marker) >= minimum_length
        and not match.group("rest").strip()
    )


def parse_todo_body(body: str) -> TodoDocument:
    title = "Thread To-Do List"
    current_section = ""
    items: list[TodoItem] = []
    extras = {key: [] for key in PARSED_SECTIONS}
    notes_lines: list[str] = []
    active_fence: tuple[str, int] | None = None
    retain_active_fence = True
    active_indented_code = False
    skip_checked_indent: int | None = None
    skip_checked_saw_blank = False
    previous_line_blank = True
    list_context_indent: int | None = None

    def retain_non_item_line(line: str) -> None:
        if current_section in PARSED_SECTIONS:
            if line.strip() and line.strip() != "- (none yet)":
                extras[current_section].append(line)
            return
        if current_section == "metadata":
            return
        if line.strip():
            notes_lines.append(line)

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if active_fence is not None:
            if retain_active_fence:
                retain_non_item_line(line)
            if closes_fence(line, *active_fence):
                active_fence = None
                retain_active_fence = True
            previous_line_blank = not line.strip()
            continue

        if active_indented_code:
            if not line.strip() or leading_indent_width(line) >= 4:
                retain_non_item_line(line)
                previous_line_blank = not line.strip()
                continue
            active_indented_code = False
            list_context_indent = None

        if skip_checked_indent is not None:
            if not line.strip():
                skip_checked_saw_blank = True
                previous_line_blank = True
                continue
            indent_width = leading_indent_width(line)
            structural_line = bool(
                ATX_HEADING_RE.match(line)
                or fence_marker(line) is not None
                or LIST_ITEM_RE.match(line)
                or INTERRUPTING_BLOCK_RE.match(line)
            )
            if indent_width > skip_checked_indent:
                marker = fence_marker(line)
                if marker is not None:
                    active_fence = marker
                    retain_active_fence = False
                previous_line_blank = False
                continue
            if not structural_line and not skip_checked_saw_blank:
                previous_line_blank = False
                continue
            skip_checked_indent = None
            skip_checked_saw_blank = False

        marker = fence_marker(line)
        if marker is not None:
            retain_non_item_line(line)
            active_fence = marker
            retain_active_fence = True
            previous_line_blank = False
            list_context_indent = None
            continue

        h1 = H1_RE.match(line)
        if h1:
            if not current_section:
                title = h1.group("title").strip() or title
            else:
                current_section = "notes"
                notes_lines.append(line)
            previous_line_blank = True
            list_context_indent = None
            continue
        heading = HEADING_RE.match(line)
        if heading:
            current_section = section_key(heading.group("title"))
            if current_section == "notes" and heading.group("title").strip().lower() not in {"notes", "metadata"}:
                notes_lines.append(line)
            previous_line_blank = True
            list_context_indent = None
            continue
        if ATX_HEADING_RE.match(line):
            retain_non_item_line(line)
            previous_line_blank = True
            list_context_indent = None
            continue

        indent_width = leading_indent_width(line)
        if (
            line.strip()
            and indent_width >= 4
            and previous_line_blank
            and list_context_indent is None
        ):
            retain_non_item_line(line)
            active_indented_code = True
            previous_line_blank = False
            continue

        if current_section in PARSED_SECTIONS:
            match = ITEM_RE.match(line)
            if match:
                item_indent = leading_indent_width(match.group("indent"))
                items.append(
                    TodoItem(
                        section=current_section,
                        checked=match.group("state").lower() == "x",
                        item_id=match.group("item_id").upper(),
                        text=match.group("text").strip(),
                    )
                )
                list_context_indent = item_indent
                previous_line_blank = False
                if match.group("state").lower() == "x":
                    skip_checked_indent = item_indent
                    skip_checked_saw_blank = False
                continue

        checklist = CHECKLIST_ITEM_RE.match(line)
        if checklist and checklist.group("state").lower() == "x":
            skip_checked_indent = leading_indent_width(checklist.group("indent"))
            skip_checked_saw_blank = False
            previous_line_blank = False
            continue

        list_item = LIST_ITEM_RE.match(line)
        if list_item:
            list_context_indent = leading_indent_width(list_item.group("indent"))
        elif line.strip() and (
            list_context_indent is None or indent_width <= list_context_indent
        ):
            list_context_indent = None
        retain_non_item_line(line)
        previous_line_blank = not line.strip()
    return TodoDocument(title=title, items=items, extras=extras, notes_lines=notes_lines)


def sanitize_forward_looking_body(body: str) -> str:
    lines: list[str] = []
    omit_completed_section = False
    active_fence: tuple[str, int] | None = None
    emit_active_fence = True
    active_indented_code = False
    skip_checked_indent: int | None = None
    skip_checked_saw_blank = False
    previous_line_blank = True
    list_context_indent: int | None = None

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if active_fence is not None:
            if emit_active_fence:
                lines.append(raw_line)
            if closes_fence(line, *active_fence):
                active_fence = None
                emit_active_fence = True
            previous_line_blank = not line.strip()
            continue

        if active_indented_code:
            if not line.strip() or leading_indent_width(line) >= 4:
                if not omit_completed_section:
                    lines.append(raw_line)
                previous_line_blank = not line.strip()
                continue
            active_indented_code = False
            list_context_indent = None

        if skip_checked_indent is not None:
            if not line.strip():
                skip_checked_saw_blank = True
                previous_line_blank = True
                continue
            indent_width = leading_indent_width(line)
            structural_line = bool(
                ATX_HEADING_RE.match(line)
                or fence_marker(line) is not None
                or LIST_ITEM_RE.match(line)
                or INTERRUPTING_BLOCK_RE.match(line)
            )
            if indent_width > skip_checked_indent:
                marker = fence_marker(line)
                if marker is not None:
                    active_fence = marker
                    emit_active_fence = False
                previous_line_blank = False
                continue
            if not structural_line and not skip_checked_saw_blank:
                previous_line_blank = False
                continue
            skip_checked_indent = None
            skip_checked_saw_blank = False

        marker = fence_marker(line)
        if marker is not None:
            active_fence = marker
            emit_active_fence = not omit_completed_section
            if emit_active_fence:
                lines.append(raw_line)
            previous_line_blank = False
            list_context_indent = None
            continue

        h1 = H1_RE.match(line)
        if h1:
            omit_completed_section = False
            previous_line_blank = True
            list_context_indent = None
            lines.append(raw_line)
            continue

        heading = HEADING_RE.match(line)
        if heading:
            normalized = heading.group("title").strip().lower()
            if normalized in {"done", "completed"}:
                omit_completed_section = True
                previous_line_blank = False
                list_context_indent = None
                continue
            omit_completed_section = False
            previous_line_blank = True
            list_context_indent = None
            lines.append(raw_line)
            continue

        if omit_completed_section:
            continue
        if ATX_HEADING_RE.match(line):
            lines.append(raw_line)
            previous_line_blank = True
            list_context_indent = None
            continue

        indent_width = leading_indent_width(line)
        if (
            line.strip()
            and indent_width >= 4
            and previous_line_blank
            and list_context_indent is None
        ):
            lines.append(raw_line)
            active_indented_code = True
            previous_line_blank = False
            continue

        checklist = CHECKLIST_ITEM_RE.match(line)
        if checklist and checklist.group("state").lower() == "x":
            skip_checked_indent = leading_indent_width(checklist.group("indent"))
            skip_checked_saw_blank = False
            previous_line_blank = False
            continue

        list_item = LIST_ITEM_RE.match(line)
        if list_item:
            list_context_indent = leading_indent_width(list_item.group("indent"))
        elif line.strip() and (
            list_context_indent is None or indent_width <= list_context_indent
        ):
            list_context_indent = None
        lines.append(raw_line)
        previous_line_blank = not line.strip()

    sanitized = "\n".join(lines).strip()
    return sanitized + "\n" if sanitized else ""


def render_todo_body(
    document: TodoDocument,
    target: ThreadTarget,
    *,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> str:
    now = updated_at or utc_now()
    thread_name = target.thread_name or "(unknown)"
    lines: list[str] = [
        f"# {document.title or 'Thread To-Do List'}",
        "",
    ]
    for section in ACTIVE_SECTIONS:
        lines.extend([f"## {SECTION_TITLES[section]}", ""])
        section_items = [
            item for item in document.items if item.section == section and not item.checked
        ]
        section_extras = [line for line in document.extras.get(section, []) if line.strip()]
        if section_items or section_extras:
            lines.extend(item.to_line() for item in section_items)
            if section_items and section_extras:
                lines.append("")
            lines.extend(section_extras)
        else:
            lines.append("- (none yet)")
        lines.append("")

    lines.extend(["## Notes", ""])
    notes_lines = [line for line in document.notes_lines if line.strip()]
    if notes_lines:
        lines.extend(notes_lines)
    else:
        lines.append(
            "- Keep only current action order here; route completion history to its owning report, checkpoint, or receipt."
        )
    lines.extend(
        [
            "",
            "## Metadata",
            "",
            f"- thread_id: `{target.thread_id}`",
            f"- thread_title: {thread_name}",
            f"- created_at: {created_at or now}",
            f"- updated_at: {now}",
            "- storage: local-only configured continuity root / thread_todos",
            "- authority: thread-local action overlay, not repo truth",
            "",
        ]
    )
    return "\n".join(lines)


def default_document(title: str = "Thread To-Do List") -> TodoDocument:
    return TodoDocument(
        title=title,
        items=[],
        extras={key: [] for key in PARSED_SECTIONS},
        notes_lines=[],
    )


def create_or_write_todo(paths: ThreadOverlayPaths, target: ThreadTarget, body: str, *,
                         pin: bool, action: str, **precondition: Any) -> dict[str, Any]:
    prepared = sanitize_forward_looking_body(body)
    return mutate_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id), path_field=PATH_FIELD,
                          action=action, change=lambda body, metadata: prepared, allow_create=True, pin=True if pin else None, **precondition)


def append_note(paths: ThreadOverlayPaths, target: ThreadTarget, note: str, *, pin: bool, **precondition: Any) -> dict[str, Any]:
    note_text = note.strip()
    if sanitize_forward_looking_body(note_text).strip() != note_text:
        raise TodoError("append note contains completed action history; remove it or place illustrative Markdown inside a fenced code block")
    def change(body: str, metadata: dict[str, Any]) -> str:
        body = sanitize_forward_looking_body(body).rstrip()
        addition = f"## Note {utc_now()}\n\n{note_text}"
        return f"{body}\n\n{addition}\n" if body else f"{addition}\n"
    return mutate_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id), path_field=PATH_FIELD,
                          action="append", change=change, pin=True if pin else None, **precondition)


def todo_status(paths: ThreadOverlayPaths, target: ThreadTarget) -> dict[str, Any]:
    return overlay_status(
        paths,
        target,
        artifact_path=todo_path(paths, target.thread_id),
        path_field=PATH_FIELD,
    )


def pinned_registry_status(paths: ThreadOverlayPaths) -> dict[str, Any]:
    return overlay_pinned_registry_status(paths, root_field=ROOT_FIELD)


def next_item_id(document: TodoDocument) -> str:
    highest = 0
    for item in document.items:
        match = re.match(r"^T(?P<number>\d+)$", item.item_id)
        if match:
            highest = max(highest, int(match.group("number")))
    return f"T{highest + 1:03d}"


def normalize_item_id(raw_item_id: str) -> str:
    item_id = raw_item_id.strip().upper()
    if not re.match(r"^T\d{3,}$", item_id):
        raise TodoError(f"invalid to-do item id: {raw_item_id!r}")
    return item_id


def pop_item(document: TodoDocument, item_id: str) -> TodoItem:
    normalized = normalize_item_id(item_id)
    for index, item in enumerate(document.items):
        if item.item_id == normalized:
            return document.items.pop(index)
    raise TodoError(f"no to-do item {normalized} exists")


def render_template(args: argparse.Namespace, target: ThreadTarget) -> str:
    title = str(getattr(args, "title", "") or "").strip() or "Thread To-Do List"
    return render_todo_body(default_document(title), target)


def usage_payload() -> dict[str, Any]:
    return tool_usage_payload(
        tool_id=TOOL_ID,
        purpose=(
            "Create, pin, read, update, and check forward-looking local-only "
            "per-thread Codex action overlays."
        ),
        entrypoint=ENTRYPOINT,
        safe_first_calls=[
            {"label": "usage", "cmd": ENTRYPOINT},
            {"label": "usage-json", "cmd": f"{ENTRYPOINT} usage --format json"},
            {"label": "check-current-thread", "cmd": f"{ENTRYPOINT} check"},
        ],
        verbs=[
            ToolVerb("outline", "local coordination lock only", "Read heading hierarchy and exact version-bound references."),
            ToolVerb("repair", "local metadata and derived listing writes", "Reconcile observed body revision/listings without replaying an edit or inventing pin state."),
            ToolVerb("usage", "none", "Return this usage contract."),
            ToolVerb("activate", "writes local to-do state", "Create or replace a forward-looking thread to-do list and pin it by default."),
            ToolVerb("write", "writes local to-do state", "Replace a to-do Markdown body while excluding completion history."),
            ToolVerb("append", "writes local to-do state", "Append a timestamped current note and reject completion-history markup."),
            ToolVerb("status", "local coordination lock only", "Show one to-do status or list pinned to-do lists."),
            ToolVerb("read", "local coordination lock only", "Read the current forward-looking to-do body."),
            ToolVerb(
                "check",
                "local coordination lock only",
                "Report a bounded pinned to-do excerpt with explicit window and truncation metadata.",
            ),
            ToolVerb("pin", "writes local to-do state", "Pin an existing to-do list."),
            ToolVerb("unpin", "writes local to-do state", "Unpin a to-do list."),
            ToolVerb("add", "writes local to-do state", "Add a checklist item with a stable item id."),
            ToolVerb("complete", "writes local to-do state", "Return a completion result and remove the item from active state."),
            ToolVerb("reopen", "writes local to-do state", "Move an existing active or legacy item into Now, Next, or Parked."),
            ToolVerb("remove", "writes local to-do state", "Remove an item from the to-do list."),
            ToolVerb("clear", "writes local to-do state", "Reset the to-do list body without deleting the thread directory."),
        ],
        selectors=[
            ToolSelector("--thread-id", False, "Exact Codex thread UUID."),
            ToolSelector("--thread-title-contains", False, "Case-insensitive thread title fragment."),
            ToolSelector("--latest-thread", False, "Fallback selector for the newest indexed thread."),
            ToolSelector("--codex-root", False, "Override Codex home (CODEX_HOME, then ~/.codex)."),
            ToolSelector("--backup-root", False, "Override continuity storage (AGENT_FRAMEWORK_STATE_HOME or platform user state)."),
            ToolSelector("--expected", False, "Mutation token from actual observed body/pin state; required for existing documents."),
            ToolSelector("--expect-absent", False, "Explicit creation precondition; both body and metadata must be absent."),
            ToolSelector("--section-ref", False, "Exact occurrence at the captured source/view revision from outline."),
            ToolSelector("--cursor", False, "Continue the same selection/view/revision returned by read."),
        ],
        command_details=argparse_command_details(build_parser()),
        extra={"mutation_contract": {
            "precondition": "Every mutation supplies --expected from the observed state, or --expect-absent for creation. Never silently reacquire and replay an old edit.",
            "basis": "Body-only edits compare raw stored bytes and preserve current pin state. Explicit pin changes compare pin state; compound changes compare both.",
            "publication": "Body, metadata and derived listings publish separately under document then registry OS locks. Partial errors identify committed/uncertain stages; read back then repair, never replay an append blindly.",
            "coordination_scope": "Participating current tools only; old copied scripts and arbitrary editors do not acquire these locks.",
            "read_effects": "Read/check/status/outline may create a local coordination lock file; they do not alter body or metadata.",
            "read_coverage": "Text and JSON retain source/view identity, selection and delivered range. Cursors reject changed bytes or transformations; final page alone is not whole-selection receipt."}},
        output_formats=["text", "json"],
        side_effects={
            "writes_repo": False,
            "writes_paths": ["<configured continuity root>/thread_todos/**"],
            "modifies_remote": False,
            "network_access": "none",
            "danger_level": "low",
            "supports_dry_run": False,
        },
        examples=[
            {"label": "Usage JSON", "cmd": f"{ENTRYPOINT} usage --format json"},
            {"label": "Check current pinned to-do list", "cmd": f"{ENTRYPOINT} check"},
            {"label": "Add item", "cmd": f"{ENTRYPOINT} add --expected <observed-token> --text \"Run focused tests\" --section now --pin"},
            {"label": "Complete item", "cmd": f"{ENTRYPOINT} complete --expected <observed-token> --id T001"},
        ],
        docs=[
            "AGENTS.md",
            "docs/instructions/agent_workflow/README.md",
        ],
        errors=[
            {
                "code": "thread_selector_required",
                "meaning": "No current thread id was available and no thread selector was provided.",
            },
            {
                "code": "missing_todo",
                "meaning": "The selected thread does not have a to-do list for the requested operation.",
            },
            {
                "code": "missing_todo_item",
                "meaning": "The selected to-do item id does not exist.",
            },
        ],
    )


def cmd_usage(args: argparse.Namespace) -> int:
    output_format = normalize_format(getattr(args, "format", "text"), json_alias=bool(getattr(args, "json", False)))
    emit_payload(usage_payload(), output_format=output_format, stream=sys.stdout)
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    body = read_body_arg(args) or render_template(args, target)
    metadata = create_or_write_todo(paths, target, body, pin=not args.no_pin, action="activate", **mutation_options(args))
    emit_kv({"action": "activate", "thread": target.__dict__, **metadata}, as_json=args.json)
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    body = read_body_arg(args)
    if body is None:
        raise TodoError("write requires --text, --body-file, or --stdin")
    metadata = create_or_write_todo(paths, target, body, pin=args.pin, action="write", **mutation_options(args))
    emit_kv({"action": "write", "thread": target.__dict__, **metadata}, as_json=args.json)
    return 0


def cmd_append(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    note = read_body_arg(args)
    if note is None:
        raise TodoError("append requires --text, --body-file, or --stdin")
    metadata = append_note(paths, target, note, pin=args.pin, **mutation_options(args))
    emit_kv({"action": "append", "thread": target.__dict__, **metadata}, as_json=args.json)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=False)
    if target is None:
        emit_kv({"action": "status", **pinned_registry_status(paths)}, as_json=args.json)
        return 0
    emit_kv({"action": "status", **todo_status(paths, target)}, as_json=args.json)
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    payload = navigate_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id),
                               path_field=PATH_FIELD, transform=sanitize_forward_looking_body,
                               view="todo-forward-looking-v1", args=args)
    emit_kv({"action": args.command, **payload}, as_json=args.json)
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    result = mutate_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id),
                            path_field=PATH_FIELD, action="repair", repair=True, **mutation_options(args))
    emit_kv({"action": "repair", **result}, as_json=args.json)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    captured = capture_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id), path_field=PATH_FIELD)
    status = overlay_status(paths, target, artifact_path=todo_path(paths, target.thread_id), path_field=PATH_FIELD, captured=captured)
    if args.expected_source is not None and args.expected_source != captured["body_revision"]:
        raise TodoError("Stored source changed; reacquire with read or outline.", code="stale_selection")
    active = bool(status["exists"] and status["pinned"])
    body = ""
    if active:
        body, status["view"] = overlay_view(captured, sanitize_forward_looking_body, "todo-forward-looking-v1")

    wants_json = (
        normalize_format(
            getattr(args, "format", "text"),
            json_alias=bool(getattr(args, "json", False)),
        )
        == "json"
    )
    excerpt, excerpt_meta = bounded_check_excerpt(
        body,
        active=active,
        max_chars=args.max_chars,
        offset=getattr(args, "offset", None),
        tail=bool(getattr(args, "tail", False)),
        entrypoint_argv=ENTRYPOINT_ARGV,
        thread_id=target.thread_id,
        codex_root=paths.codex_root,
        backup_root=paths.backup_root,
        noun="to-do",
        as_json=wants_json,
        source_revision=captured["body_revision"],
    )

    payload = {
        "action": "check",
        "active": active,
        **status,
        "excerpt_meta": excerpt_meta,
        "excerpt": excerpt,
    }
    emit_kv(payload, as_json=wants_json)
    return 0


def cmd_pin(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    metadata = set_pinned(paths, target, True, **mutation_options(args))
    emit_kv({"action": "pin", "thread": target.__dict__, **metadata}, as_json=args.json)
    return 0


def cmd_unpin(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    metadata = set_pinned(paths, target, False, **mutation_options(args))
    emit_kv({"action": "unpin", "thread": target.__dict__, **metadata}, as_json=args.json)
    return 0


def cmd_item_mutation(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    item_result: dict[str, Any] = {}
    if args.command == "add" and not str(args.text or "").strip():
        raise TodoError("add requires --text")
    def change(body: str, metadata: dict[str, Any]) -> str:
        document = parse_todo_body(body) if body else default_document()
        if args.command == "clear":
            document = default_document()
        elif args.command == "add":
            item = TodoItem(section=args.section, checked=False, item_id=next_item_id(document), text=args.text.strip())
            document.items.append(item)
            item_result.update(item.to_payload())
        else:
            item = pop_item(document, args.id)
            if args.command == "reopen":
                item.checked = False
                item.section = args.section
                document.items.append(item)
            elif args.command == "complete":
                item.checked = True
            item_result.update(item.to_payload())
        return render_todo_body(document, target, created_at=str(metadata.get("created_at") or utc_now()), updated_at=utc_now()).strip() + "\n"
    pin = not args.no_pin if args.command == "clear" else True if getattr(args, "pin", False) else None
    result = mutate_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id), path_field=PATH_FIELD,
                            action=args.command, change=change, pin=pin, allow_create=args.command in {"add", "clear"}, **mutation_options(args))
    emit_kv({"action": args.command, "thread": target.__dict__, "item": item_result,
             "removed_from_active": args.command == "complete", **result}, as_json=args.json)
    return 0


def add_body_args(parser: argparse.ArgumentParser, *, text_help: str) -> None:
    parser.add_argument("--text", default=None, help=text_help)
    parser.add_argument("--body-file", default=None, help="Read text from file.")
    parser.add_argument("--stdin", action="store_true", help="Read text from stdin.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, pin, read, and update forward-looking local-only per-thread Codex to-do lists."
    )
    add_common(parser, defaults=True)
    parser.set_defaults(func=cmd_usage)
    subparsers = parser.add_subparsers(dest="command")

    usage = subparsers.add_parser("usage", help="Return the clean-command usage contract.")
    add_common(usage, defaults=False)
    usage.set_defaults(func=cmd_usage)

    activate = subparsers.add_parser("activate", help="Create or replace a thread to-do list and pin it by default.")
    add_common(activate, defaults=False)
    add_selector(activate)
    activate.add_argument("--title", default="Thread To-Do List", help="To-do list title.")
    add_body_args(activate, text_help="Full to-do body.")
    activate.add_argument("--no-pin", action="store_true", help="Create without pinning.")
    activate.set_defaults(func=cmd_activate)

    write = subparsers.add_parser("write", help="Replace an existing thread to-do body.")
    add_common(write, defaults=False)
    add_selector(write)
    add_body_args(write, text_help="Full to-do body.")
    write.add_argument("--pin", action="store_true", help="Pin after writing.")
    write.set_defaults(func=cmd_write)

    append = subparsers.add_parser("append", help="Append a timestamped note to a thread to-do list.")
    add_common(append, defaults=False)
    add_selector(append)
    add_body_args(append, text_help="Note text.")
    append.add_argument("--pin", action="store_true", help="Pin after appending.")
    append.set_defaults(func=cmd_append)

    status = subparsers.add_parser("status", help="Show one to-do status, or list pinned to-do lists.")
    add_common(status, defaults=False)
    add_selector(status)
    status.set_defaults(func=cmd_status)

    read = subparsers.add_parser("read", help="Read a to-do body.")
    add_common(read, defaults=False)
    add_selector(read)
    read.add_argument("--pinned-only", action="store_true", help="Return inactive status instead of body when unpinned.")
    add_navigation_options(read)
    read.set_defaults(func=cmd_read)

    check = subparsers.add_parser("check", help="Agent hook: report/read the pinned to-do list for a selected thread.")
    add_common(check, defaults=False)
    add_selector(check)
    check.add_argument(
        "--max-chars",
        type=positive_int,
        default=DEFAULT_MAX_CHECK_CHARS,
        help="Maximum Unicode characters in the excerpt (default: 4000).",
    )
    window = check.add_mutually_exclusive_group()
    window.add_argument(
        "--offset",
        type=nonnegative_int,
        default=None,
        help="Zero-based Unicode-character offset into the active forward-looking to-do view.",
    )
    window.add_argument(
        "--tail",
        action="store_true",
        help="Return the final --max-chars Unicode characters.",
    )
    check.add_argument("--expected-source", help="Require the exact stored-byte revision returned by an earlier check.")
    check.set_defaults(func=cmd_check)

    pin = subparsers.add_parser("pin", help="Pin an existing to-do list.")
    add_common(pin, defaults=False)
    add_selector(pin)
    pin.set_defaults(func=cmd_pin)

    unpin = subparsers.add_parser("unpin", help="Unpin a to-do list.")
    add_common(unpin, defaults=False)
    add_selector(unpin)
    unpin.set_defaults(func=cmd_unpin)

    add_item = subparsers.add_parser("add", help="Add a checklist item with a stable id.")
    add_common(add_item, defaults=False)
    add_selector(add_item)
    add_item.add_argument("--text", required=True, help="Checklist item text.")
    add_item.add_argument("--section", choices=SECTION_CHOICES, default="now", help="Target open-item section.")
    add_item.add_argument("--pin", action="store_true", help="Pin after adding.")
    add_item.set_defaults(func=cmd_item_mutation)

    complete = subparsers.add_parser("complete", help="Complete and remove a checklist item.")
    add_common(complete, defaults=False)
    add_selector(complete)
    complete.add_argument("--id", required=True, help="Stable checklist item id, for example T001.")
    complete.set_defaults(func=cmd_item_mutation)

    reopen = subparsers.add_parser("reopen", help="Move an existing active or legacy checklist item.")
    add_common(reopen, defaults=False)
    add_selector(reopen)
    reopen.add_argument("--id", required=True, help="Stable checklist item id, for example T001.")
    reopen.add_argument("--section", choices=SECTION_CHOICES, default="now", help="Target open-item section.")
    reopen.set_defaults(func=cmd_item_mutation)

    remove = subparsers.add_parser("remove", help="Remove a checklist item.")
    add_common(remove, defaults=False)
    add_selector(remove)
    remove.add_argument("--id", required=True, help="Stable checklist item id, for example T001.")
    remove.set_defaults(func=cmd_item_mutation)

    clear = subparsers.add_parser("clear", help="Reset the to-do list body without deleting the thread directory.")
    add_common(clear, defaults=False)
    add_selector(clear)
    clear.add_argument("--no-pin", action="store_true", help="Clear without pinning the to-do list.")
    clear.set_defaults(func=cmd_item_mutation)

    outline = subparsers.add_parser("outline", help="List exact revision-bound section occurrences and preamble.")
    add_common(outline, defaults=False)
    add_selector(outline)
    outline.set_defaults(func=cmd_read)
    repair = subparsers.add_parser("repair", help="Reconcile recorded body revision and derived listings, preserving known pin state; never replay an edit.")
    add_common(repair, defaults=False)
    add_selector(repair)
    repair.set_defaults(func=cmd_repair)
    for command in (activate, write, append, pin, unpin, add_item, complete, reopen, remove, clear, repair):
        add_mutation_options(command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.json = normalize_format(args.format, json_alias=args.json) == "json"
    try:
        return int(args.func(args))
    except ThreadOverlayError as exc:
        wants_json = bool(getattr(args, "json", False)) or str(getattr(args, "format", "text")) == "json"
        if wants_json:
            emit_payload(
                tool_result_payload(
                    tool_id=TOOL_ID,
                    verb=str(getattr(args, "command", "error") or "error"),
                    status="error",
                    summary=str(exc),
                    errors=[
                        {
                            "code": exc.code,
                            "details": exc.details,
                            "message": str(exc),
                            "retry_hint": f"Run `{ENTRYPOINT} usage`.",
                        }
                    ],
                ),
                output_format="json",
                stream=sys.stderr,
            )
        else:
            print(f"ERROR [{exc.code}]: {exc}", file=sys.stderr)
            if exc.details:
                import json
                print("details: " + json.dumps(exc.details, sort_keys=True), file=sys.stderr)
            print(f"Retry hint: run `{ENTRYPOINT} usage`.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
