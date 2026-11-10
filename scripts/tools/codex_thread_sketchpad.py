#!/usr/bin/env python3
"""Local-only per-thread sketchpad for Codex intent overlays."""

from __future__ import annotations

import argparse
import sys
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


TOOL_ID = "tool.ops.codex_thread_sketchpad"
ENTRYPOINT = "python scripts/tools/codex_thread_sketchpad.py"
ENTRYPOINT_ARGV = (sys.executable, str(Path(__file__).resolve()))
DEFAULT_MAX_CHECK_CHARS = 4000
OVERLAY_ROOT_NAME = "thread_sketchpads"
BODY_FILENAME = "sketchpad.md"
PATH_FIELD = "sketchpad_path"
ROOT_FIELD = "sketchpad_root"


SketchpadError = ThreadOverlayError


def resolve_paths(args: argparse.Namespace) -> ThreadOverlayPaths:
    return resolve_overlay_paths(args, OVERLAY_ROOT_NAME)


def sketchpad_path(paths: ThreadOverlayPaths, thread_id: str) -> Path:
    return body_path(paths, thread_id, BODY_FILENAME)


def set_pinned(paths: ThreadOverlayPaths, target: ThreadTarget, pinned: bool, **precondition: Any) -> dict[str, Any]:
    return mutate_overlay(paths, target, artifact_path=sketchpad_path(paths, target.thread_id),
                          path_field=PATH_FIELD, action="pin" if pinned else "unpin", pin=pinned, **precondition)


def render_template(args: argparse.Namespace, target: ThreadTarget) -> str:
    now = utc_now()
    main_goal = str(getattr(args, "main_goal", "") or "").strip() or "(fill in the current thread goal)"
    intent_lens = str(getattr(args, "intent_lens", "") or "").strip() or "(what should work be judged through?)"
    return_to = str(getattr(args, "return_to", "") or "").strip() or "(where to loop back after necessary side quests)"
    non_goals = [str(item).strip() for item in getattr(args, "non_goal", []) if str(item).strip()]
    non_goal_text = "\n".join(f"- {item}" for item in non_goals) if non_goals else "- (none yet)"
    title = str(getattr(args, "title", "") or "").strip() or "Thread Sketchpad"
    thread_name = target.thread_name or "(unknown)"
    return "\n".join(
        [
            f"# {title}",
            "",
            "## Main Goal",
            "",
            main_goal,
            "",
            "## Intent Lens",
            "",
            intent_lens,
            "",
            "## Non-Goals / Guardrails",
            "",
            non_goal_text,
            "",
            "## Return-To Thread",
            "",
            return_to,
            "",
            "## Promotion Notes",
            "",
            "- Promote this sketchpad into formal repo docs or plan docs only when the content becomes durable repo intent, current truth, or accepted execution structure.",
            "",
            "## Metadata",
            "",
            f"- thread_id: `{target.thread_id}`",
            f"- thread_title: {thread_name}",
            f"- created_at: {now}",
            "- storage: local-only configured continuity root / thread_sketchpads",
            "- authority: thread-local intent overlay, not repo truth",
            "",
        ]
    )


def create_or_write_sketchpad(paths: ThreadOverlayPaths, target: ThreadTarget, body: str, *,
                              pin: bool, action: str, **precondition: Any) -> dict[str, Any]:
    prepared = body.strip() + "\n"
    return mutate_overlay(paths, target, artifact_path=sketchpad_path(paths, target.thread_id),
                          path_field=PATH_FIELD, action=action, change=lambda body, metadata: prepared, allow_create=True,
                          pin=True if pin else None, **precondition)


def append_note(paths: ThreadOverlayPaths, target: ThreadTarget, note: str, *, pin: bool, **precondition: Any) -> dict[str, Any]:
    def change(body: str, metadata: dict[str, Any]) -> str:
        return body + f"\n## Note {utc_now()}\n\n{note.strip()}\n"
    return mutate_overlay(paths, target, artifact_path=sketchpad_path(paths, target.thread_id),
                          path_field=PATH_FIELD, action="append", change=change,
                          pin=True if pin else None, **precondition)


def sketchpad_status(paths: ThreadOverlayPaths, target: ThreadTarget) -> dict[str, Any]:
    return overlay_status(
        paths,
        target,
        artifact_path=sketchpad_path(paths, target.thread_id),
        path_field=PATH_FIELD,
    )


