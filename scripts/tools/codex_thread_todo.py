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
    nonnegative_int,
    pinned_registry_status as overlay_pinned_registry_status,
    positive_int,
    read_body_arg,
    resolve_paths as resolve_overlay_paths,
    resolve_thread_target,
    mutate_overlay, mutation_options, add_mutation_options,
    capture_overlay, overlay_view, navigate_overlay, add_navigation_options,
    overlay_status,
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
    start: int = 0
    end: int = 0

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
    lines: list[str]
    items: list[TodoItem]
    kinds: list[str]


def resolve_paths(args: argparse.Namespace) -> ThreadOverlayPaths:
    return resolve_overlay_paths(args, OVERLAY_ROOT_NAME)


def todo_path(paths: ThreadOverlayPaths, thread_id: str) -> Path:
    return body_path(paths, thread_id, BODY_FILENAME)


def set_pinned(paths: ThreadOverlayPaths, target: ThreadTarget, pinned: bool, **precondition: Any) -> dict[str, Any]:
    return mutate_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id),
                          path_field=PATH_FIELD, action="pin" if pinned else "unpin", pin=pinned, **precondition)


def section_key(raw_title: str) -> str:
    normalized = raw_title.strip().lower()
    return "done" if normalized == "completed" else normalized


def leading_indent_width(line: str) -> int:
    prefix = line[: len(line) - len(line.lstrip(" \t"))]
    return len(prefix.expandtabs(4))


def markdown_line_kinds(lines: list[str]) -> list[str]:
    """Identify the existing ATX/list syntax without interpreting literal examples."""
    kinds: list[str] = []
    active_fence: tuple[str, int] | None = None
    indented_code = False
    previous_blank = True
    list_indent: int | None = None
    for line in lines:
        stripped = line.lstrip(" \t")
        marker = FENCE_RE.match(stripped)
        if active_fence is not None:
            kinds.append("literal")
            if marker and marker.group("marker")[0] == active_fence[0] and len(marker.group("marker")) >= active_fence[1] and not marker.group("rest").strip():
                active_fence = None
            previous_blank = not line.strip()
            continue
        indent = leading_indent_width(line)
        if indented_code:
            if not line.strip() or indent >= 4:
                kinds.append("literal")
                previous_blank = not line.strip()
                continue
            indented_code = False
            list_indent = None
        if line.strip() and indent >= 4 and previous_blank and list_indent is None:
            kinds.append("literal")
            indented_code = True
            previous_blank = False
            continue
        if marker and (indent <= 3 or (list_indent is not None and indent > list_indent)):
            active_fence = (marker.group("marker")[0], len(marker.group("marker")))
            kind = "fence"
        elif ATX_HEADING_RE.match(line) and list_indent is not None and indent > list_indent:
            kind = "nested_heading"
        elif H1_RE.match(line):
            kind = "h1"
            list_indent = None
        elif HEADING_RE.match(line):
            kind = "h2"
            list_indent = None
        elif ATX_HEADING_RE.match(line):
            kind = "heading"
            list_indent = None
        elif LIST_ITEM_RE.match(line):
            kind = "list"
            list_indent = indent
        elif INTERRUPTING_BLOCK_RE.match(line):
            kind = "block"
        else:
            kind = "text" if line.strip() else "blank"
            if line.strip() and (list_indent is None or indent <= list_indent):
                list_indent = None
        kinds.append(kind)
        previous_blank = not line.strip() or kind in {"h1", "h2", "heading"}
    return kinds


def item_end(lines: list[str], kinds: list[str], start: int) -> int:
    """Include nested blocks and lazy continuation, but leave sibling discussion."""
    indent = leading_indent_width(lines[start])
    saw_blank = False
    end = start + 1
    while end < len(lines):
        kind = kinds[end]
        if kind == "blank":
            saw_blank = True
        elif kind == "literal" or leading_indent_width(lines[end]) > indent:
            pass
        elif kind != "text" or saw_blank:
            break
        end += 1
    # The separator before the next block belongs to neither item's detail.
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def parse_todo_body(body: str) -> TodoDocument:
    lines = body.splitlines()
    kinds = markdown_line_kinds(lines)
    section = ""
    items: list[TodoItem] = []
    for index, (line, kind) in enumerate(zip(lines, kinds)):
        if kind == "h2":
            section = section_key(HEADING_RE.match(line).group("title"))
        elif kind == "h1":
            section = ""
        elif kind == "list" and section in PARSED_SECTIONS:
            match = ITEM_RE.match(line)
            if match:
                items.append(TodoItem(
                    section=section,
                    checked=match.group("state").lower() == "x",
                    item_id=match.group("item_id"),
                    text=match.group("text").strip(),
                    start=index,
                    end=item_end(lines, kinds, index),
                ))
    return TodoDocument(lines=lines, items=items, kinds=kinds)


