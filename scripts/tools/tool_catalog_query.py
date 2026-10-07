#!/usr/bin/env python3
"""Compact discovery and focused reads for scripts/tool_catalog.manifest.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(REPO_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_FOR_IMPORTS))

from repo_platform.tooling.tool_handshake import (  # noqa: E402
    ToolSelector,
    ToolVerb,
    argparse_command_details,
    emit_payload,
    normalize_format,
    tool_result_payload,
    tool_usage_payload,
)


TOOL_ID = "tool.dev_helper.tool_catalog_query"
ENTRYPOINT = "python scripts/tools/tool_catalog_query.py"
DEFAULT_LIMIT = 10


class ToolCatalogQueryError(RuntimeError):
    """Raised when the tool catalog query cannot resolve the request."""


def _repo_root(raw: str | None) -> Path:
    return Path(raw).expanduser().resolve() if raw else REPO_ROOT_FOR_IMPORTS


def _catalog_path(root: Path, raw: str | None) -> Path:
    if raw:
        candidate = Path(raw).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    return (root / "scripts" / "tool_catalog.manifest.json").resolve()


def _load_catalog(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolCatalogQueryError(f"cannot read catalog {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ToolCatalogQueryError(f"invalid catalog JSON {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("tools"), list):
        raise ToolCatalogQueryError(f"{path} is not a v1 tool catalog object")
    return value


def _tools(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [tool for tool in catalog.get("tools", []) if isinstance(tool, dict)]


def _side_effects(tool: dict[str, Any]) -> dict[str, Any]:
    value = tool.get("side_effects")
    return value if isinstance(value, dict) else {}


def _commands(tool: dict[str, Any]) -> dict[str, Any]:
    value = tool.get("commands")
    return value if isinstance(value, dict) else {}


def _entrypoints(tool: dict[str, Any]) -> list[dict[str, Any]]:
    value = tool.get("entrypoints")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float, bool))]


def _compact_tool(tool: dict[str, Any]) -> dict[str, Any]:
    side_effects = _side_effects(tool)
    commands = _commands(tool)
    return {
        "id": tool.get("id"),
        "name": tool.get("name"),
        "display_name": tool.get("display_name"),
        "category": tool.get("category"),
        "description": tool.get("description"),
        "stability": tool.get("stability"),
        "command_names": sorted(commands.keys()),
        "entrypoints": [
            {"type": entry.get("type"), "path": entry.get("path")}
            for entry in _entrypoints(tool)
        ],
        "docs": _as_str_list(tool.get("docs")),
        "safe_first_call": _safe_first_call(tool),
        "risk": {
            "danger_level": side_effects.get("danger_level"),
            "network_access": side_effects.get("network_access"),
            "writes_repo": side_effects.get("writes_repo"),
            "modifies_remote": side_effects.get("modifies_remote"),
            "idempotent": side_effects.get("idempotent"),
            "default_mode": side_effects.get("default_mode"),
            "supports_dry_run": side_effects.get("supports_dry_run"),
            "destructive_infra": bool(side_effects.get("destructive_infra")),
        },
    }


def _safe_first_call(tool: dict[str, Any]) -> str | None:
    interface = tool.get("agent_interface")
    if isinstance(interface, dict):
        for key in ("json_usage_command", "usage_command"):
            value = interface.get(key)
            if isinstance(value, str) and value.strip():
                return value
    for key in ("usage_json", "usage"):
        command = _commands(tool).get(key)
        value = command.get("cmd") if isinstance(command, dict) else command
        if isinstance(value, str) and value.strip():
            return value
    # Do not guess no-argument safety from an executable path or legacy status.
    return None


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.lower()))


def _search_fields(tool: dict[str, Any]) -> dict[str, set[str]]:
    descriptions = [str(tool.get(key) or "") for key in ("description", "purpose")]
    aliases = _as_str_list(tool.get("aliases")) + _as_str_list(tool.get("tags"))
    command_terms = []
    for name, command in _commands(tool).items():
        command_terms.append(name)
        if isinstance(command, dict):
            command_terms.extend(str(command.get(key) or "") for key in ("description", "summary", "purpose"))
            command_terms.extend(_as_str_list(command.get("aliases")))
            command_terms.extend(_as_str_list(command.get("tags")))
    paths = [str(entry.get("path") or "") for entry in _entrypoints(tool)]
    paths.extend(_as_str_list(tool.get("docs")))
    return {
        "identity": _terms(" ".join(str(tool.get(key) or "") for key in ("id", "name", "display_name"))),
        "purpose": _terms(" ".join(descriptions)),
        "commands": _terms(" ".join(command_terms)),
        "aliases_tags": _terms(" ".join(aliases)),
        "paths": _terms(" ".join(paths)),
    }


def _match_tool(tool: dict[str, Any], query_terms: Sequence[str], query_text: str) -> dict[str, Any]:
    """Bounded lexical coverage, deliberately excluding risk/authority prose."""

    terms = set(query_terms)
    fields = _search_fields(tool)
    matches = {field: sorted(terms & values) for field, values in fields.items() if terms & values}
    primary = set().union(*(set(matches.get(key, [])) for key in ("identity", "purpose", "commands", "aliases_tags")))
    path_only = set(matches.get("paths", [])) - primary
    exact = next((key for key in ("id", "name", "display_name") if query_text == str(tool.get(key) or "").strip().lower()), None)
    # Coverage outranks incidental path mentions. Repetition within a field adds
    # nothing; field bonuses only break ties between equally broad matches.
    coverage = len(primary)
    score = (
        (1_000_000 if exact else 0)
        + 100 * coverage
        + 10 * len(matches.get("identity", [])) / max(len(terms), 1)
        + 5 * len(matches.get("purpose", [])) / max(len(terms), 1)
        + len(path_only) / max(len(terms), 1)
    )
    reason = f"exact {exact}" if exact else f"{coverage}/{len(terms)} task terms"
    if path_only:
        reason += f"; {len(path_only)} additional path terms"
    return {
        "score": round(score, 6),
        "exact": exact,
        "matched_terms": matches,
        "unmatched_terms": sorted(terms - primary - path_only),
        "explanation": reason,
    }


def _score_tool(tool: dict[str, Any], query_terms: Sequence[str], query_text: str) -> float:
    return _match_tool(tool, query_terms, query_text)["score"]


def _matches_category(tool: dict[str, Any], category: str | None) -> bool:
    return category is None or str(tool.get("category") or "") == category


def _resolve_tool(tools: Sequence[dict[str, Any]], selector: str) -> dict[str, Any]:
    needle = selector.lower()
    exact = [
        tool for tool in tools
        if needle in {str(tool.get("id") or "").lower(), str(tool.get("name") or "").lower()}
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [
        tool for tool in tools
        if needle in str(tool.get("id") or "").lower()
        or needle in str(tool.get("name") or "").lower()
        or needle in str(tool.get("display_name") or "").lower()
    ]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ToolCatalogQueryError(f"no tool matched selector '{selector}'")
    matches = ", ".join(str(tool.get("id") or tool.get("name")) for tool in partial[:10])
    suffix = "" if len(partial) <= 10 else f", ... ({len(partial) - 10} more)"
    raise ToolCatalogQueryError(f"selector '{selector}' matched multiple tools: {matches}{suffix}")


def _emit_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False)) or str(getattr(args, "format", "text")) == "json"


def _usage_payload() -> dict[str, Any]:
    return tool_usage_payload(
        tool_id=TOOL_ID,
        purpose="Query the canonical scripts/tool_catalog.manifest.json without loading the whole catalog into context.",
        entrypoint=ENTRYPOINT,
        safe_first_calls=[
            {"label": "usage", "cmd": ENTRYPOINT},
            {"label": "usage-json", "cmd": f"{ENTRYPOINT} usage --format json"},
            {"label": "search", "cmd": f"{ENTRYPOINT} search --query \"task continuity\""},
        ],
        verbs=[
            ToolVerb("usage", "none", "Return this usage contract."),
            ToolVerb("summary", "none", "Print compact catalog counts."),
            ToolVerb("list", "none", "List compact tool entries by optional category."),
            ToolVerb("search", "none", "Search compact tool metadata by query terms."),
            ToolVerb("show", "none", "Show one selected tool compactly or with --full JSON."),
            ToolVerb("commands", "none", "Show cataloged commands for one selected tool."),
            ToolVerb("risks", "none", "Show side-effect and verification metadata for one selected tool."),
        ],
        selectors=[
            ToolSelector("--root", False, "Repository containing scripts/tool_catalog.manifest.json; defaults to this tool's installation."),
            ToolSelector("--catalog", False, "Optional tool catalog path."),
            ToolSelector("--tool-id", False, "Preferred explicit selector for one tool; positional selector is retained."),
            ToolSelector("--category", False, "Filter list/search results by catalog category."),
            ToolSelector("--query", False, "Preferred named search query; positional query terms are retained."),
        ],
        output_formats=["text", "json"],
        side_effects={
            "writes_repo": False,
            "writes_paths": [],
            "modifies_remote": False,
            "network_access": "none",
            "danger_level": "low",
            "supports_dry_run": False,
        },
        examples=[
            {"label": "Usage JSON", "cmd": f"{ENTRYPOINT} usage --format json"},
            {"label": "Search tools", "cmd": f"{ENTRYPOINT} search --query \"task continuity\""},
            {"label": "Commands JSON", "cmd": f"{ENTRYPOINT} commands --root . --tool-id {TOOL_ID} --format json"},
        ],
        docs=[
            "docs/instructions/repo_organization/tool_interfaces.md",
        ],
        errors=[
            {
                "code": "selector_not_found",
                "meaning": "The tool selector did not match any cataloged tool.",
            },
            {
                "code": "selector_ambiguous",
                "meaning": "The selector matched more than one tool; pass --tool-id.",
            },
        ],
        command_details=argparse_command_details(build_parser(), semantics={
            name: {
                "prerequisites": [] if name == "usage" else ["A readable canonical catalog at --root/scripts/tool_catalog.manifest.json or --catalog."],
                "side_effects": "Offline metadata read only; never invokes a discovered command or probes provider access.",
                "constraints": (
                    ["Provide --query or positional query words; --query takes precedence. The query must contain a searchable term.", "--limit must be positive. Search is lexical; it does not interpret negation or prove suitability."] if name == "search"
                    else ["Provide --tool-id or a positional selector; --tool-id takes precedence. Ambiguous partial selectors require an exact ID."] if name in {"show", "commands", "risks"}
                    else ["--limit must be positive; --all bypasses the list limit."] if name == "list"
                    else []
                ),
                "errors": [] if name == "usage" else [{"code": "catalog_query_error", "meaning": "Catalog read/shape failure or unresolved selector/query.", "retry_hint": "Correct the catalog path or selector; use an exact ID after an ambiguous match."}],
            }
            for name in ("usage", "summary", "list", "search", "show", "commands", "risks")
        }),
    )


def cmd_usage(args: argparse.Namespace) -> int:
    output_format = normalize_format(getattr(args, "format", "text"), json_alias=bool(getattr(args, "json", False)))
    emit_payload(_usage_payload(), output_format=output_format, stream=sys.stdout)
    return 0


def _print_tool_line(tool: dict[str, Any]) -> None:
    compact = _compact_tool(tool)
    risk = compact["risk"]
    commands = ",".join(compact["command_names"][:6])
    if len(compact["command_names"]) > 6:
        commands += f",...+{len(compact['command_names']) - 6}"
    print(
        f"{compact['id']} | {compact['category']} | danger={risk.get('danger_level')} "
        f"network={risk.get('network_access')} writes_repo={risk.get('writes_repo')} | "
        f"commands={commands}"
    )
    print(f"  {compact.get('description')}")
    print(f"  safe first call: {compact.get('safe_first_call') or 'not declared; inspect owning documentation'}")


def cmd_summary(args: argparse.Namespace) -> int:
    catalog = _load_catalog(_catalog_path(_repo_root(args.root), args.catalog))
    tools = _tools(catalog)
    category_counts = Counter(str(tool.get("category") or "<missing>") for tool in tools)
    entrypoint_counts = Counter(
        str(entry.get("type") or "<missing>")
        for tool in tools
        for entry in _entrypoints(tool)
    )
    command_count = sum(len(_commands(tool)) for tool in tools)
    payload = {
        "catalog": str(_catalog_path(_repo_root(args.root), args.catalog)),
        "tool_count": len(tools),
        "command_count": command_count,
        "category_counts": dict(sorted(category_counts.items())),
        "entrypoint_type_counts": dict(sorted(entrypoint_counts.items())),
    }
    if _wants_json(args):
        _emit_json(payload)
    else:
        print(f"catalog: {payload['catalog']}")
        print(f"tool_count: {payload['tool_count']}")
        print(f"command_count: {payload['command_count']}")
        print(f"category_counts: {payload['category_counts']}")
        print(f"entrypoint_type_counts: {payload['entrypoint_type_counts']}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    catalog = _load_catalog(_catalog_path(_repo_root(args.root), args.catalog))
    tools = [tool for tool in _tools(catalog) if _matches_category(tool, args.category)]
    tools.sort(key=lambda tool: (str(tool.get("category") or ""), str(tool.get("id") or "")))
    selected = tools if args.all else tools[: args.limit]
    if _wants_json(args):
        _emit_json([_compact_tool(tool) for tool in selected])
    else:
        print(f"showing {len(selected)} of {len(tools)} tools")
        for tool in selected:
            _print_tool_line(tool)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    catalog = _load_catalog(_catalog_path(_repo_root(args.root), args.catalog))
    raw_query = args.named_query if args.named_query is not None else " ".join(args.query)
    query_text = raw_query.strip().lower()
    if not query_text:
        raise ToolCatalogQueryError("search query must not be empty")
    query_terms = sorted(_terms(query_text))
    if not query_terms:
        raise ToolCatalogQueryError("search query must contain a searchable term")
    scored = []
    for tool in _tools(catalog):
        if not _matches_category(tool, args.category):
            continue
        match = _match_tool(tool, query_terms, query_text)
        if match["score"] > 0:
            scored.append((match, tool))
    scored.sort(key=lambda item: (not bool(item[0]["exact"]), -item[0]["score"], str(item[1].get("id") or "")))
    selected = scored[: args.limit]
    if _wants_json(args):
        _emit_json([
            {"score": match["score"], "tool": _compact_tool(tool), "match": {key: value for key, value in match.items() if key != "score"}}
            for match, tool in selected
        ])
    else:
        print(f"matches: {len(scored)}; showing: {len(selected)}")
        for match, tool in selected:
            print(f"score={match['score']} | {match['explanation']}")
            _print_tool_line(tool)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    catalog = _load_catalog(_catalog_path(_repo_root(args.root), args.catalog))
    selector = args.tool_id or args.selector
    if not selector:
        raise ToolCatalogQueryError("show requires --tool-id or a selector; run `usage` for examples")
    tool = _resolve_tool(_tools(catalog), selector)
    payload = tool if args.full else _compact_tool(tool)
    if _wants_json(args) or args.full:
        _emit_json(payload)
    else:
        _print_tool_line(tool)
        docs = _as_str_list(tool.get("docs"))
        if docs:
            print(f"  docs: {', '.join(docs)}")
    return 0


def cmd_commands(args: argparse.Namespace) -> int:
    catalog = _load_catalog(_catalog_path(_repo_root(args.root), args.catalog))
    selector = args.tool_id or args.selector
    if not selector:
        raise ToolCatalogQueryError("commands requires --tool-id or a selector; run `usage` for examples")
    tool = _resolve_tool(_tools(catalog), selector)
    commands = _commands(tool)
    if _wants_json(args):
        _emit_json({"id": tool.get("id"), "commands": commands})
    else:
        print(f"tool: {tool.get('id')}")
        for name in sorted(commands):
            command = commands[name]
            cmd = command.get("cmd") if isinstance(command, dict) else command
            print(f"{name}: {cmd}")
    return 0


def cmd_risks(args: argparse.Namespace) -> int:
    catalog = _load_catalog(_catalog_path(_repo_root(args.root), args.catalog))
    selector = args.tool_id or args.selector
    if not selector:
        raise ToolCatalogQueryError("risks requires --tool-id or a selector; run `usage` for examples")
    tool = _resolve_tool(_tools(catalog), selector)
    side_effects = _side_effects(tool)
    payload = {
        "id": tool.get("id"),
        "name": tool.get("name"),
        "side_effects": side_effects,
        "verification": tool.get("verification") if isinstance(tool.get("verification"), dict) else {},
    }
    if _wants_json(args):
        _emit_json(payload)
    else:
        print(f"tool: {payload['id']}")
        for key in (
            "danger_level",
            "network_access",
            "writes_repo",
            "writes_paths",
            "reads_paths",
            "modifies_remote",
            "idempotent",
            "supports_dry_run",
            "default_mode",
            "destructive_infra",
        ):
            if key in side_effects:
                print(f"{key}: {side_effects[key]}")
        verification = payload["verification"]
        if verification:
            print(f"success: {verification.get('success')}")
            print(f"failure: {verification.get('failure')}")
    return 0


def _add_common(parser: argparse.ArgumentParser, *, defaults: bool) -> None:
    default = None if defaults else argparse.SUPPRESS
    parser.add_argument("--root", default=None if defaults else argparse.SUPPRESS, help="Repository root. Defaults to this tool's installation, independent of the current working directory.")
    parser.add_argument("--catalog", default=default, help="Optional tool catalog path. Defaults to scripts/tool_catalog.manifest.json.")
    parser.add_argument("--format", choices=("text", "json"), default="text" if defaults else argparse.SUPPRESS, help="Output format.")
    parser.add_argument("--json", action="store_true", default=False if defaults else argparse.SUPPRESS, help="Alias for --format json.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=ENTRYPOINT, description=__doc__)
    _add_common(parser, defaults=True)
    parser.set_defaults(func=cmd_usage)
    subparsers = parser.add_subparsers(dest="command")

    usage_parser = subparsers.add_parser("usage", help="Return the clean-command usage contract.")
    _add_common(usage_parser, defaults=False)
    usage_parser.set_defaults(func=cmd_usage)

    summary_parser = subparsers.add_parser("summary", help="Print compact catalog counts.")
    _add_common(summary_parser, defaults=False)
    summary_parser.set_defaults(func=cmd_summary)

    list_parser = subparsers.add_parser("list", help="List compact tool entries.")
    _add_common(list_parser, defaults=False)
    list_parser.add_argument("--category", help="Filter by category.")
    list_parser.add_argument("--limit", type=int, default=25, help="Maximum rows unless --all is used.")
    list_parser.add_argument("--all", action="store_true", help="Show all matching rows.")
    list_parser.set_defaults(func=cmd_list)

    search_parser = subparsers.add_parser("search", help="Search compact catalog metadata.")
    _add_common(search_parser, defaults=False)
    search_parser.add_argument("query", nargs="*", help="Search terms.")
    search_parser.add_argument("--query", dest="named_query", help="Named search query. Prefer this in agent-facing examples.")
    search_parser.add_argument("--category", help="Filter by category.")
    search_parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum matches to show.")
    search_parser.set_defaults(func=cmd_search)

    show_parser = subparsers.add_parser("show", help="Show one tool entry compactly, or full JSON with --full.")
    _add_common(show_parser, defaults=False)
    show_parser.add_argument("selector", nargs="?", help="Tool id, name, display name, or unique substring.")
    show_parser.add_argument("--tool-id", help="Explicit tool catalog id selector.")
    show_parser.add_argument("--full", action="store_true", help="Emit the full canonical entry for one tool.")
    show_parser.set_defaults(func=cmd_show)

    commands_parser = subparsers.add_parser("commands", help="Show commands for one tool.")
    _add_common(commands_parser, defaults=False)
    commands_parser.add_argument("selector", nargs="?", help="Tool id, name, display name, or unique substring.")
    commands_parser.add_argument("--tool-id", help="Explicit tool catalog id selector.")
    commands_parser.set_defaults(func=cmd_commands)

    risks_parser = subparsers.add_parser("risks", help="Show side effects and verification contract for one tool.")
    _add_common(risks_parser, defaults=False)
    risks_parser.add_argument("selector", nargs="?", help="Tool id, name, display name, or unique substring.")
    risks_parser.add_argument("--tool-id", help="Explicit tool catalog id selector.")
    risks_parser.set_defaults(func=cmd_risks)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "limit", 1) is not None and getattr(args, "limit", 1) <= 0:
        raise ToolCatalogQueryError("--limit must be positive")
    return int(args.func(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ToolCatalogQueryError as exc:
        wants_json = "--json" in sys.argv[1:] or "--format" in sys.argv[1:] and "json" in sys.argv[1:]
        if wants_json:
            emit_payload(
                tool_result_payload(
                    tool_id=TOOL_ID,
                    verb="error",
                    status="error",
                    summary=str(exc),
                    errors=[
                        {
                            "code": "catalog_query_error",
                            "message": str(exc),
                            "retry_hint": "Run `python scripts/tools/tool_catalog_query.py usage`.",
                        }
                    ],
                ),
                output_format="json",
                stream=sys.stderr,
            )
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("Retry hint: run `python scripts/tools/tool_catalog_query.py usage`.", file=sys.stderr)
        raise SystemExit(1)