def pinned_registry_status(paths: ThreadOverlayPaths) -> dict[str, Any]:
    return overlay_pinned_registry_status(paths, root_field=ROOT_FIELD)


def usage_payload() -> dict[str, Any]:
    return tool_usage_payload(
        tool_id=TOOL_ID,
        purpose="Create, pin, read, append, and check local-only per-thread Codex sketchpads.",
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
            ToolVerb("activate", "writes local sketchpad state", "Create or replace a thread sketchpad and pin it by default."),
            ToolVerb("write", "writes local sketchpad state", "Replace an existing thread sketchpad body."),
            ToolVerb("append", "writes local sketchpad state", "Append a timestamped note to a thread sketchpad."),
            ToolVerb("status", "local coordination lock only", "Show one sketchpad status or list pinned sketchpads."),
            ToolVerb("read", "local coordination lock only", "Read a sketchpad body."),
            ToolVerb(
                "check",
                "local coordination lock only",
                "Report a bounded pinned sketchpad excerpt with explicit window and truncation metadata.",
            ),
            ToolVerb("pin", "writes local sketchpad state", "Pin an existing sketchpad."),
            ToolVerb("unpin", "writes local sketchpad state", "Unpin a sketchpad."),
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
            "writes_paths": ["<configured continuity root>/thread_sketchpads/**"],
            "modifies_remote": False,
            "network_access": "none",
            "danger_level": "low",
            "supports_dry_run": False,
        },
        examples=[
            {"label": "Usage JSON", "cmd": f"{ENTRYPOINT} usage --format json"},
            {"label": "Check current pinned sketchpad", "cmd": f"{ENTRYPOINT} check"},
            {"label": "Append note", "cmd": f"{ENTRYPOINT} append --expected <observed-token> --text \"Updated intent lens.\""},
        ],
        docs=["AGENTS.md"],
        errors=[
            {
                "code": "thread_selector_required",
                "meaning": "No current thread id was available and no thread selector was provided.",
            },
            {
                "code": "missing_sketchpad",
                "meaning": "The selected thread does not have a sketchpad for the requested operation.",
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
    metadata = create_or_write_sketchpad(paths, target, body, pin=not args.no_pin, action="activate", **mutation_options(args))
    emit_kv({"action": "activate", "thread": target.__dict__, **metadata}, as_json=args.json)
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    body = read_body_arg(args)
    if body is None:
        raise SketchpadError("write requires --text, --body-file, or --stdin")
    metadata = create_or_write_sketchpad(paths, target, body, pin=args.pin, action="write", **mutation_options(args))
    emit_kv({"action": "write", "thread": target.__dict__, **metadata}, as_json=args.json)
    return 0


def cmd_append(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    note = read_body_arg(args)
    if note is None:
        raise SketchpadError("append requires --text, --body-file, or --stdin")
    metadata = append_note(paths, target, note, pin=args.pin, **mutation_options(args))
    emit_kv({"action": "append", "thread": target.__dict__, **metadata}, as_json=args.json)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=False)
    if target is None:
        emit_kv({"action": "status", **pinned_registry_status(paths)}, as_json=args.json)
        return 0
    emit_kv({"action": "status", **sketchpad_status(paths, target)}, as_json=args.json)
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    payload = navigate_overlay(paths, target, artifact_path=sketchpad_path(paths, target.thread_id),
                               path_field=PATH_FIELD, transform=lambda body: body, view="utf8-newlines-v1", args=args)
    emit_kv({"action": args.command, **payload}, as_json=args.json)
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    result = mutate_overlay(paths, target, artifact_path=sketchpad_path(paths, target.thread_id),
                            path_field=PATH_FIELD, action="repair", repair=True, **mutation_options(args))
    emit_kv({"action": "repair", **result}, as_json=args.json)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    target = resolve_thread_target(args, paths, required=True)
    assert target is not None
    captured = capture_overlay(paths, target, artifact_path=sketchpad_path(paths, target.thread_id), path_field=PATH_FIELD)
    status = overlay_status(paths, target, artifact_path=sketchpad_path(paths, target.thread_id), path_field=PATH_FIELD, captured=captured)
    if args.expected_source is not None and args.expected_source != captured["body_revision"]:
        raise SketchpadError("Stored source changed; reacquire with read or outline.", code="stale_selection")
    active = bool(status["exists"] and status["pinned"])
    body = ""
    if active:
        body, status["view"] = overlay_view(captured, lambda body: body, "utf8-newlines-v1")

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
        noun="sketchpad",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, pin, read, and update local-only per-thread Codex sketchpads."
    )
    add_common(parser, defaults=True)
    parser.set_defaults(func=cmd_usage)
    subparsers = parser.add_subparsers(dest="command")

    usage = subparsers.add_parser("usage", help="Return the clean-command usage contract.")
    add_common(usage, defaults=False)
    usage.set_defaults(func=cmd_usage)

    activate = subparsers.add_parser("activate", help="Create or replace a thread sketchpad and pin it by default.")
    add_common(activate, defaults=False)
    add_selector(activate)
    activate.add_argument("--title", default="Thread Sketchpad", help="Sketchpad title.")
    activate.add_argument("--main-goal", default="", help="Primary thread goal.")
    activate.add_argument("--intent-lens", default="", help="How work should be evaluated.")
    activate.add_argument("--non-goal", action="append", default=[], help="Non-goal or guardrail. Repeatable.")
    activate.add_argument("--return-to", default="", help="Main thread to return to after side quests.")
    activate.add_argument("--text", default=None, help="Full sketchpad body.")
    activate.add_argument("--body-file", default=None, help="Read full sketchpad body from file.")
    activate.add_argument("--stdin", action="store_true", help="Read full sketchpad body from stdin.")
    activate.add_argument("--no-pin", action="store_true", help="Create without pinning.")
    activate.set_defaults(func=cmd_activate)

    write = subparsers.add_parser("write", help="Replace an existing thread sketchpad body.")
    add_common(write, defaults=False)
    add_selector(write)
    write.add_argument("--text", default=None, help="Full sketchpad body.")
    write.add_argument("--body-file", default=None, help="Read full sketchpad body from file.")
    write.add_argument("--stdin", action="store_true", help="Read full sketchpad body from stdin.")
    write.add_argument("--pin", action="store_true", help="Pin after writing.")
    write.set_defaults(func=cmd_write)

    append = subparsers.add_parser("append", help="Append a timestamped note to a thread sketchpad.")
    add_common(append, defaults=False)
    add_selector(append)
    append.add_argument("--text", default=None, help="Note text.")
    append.add_argument("--body-file", default=None, help="Read note text from file.")
    append.add_argument("--stdin", action="store_true", help="Read note text from stdin.")
    append.add_argument("--pin", action="store_true", help="Pin after appending.")
    append.set_defaults(func=cmd_append)

    status = subparsers.add_parser("status", help="Show one sketchpad status, or list pinned sketchpads.")
    add_common(status, defaults=False)
    add_selector(status)
    status.set_defaults(func=cmd_status)

    read = subparsers.add_parser("read", help="Read a sketchpad body.")
    add_common(read, defaults=False)
    add_selector(read)
    read.add_argument("--pinned-only", action="store_true", help="Return inactive status instead of body when unpinned.")
    add_navigation_options(read)
    read.set_defaults(func=cmd_read)

    check = subparsers.add_parser("check", help="Agent hook: report/read the pinned sketchpad for a selected thread.")
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
        help="Zero-based Unicode-character offset into the active sketchpad.",
    )
    window.add_argument(
        "--tail",
        action="store_true",
        help="Return the final --max-chars Unicode characters.",
    )
    check.add_argument("--expected-source", help="Require the exact stored-byte revision returned by an earlier check.")
    check.set_defaults(func=cmd_check)

    pin = subparsers.add_parser("pin", help="Pin an existing sketchpad.")
    add_common(pin, defaults=False)
    add_selector(pin)
    pin.set_defaults(func=cmd_pin)

    unpin = subparsers.add_parser("unpin", help="Unpin a sketchpad.")
    add_common(unpin, defaults=False)
    add_selector(unpin)
    unpin.set_defaults(func=cmd_unpin)

    outline = subparsers.add_parser("outline", help="List exact revision-bound section occurrences and preamble.")
    add_common(outline, defaults=False)
    add_selector(outline)
    outline.set_defaults(func=cmd_read)
    repair = subparsers.add_parser("repair", help="Reconcile recorded body revision and derived listings, preserving known pin state; never replay an edit.")
    add_common(repair, defaults=False)
    add_selector(repair)
    repair.set_defaults(func=cmd_repair)
    for command in (activate, write, append, pin, unpin, repair):
        add_mutation_options(command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.json = normalize_format(args.format, json_alias=args.json) == "json"
    try:
        return int(args.func(args))
    except SketchpadError as exc:
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
