"""Read-only owner lookup against disposable Codex profile metadata."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from repo_platform.concurrent_development.repl_chat_owner import resolve_chat_owners


FIRST = "019af011-1111-7111-8111-111111111111"
SECOND = "019af022-2222-7222-8222-222222222222"
PARENT = "019af033-3333-7333-8333-333333333333"
START = int(datetime(2025, 1, 7, 10, 20, 30, tzinfo=timezone.utc).timestamp())
PRIVATE = "private-content-not-for-diagnostics"


def invocation(tool: str, namespace: str) -> dict:
    return {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": tool,
            "namespace": namespace, "arguments": json.dumps({"code": PRIVATE})}


def dispatch(source: str) -> dict:
    return {"type": "custom_tool_call", "id": "ct_2", "call_id": "call_2",
            "name": "exec", "namespace": "functions", "input": source}


@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                 "Codex chat ownership is resolved in the unprivileged profile only")
class ReplChatOwner(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.profile = self.directory / ".codex"
        self.profile.mkdir(mode=0o700)
        self.config = self.profile / "config.toml"
        self.config.write_text("", encoding="utf-8")
        self.database = self.profile / "state_5.sqlite"
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript("""
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY, name TEXT, title TEXT, agent_path TEXT,
                    agent_nickname TEXT, created_at INTEGER, updated_at INTEGER,
                    rollout_path TEXT, cwd TEXT, archived INTEGER DEFAULT 0
                );
                CREATE TABLE thread_spawn_edges (
                    parent_thread_id TEXT, child_thread_id TEXT, status TEXT
                );
            """)

    def add_thread(self, thread_id: str, *events: dict, name: str | None = None,
                   agent_path: str | None = None) -> Path:
        day = self.profile / "sessions" / "2025" / "01" / "07"
        day.mkdir(parents=True, exist_ok=True)
        rollout = day / f"rollout-2025-01-07T10-00-00-{thread_id}.jsonl"
        def record(event: dict) -> str:
            return json.dumps({"timestamp": "2025-01-07T10:20:30.300Z", "type": "response_item",
                               "payload": event}, separators=(",", ":")) + "\n"
        rollout.write_text("".join(record(event) for event in events), encoding="utf-8")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO threads(id,name,title,agent_path,created_at,updated_at,rollout_path,cwd)"
                       " VALUES (?,?,?,?,?,?,?,?)",
                       (thread_id, name, PRIVATE, agent_path, START - 1230, START + 30, str(rollout),
                        str(self.directory / "repo")))
        return rollout

    def owner(self, **updates) -> dict:
        process = {"pid": 321, "parent_pid": 123, "started_at_ms": START * 1000 + 100,
                   "kind": "computer_use_repl", "role": "kernel",
                   "working_directory_scope": "maintained_checkout", "owner_lookup_eligible": True,
                   **updates}
        (result,) = resolve_chat_owners(self.config, [process])
        self.assertEqual(result["pid"], process["pid"])
        self.assertNotIn(PRIVATE, json.dumps(result))
        return result["chat_owner"]

    def test_direct_binding_includes_subagent_and_owning_chat_without_prompt_title(self) -> None:
        self.add_thread(PARENT, name="Example project")
        self.add_thread(FIRST, agent_path="/root/subtask")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO thread_spawn_edges VALUES (?,?,?)", (PARENT, FIRST, "completed"))
        owner = self.owner(thread_id=FIRST)
        self.assertEqual(owner["status"], "direct")
        self.assertEqual(owner["thread_id"], FIRST)
        self.assertEqual(owner["agent_path"], "/root/subtask")
        self.assertEqual(owner["parent_thread_id"], PARENT)
        self.assertEqual(owner["parent_name"], "Example project")

    def test_direct_id_survives_older_profile_without_optional_display_metadata(self) -> None:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript("""
                DROP TABLE thread_spawn_edges;
                DROP TABLE threads;
                CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT);
            """)
            db.execute("INSERT INTO threads VALUES (?,?)", (FIRST, PRIVATE))
        owner = self.owner(thread_id=FIRST)
        self.assertEqual((owner["status"], owner["thread_id"]), ("direct", FIRST))
        self.assertNotIn("name", owner)

    def test_matching_cua_call_is_correlated_and_does_not_modify_evidence(self) -> None:
        path = self.add_thread(FIRST, invocation("js", "mcp__cua_repl"), name="UI verification")
        before = [(p, p.read_bytes(), p.stat().st_mtime_ns) for p in (self.database, path)]
        owner = self.owner()
        self.assertEqual((owner["status"], owner["thread_id"], owner["name"]),
                         ("correlated", FIRST, "UI verification"))
        self.assertEqual(before, [(p, p.read_bytes(), p.stat().st_mtime_ns) for p, _, _ in before])

    def test_actual_node_dispatch_matches_despite_overlapping_unrelated_tool(self) -> None:
        self.add_thread(FIRST, dispatch('const r = await tools.mcp__node_repl__js({code: "1 + 1"}); text(r);'))
        self.add_thread(SECOND, invocation("js", "mcp__cua_repl"))
        owner = self.owner(kind="node_repl")
        self.assertEqual((owner["status"], owner["thread_id"]), ("correlated", FIRST))

    def test_two_chats_using_the_same_tool_do_not_identify_one_owner(self) -> None:
        for thread in (FIRST, SECOND):
            self.add_thread(thread, invocation("js", "mcp__cua_repl"))
        owner = self.owner()
        self.assertEqual(owner["status"], "unknown")
        self.assertNotIn("thread_id", owner)

    def test_native_exec_options_before_a_node_dispatch_still_identify_the_chat(self) -> None:
        self.add_thread(FIRST, dispatch('// @exec: {"yield_time_ms": 1000}\n'
                                        'text((await tools.mcp__node_repl__js({code: "1"})).content)'))
        self.assertEqual(self.owner(kind="node_repl")["thread_id"], FIRST)

    def test_strings_comments_messages_and_outputs_do_not_supply_invocation_evidence(self) -> None:
        example = "tools.mcp__node_repl__js({})"
        self.add_thread(FIRST, {"type": "message", "role": "user", "content": [{"type": "input_text", "text": example}]},
                        {"type": "function_call_output", "call_id": "other", "output": example},
                        dispatch('text("tools.mcp__node_repl__js({})");'),
                        dispatch("// " + example + "\ntext(1);"))
        self.assertEqual(self.owner(kind="node_repl")["status"], "unknown")

    def test_foreign_principal_cannot_be_matched_to_this_profiles_thread(self) -> None:
        self.add_thread(FIRST, invocation("js", "mcp__cua_repl"), name="Local chat")
        owner = self.owner(thread_id=FIRST, owner_lookup_eligible=False)
        self.assertEqual(owner["status"], "unknown")
        self.assertNotIn("thread_id", owner)

    @unittest.skipUnless(hasattr(os, "geteuid"), "POSIX identity")
    def test_privileged_caller_does_not_open_a_users_database(self) -> None:
        self.add_thread(FIRST, name="Ordinary profile")
        with mock.patch.object(os, "geteuid", return_value=0), mock.patch.object(sqlite3, "connect") as opened:
            self.assertEqual(self.owner(thread_id=FIRST)["status"], "unknown")
            opened.assert_not_called()

    def test_worker_link_uses_parent_incarnation_and_is_only_correlated(self) -> None:
        self.add_thread(FIRST, name="Ordinary profile")
        common = {"kind": "node_repl", "started_at_ms": START * 1000, "owner_lookup_eligible": True,
                  "parent_pid": 4, "parent_started_at_ms": START * 1000 - 1000}
        kernel = {**common, "role": "kernel", "pid": 5, "thread_id": FIRST}
        worker = {**common, "role": "worker", "pid": 6}
        other = {**worker, "pid": 7, "parent_started_at_ms": START * 1000 - 1001}
        resolved = resolve_chat_owners(self.config, [kernel, worker, other])
        self.assertEqual([r["chat_owner"]["status"] for r in resolved], ["direct", "correlated", "unknown"])
        self.assertEqual(resolved[1]["chat_owner"]["thread_id"], FIRST)
        resolved = resolve_chat_owners(self.config, [kernel, {**kernel, "pid": 8}, worker])
        self.assertEqual(resolved[-1]["chat_owner"]["status"], "unknown")

    def test_sqlite_rollout_pointer_cannot_escape_configured_profile(self) -> None:
        path = self.add_thread(FIRST, invocation("js", "mcp__cua_repl"))
        outside = self.directory / path.name
        path.rename(outside)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE threads SET rollout_path = ?", (str(outside),))
        self.assertEqual(self.owner()["status"], "unknown")

    def test_symlinked_rollout_does_not_supply_matching_evidence(self) -> None:
        path = self.add_thread(FIRST, invocation("js", "mcp__cua_repl"))
        outside = self.directory / path.name
        path.rename(outside)
        try:
            path.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"symlinks unavailable in this fixture: {error}")
        self.assertEqual(self.owner()["status"], "unknown")

    def test_missing_or_unreadable_metadata_preserves_process_and_reports_unknown(self) -> None:
        self.database.unlink()
        self.assertEqual(self.owner()["status"], "unknown")
        self.assertFalse(self.database.exists())
        self.database.write_bytes(b"unreadable SQLite database")
        self.assertEqual(self.owner()["status"], "unknown")
