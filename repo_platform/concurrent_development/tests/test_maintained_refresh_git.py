"""Guarded primary-checkout incorporation using temporary real Git repositories."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from repo_platform.concurrent_development import core


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout.decode().strip()


class MaintainedCheckoutGitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="primary-refresh-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.writer = self.root / "writer"
        self.remote = self.root / "local.git"
        self.main = self.root / "primary"
        self.state = self.root / "tool-state"
        self.writer.mkdir()
        self.remote.mkdir()
        git(self.remote, "init", "--bare", "-b", "accepted")
        git(self.writer, "init", "-b", "accepted")
        self.configure_git(self.writer)
        (self.writer / "editable").write_text("base\n")
        (self.writer / "updates").write_text("initial\n")
        git(self.writer, "add", ".")
        git(self.writer, "commit", "-m", "base")
        git(self.writer, "remote", "add", "upstream", str(self.remote))
        git(self.writer, "push", "upstream", "accepted")
        git(self.root, "clone", "-o", "upstream", str(self.remote), str(self.main))
        self.configure_git(self.main)
        self.service = self.service_at(self.main)
        self.base = self.service._head()

    def configure_git(self, path):
        git(path, "config", "user.email", "test@example.invalid")
        git(path, "config", "user.name", "Integration test")
        git(path, "config", "core.autocrlf", "false")

    def service_at(self, path):
        return core.ConcurrentDevelopment(path, self.state, remote="upstream", shared_branch="accepted")

    def publish(self, name="updates", body="next\n"):
        (self.writer / name).write_text(body)
        git(self.writer, "add", name)
        git(self.writer, "commit", "-m", "shared source")
        git(self.writer, "push", "upstream", "accepted")
        # A real upkeep request already knows the remote shared revision.
        return self.service._shared()

    def test_fast_forward_retains_working_and_staged_edits_separately(self):
        target = self.publish()
        (self.main / "editable").write_text("staged\n")
        git(self.main, "add", "editable")
        (self.main / "editable").write_text("working\n")
        (self.main / "untracked").write_text("mine\n")
        self.assertEqual(self.service.refresh_maintained(target)["workspace_head"], target)
        self.assertEqual(self.service._head(), target)
        self.assertEqual(git(self.main, "show", ":editable"), "staged")
        self.assertEqual((self.main / "editable").read_text(), "working\n")
        self.assertEqual((self.main / "updates").read_text(), "next\n")
        self.assertEqual((self.main / "untracked").read_text(), "mine\n")
        self.assertIsNone(self.service.maintained_refresh_pending())

    def test_local_rename_cannot_redirect_shared_edit_into_an_active_input_hold(self):
        first = self.publish("updates", "one\ntwo\nthree\nfour\n")
        self.service.refresh_maintained(first)
        git(self.main, "mv", "updates", "held")
        second = self.publish("updates", "one\ntwo\nthree\nchanged\n")
        with self.assertRaises(core.ConcurrentDevelopmentError) as held:
            self.service.refresh_maintained(second, protected_paths=["held"], expected_head=first)
        self.assertEqual(held.exception.code, "main_input_hold")
        self.assertEqual((self.main / "held").read_text(), "one\ntwo\nthree\nfour\n")
        self.assertEqual(self.service._head(), first)
        self.assertEqual(self.service.refresh_maintained(second)["workspace_head"], second)
        self.assertEqual((self.main / "held").read_text(), "one\ntwo\nthree\nchanged\n")

    def test_untracked_collision_and_divergent_history_defer(self):
        (self.main / "added").write_text("private local\n")
        target = self.publish("added", "incoming\n")
        with self.assertRaises(core.ConcurrentDevelopmentError) as caught:
            self.service.refresh_maintained(target)
        self.assertEqual(caught.exception.code, "untracked_collision")
        self.assertEqual((self.main / "added").read_text(), "private local\n")
        self.assertEqual(self.service._head(), self.base)
        git(self.main, "add", "added")
        git(self.main, "commit", "-m", "retain local history")
        local_head = self.service._head()
        with self.assertRaises(core.ConcurrentDevelopmentError):
            self.service.refresh_maintained(target)
        self.assertEqual(self.service._head(), local_head)

    def test_ordinary_owner_and_other_physical_transactions_prevent_maintenance(self):
        self.service.bind("writer", "current owner", self.base)
        target = self.publish()
        with self.assertRaises(core.ConcurrentDevelopmentError) as owned:
            self.service.refresh_maintained(target)
        self.assertEqual(owned.exception.code, "maintained_checkout_owned")
        with self.assertRaises(core.ConcurrentDevelopmentError) as direct:
            self.service.refresh("writer", target, 0)
        self.assertEqual(direct.exception.code, "workspace_branch_protected")
        self.service.release_binding("writer", "current owner")
        self.service._refresh_directory("foreign").mkdir(parents=True)
        with self.assertRaises(core.ConcurrentDevelopmentError) as unknown:
            self.service.maintained_refresh_pending()
        self.assertEqual(unknown.exception.code, "foreign_refresh_journal")
        self.assertEqual(self.service._head(), self.base)

    def test_physical_writer_lock_leaves_other_worktree_bindings_available(self):
        target = self.publish()
        linked = self.root / "linked"
        git(self.main, "worktree", "add", "--detach", str(linked), self.base)
        original = self.service._install_source

        def install(*args):
            self.service_at(linked).bind("other", "task", self.base)
            with self.assertRaises(core.ConcurrentDevelopmentError) as busy:
                self.service.bind("racing", "writer", self.base)
            self.assertEqual(busy.exception.code, "binding_busy")
            return original(*args)

        with patch.object(self.service, "_install_source", side_effect=install):
            self.assertEqual(self.service.refresh_maintained(target)["workspace_head"], target)

    def test_requires_real_primary_git_directory_and_configured_branch(self):
        linked = self.root / "linked"
        git(self.main, "worktree", "add", "--detach", str(linked), self.base)
        with self.assertRaises(core.ConcurrentDevelopmentError) as wrong:
            self.service_at(linked).refresh_maintained(self.base)
        self.assertEqual(wrong.exception.code, "maintained_checkout_required")
        git(self.main, "checkout", "-b", "feature")
        with self.assertRaises(core.ConcurrentDevelopmentError):
            self.service.refresh_maintained(self.base)

    def test_fresh_last_moment_deferral_discards_only_unused_preparation(self):
        target = self.publish()
        index_before = self.service._index_path().read_bytes()

        def active_reader():
            self.assertEqual(self.service._head(), self.base)
            self.assertFalse((self.service.git_dir / "index.lock").exists())
            self.assertEqual(self.service.maintained_refresh_pending(), target)
            raise RuntimeError("active reader")

        with self.assertRaisesRegex(RuntimeError, "active reader"):
            self.service.refresh_maintained(target, before_install=active_reader)
        self.assertIsNone(self.service.maintained_refresh_pending())
        self.assertFalse(self.service._refresh_directory(core._MAINTAINED).exists())
        self.assertEqual(git(self.main, "for-each-ref", "--format=%(refname)", "refs/agent-framework/refresh-state"), "")
        self.assertEqual(self.service._index_path().read_bytes(), index_before)
        self.assertEqual(self.service._head(), self.base)
        self.assertEqual((self.main / "updates").read_text(), "initial\n")

    def test_interruption_before_active_journal_is_saved_rebuilds_from_current_source(self):
        target = self.publish()
        original_save = self.service._save

        def interrupt_before_publication(binding):
            if binding.get("refresh_operation"):
                raise InterruptedError("interrupted before journal publication")
            original_save(binding)

        with patch.object(self.service, "_save", side_effect=interrupt_before_publication):
            with self.assertRaises(InterruptedError):
                self.service.refresh_maintained(target)
        self.assertEqual(self.service.maintained_refresh_pending(), target)
        self.assertEqual(self.service._head(), self.base)
        (self.main / "editable").write_text("newer source\n")
        self.service.refresh_maintained(target)
        self.assertEqual((self.main / "editable").read_text(), "newer source\n")
        self.assertIsNone(self.service.maintained_refresh_pending())

    def test_interrupted_guarded_refresh_fences_and_finishes_without_generic_bypass(self):
        target = self.publish()
        program = """
