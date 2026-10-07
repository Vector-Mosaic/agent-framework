from __future__ import annotations

import argparse
import io
import json

from repo_platform.tooling.tool_handshake import (
    ToolSelector,
    ToolVerb,
    argparse_command_details,
    emit_payload,
    format_usage_text,
    tool_usage_payload,
)


def usage(**extra):
    return tool_usage_payload(
        tool_id="tool.example", purpose="Example public interface", entrypoint="example",
        safe_first_calls=[{"label": "help", "cmd": "example usage"}],
        verbs=[ToolVerb("apply", "writes selected fixture", "Apply one fixture")],
        selectors=[ToolSelector("--id", True, "Exact fixture identity")],
        output_formats=["text", "json"], side_effects={"writes_repo": False, "network_access": "none"},
        examples=[{"label": "apply", "cmd": "example apply --id fixture"}],
        docs=["docs/owner.md"], errors=[{"code": "conflict", "meaning": "Changed fixture", "retry_hint": "Read the new fixture first"}],
        **extra,
    )


def test_legacy_payload_shape_is_unchanged_and_text_exposes_material_information():
    payload = usage()
    assert "command_details" not in payload
    assert payload["verbs"] == [{"name": "apply", "side_effects": "writes selected fixture", "summary": "Apply one fixture"}]
    text = format_usage_text(payload)
    for expected in ("writes selected fixture", "writes_repo: false", "network_access: none", "example apply --id fixture", "Changed fixture", "Read the new fixture first"):
        assert expected in text


def test_command_metadata_and_other_extensions_remain_visible_in_both_formats():
    details = [{
        "name": "apply", "syntax": "example apply --id ID",
        "parameters": [{"name": "--id", "required": True, "type": "string", "choices": ["fixture"], "default": "fixture"}],
        "constraints": ["one identity only"], "prerequisites": ["fixture exists"],
        "authority": "owning task", "side_effects": {"writes": "selected fixture"},
        "errors": [{"code": "busy", "meaning": "Active writer", "retry_hint": "Wait for that writer"}],
    }]
    payload = usage(command_details=details, extra={"protected_helper": {"command": "owned helper", "enabled": False, "attempts": 0}})
    stream = io.StringIO()
    emit_payload(payload, output_format="json", stream=stream)
    assert json.loads(stream.getvalue())["command_details"] == details
    text = format_usage_text(payload)
    for expected in ("one identity only", "fixture exists", "owning task", "Wait for that writer", "choices:", "default: fixture", "enabled: false", "attempts: 0", "owned helper"):
        assert expected in text


def test_argparse_projection_tracks_defaults_types_choices_and_combinations_without_running():
    parser = argparse.ArgumentParser(prog="example")
    parser.add_argument("--root", default=".")
    global_group = parser.add_mutually_exclusive_group()
    global_group.add_argument("--online", action="store_true")
    global_group.add_argument("--offline", action="store_true")
    child = parser.add_subparsers().add_parser("apply")
    child.add_argument("--root", default=argparse.SUPPRESS)
    child.add_argument("--count", type=int, default=3, choices=[1, 3, 5])
    child.add_argument("--json", action="store_true")
    group = child.add_mutually_exclusive_group(required=True)
    group.add_argument("--id")
    group.add_argument("--all", action="store_true")
    child.set_defaults(func=lambda args: (_ for _ in ()).throw(AssertionError("must not execute")))
    details = argparse_command_details(parser, semantics={"apply": {"prerequisites": ["approved target"], "constraints": ["count limits work"], "parameters": ["must not override parser"]}})
    params = {entry["name"]: entry for entry in details[0]["parameters"]}
    assert params["--root"]["default"] == "."
    assert params["--count"]["type"] == "int"
    assert params["--count"]["choices"] == [1, 3, 5]
    assert params["--json"]["type"] == "boolean"
    assert details[0]["constraints"] == [
        {"kind": "at_most_one", "parameters": ["--online", "--offline"]},
        {"kind": "exactly_one", "parameters": ["--id", "--all"]},
        "count limits work",
    ]
    assert details[0]["prerequisites"] == ["approved target"]
