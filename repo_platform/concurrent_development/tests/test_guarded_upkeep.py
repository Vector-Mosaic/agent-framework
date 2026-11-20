"""Public primary upkeep through publication, competing input holds and retry."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from repo_platform.concurrent_development import cli, core
from repo_platform.concurrent_development import main_upkeep as owner
from repo_platform.concurrent_development.tests.test_git_mechanics import git


class GuardedUpkeep(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        remote = self.home / "remote.git"
        self.primary = self.home / "primary"
        self.writer = self.home / "writer"
        self.primary.mkdir()
        git(self.home, "init", "--bare", str(remote))
        git(self.primary, "init", "-b", "integration")
        for key, value in (("user.name", "Fixture"), ("user.email", "test@example.invalid"), ("core.autocrlf", "false")):
            git(self.primary, "config", key, value)
        (self.primary / "shared.txt").write_text("base\n")
        (self.primary / "local.txt").write_text("base local\n")
        git(self.primary, "add", ".")
        git(self.primary, "commit", "-m", "base")
        git(self.primary, "remote", "add", "team", str(remote))
        git(self.primary, "push", "team", "integration")
        git(self.primary, "worktree", "add", "--detach", str(self.writer))
        self.service = core.ConcurrentDevelopment(self.writer, self.home / "state", remote="team", shared_branch="integration")
        self.base = git(self.primary, "rev-parse", "HEAD")
        self.service.bind("writer", "writer-owner", self.base)
        self.upkeep = owner.MainUpkeep(self.service)
        self.clean = {"defer_reasons": [], "process_observation_complete": True,
                      "blocking_process_count": 0, "blocking_processes": []}

    def publish(self):
        (self.writer / "shared.txt").write_text("ready\n")
        candidate = self.service.prepare("writer", ["shared.txt"], "ready", 0)["candidate"]
        published = self.service.publish("writer", candidate["candidate_id"], self.base)
        self.assertEqual(published["status"], "published")
        return candidate, published

    def test_publication_queues_only_configured_primary_and_close_and_release_retry(self):
        self.assertFalse(self.upkeep.status()["enabled"])
        self.upkeep.configure(enabled=True, main_root=self.primary, process_scope="host")
        self.upkeep.hold("reader", "actual-reader", ["shared.txt"])
        candidate, published = self.publish()
        target = candidate["commit"]
        self.assertEqual(published["main_upkeep"]["pending_target"], target)
        self.assertEqual(git(self.primary, "rev-parse", "HEAD"), self.base)
        self.service.refresh("writer", target, 1)
        closed = self.service.close("writer", candidate["candidate_id"])
        self.assertEqual(closed["main_upkeep"]["hold_ids"], ["reader"])
        self.assertEqual(self.upkeep.status()["pending_target"], target)
        with self.assertRaisesRegex(core.ConcurrentDevelopmentError, "recorded owner"):
            self.upkeep.release("reader", "someone-else")
        row = {"pid": 456, "started_at_ms": 2345678901234, "role": "kernel", "kind": "node_repl",
               "working_directory_scope": "maintained_checkout"}
        report = {**self.clean, "defer_reasons": ["unmanaged_node_consumer"], "blocking_process_count": 1,
                  "blocking_processes": [row]}
        with patch.object(owner, "observe_refresh_processes", return_value=report):
            released = self.upkeep.release("reader", "actual-reader")["main_upkeep"]
        self.assertEqual(released["blocking_processes"][0]["pid"], 456)
        self.assertEqual(released["blocking_processes"][0]["chat_owner"]["status"], "unknown")
        self.assertEqual(self.upkeep.status()["last_outcome"]["reason"], "runtime_input_boundary")
        self.assertNotIn("chat_owner", self.upkeep.path.read_text())
        display = {**released, "blocking_processes": [{**row, "chat_owner": {"status": "correlated",
                   "name": "Reader", "thread_id": "child-id", "agent_path": "/root/reader",
                   "parent_name": "Build chat", "parent_thread_id": "parent-id"}}]}
        output = io.StringIO()
        cli._emit({"schema_version": "tool_result.v1", "data": {"last_outcome": display}}, "text", stream=output)
        self.assertIn("Node REPL kernel PID 456 — Reader (child-id) /root/reader in Build chat (parent-id) (correlated)",
                      output.getvalue().split("data:\n")[0])
        (self.primary / "local.txt").write_text("staged\n")
        git(self.primary, "add", "local.txt")
        (self.primary / "local.txt").write_text("unstaged\n")
        with patch.object(owner, "observe_refresh_processes", return_value=self.clean):
            self.assertEqual(self.upkeep.attempt()["status"], "refreshed")
        self.assertEqual(git(self.primary, "rev-parse", "HEAD"), target)
        self.assertEqual(git(self.primary, "show", ":local.txt"), "staged")
        self.assertEqual((self.primary / "local.txt").read_text(), "unstaged\n")
        status = self.upkeep.status()
        self.assertIsNone(status["pending_target"])
        self.assertNotIn("blocking_processes", status["last_outcome"])

    def test_process_scope_and_physical_identity_remain_bound(self):
        with patch.object(owner, "current_principal", return_value="configured-user"):
            self.upkeep.configure(enabled=True, main_root=self.primary, process_scope="current_user")
        self.publish()
        with patch.object(owner, "current_principal", return_value="other-user"), patch.object(owner, "observe_refresh_processes") as probe:
            self.assertEqual(self.upkeep.attempt()["reason"], "process_principal_unavailable")
            probe.assert_not_called()
        self.upkeep.configure(enabled=False)
        self.assertIsNotNone(self.upkeep.status()["pending_target"])
        with self.assertRaisesRegex(core.ConcurrentDevelopmentError, "Finish pending"):
            self.upkeep.clear()
        self.upkeep.configure(enabled=True, process_scope="cooperative")
        report = {**self.clean, "process_observation_complete": False}
        with patch.object(owner, "observe_refresh_processes", return_value=report):
            self.assertFalse(self.upkeep.attempt()["process_observation_complete"])
        self.upkeep.configure(enabled=False)
        stored = json.loads(self.upkeep.path.read_bytes())
        corrupted = {**stored, "checkout_identity": [0, 999]}
        core._atomic_write(self.upkeep.path, corrupted)
        with self.assertRaisesRegex(core.ConcurrentDevelopmentError, "physical checkout"):
            self.upkeep.clear()
        core._atomic_write(self.upkeep.path, stored)
        self.assertEqual(self.upkeep.clear()["status"], "unconfigured")

    def test_shared_branch_external_source_requirement_and_bootstrap_refusal(self):
        with self.assertRaisesRegex(core.ConcurrentDevelopmentError, "physical primary"):
            self.upkeep.configure(enabled=True, main_root=self.writer)
        with patch.object(owner, "__file__", str(self.primary / "repo_platform/module.py")):
            with self.assertRaisesRegex(core.ConcurrentDevelopmentError, "separate stable"):
                self.upkeep.configure(enabled=True, main_root=self.primary)
        self.upkeep.configure(enabled=True, main_root=self.primary)
        script = self.primary / "scripts/tools/concurrent_development.py"
        script.parent.mkdir(parents=True)
        independent = Path(__file__).resolve().parents[3] / "scripts/tools/concurrent_development.py"
        shutil.copyfile(independent, script)
        result = subprocess.run([sys.executable, str(script), "usage", "--format", "json"],
                                capture_output=True, env={**os.environ, "PYTHONPATH": ""})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["errors"][0]["code"], "main_upkeep_external_toolkit_required")