def has_unfinished_descendants(document: TodoDocument, start: int, end: int) -> bool:
    indent = leading_indent_width(document.lines[start])
    for index in range(start + 1, end):
        if document.kinds[index] != "list" or leading_indent_width(document.lines[index]) <= indent:
            continue
        match = CHECKLIST_ITEM_RE.match(document.lines[index])
        if match and match.group("state") == " ":
            return True
    return False


GENERATED_NOTE = "- Keep only current action order here; route completion history to its owning report, checkpoint, or receipt."
GENERATED_METADATA_RE = re.compile(
    r"^- (?:thread_id: `[^`]+`|thread_title: .*|(?:created_at|updated_at): \d{4}-\d{2}-\d{2}T[^ ]+|"
    r"storage: local-only configured continuity root / thread_todos|authority: thread-local action overlay, not repo truth)$"
)


def sanitize_forward_looking_body(body: str) -> str:
    document = parse_todo_body(body)
    lines, kinds = document.lines, document.kinds
    remove: set[int] = set()
    preserve: set[int] = set()
    for index, (line, kind) in enumerate(zip(lines, kinds)):
        match = CHECKLIST_ITEM_RE.match(line) if kind == "list" else None
        if match and match.group("state").lower() == "x":
            end = item_end(lines, kinds, index)
            if has_unfinished_descendants(document, index, end):
                preserve.update(range(index, end))
    for index, (line, kind) in enumerate(zip(lines, kinds)):
        if index in remove or index in preserve:
            continue
        if kind == "h2":
            section = section_key(HEADING_RE.match(line).group("title"))
            end = next((i for i in range(index + 1, len(lines)) if kinds[i] in {"h1", "h2"}), len(lines))
            if section == "done":
                # A historical heading cannot decide an unfinished item's fate.
                unfinished = any(
                    kinds[i] == "list" and (match := CHECKLIST_ITEM_RE.match(lines[i]))
                    and match.group("state") == " "
                    for i in range(index + 1, end)
                )
                if not unfinished and not any(i in preserve for i in range(index, end)):
                    remove.update(range(index, end))
            elif section in {"notes", "metadata"}:
                generated = lambda i: not lines[i].strip() or (kinds[i] == "list" and (
                    lines[i].strip() in {GENERATED_NOTE, "- (none yet)"} if section == "notes"
                    else bool(GENERATED_METADATA_RE.fullmatch(lines[i].strip()))))
                if all(generated(i) for i in range(index + 1, end)):
                    remove.update(range(index, end))
        elif kind == "list":
            checklist = CHECKLIST_ITEM_RE.match(line)
            if checklist and checklist.group("state").lower() == "x":
                remove.update(range(index, item_end(lines, kinds, index)))
            elif line.strip() == "- (none yet)":
                remove.add(index)
    sanitized = "\n".join(line for i, line in enumerate(lines) if i not in remove).strip()
    return sanitized + "\n" if sanitized else ""


def validate_current_body(body: str) -> None:
    """Never silently discard substantive legacy sections to normalize an update."""
    document = parse_todo_body(body)
    for index, (line, kind) in enumerate(zip(document.lines, document.kinds)):
        match = CHECKLIST_ITEM_RE.match(line) if kind == "list" else None
        if match and match.group("state").lower() == "x" and has_unfinished_descendants(
            document, index, item_end(document.lines, document.kinds, index)
        ):
            raise TodoError(
                "A completed item still contains unfinished child items. Read the body "
                "and use write to reconcile or move the remaining work before completing "
                "the parent; nothing has been changed.", code="todo_reconciliation_required",
            )
    section = ""
    title_seen = False
    for line, kind in zip(document.lines, document.kinds):
        if kind == "blank":
            continue
        if kind == "h1" and not title_seen and not section:
            title_seen = True
            continue
        if kind == "h2":
            section = section_key(HEADING_RE.match(line).group("title"))
            if section in ACTIVE_SECTIONS:
                continue
        elif kind != "h1" and section in ACTIVE_SECTIONS:
            continue
        raise TodoError(
            "To-do content must be under Now, Next, or Parked. Read the existing body "
            "and use write to reconcile substantive legacy Notes, Metadata, timestamped "
            "notes, or other sections into current unfinished work and discussion; "
            "nothing has been changed.",
            code="todo_reconciliation_required",
        )