import os, sys
from pathlib import Path
from repo_platform.concurrent_development import core
main, state, target = sys.argv[1:]
original = os.replace
def interrupt(source, dest):
    original(source, dest)
    if Path(dest) == Path(main) / "updates":
        os._exit(47)
core.os.replace = interrupt
core.ConcurrentDevelopment(Path(main), Path(state), remote="upstream", shared_branch="accepted").refresh_maintained(target)
"""
        run = subprocess.run([sys.executable, "-c", program, str(self.main), str(self.state), target],
                             cwd=Path(__file__).resolve().parents[3], capture_output=True)
        self.assertEqual(run.returncode, 47, run.stderr.decode())
        self.assertEqual(self.service.maintained_refresh_pending(), target)
        self.assertEqual(self.service._head(), self.base)
        for call in (lambda: self.service.reconcile_refresh(core._MAINTAINED, "finish"),
                     lambda: self.service.bind(core._MAINTAINED, "other", self.base),
                     lambda: self.service.refresh(core._MAINTAINED, target, 0)):
            with self.assertRaises(core.ConcurrentDevelopmentError) as direct:
                call()
            self.assertEqual(direct.exception.code, "invalid_identity")
        def still_blocked():
            raise RuntimeError("reader still active")

        with self.assertRaisesRegex(RuntimeError, "reader still active"):
            self.service.refresh_maintained(target, before_install=still_blocked)
        self.assertEqual(self.service.maintained_refresh_pending(), target)
        self.assertEqual(self.service._head(), self.base)
        (self.main / "updates").write_text("newer editor\n")
        with self.assertRaises(core.ConcurrentDevelopmentError) as changed:
            self.service.refresh_maintained(target)
        self.assertEqual(changed.exception.code, "refresh_source_changed")
        self.assertEqual((self.main / "updates").read_text(), "newer editor\n")
        (self.main / "updates").write_text("next\n")
        self.assertEqual(self.service.refresh_maintained(target)["workspace_head"], target)
        self.assertIsNone(self.service.maintained_refresh_pending())


if __name__ == "__main__":
    unittest.main()
