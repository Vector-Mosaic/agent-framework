from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.tools import tool_catalog_query as query


def tool(tool_id: str, description: str = "", **extra):
    return {"id": tool_id, "name": tool_id.rsplit(".", 1)[-1], "description": description, **extra}


def search(tmp_path, capsys, tools, text, *options):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"tools": tools}), encoding="utf-8")
    assert query.main(["search", "--catalog", str(catalog), "--query", text, "--format", "json", *options]) == 0
    return json.loads(capsys.readouterr().out)


def test_search_prefers_distinct_task_coverage_over_repetition_and_paths(tmp_path, capsys):
    results = search(tmp_path, capsys, [
        tool("tool.repetitive", "runtime " * 200),
        tool("tool.path", docs=["runtime/status/local/" * 200]),
        tool("tool.relevant", "Inspect local runtime status."),
    ], "local runtime status")
    assert [row["tool"]["id"] for row in results] == ["tool.relevant", "tool.repetitive", "tool.path"]
    assert results[0]["match"]["matched_terms"]["purpose"] == ["local", "runtime", "status"]


def test_risk_and_status_do_not_create_relevance_or_hide_legacy_capability(tmp_path, capsys):
    results = search(tmp_path, capsys, [
        tool("tool.risk_only", "Install fonts", side_effects={"network_access": "none; provider accounts " * 100}),
        tool("tool.legacy", "Read provider accounts", stability="legacy"),
    ], "provider accounts")
    assert [row["tool"]["id"] for row in results] == ["tool.legacy"]
    assert results[0]["tool"]["safe_first_call"] is None


def test_exact_identity_precedes_description_and_ties_are_stable(tmp_path, capsys):
    tools = [tool("tool.z", "runtime_status"), tool("tool.runtime_status"), tool("tool.a", "runtime_status")]
    results = search(tmp_path, capsys, tools, "runtime_status")
    assert [row["tool"]["id"] for row in results] == ["tool.runtime_status", "tool.a", "tool.z"]
    assert results[0]["match"]["exact"] == "name"
    assert query._resolve_tool(tools, "tool.runtime_status")["id"] == "tool.runtime_status"
    with pytest.raises(query.ToolCatalogQueryError, match="multiple"):
        query._resolve_tool(tools, "tool.")


def test_command_descriptions_aliases_and_tags_participate_without_running_commands(tmp_path, capsys):
    results = search(tmp_path, capsys, [tool(
        "tool.access", commands={"inspect": {"description": "find tenant", "aliases": ["mailbox"], "tags": ["identity"]}},
        tags=["provider"], agent_interface={"json_usage_command": "never execute this declaration"},
    )], "tenant mailbox identity provider")
    assert results[0]["match"]["unmatched_terms"] == []
    assert results[0]["tool"]["safe_first_call"] == "never execute this declaration"


def test_query_repetition_does_not_change_score_and_category_filter_survives(tmp_path, capsys):
    tools = [tool("tool.one", "status", category="ops"), tool("tool.two", "status", category="dev_helper")]
    first = search(tmp_path, capsys, tools, "status", "--category", "ops")
    repeated = search(tmp_path, capsys, tools, "status status status", "--category", "ops")
    assert first == repeated


def test_named_and_positional_selectors_keep_existing_containers(tmp_path, capsys):
    catalog = tmp_path / "catalog.json"
    record = tool("tool.sample", commands={"usage": {"cmd": "sample usage"}})
    catalog.write_text(json.dumps({"tools": [record]}), encoding="utf-8")
    for selector in (["tool.sample"], ["--tool-id", "tool.sample"]):
        query.main(["commands", "--catalog", str(catalog), *selector, "--json"])
        assert json.loads(capsys.readouterr().out) == {"id": "tool.sample", "commands": record["commands"]}
    query.main(["show", "--catalog", str(catalog), "tool.sample", "--full"])
    assert json.loads(capsys.readouterr().out) == record


def test_usage_is_offline_and_has_current_command_metadata(capsys):
    query.main(["usage", "--catalog", "does-not-exist.json", "--json"])
    payload = json.loads(capsys.readouterr().out)
    details = {entry["name"]: entry for entry in payload["command_details"]}
    parameters = {entry["name"]: entry for entry in details["search"]["parameters"]}
    assert payload["schema_version"] == "tool_usage.v1"
    assert parameters["--limit"]["default"] == query.DEFAULT_LIMIT
    assert parameters["--limit"]["type"] == "int"
    assert parameters["--root"]["default"] is None
    assert parameters["--format"]["choices"] == ["text", "json"]
    assert "lexical" in " ".join(details["search"]["constraints"])
    assert "tool.validator" not in json.dumps(payload)


def test_default_catalog_is_installation_relative_not_current_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert query.main(["summary", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert Path(result["catalog"]) == query.REPO_ROOT_FOR_IMPORTS / "scripts" / "tool_catalog.manifest.json"
    assert result["tool_count"] >= 5


def test_public_catalog_has_only_present_entrypoints_and_document_owners():
    root = query.REPO_ROOT_FOR_IMPORTS
    catalog = query._load_catalog(query._catalog_path(root, None))
    tools = catalog["tools"]
    assert catalog["version"] == 1
    assert catalog["kind"] == "tool_catalog"
    assert len({tool["id"] for tool in tools}) == len(tools)
    for record in tools:
        assert query._safe_first_call(record)
        for entry in record["entrypoints"]:
            assert (root / "scripts" / entry["path"]).is_file(), entry
        for doc in record["docs"]:
            assert (root / doc).is_file(), doc


def test_public_catalog_safe_usage_matches_real_cli_contracts(tmp_path, monkeypatch):
    root = query.REPO_ROOT_FOR_IMPORTS
    catalog = query._load_catalog(query._catalog_path(root, None))
    # Any accidental default-home access stays within disposable synthetic state.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("AGENT_FRAMEWORK_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("AGENT_FRAMEWORK_STATE_HOME", str(tmp_path / "state"))
    for record in catalog["tools"]:
        path = root / "scripts" / record["entrypoints"][0]["path"]
        usage = subprocess.run([sys.executable, str(path), "usage", "--format", "json"], cwd=tmp_path, capture_output=True, text=True, check=True)
        payload = json.loads(usage.stdout)
        assert payload["schema_version"] == "tool_usage.v1", record["id"]
        assert payload["tool_id"] == record["id"]
        assert set(record["agent_interface"]["verbs"]) == {verb["name"] for verb in payload["verbs"]}
        if record["agent_interface"].get("safe_no_arg") == "usage":
            default = subprocess.run([sys.executable, str(path)], cwd=tmp_path, capture_output=True, text=True, check=True)
            assert default.stdout.strip(), record["id"]
    assert list(tmp_path.iterdir()) == []
