"""Disposable Git proof for binding-path relocation; never uses live state."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repo_platform.concurrent_development import cli, core, relocation


def git(root: Path, *args: str) -> str:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(["git", "-C", str(root), *args], env=env, capture_output=True, check=True)
    return result.stdout.decode().strip()


class CommonDirectoryRelocation(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="concurrent-relocation-test-")
        self.addCleanup(temporary.cleanup)
        self.fixture = Path(temporary.name).resolve()
        self.old = self.fixture / "main"
        self.new = self.fixture / "renamed"
        self.state = self.fixture / "state"
        remote = self.fixture / "remote.git"
        remote.mkdir()
        git(remote, "init", "--bare")
        self.old.mkdir()
        git(self.old, "init", "-b", "main")
        git(self.old, "config", "user.name", "Disposable relocation")
        git(self.old, "config", "user.email", "relocation@example.invalid")
        git(self.old, "config", "core.autocrlf", "false")
        (self.old / "source.txt").write_text("base\n")
        git(self.old, "add", "source.txt")
        git(self.old, "commit", "-m", "base")
        git(self.old, "remote", "add", "origin", str(remote))
        git(self.old, "push", "origin", "main")
        self.base = git(self.old, "rev-parse", "HEAD")
        self.service = core.ConcurrentDevelopment(self.old, self.state)
        self.service.bind("main", "task-main", self.base)
        self.linked = self.fixture / "linked"
        self.absent = self.fixture / "absent"
        for name, path in (("linked", self.linked), ("absent", self.absent)):
            git(self.old, "worktree", "add", "--detach", str(path), self.base)
            core.ConcurrentDevelopment(path, self.state).bind(name, f"task-{name}", self.base)
        # Deliberately keep the owner's historical binding while only the
        # disposable fixture workspace is removed.
        git(self.old, "worktree", "remove", str(self.absent))
        self.other = self.fixture / "other-clone"
        git(self.fixture, "clone", "-b", "main", str(remote), str(self.other))
        self.other_service = core.ConcurrentDevelopment(self.other, self.state)
        self.other_service.bind("other", "task-other", self.base)
        self.source = (self.old / ".git").as_posix()
        self.target = (self.new / ".git").as_posix()

    def state_bytes(self, *, include_locks: bool = True) -> dict[str, bytes]:
        return {p.relative_to(self.state).as_posix(): p.read_bytes()
                for p in self.state.rglob("*") if p.is_file() and (include_locks or p.suffix != ".lock")}

    def record(self, name: str) -> dict:
        return json.loads(self.service._file(name).read_text())

    def preview(self, **kwargs) -> dict:
        return self.service.relocate_common_dir(self.source, self.target, dry_run=True, **kwargs)

    def move(self, source: Path, target: Path, *, repair: bool = True) -> None:
        # Both verified disposable paths remain inside this test's unique root.
        self.assertTrue(source.resolve().is_relative_to(self.fixture))
        self.assertTrue(target.resolve().is_relative_to(self.fixture))
        self.assertFalse(target.exists())
        source.rename(target)
        if repair:
            git(target, "worktree", "repair")
        self.service = core.ConcurrentDevelopment(target, self.state)

    def apply(self, token: str, **kwargs) -> dict:
        return self.service.relocate_common_dir(self.source, self.target, expected_state=token, **kwargs)

    def interrupt_after_one_binding(self, token: str) -> None:
        original = core._atomic_write
        writes = 0

        def fail_second(path, value):
            nonlocal writes
            if path.parent == self.service.directory:
                writes += 1
                if writes == 2:
                    raise OSError("simulated process interruption")
            original(path, value)

        with patch.object(core, "_atomic_write", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.apply(token)
        self.assertTrue(relocation._marker(self.service).exists())

    def test_preview_is_read_only_and_excludes_other_clone(self) -> None:
        before = self.state_bytes()
        with patch.object(core, "_atomic_write", side_effect=AssertionError("preview wrote state")), \
                patch.object(core, "_lock", side_effect=AssertionError("preview acquired a lock")):
            result = self.preview()
        self.assertEqual(result["binding_count"], 3)
        self.assertEqual({r["binding_id"] for r in result["bindings"]}, {"main", "linked", "absent"})
        self.assertFalse(next(r for r in result["bindings"] if r["binding_id"] == "absent")["workspace_present"])
        self.assertTrue(result["ready"])
        self.assertEqual(self.state_bytes(), before)
        self.assertFalse(self.new.exists())

    def test_forward_and_fresh_reverse_preserve_source_index_candidates_and_holds(self) -> None:
        (self.old / "source.txt").write_text("ready\n")
        candidate = self.service.prepare("main", ["source.txt"], "ready", 0)["candidate"]
        self.service.expose("main", candidate["candidate_id"], None)
        self.service.retain("main", candidate["candidate_id"], "real-fixture-consumer")
        (self.old / "source.txt").write_text("unfinished after preparation\n")
        git(self.old, "add", "source.txt")
        (self.old / "source.txt").write_text("newer unstaged source\n")
        (self.old / "untracked.txt").write_text("untracked source\n")
        original = {name: self.service._file(name).read_bytes() for name in ("main", "linked", "absent", "other")}
        index = self.service._index_path().read_bytes()
        refs = git(self.old, "for-each-ref", "--format=%(refname) %(objectname)")
        token = self.preview()["expected_state"]
        self.move(self.old, self.new)
        result = self.apply(token)
        self.assertEqual(result["status"], "relocated")
        self.assertEqual(self.apply(token)["status"], "relocated")  # response-loss retry
        for name in ("main", "linked", "absent"):
            before = json.loads(original[name])
            after = self.record(name)
            self.assertEqual(after.pop("git_common_dir"), self.target)
            expected_workspace = self.new.as_posix() if name == "main" else before["workspace"]
            self.assertEqual(after.pop("workspace"), expected_workspace)
            before.pop("git_common_dir")
            before.pop("workspace")
            self.assertEqual(after, before)
        self.assertEqual(self.service._file("other").read_bytes(), original["other"])
        self.assertEqual(self.service._index_path().read_bytes(), index)
        self.assertEqual((self.new / "source.txt").read_text(), "newer unstaged source\n")
        self.assertEqual((self.new / "untracked.txt").read_text(), "untracked source\n")
        self.assertEqual(git(self.new, "for-each-ref", "--format=%(refname) %(objectname)"), refs)
        self.assertFalse(self.absent.exists())
        reverse = self.service.relocate_common_dir(self.target, self.source, dry_run=True)
        self.move(self.new, self.old)
        self.service.relocate_common_dir(self.target, self.source, expected_state=reverse["expected_state"])
        for name, raw in original.items():
            self.assertEqual(self.service._file(name).read_bytes(), raw)

    def test_changed_binding_set_contents_or_refs_reject_original_expectation(self) -> None:
        for change in ("contents", "set", "refs"):
            with self.subTest(change=change):
                token = self.preview()["expected_state"]
                if change == "contents":
                    value = self.record("linked")
                    value["generation"] += 1
                    core._atomic_write(self.service._file("linked"), value)
                elif change == "set":
                    value = {**self.record("absent"), "binding_id": "another-absent"}
                    core._atomic_write(self.service._file("another-absent"), value)
                else:
                    git(self.old, "update-ref", "refs/agent-framework/candidates/main", self.base)
                before = self.state_bytes()
                with self.assertRaises(core.ConcurrentDevelopmentError) as error:
                    self.preview(expected_state=token)
                self.assertEqual(error.exception.code, "relocation_state_changed")
                self.assertEqual(self.state_bytes(), before)

    def test_partial_retry_fences_mutations_and_keeps_unrelated_clone_available(self) -> None:
        original = {name: self.record(name) for name in ("main", "linked", "absent")}
        token = self.preview()["expected_state"]
        self.move(self.old, self.new)
        self.interrupt_after_one_binding(token)
        result = self.preview(expected_state=token)
        self.assertEqual({row["record_state"] for row in result["bindings"]}, {"before", "after"})
        for invoke in (lambda: self.service.bind("new-owner", "task-new", self.base),
                       lambda: self.service.release_binding("absent", "task-absent")):
            with self.assertRaises(core.ConcurrentDevelopmentError) as blocked:
                invoke()
            self.assertEqual(blocked.exception.code, "relocation_interrupted")
        self.assertEqual(self.other_service.bind("other", "task-other", self.base)["owner_id"], "task-other")
        self.assertEqual(self.apply(token)["status"], "relocated")
        self.assertFalse(relocation._marker(self.service).exists())
        for name in original:
            self.assertEqual(self.record(name)["generation"], original[name]["generation"])

    def test_partial_reverse_requires_physical_return_then_restores_exact_records(self) -> None:
        original = {name: self.service._file(name).read_bytes() for name in ("main", "linked", "absent")}
        token = self.preview()["expected_state"]
        self.move(self.old, self.new)
        self.interrupt_after_one_binding(token)
        with self.assertRaises(core.ConcurrentDevelopmentError) as not_returned:
            self.apply(token, outcome="rollback")
        self.assertEqual(not_returned.exception.code, "relocation_move_required")
        self.move(self.new, self.old)
        self.assertEqual(self.apply(token, outcome="rollback")["status"], "relocation_rolled_back")
        for name, raw in original.items():
            self.assertEqual(self.service._file(name).read_bytes(), raw)

    def test_partial_recovery_preserves_newer_or_crossed_records(self) -> None:
        token = self.preview()["expected_state"]
        self.move(self.old, self.new)
        self.interrupt_after_one_binding(token)
        original = self.record("main")
        for field, value, code in (("generation", 99, "relocation_state_changed"),
                                   ("workspace", self.new.as_posix(), "relocation_binding_conflict")):
            with self.subTest(field=field):
                changed = {**original, field: value}
                core._atomic_write(self.service._file("main"), changed)
                before = self.state_bytes()
                with self.assertRaises(core.ConcurrentDevelopmentError) as error:
                    self.apply(token)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(self.state_bytes(), before)
                core._atomic_write(self.service._file("main"), original)
        self.apply(token)

    def test_git_lock_pending_operation_and_unrepaired_workspace_block_application(self) -> None:
        token = self.preview()["expected_state"]
        lock = self.old / ".git" / "index.lock"
        lock.write_text("foreign lock")
        result = self.preview()
        self.assertFalse(result["ready"])
        self.assertEqual(result["blockers"][0]["code"], "git_operation_active")
        self.assertEqual(lock.read_text(), "foreign lock")
        lock.unlink()
        for field in ("refresh_operation", "pending_local_ref"):
            value = self.record("main")
            value[field] = {"fixture": "unfinished"}
            core._atomic_write(self.service._file("main"), value)
            self.assertFalse(self.preview()["ready"])
            value.pop(field)
            core._atomic_write(self.service._file("main"), value)
        self.move(self.old, self.new, repair=False)
        before = self.state_bytes(include_locks=False)
        with self.assertRaises(core.ConcurrentDevelopmentError) as error:
            self.apply(token)
        self.assertEqual(error.exception.code, "relocation_busy")
        self.assertEqual(error.exception.details["blockers"][0]["code"], "workspace_repair_required")
        self.assertEqual(self.state_bytes(include_locks=False), before)
        git(self.new, "worktree", "repair")
        self.apply(token)

    def test_lock_conflict_and_replaced_physical_identity_do_not_rewrite_bindings(self) -> None:
        token = self.preview()["expected_state"]
        self.move(self.old, self.new)
        with core._lock(self.service._file("linked").with_suffix(".lock")):
            before = self.state_bytes(include_locks=False)
            with self.assertRaises(core.ConcurrentDevelopmentError) as error:
                self.apply(token)
            self.assertEqual(error.exception.code, "binding_busy")
            self.assertEqual(self.state_bytes(include_locks=False), before)
        before = self.state_bytes()
        real_stat = Path.stat

        def changed_identity(path, *args, **kwargs):
            stat = real_stat(path, *args, **kwargs)
            if path == self.service.common:
                fields = list(stat)
                fields[1] += 1
                return os.stat_result(fields)
            return stat

        with patch.object(Path, "stat", changed_identity):
            with self.assertRaises(core.ConcurrentDevelopmentError) as error:
                self.apply(token)
        self.assertEqual(error.exception.code, "relocation_state_changed")
        self.assertEqual(self.state_bytes(), before)

    def test_cli_preview_and_usage_expose_scope_and_expected_state(self) -> None:
        before = self.state_bytes()
        args = ["relocate-common-dir", "--root", str(self.old), "--state-root", str(self.state),
                "--from-common-dir", self.source, "--to-common-dir", self.target, "--dry-run"]
        for output_format in ("json", "text"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([*args, "--format", output_format]), 0)
            self.assertIn("expected_state", out.getvalue())
            self.assertIn("workspace_present", out.getvalue())
        payload = cli.usage_payload()
        self.assertIn("relocate-common-dir", [verb["name"] for verb in payload["verbs"]])
        self.assertTrue(payload["side_effects"]["supports_dry_run"])
        self.assertEqual(self.state_bytes(), before)

    def test_final_boundary_preserves_newer_binding_and_invalid_metadata_is_structured(self) -> None:
        value = self.record("main")
        for candidate in ("invalid", {"descriptor": []}, {"consumers": None}):
            core._atomic_write(self.service._file("main"), {**value, "candidate": candidate})
            with self.assertRaises(core.ConcurrentDevelopmentError) as malformed:
                self.preview()
            self.assertEqual(malformed.exception.code, "relocation_binding_invalid")
        core._atomic_write(self.service._file("main"), value)
        destination = {**self.record("absent"), "git_common_dir": self.target}
        core._atomic_write(self.service._file("absent"), destination)
        with self.assertRaises(core.ConcurrentDevelopmentError) as destination_bound:
            self.preview()
        self.assertEqual(destination_bound.exception.code, "relocation_destination_bound")
        core._atomic_write(self.service._file("absent"), {**destination, "git_common_dir": self.source})
        token = self.preview()["expected_state"]
        self.move(self.old, self.new)
        original_write = core._atomic_write
        newer = {**self.record("absent"), "generation": 900}

        def change_after_marker(path, value):
            original_write(path, value)
            if path == relocation._marker(self.service):
                original_write(self.service._file("absent"), newer)

        with patch.object(core, "_atomic_write", side_effect=change_after_marker):
            with self.assertRaises(core.ConcurrentDevelopmentError) as race:
                self.apply(token)
        self.assertEqual(race.exception.code, "relocation_state_changed")
        self.assertEqual(self.record("absent"), newer)
        self.assertTrue(relocation._marker(self.service).exists())


if __name__ == "__main__":
    unittest.main()
