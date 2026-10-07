"""Small disposable-Git checks for source preservation and publication races."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repo_platform.concurrent_development import core


def git(root: Path, *args: str, data: bytes | None = None) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], input=data, capture_output=True, check=True)
    return result.stdout.decode().replace("\r\n", "\n").strip()


class GitMechanics(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="concurrent-test-")
        self.addCleanup(self.temp.cleanup)
        self.base_dir = Path(self.temp.name)
        self.remote = self.base_dir / "remote.git"
        self.remote.mkdir()
        git(self.remote, "init", "--bare")
        self.seed = self.base_dir / "seed"
        self.seed.mkdir()
        git(self.seed, "init", "-b", "main")
        self.configure(self.seed)
        (self.seed / "a.txt").write_text("one\ntwo\nthree\n")
        (self.seed / "b.txt").write_text("base b\n")
        (self.seed / "binary.dat").write_bytes(b"\0old\xff")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "base")
        git(self.seed, "remote", "add", "origin", str(self.remote))
        git(self.seed, "push", "origin", "main")
        self.base = git(self.seed, "rev-parse", "HEAD")
        self.state = self.base_dir / "state"
        self.root, self.service = self.clone("first")

    def configure(self, path: Path) -> None:
        git(path, "config", "user.name", "Disposable Git Test")
        git(path, "config", "user.email", "git-test@example.invalid")
        git(path, "config", "core.autocrlf", "false")

    def clone(self, identity: str) -> tuple[Path, core.ConcurrentDevelopment]:
        root = self.base_dir / identity
        git(self.base_dir, "clone", "--no-checkout", str(self.remote), str(root))
        self.configure(root)
        git(root, "checkout", "--detach", self.base)
        service = core.ConcurrentDevelopment(root, self.state)
        service.bind(identity, f"task-{identity}", self.base)
        return root, service

    def candidate(self, paths: list[str], *, generation: int = 0,
                  ready_tree: str | None = None) -> dict:
        return self.service.prepare("first", paths, "selected change", generation, ready_tree)["candidate"]

    def test_state_root_precedence_preserves_explicit_and_shared_configuration(self) -> None:
        state_home = self.base_dir / "shared-state-home"
        platform_home = self.base_dir / "platform-state-home"
        with patch.dict(os.environ, {"AGENT_FRAMEWORK_STATE_HOME": str(state_home),
                                     "LOCALAPPDATA": str(platform_home), "XDG_STATE_HOME": str(platform_home)}):
            explicit = core.ConcurrentDevelopment(self.root, self.state)
            shared = core.ConcurrentDevelopment(self.root)
            self.assertEqual(explicit.state_root, self.state)
            self.assertEqual(shared.state_root, state_home / "concurrent-development")
            os.environ.pop("AGENT_FRAMEWORK_STATE_HOME")
            platform = core.ConcurrentDevelopment(self.root)
            platform_name = "AgentFramework" if os.name == "nt" else "agent-framework"
            self.assertEqual(platform.state_root, platform_home / platform_name / "concurrent-development")
        self.assertFalse(state_home.exists())
        self.assertFalse(platform_home.exists())

    def test_configured_destination_and_feature_branch_preserve_other_shared_branch(self) -> None:
        self.service.release_binding("first", "task-first")
        git(self.seed, "push", "origin", "HEAD:integration")
        git(self.root, "remote", "rename", "origin", "upstream")
        git(self.root, "checkout", "-b", "feature/example")
        self.service = core.ConcurrentDevelopment(self.root, self.state, remote="upstream", shared_branch="integration")
        binding = self.service.bind("first", "task-first", self.base)
        self.assertEqual(binding["lifecycle_owner"], "external_workspace")
        self.assertEqual(binding["workspace_ref"], "refs/heads/feature/example")
        (self.root / "a.txt").write_text("READY\ntwo\nthree\n")
        candidate = self.candidate(["a.txt"])
        self.assertEqual(candidate["remote"], "upstream")
        self.assertEqual(candidate["shared_ref"], "refs/heads/integration")
        self.assertEqual(self.service.publish("first", candidate["candidate_id"], self.base)["status"], "published")
        self.service.refresh("first", candidate["commit"], 1)
        self.service.close("first", candidate["candidate_id"])
        self.assertEqual(git(self.root, "symbolic-ref", "HEAD"), "refs/heads/feature/example")
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.base)
        self.assertEqual(git(self.remote, "rev-parse", "integration"), candidate["commit"])
        with self.assertRaises(core.ConcurrentDevelopmentError) as changed:
            core.ConcurrentDevelopment(self.root, self.state, remote="upstream").status("first")
        self.assertEqual(changed.exception.code, "binding_mismatch")

    def test_binding_prevents_refresh_into_a_different_branch(self) -> None:
        git(self.root, "checkout", "-b", "someone-elses-work")
        with self.assertRaises(core.ConcurrentDevelopmentError) as changed:
            self.service.refresh("first", self.base, 0)
        self.assertEqual(changed.exception.code, "workspace_branch_protected")
        self.assertEqual(git(self.root, "symbolic-ref", "HEAD"), "refs/heads/someone-elses-work")

    def test_distinct_fetch_push_endpoint_and_command_options_are_rejected(self) -> None:
        for remote, branch in (("--upload-pack=bad", "main"), ("origin", "../bad"), ("origin", "refs/heads/main")):
            with self.assertRaises(core.ConcurrentDevelopmentError):
                core.ConcurrentDevelopment(self.root, self.state, remote=remote, shared_branch=branch)
        git(self.root, "remote", "set-url", "--push", "origin", str(self.base_dir / "other.git"))
        with self.assertRaises(core.ConcurrentDevelopmentError) as endpoints:
            core.ConcurrentDevelopment(self.root, self.state)
        self.assertEqual(endpoints.exception.code, "remote_endpoint_mismatch")

    def test_ready_and_residual_preserve_unrelated_staging(self) -> None:
        (self.root / "b.txt").write_text("staged unrelated\n")
        git(self.root, "add", "b.txt")
        (self.root / "a.txt").write_text("READY\ntwo\nthree\n")
        (self.root / "untracked.txt").write_text("do not publish\n")
        index = self.service._index_path().read_bytes()
        candidate = self.candidate(["a.txt"])
        self.assertEqual(self.service._index_path().read_bytes(), index)
        self.assertEqual(git(self.root, "show", f"{candidate['commit']}:b.txt"), "base b")
        self.assertEqual(self.service.publish("first", candidate["candidate_id"], self.base)["status"], "published")
        self.assertEqual(self.service._index_path().read_bytes(), index)
        self.service.refresh("first", candidate["commit"], 1)
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"), "b.txt")
        self.assertEqual((self.root / "b.txt").read_text(), "staged unrelated\n")
        self.assertEqual((self.root / "untracked.txt").read_text(), "do not publish\n")
        self.service.close("first", candidate["candidate_id"])
        self.assertIsNone(self.service.status("first")["binding"]["candidate"])

    def test_partial_file_selection_and_newer_unready_edits_survive(self) -> None:
        (self.root / "a.txt").write_text("READY\ntwo\nthree\n")
        ready = self.service._capture(self.base, ["a.txt"])
        (self.root / "a.txt").write_text("READY\ntwo\nUNREADY\n")
        candidate = self.candidate([], ready_tree=ready)
        self.service.publish("first", candidate["candidate_id"], self.base)
        (self.root / "a.txt").write_text("READY\ntwo\nNEWER UNREADY\n")
        self.service.refresh("first", candidate["commit"], 1)
        self.assertEqual((self.root / "a.txt").read_text(), "READY\ntwo\nNEWER UNREADY\n")
        self.assertEqual(git(self.root, "show", "HEAD:a.txt"), "READY\ntwo\nthree")

    def test_racing_publishers_recombine_without_dropping_winner(self) -> None:
        second_root, second = self.clone("second")
        (self.root / "a.txt").write_text("first wins\n")
        (second_root / "b.txt").write_text("second contribution\n")
        first_candidate = self.candidate(["a.txt"])
        other = second.prepare("second", ["b.txt"], "second", 0)["candidate"]
        self.service.publish("first", first_candidate["candidate_id"], self.base)
        self.assertEqual(second.publish("second", other["candidate_id"], self.base)["status"], "stale")
        combined = second.prepare("second", ["b.txt"], "combined", 1)["candidate"]
        second.publish("second", combined["candidate_id"], combined["parent"])
        self.assertEqual(git(second_root, "show", f"{combined['commit']}:a.txt"), "first wins")
        self.assertEqual(git(second_root, "show", f"{combined['commit']}:b.txt"), "second contribution")

    def test_conflict_does_not_change_source_or_index(self) -> None:
        (self.root / "a.txt").write_text("local meaning\n")
        (self.seed / "a.txt").write_text("other meaning\n")
        git(self.seed, "commit", "-am", "shared change")
        git(self.seed, "push", "origin", "main")
        index = self.service._index_path().read_bytes()
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.candidate(["a.txt"])
        self.assertEqual(error.exception.code, "merge_conflict")
        self.assertEqual((self.root / "a.txt").read_text(), "local meaning\n")
        self.assertEqual(self.service._index_path().read_bytes(), index)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base)

    def test_residual_conflict_is_not_published(self) -> None:
        (self.root / "a.txt").write_text("selected alternative\n")
        ready = self.service._capture(self.base, ["a.txt"])
        (self.root / "a.txt").write_text("unready alternative\n")
        (self.seed / "a.txt").write_text("selected alternative\nshared tail\n")
        git(self.seed, "commit", "-am", "shared related")
        git(self.seed, "push", "origin", "main")
        with self.assertRaises(core.ConcurrentDevelopmentError):
            self.candidate([], ready_tree=ready)
        self.assertEqual((self.root / "a.txt").read_text(), "unready alternative\n")

    def test_changed_selection_and_retraction_refuse_publication(self) -> None:
        (self.root / "a.txt").write_text("ready\n")
        candidate = self.candidate(["a.txt"])
        (self.root / "a.txt").write_text("different intent\n")
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.publish("first", candidate["candidate_id"], self.base)
        self.assertEqual(error.exception.code, "ready_selection_changed")
        self.service.retract("first", candidate["candidate_id"])
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.expose("first", candidate["candidate_id"], None)
        self.assertEqual(error.exception.code, "candidate_retracted")

    def test_remote_consumer_prevents_replacement_and_cleanup(self) -> None:
        (self.root / "a.txt").write_text("ready\n")
        candidate = self.candidate(["a.txt"])
        self.service.expose("first", candidate["candidate_id"], None)
        retained = self.service.retain("first", candidate["candidate_id"], "job-1")
        self.assertEqual(retained, candidate)
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.prepare("first", ["a.txt"], "replace", 1)
        self.assertEqual(error.exception.code, "candidate_in_use")
        self.service.retract("first", candidate["candidate_id"])
        with self.assertRaises(core.ConcurrentDevelopmentError):
            self.service.close("first", candidate["candidate_id"])
        self.service.release("first", candidate["candidate_id"], "job-1")
        self.service.close("first", candidate["candidate_id"])
        self.assertIsNone(self.service._remote_oid(candidate["remote_ref"]))

    def test_lost_push_response_is_reconciled_as_published(self) -> None:
        (self.root / "a.txt").write_text("ready\n")
        candidate = self.candidate(["a.txt"])
        real_git = core._git

        def interrupted(root: Path, args: list[str], **kwargs):
            result = real_git(root, args, **kwargs)
            if args[0] == "push":
                raise core.ConcurrentDevelopmentError("git_unavailable", "simulated response loss")
            return result

        with patch.object(core, "_git", side_effect=interrupted):
            result = self.service.publish("first", candidate["candidate_id"], self.base)
        self.assertEqual(result["status"], "published")
        self.assertEqual(self.service.publish("first", candidate["candidate_id"], self.base)["status"], "already_included")

    def test_binary_deletion_new_file_and_literal_path(self) -> None:
        (self.root / "binary.dat").write_bytes(b"\0new\xfe")
        (self.root / "b.txt").unlink()
        (self.root / "literal[1].txt").write_text("new source\n")
        (self.root / "literal1.txt").write_text("unselected\n")
        candidate = self.candidate(["binary.dat", "b.txt", "literal[1].txt"])
        self.assertEqual(core._git(self.root, ["show", f"{candidate['commit']}:binary.dat"]).stdout, b"\0new\xfe")
        files = git(self.root, "ls-tree", "-r", "--name-only", candidate["commit"]).splitlines()
        self.assertIn("literal[1].txt", files)
        self.assertNotIn("literal1.txt", files)
        self.assertNotIn("b.txt", files)

    def test_untracked_collision_blocks_refresh_without_source_loss(self) -> None:
        (self.seed / "new.txt").write_text("shared\n")
        git(self.seed, "add", "new.txt")
        git(self.seed, "commit", "-m", "new shared file")
        git(self.seed, "push", "origin", "main")
        target = git(self.seed, "rev-parse", "HEAD")
        (self.root / "new.txt").write_text("private untracked\n")
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.refresh("first", target, 0)
        self.assertEqual(error.exception.code, "untracked_collision")
        self.assertEqual((self.root / "new.txt").read_text(), "private untracked\n")
        self.assertEqual(self.service.status("first")["binding"]["base"], self.base)

    def test_descriptor_tampering_and_wrong_owner_are_rejected(self) -> None:
        (self.root / "a.txt").write_text("ready\n")
        candidate = self.candidate(["a.txt"])
        modified = {**candidate, "owner_id": "different-owner"}
        with self.assertRaises(core.ConcurrentDevelopmentError):
            core.validate_candidate_descriptor(modified)
        with self.assertRaises(core.ConcurrentDevelopmentError):
            self.service.bind("first", "different-owner", self.base)

    def test_local_commit_and_staging_survive_shared_refresh(self) -> None:
        (self.root / "a.txt").write_text("ordinary local commit\n")
        git(self.root, "commit", "-am", "local save")
        local = git(self.root, "rev-parse", "HEAD")
        (self.root / "b.txt").write_text("staged local\n")
        git(self.root, "add", "b.txt")
        (self.seed / "new.txt").write_text("new shared source\n")
        git(self.seed, "add", "new.txt")
        git(self.seed, "commit", "-m", "shared addition")
        git(self.seed, "push", "origin", "main")
        target = git(self.seed, "rev-parse", "HEAD")
        self.service.refresh("first", target, 0)
        self.assertTrue(self.service._included(local, git(self.root, "rev-parse", "HEAD")))
        self.assertEqual((self.root / "a.txt").read_text(), "ordinary local commit\n")
        self.assertEqual((self.root / "new.txt").read_text(), "new shared source\n")
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"), "b.txt")

    def test_refresh_conflicts_accept_explicit_complete_source_and_index_resolution(self) -> None:
        (self.root / "a.txt").write_text("local intent\n")
        git(self.root, "add", "a.txt")
        (self.seed / "a.txt").write_text("shared intent\n")
        git(self.seed, "commit", "-am", "shared conflicting change")
        git(self.seed, "push", "origin", "main")
        target = git(self.seed, "rev-parse", "HEAD")
        original_index = self.service._index_path().read_bytes()
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.refresh("first", target, 0)
        self.assertEqual(error.exception.code, "merge_conflict")
        self.assertEqual(self.service._index_path().read_bytes(), original_index)
        inputs = {key: error.exception.details[key] for key in (
            "expected_base", "expected_source_tree", "expected_head", "expected_index_tree")}
        # Build the deliberate resolution in a private index, then restore the
        # original source. No real staging or working bytes authorize themselves.
        (self.root / "a.txt").write_text("local and shared intent\n")
        resolved = self.service._capture(self.base, ["a.txt"])
        (self.root / "a.txt").write_text("local intent\n")
        self.service.refresh("first", target, 0, resolved_source_tree=resolved, resolved_index_tree=resolved, **inputs)
        self.assertEqual((self.root / "a.txt").read_text(), "local and shared intent\n")
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"), "a.txt")

    def test_interrupted_prepare_ref_update_and_close_are_reconcilable(self) -> None:
        (self.root / "a.txt").write_text("ready\n")
        real_git = core._git

        def interrupted(root: Path, args: list[str], **kwargs):
            if args[0] == "update-ref":
                raise core.ConcurrentDevelopmentError("git_unavailable", "simulated process interruption")
            return real_git(root, args, **kwargs)

        with patch.object(core, "_git", side_effect=interrupted):
            with self.assertRaises(core.ConcurrentDevelopmentError):
                self.candidate(["a.txt"])
        state = self.service.status("first")["binding"]
        candidate = state["candidate"]["descriptor"]
        self.assertIn("pending_local_ref", state)
        self.service.retract("first", candidate["candidate_id"])
        # Simulate exit after ref removal but before metadata closeout.
        git(self.root, "update-ref", "-d", "refs/agent-framework/candidates/first", candidate["commit"])
        self.service.close("first", candidate["candidate_id"])
        self.assertIsNone(self.service.status("first")["binding"]["candidate"])

    def test_uncertain_publication_cannot_be_replaced_before_exact_readback(self) -> None:
        (self.root / "a.txt").write_text("ready\n")
        candidate = self.candidate(["a.txt"])
        real_shared = self.service._shared
        calls = 0

        def interrupted_readback():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise core.ConcurrentDevelopmentError("remote_unavailable", "simulated readback loss")
            return real_shared()

        with patch.object(self.service, "_shared", side_effect=interrupted_readback):
            self.assertEqual(self.service.publish("first", candidate["candidate_id"], self.base)["status"], "uncertain")
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.retract("first", candidate["candidate_id"])
        self.assertEqual(error.exception.code, "remote_outcome_uncertain")
        self.assertEqual(self.service.publish("first", candidate["candidate_id"], self.base)["status"], "already_included")

    def test_changed_local_candidate_ref_blocks_publication(self) -> None:
        (self.root / "a.txt").write_text("ready\n")
        candidate = self.candidate(["a.txt"])
        git(self.root, "update-ref", "refs/agent-framework/candidates/first", self.base, candidate["commit"])
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.publish("first", candidate["candidate_id"], self.base)
        self.assertEqual(error.exception.code, "candidate_ref_changed")

    def test_release_binding_requires_actual_owner_and_completed_candidate(self) -> None:
        with self.assertRaises(core.ConcurrentDevelopmentError):
            self.service.release_binding("first", "different-owner")
        (self.root / "a.txt").write_text("ready\n")
        candidate = self.candidate(["a.txt"])
        with self.assertRaises(core.ConcurrentDevelopmentError):
            self.service.release_binding("first", "task-first")
        self.service.retract("first", candidate["candidate_id"])
        self.service.close("first", candidate["candidate_id"])
        self.service.release_binding("first", "task-first")
        self.assertFalse(self.service._file("first").exists())
        self.assertEqual((self.root / "a.txt").read_text(), "ready\n")

    def interrupt_refresh(self, *, after_index: bool = False) -> str:
        (self.seed / "a.txt").write_text("new shared a\n")
        (self.seed / "b.txt").write_text("new shared b\n")
        git(self.seed, "commit", "-am", "shared update")
        git(self.seed, "push", "origin", "main")
        target = git(self.seed, "rev-parse", "HEAD")
        stop_at = self.service._index_path() if after_index else self.root / "a.txt"
        program = f'''
import os
from pathlib import Path
from repo_platform.concurrent_development.core import ConcurrentDevelopment
service = ConcurrentDevelopment(Path({str(self.root)!r}), Path({str(self.state)!r}))
original = os.replace
def interrupted(source, destination):
    original(source, destination)
    if Path(destination).resolve() == Path({str(stop_at)!r}).resolve():
        os._exit(73)
os.replace = interrupted
service.refresh("first", {target!r}, 0)
'''
        result = subprocess.run([sys.executable, "-c", program], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr.decode())
        self.assertIn("refresh_operation", self.service.status("first")["binding"])
        return target

    def test_killed_refresh_rolls_back_exact_original_source_index_and_head(self) -> None:
        (self.root / "binary.dat").write_bytes(b"\0local staged\xff")
        git(self.root, "add", "binary.dat")
        old_index = self.service._index_path().read_bytes()
        self.interrupt_refresh()
        self.assertEqual((self.root / "a.txt").read_text(), "new shared a\n")
        self.assertEqual((self.root / "b.txt").read_text(), "base b\n")
        result = self.service.reconcile_refresh("first", "rollback")
        self.assertEqual(result["status"], "refresh_rolled_back")
        self.assertEqual((self.root / "a.txt").read_text(), "one\ntwo\nthree\n")
        self.assertEqual(self.service._index_path().read_bytes(), old_index)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base)
        self.assertFalse(self.service._refresh_directory("first").exists())
        self.assertFalse(self.service._index_path().with_name("index.lock").exists())

    def test_killed_refresh_finishes_after_head_and_index_install(self) -> None:
        target = self.interrupt_refresh(after_index=True)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), target)
        result = self.service.reconcile_refresh("first", "finish")
        self.assertEqual(result["status"], "refresh_finished")
        self.assertEqual(self.service.status("first")["binding"]["base"], target)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertFalse(self.service._refresh_directory("first").exists())

    def test_interrupted_refresh_preserves_newer_bytes_and_foreign_lock(self) -> None:
        self.interrupt_refresh()
        (self.root / "a.txt").write_text("newer editor content\n")
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.reconcile_refresh("first", "rollback")
        self.assertEqual(error.exception.code, "refresh_source_changed")
        self.assertEqual((self.root / "a.txt").read_text(), "newer editor content\n")
        (self.root / "a.txt").write_text("new shared a\n")
        lock = self.service._index_path().with_name("index.lock")
        lock.unlink()
        lock.write_bytes(b"foreign Git operation")
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.reconcile_refresh("first", "finish")
        self.assertEqual(error.exception.code, "index_busy")
        self.assertEqual(lock.read_bytes(), b"foreign Git operation")

    def test_ready_tree_new_file_residual_is_retained_through_gc(self) -> None:
        (self.root / "new.txt").write_text("ready first line\n")
        ready = self.service._capture(self.base, ["new.txt"])
        (self.root / "new.txt").write_text("ready first line\nunready second line\n")
        candidate = self.candidate([], ready_tree=ready)
        state = self.service.status("first")["binding"]["candidate"]
        self.assertEqual(git(self.root, "show", f"{state['residual_tree']}:new.txt"), "ready first line\nunready second line")
        git(self.root, "prune", "--expire", "now")
        self.service.publish("first", candidate["candidate_id"], self.base)
        self.service.refresh("first", candidate["commit"], 1)
        self.assertEqual((self.root / "new.txt").read_text(), "ready first line\nunready second line\n")
        self.service.close("first", candidate["candidate_id"])
        self.assertEqual(git(self.root, "for-each-ref", "--format=%(refname)", "refs/agent-framework/"), "")

    def test_git_writer_between_refresh_check_and_lock_is_preserved(self) -> None:
        (self.seed / "a.txt").write_text("shared a\n")
        git(self.seed, "commit", "-am", "shared change")
        git(self.seed, "push", "origin", "main")
        target = git(self.seed, "rev-parse", "HEAD")
        link = os.link
        newer_head = None
        newer_index = None

        def racing_link(source, destination, *args, **kwargs):
            nonlocal newer_head, newer_index
            (self.root / "b.txt").write_text("another writer's commit\n")
            git(self.root, "commit", "-am", "concurrent workspace write")
            newer_head = git(self.root, "rev-parse", "HEAD")
            newer_index = self.service._index_path().read_bytes()
            return link(source, destination, *args, **kwargs)

        with patch.object(core.os, "link", side_effect=racing_link):
            with self.assertRaises(core.ConcurrentDevelopmentError) as error:
                self.service.refresh("first", target, 0)
        self.assertEqual(error.exception.code, "refresh_workspace_changed")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), newer_head)
        self.assertEqual(self.service._index_path().read_bytes(), newer_index)
        self.assertEqual((self.root / "a.txt").read_text(), "one\ntwo\nthree\n")

    def test_prior_resolution_survives_ref_race_and_new_unready_edits(self) -> None:
        (self.root / "a.txt").write_text("local meaning\n")
        (self.seed / "a.txt").write_text("shared meaning\n")
        git(self.seed, "commit", "-am", "shared conflicting meaning")
        git(self.seed, "push", "origin", "main")
        with self.assertRaises(core.ConcurrentDevelopmentError) as conflict:
            self.candidate(["a.txt"])
        (self.root / "a.txt").write_text("deliberately combined meaning\n")
        resolved = self.service._capture(self.base, ["a.txt"])
        (self.root / "a.txt").write_text("local meaning\n")
        context = conflict.exception.details
        first = self.service.prepare("first", [], "resolved", 0, ready_tree=context["ready_tree"],
                                     expected_base=context["expected_base"], expected_shared=context["expected_shared"],
                                     resolved_candidate_tree=resolved)["candidate"]
        (self.root / "b.txt").write_text("new unfinished edit\n")
        (self.seed / "new.txt").write_text("another completed task\n")
        git(self.seed, "add", "new.txt")
        git(self.seed, "commit", "-m", "race winner")
        git(self.seed, "push", "origin", "main")
        self.assertEqual(self.service.publish("first", first["candidate_id"], first["parent"])["status"], "stale")
        next_candidate = self.service.prepare("first", ["a.txt"], "combined", 1)["candidate"]
        self.assertEqual(git(self.root, "show", f"{next_candidate['commit']}:a.txt"), "deliberately combined meaning")
        self.assertEqual(git(self.root, "show", f"{next_candidate['commit']}:b.txt"), "base b")
        self.service.publish("first", next_candidate["candidate_id"], next_candidate["parent"])
        self.service.refresh("first", next_candidate["commit"], 2)
        self.assertEqual((self.root / "a.txt").read_text(), "deliberately combined meaning\n")
        self.assertEqual((self.root / "b.txt").read_text(), "new unfinished edit\n")

    def test_uncertain_exposure_readback_does_not_require_unchanged_source(self) -> None:
        (self.root / "a.txt").write_text("ready\n")
        candidate = self.candidate(["a.txt"])
        remote_oid = self.service._remote_oid
        calls = 0

        def lost_readback(ref):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise core.ConcurrentDevelopmentError("remote_unavailable", "lost exposure response")
            return remote_oid(ref)

        with patch.object(self.service, "_remote_oid", side_effect=lost_readback):
            self.assertEqual(self.service.expose("first", candidate["candidate_id"], None)["status"], "uncertain")
        (self.root / "a.txt").write_text("newer unready intent\n")
        self.assertEqual(self.service.expose("first", candidate["candidate_id"], None)["status"], "already_exposed")
        self.service.retract("first", candidate["candidate_id"])
        self.service.close("first", candidate["candidate_id"])

    def test_integrated_deletion_does_not_break_later_ready_selection(self) -> None:
        (self.root / "b.txt").unlink()
        candidate = self.candidate(["b.txt"])
        self.service.publish("first", candidate["candidate_id"], self.base)
        self.service.refresh("first", candidate["commit"], 1)
        self.service.close("first", candidate["candidate_id"])
        (self.root / "a.txt").write_text("next ready contribution\n")
        next_candidate = self.candidate(["a.txt"], generation=1)
        self.assertEqual(next_candidate["parent"], candidate["commit"])

    def test_new_residual_conflict_has_explicit_carry_resolution(self) -> None:
        (self.root / "a.txt").write_text("ready a\n")
        (self.root / "b.txt").write_text("local unfinished b\n")
        (self.seed / "b.txt").write_text("shared b\n")
        git(self.seed, "commit", "-am", "shared b")
        git(self.seed, "push", "origin", "main")
        with self.assertRaises(core.ConcurrentDevelopmentError) as initial_conflict:
            self.service.prepare("first", ["a.txt"], "ready with residual", 0)
        context = initial_conflict.exception.details
        (self.root / "b.txt").write_text("combined b\n")
        residual = self.service._capture(self.base, ["a.txt", "b.txt"])
        (self.root / "b.txt").write_text("local unfinished b\n")
        self.service.prepare("first", [], "ready with resolved residual", 0,
                             ready_tree=context["ready_tree"], expected_base=context["expected_base"],
                             expected_shared=context["expected_shared"], expected_source_tree=context["expected_source_tree"],
                             resolved_residual_tree=residual)
        (self.root / "b.txt").write_text("newer local unfinished b\n")
        index = self.service._index_path().read_bytes()
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.service.prepare("first", ["a.txt"], "carry", 1)
        self.assertEqual(error.exception.details["role"], "new_residual_edits")
        context = error.exception.details
        (self.root / "b.txt").write_text("combined and newer b\n")
        carry = self.service._capture(self.base, ["a.txt", "b.txt"])
        (self.root / "b.txt").write_text("newer local unfinished b\n")
        (self.root / "b.txt").write_text("another later unfinished edit\n")
        with self.assertRaises(core.ConcurrentDevelopmentError) as changed_source:
            self.service.prepare("first", [], "carry", 1, resolved_carry_tree=carry,
                                 ready_tree=context["ready_tree"], expected_base=context["expected_base"],
                                 expected_shared=context["expected_shared"], expected_source_tree=context["expected_source_tree"])
        self.assertEqual(changed_source.exception.code, "stale_resolution")
        self.assertEqual(changed_source.exception.details["input"], "source_tree")
        (self.root / "b.txt").write_text("newer local unfinished b\n")
        self.service.prepare("first", [], "carry", 1, resolved_carry_tree=carry,
                             ready_tree=context["ready_tree"], expected_base=context["expected_base"],
                             expected_shared=context["expected_shared"], expected_source_tree=context["expected_source_tree"])
        state = self.service.status("first")["binding"]["candidate"]
        self.assertEqual(git(self.root, "show", f"{state['residual_tree']}:b.txt"), "combined and newer b")
        self.assertEqual(self.service._index_path().read_bytes(), index)
        self.assertEqual((self.root / "b.txt").read_text(), "newer local unfinished b\n")

    def test_tracked_ignored_paths_added_after_base_capture_without_ignored_neighbors(self) -> None:
        (self.root / ".gitignore").write_text("packs/\n")
        (self.root / "packs").mkdir()
        (self.root / "packs" / "owned.py").write_text("tracked source\n")
        git(self.root, "add", ".gitignore")
        git(self.root, "add", "--force", "packs/owned.py")
        git(self.root, "commit", "-m", "tracked source under ignored folder")
        (self.root / "packs" / "private.txt").write_text("untracked ignored neighbor\n")
        head = git(self.root, "rev-parse", "HEAD")
        ready = git(self.root, "rev-parse", "HEAD^{tree}")
        index = self.service._index_path().read_bytes()
        candidate = self.candidate([], ready_tree=ready)
        files = git(self.root, "ls-tree", "-r", "--name-only", candidate["commit"]).splitlines()
        self.assertIn("packs/owned.py", files)
        self.assertNotIn("packs/private.txt", files)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), head)
        self.assertEqual(self.service._index_path().read_bytes(), index)
        with self.assertRaises(core.ConcurrentDevelopmentError):
            self.service._capture(self.base, ["packs/private.txt"])

    def test_explicit_resolution_rejects_new_shared_push_before_accepting_old_tree(self) -> None:
        (self.root / "a.txt").write_text("local intent\n")
        (self.seed / "a.txt").write_text("first shared intent\n")
        git(self.seed, "commit", "-am", "first shared conflict")
        git(self.seed, "push", "origin", "main")
        with self.assertRaises(core.ConcurrentDevelopmentError) as conflict:
            self.candidate(["a.txt"])
        context = conflict.exception.details
        (self.root / "a.txt").write_text("resolved first conflict\n")
        old_resolution = self.service._capture(self.base, ["a.txt"])
        (self.root / "a.txt").write_text("local intent\n")
        (self.seed / "a.txt").write_text("newer shared intent\n")
        (self.seed / "b.txt").write_text("unrelated newer contribution\n")
        git(self.seed, "commit", "-am", "another task finishes after resolution")
        git(self.seed, "push", "origin", "main")
        latest = git(self.seed, "rev-parse", "HEAD")
        index = self.service._index_path().read_bytes()
        with self.assertRaises(core.ConcurrentDevelopmentError) as stale:
            self.service.prepare("first", [], "stale explicit resolution", 0,
                                 ready_tree=context["ready_tree"], resolved_candidate_tree=old_resolution,
                                 expected_base=context["expected_base"], expected_shared=context["expected_shared"])
        self.assertEqual(stale.exception.code, "stale_resolution")
        self.assertEqual(stale.exception.details["observed"], latest)
        self.assertEqual(self.service.status("first")["binding"]["generation"], 0)
        self.assertIsNone(self.service.status("first")["binding"]["candidate"])
        self.assertEqual(self.service._index_path().read_bytes(), index)
        self.assertEqual((self.root / "a.txt").read_text(), "local intent\n")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base)
        with self.assertRaises(core.ConcurrentDevelopmentError) as missing:
            self.service.prepare("first", [], "unbound resolution", 0,
                                 ready_tree=context["ready_tree"], resolved_candidate_tree=old_resolution)
        self.assertEqual(missing.exception.code, "resolution_inputs_required")
        # A safe refresh can advance the binding while its generation remains
        # unchanged. The earlier resolution must not silently use that new A.
        (self.root / "a.txt").write_text("local and newer shared intent\n")
        (self.root / "b.txt").write_text("unrelated newer contribution\n")
        refreshed_source = self.service._capture(self.base, ["a.txt", "b.txt"])
        (self.root / "a.txt").write_text("local intent\n")
        (self.root / "b.txt").write_text("base b\n")
        self.service.refresh("first", latest, 0, resolved_source_tree=refreshed_source,
                             expected_base=context["expected_base"], expected_source_tree=context["expected_source_tree"])
        with self.assertRaises(core.ConcurrentDevelopmentError) as changed_base:
            self.service.prepare("first", [], "old binding resolution", 0,
                                 ready_tree=context["ready_tree"], resolved_candidate_tree=old_resolution,
                                 expected_base=context["expected_base"], expected_shared=latest)
        self.assertEqual(changed_base.exception.code, "stale_resolution")
        self.assertEqual(changed_base.exception.details["input"], "base")

    def test_explicit_refresh_rejects_new_source_staging_and_head_inputs(self) -> None:
        (self.root / "a.txt").write_text("local intent\n")
        git(self.root, "add", "a.txt")
        (self.seed / "a.txt").write_text("shared intent\n")
        git(self.seed, "commit", "-am", "shared conflict")
        git(self.seed, "push", "origin", "main")
        target = git(self.seed, "rev-parse", "HEAD")
        with self.assertRaises(core.ConcurrentDevelopmentError) as conflict:
            self.service.refresh("first", target, 0)
        inputs = {key: conflict.exception.details[key] for key in (
            "expected_base", "expected_source_tree", "expected_head", "expected_index_tree")}
        (self.root / "a.txt").write_text("resolved local and shared\n")
        resolved = self.service._capture(self.base, ["a.txt"])
        (self.root / "a.txt").write_text("newer local intent\n")
        with self.assertRaises(core.ConcurrentDevelopmentError) as source_changed:
            self.service.refresh("first", target, 0, resolved_source_tree=resolved,
                                 resolved_index_tree=resolved, **inputs)
        self.assertEqual(source_changed.exception.details["input"], "source_tree")
        self.assertEqual((self.root / "a.txt").read_text(), "newer local intent\n")
        (self.root / "a.txt").write_text("local intent\n")
        (self.root / "b.txt").write_text("newly staged intent\n")
        git(self.root, "add", "b.txt")
        (self.root / "b.txt").write_text("base b\n")
        staged_index = self.service._index_path().read_bytes()
        with self.assertRaises(core.ConcurrentDevelopmentError) as index_changed:
            self.service.refresh("first", target, 0, resolved_source_tree=resolved,
                                 resolved_index_tree=resolved, **inputs)
        self.assertEqual(index_changed.exception.details["input"], "index_tree")
        self.assertEqual(self.service._index_path().read_bytes(), staged_index)
        git(self.root, "commit", "-m", "new local commit")
        current_head = git(self.root, "rev-parse", "HEAD")
        with self.assertRaises(core.ConcurrentDevelopmentError) as head_changed:
            self.service.refresh("first", target, 0, resolved_source_tree=resolved,
                                 resolved_index_tree=resolved, **inputs)
        self.assertEqual(head_changed.exception.details["input"], "head")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), current_head)
        self.assertEqual(self.service.status("first")["binding"]["base"], self.base)

    def test_retraction_invalidates_pending_resolution_generation_once(self) -> None:
        (self.root / "a.txt").write_text("ready intent\n")
        candidate = self.candidate(["a.txt"])
        self.service.retract("first", candidate["candidate_id"])
        self.service.retract("first", candidate["candidate_id"])
        self.assertEqual(self.service.status("first")["binding"]["generation"], 2)
        with self.assertRaises(core.ConcurrentDevelopmentError) as stale:
            self.service.prepare("first", [], "stale pending resolution", 1,
                                 ready_tree=candidate["selected_tree"], resolved_candidate_tree=candidate["tree"],
                                 expected_base=self.base, expected_shared=self.base)
        self.assertEqual(stale.exception.code, "generation_changed")
        self.service.close("first", candidate["candidate_id"])
        self.assertIsNone(self.service.status("first")["binding"]["candidate"])

    def test_exact_tree_merge_retains_rename_and_content_semantics_using_commits(self) -> None:
        (self.root / "a.txt").rename(self.root / "renamed.txt")
        ours = self.service._capture(self.base, None, ["renamed.txt"])
        (self.root / "renamed.txt").rename(self.root / "a.txt")
        (self.root / "a.txt").write_text("one\nchanged second line\nthree\n")
        theirs = self.service._capture(self.base, ["a.txt"])
        original_index = self.service._index_path().read_bytes()
        real_git = core._git
        observed = []

        def commits_only(root, args, **kwargs):
            if args[0] == "merge-tree":
                self.assertFalse(any(arg.startswith("--merge-base") for arg in args))
                left, right = args[-2:]
                self.assertEqual(git(root, "cat-file", "-t", left), "commit")
                self.assertEqual(git(root, "cat-file", "-t", right), "commit")
                common = git(root, "merge-base", left, right)
                self.assertEqual(git(root, "rev-parse", f"{common}^{{tree}}"), core._tree(root, self.base))
                observed.append((left, right))
            return real_git(root, args, **kwargs)

        with patch.object(core, "_git", side_effect=commits_only):
            combined = core._merge(self.root, core._tree(self.root, self.base), ours, theirs, role="portable_exact_trees")
        self.assertEqual(len(observed), 1)
        self.assertEqual(git(self.root, "show", f"{combined}:renamed.txt"), "one\nchanged second line\nthree")
        self.assertNotIn("a.txt", git(self.root, "ls-tree", "-r", "--name-only", combined).splitlines())
        self.assertEqual(self.service._index_path().read_bytes(), original_index)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base)
        self.assertEqual((self.root / "a.txt").read_text(), "one\nchanged second line\nthree\n")


if __name__ == "__main__":
    unittest.main()