def render_template(args: argparse.Namespace, target: ThreadTarget) -> str:
    title = str(getattr(args, "title", "") or "").strip() or "Thread To-Do List"
    if "\n" in title or "\r" in title:
        raise TodoError("--title must be one heading line; use write for current work and discussion")
    return f"# {title}\n\n## Now\n\n## Next\n\n## Parked\n"


def create_or_write_todo(paths: ThreadOverlayPaths, target: ThreadTarget, body: str, *,
                         pin: bool, action: str, **precondition: Any) -> dict[str, Any]:
    def change(current: str, metadata: dict[str, Any]) -> str:
        prepared = sanitize_forward_looking_body(body)
        validate_current_body(prepared)
        return prepared
    return mutate_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id), path_field=PATH_FIELD,
                          action=action, change=change, allow_create=True, pin=True if pin else None, **precondition)


def insert_item(body: str, section: str, block: list[str]) -> str:
    document = parse_todo_body(body)
    lines = document.lines
    for index, (line, kind) in enumerate(zip(lines, document.kinds)):
        if kind == "h2" and section_key(HEADING_RE.match(line).group("title")) == section:
            end = next((i for i in range(index + 1, len(lines))
                        if document.kinds[i] in {"h1", "h2", "heading"}), len(lines))
            before = [] if end and not lines[end - 1].strip() else [""]
            after = [] if end < len(lines) and not lines[end].strip() else [""]
            lines[end:end] = [*before, *block, *after]
            break
    else:
        lines.extend(["", f"## {SECTION_TITLES[section]}", "", *block, ""])
    return "\n".join(lines).strip() + "\n"


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
    matches = [item for item in document.items if item.item_id == normalized]
    if len(matches) > 1:
        raise TodoError(f"to-do item {normalized} is ambiguous; use write to reconcile duplicate ids")
    if matches:
        return matches[0]
    raise TodoError(f"no to-do item {normalized} exists")


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
            ToolVerb("activate", "writes local to-do state", "Create and pin a forward-looking to-do; empty input preserves an existing body. Explicit content replaces it."),
            ToolVerb("write", "writes local to-do state", "Replace current Now/Next/Parked work and discussion while excluding completion history."),
            ToolVerb("status", "local coordination lock only", "Show exact-task revision, pin state and mutation token without the body, or list pinned to-do lists."),
            ToolVerb("read", "local coordination lock only", "Read the current forward-looking to-do body."),
            ToolVerb(
                "check",
                "local coordination lock only",
                "Read a bounded pinned to-do excerpt with explicit coverage. A complete excerpt is already the body read; recover only missing content.",
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
            "publication": "Body, metadata and derived listings publish separately under document then registry OS locks. Partial errors identify committed/uncertain stages; read back then repair, never replay an edit blindly.",
            "coordination_scope": "Participating current tools only; old copied scripts and arbitrary editors do not acquire these locks.",
            "read_effects": "Read/check/status/outline may create a local coordination lock file; they do not alter body or metadata.",
            "read_coverage": "Text and JSON retain source/view identity, selection and delivered range. A complete check already supplies the body. Reuse adequate retained content; use status when revision/pin freshness matters. Cursors reject changed bytes or transformations; final page alone is not whole-selection receipt.",
            "read_presentation": "Text presents concise readback; JSON retains full structured metadata and navigation. Use one representation. For section ancestry already retained at the same revision, start with --context references; continuation cursors preserve that mode."}},
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
            {
                "code": "todo_reconciliation_required",
                "meaning": "A parent being completed still has unfinished children, or substantive content lies outside Now/Next/Parked. Read it, reconcile the remaining work and discussion with reopen or write, then retry; the failed mutation changes nothing.",
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
    supplied = read_body_arg(args)
    def change(body: str, metadata: dict[str, Any]) -> str:
        if not supplied or not supplied.strip():
            return body if metadata else render_template(args, target)
        prepared = sanitize_forward_looking_body(supplied)
        validate_current_body(prepared)
        return prepared
    metadata = mutate_overlay(paths, target, artifact_path=todo_path(paths, target.thread_id),
                              path_field=PATH_FIELD, action="activate", change=change,
                              allow_create=True, pin=True if not args.no_pin else None, **mutation_options(args))
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
                               view="todo-forward-looking-v2", args=args)
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
        body, status["view"] = overlay_view(captured, sanitize_forward_looking_body, "todo-forward-looking-v2")

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
        if args.command == "clear":
            return render_template(args, target)
        document = parse_todo_body(body)
        if args.command == "add":
            prepared = sanitize_forward_looking_body(body) if body else render_template(args, target)
            validate_current_body(prepared)
            item = TodoItem(section=args.section, checked=False, item_id=next_item_id(document), text=args.text.strip())
            prepared = insert_item(prepared, item.section, [item.to_line()])
            item_result.update(item.to_payload())
        else:
            item = pop_item(document, args.id)
            if args.command == "complete" and has_unfinished_descendants(document, item.start, item.end):
                raise TodoError(
                    f"To-do item {item.item_id} still contains unfinished child items. "
                    "Move or reconcile the remaining work with reopen or write before "
                    "completing the parent; nothing has been changed.",
                    code="todo_reconciliation_required",
                )
            block = document.lines[item.start:item.end]
            del document.lines[item.start:item.end]
            if item.start and not document.lines[item.start - 1].strip():
                # Collapse only the separator seam left by this removal.
                while item.start < len(document.lines) and not document.lines[item.start].strip():
                    del document.lines[item.start]
            prepared = sanitize_forward_looking_body("\n".join(document.lines))
            validate_current_body(prepared)
            if args.command == "reopen":
                # Moving a nested item also moves its descendants and attached detail.
                # Dedent it as one block, preserving relative child indentation.
                indent = leading_indent_width(block[0])
                block = [line.expandtabs(4)[indent:] if leading_indent_width(line) >= indent else line
                         for line in block]
                block[0] = re.sub(r"\[[ xX]\]", "[ ]", block[0], count=1)
                item.checked = False
                item.section = args.section
                prepared = insert_item(prepared, item.section, block)
            elif args.command == "complete":
                item.checked = True
            item_result.update(item.to_payload())
        prepared = sanitize_forward_looking_body(prepared)
        validate_current_body(prepared)
        return prepared
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

    activate = subparsers.add_parser("activate", help="Create and pin a to-do; empty input preserves an existing body, explicit content replaces it.")
    add_common(activate, defaults=False)
    add_selector(activate)
    activate.add_argument("--title", default="Thread To-Do List", help="To-do list title.")
    add_body_args(activate, text_help="Full current Now/Next/Parked body; empty input preserves existing content.")
    activate.add_argument("--no-pin", action="store_true", help="Create without pinning.")
    activate.set_defaults(func=cmd_activate)

    write = subparsers.add_parser("write", help="Replace an existing thread to-do body.")
    add_common(write, defaults=False)
    add_selector(write)
    add_body_args(write, text_help="Full current Now/Next/Parked work and discussion; reconciles substantive legacy sections.")
    write.add_argument("--pin", action="store_true", help="Pin after writing.")
    write.set_defaults(func=cmd_write)

    status = subparsers.add_parser("status", help="Show exact-task revision/pin state and mutation token without the body, or list pinned to-do lists.")
    add_common(status, defaults=False)
    add_selector(status)
    status.set_defaults(func=cmd_status)

    read = subparsers.add_parser("read", help="Read a to-do body.")
    add_common(read, defaults=False)
    add_selector(read)
    read.add_argument("--pinned-only", action="store_true", help="Return inactive status instead of body when unpinned.")
    add_navigation_options(read)
    read.set_defaults(func=cmd_read)

    check = subparsers.add_parser("check", help="Read a bounded pinned to-do excerpt; a complete excerpt already supplies the body.")
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
    for command in (activate, write, pin, unpin, add_item, complete, reopen, remove, clear, repair):
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
