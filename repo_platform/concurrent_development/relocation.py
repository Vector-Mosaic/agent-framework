"""Path-only integration-binding transition for a same-volume main-root rename.

The filesystem move and Git worktree repair have separate owners. This module
does not perform either, change refs, or reconstruct absent historical workspaces.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

from . import core

if TYPE_CHECKING:
    from .core import ConcurrentDevelopment

SCHEMA = "agent_framework.concurrent_relocation.v1"
_TOKEN = re.compile(r"[0-9a-f]{64}\Z")


def _marker(service: ConcurrentDevelopment) -> Path:
    return service.directory / "relocation" / "active.json"


def _common_path(value: str) -> Path:
    path = Path(value)
    if (not path.is_absolute() or path.name != ".git" or path != path.resolve() or
            path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())):
        core._fail("relocation_path_invalid", "Use exact absolute, unredirected main-workspace .git paths.")
    return path.resolve()


def _pending(service: ConcurrentDevelopment) -> dict[str, Any] | None:
    path = _marker(service)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        valid = (isinstance(value, dict) and
                 set(value) == {"schema", "repository", "from_common_dir", "to_common_dir", "expected_state"} and
                 value["schema"] == SCHEMA and value["repository"] == service.repository and
                 isinstance(value["expected_state"], str) and _TOKEN.fullmatch(value["expected_state"]))
        if not valid:
            raise ValueError
        _common_path(value["from_common_dir"])
        _common_path(value["to_common_dir"])
    except (OSError, ValueError, TypeError):
        core._fail("relocation_marker_invalid", "Preserve and inspect the unreadable relocation marker.",
                   operation_path=path.as_posix())
    return value


def check_pending(service: ConcurrentDevelopment) -> None:
    """Called inside the existing mutation lock, never a source/ref repair."""
    pending = _pending(service)
    if pending and service.common.as_posix() in {pending["from_common_dir"], pending["to_common_dir"]}:
        core._fail("relocation_interrupted", "Finish or reverse the exact binding-path relocation before integration.",
                   operation_path=_marker(service).as_posix(), expected_state=pending["expected_state"],
                   command="relocate-common-dir")


def _mapped(workspace: str, source: Path, target: Path) -> str:
    path = Path(workspace)
    if not path.is_absolute():
        core._fail("relocation_binding_invalid", "A selected binding has a non-absolute workspace.")
    return (target / path.relative_to(source)).as_posix() if path.is_relative_to(source) else workspace


def _snapshot(service: ConcurrentDevelopment, source: Path, target: Path,
              *, allow_after: bool) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    records: dict[str, Any] = {}
    refs: dict[str, str | None] = {}
    rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for path in sorted(service.directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            core._fail("binding_unreadable", "A binding is unreadable; relocation scope cannot be established.",
                       binding_file=path.name)
        if not isinstance(value, dict):
            core._fail("binding_unreadable", "A binding is not an object.", binding_file=path.name)
        common = value.get("git_common_dir")
        if common not in {source.as_posix(), target.as_posix()}:
            continue
        bid = value.get("binding_id")
        if (not isinstance(bid, str) or not core._ID.fullmatch(bid) or path.name != f"{bid}.json" or
                value.get("schema") != core.SCHEMA or value.get("repository") != service.repository or
                not isinstance(value.get("workspace"), str)):
            core._fail("relocation_binding_invalid", "A selected binding has inconsistent identity.", binding_file=path.name)
        after = common == target.as_posix()
        if after and not allow_after:
            core._fail("relocation_destination_bound", "Fresh preview cannot adopt existing destination bindings.")
        # Accept a whole before or after record. A crossed workspace/common-dir
        # pair is not an interrupted atomic replacement.
        wrong_root = source.parent if after else target.parent
        if Path(value["workspace"]).is_relative_to(wrong_root):
            core._fail("relocation_binding_conflict", "A binding contains crossed before/after paths.", binding_id=bid)
        before = {**value, "git_common_dir": source.as_posix(),
                  "workspace": _mapped(value["workspace"], target.parent, source.parent) if after else value["workspace"]}
        replacement = {**before, "git_common_dir": target.as_posix(),
                       "workspace": _mapped(before["workspace"], source.parent, target.parent)}
        records[path.name] = before
        for kind in ("candidates", "integration-state", "refresh-state"):
            ref = f"refs/agent-framework/{kind}/{bid}"
            refs[ref] = core._text(service.root, ["show-ref", "--verify", "--hash", ref], check=False) or None
        for key in ("refresh_operation", "pending_local_ref"):
            if value.get(key):
                blockers.append({"code": "binding_operation_pending", "binding_id": bid, "operation": key})
        actual = Path(before["workspace"] if service.common == source else replacement["workspace"])
        exists = os.path.lexists(actual)
        if exists:
            try:
                top = Path(core._text(actual, ["rev-parse", "--show-toplevel"])).resolve()
                common_dir = Path(core._text(actual, ["rev-parse", "--path-format=absolute", "--git-common-dir"])).resolve()
                if top != actual or common_dir != service.common:
                    raise ValueError
            except (core.ConcurrentDevelopmentError, ValueError):
                blockers.append({"code": "workspace_repair_required", "binding_id": bid, "workspace": actual.as_posix()})
        candidate = value.get("candidate")
        if candidate is None:
            candidate = {}
        if (not isinstance(candidate, dict) or not isinstance(candidate.get("descriptor", {}), dict) or
                not isinstance(candidate.get("consumers", []), list)):
            core._fail("relocation_binding_invalid", "A selected binding has unreadable candidate metadata.", binding_id=bid)
        rows.append({"binding_id": bid, "workspace_before": before["workspace"],
                     "workspace_after": replacement["workspace"], "workspace_present": exists,
                     "record_state": "after" if after else "before",
                     "candidate_id": candidate.get("descriptor", {}).get("candidate_id"),
                     "consumer_count": len(candidate.get("consumers", []))})
    git_dirs = [service.common]
    worktrees = service.common / "worktrees"
    if worktrees.is_dir():
        git_dirs.extend(path for path in worktrees.iterdir() if path.is_dir())
    for git_dir in git_dirs:
        for name in ("index.lock", "HEAD.lock", "config.lock", "packed-refs.lock", "MERGE_HEAD",
                     "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
            if (git_dir / name).exists():
                blockers.append({"code": "git_operation_active", "path": (git_dir / name).as_posix()})
    identity = service.common.stat()
    if not identity.st_dev or not identity.st_ino:
        core._fail("relocation_identity_unavailable", "The filesystem does not expose a usable common-directory identity.")
    snapshot = {"schema": SCHEMA, "repository": service.repository,
                "from_common_dir": source.as_posix(), "to_common_dir": target.as_posix(),
                "directory_identity": [identity.st_dev, identity.st_ino], "bindings": records, "refs": refs}
    return snapshot, rows, blockers


def relocate_common_dir(service: ConcurrentDevelopment, from_common_dir: str, to_common_dir: str,
                        *, dry_run: bool = False, expected_state: str | None = None,
                        outcome: str = "finish") -> dict[str, Any]:
    source, target = _common_path(from_common_dir), _common_path(to_common_dir)
    if (source == target or source.parent.is_relative_to(target.parent) or
            target.parent.is_relative_to(source.parent)):
        core._fail("relocation_path_invalid", "Source and destination main roots must be distinct and non-overlapping.")
    if (service.common not in {source, target} or service.git_dir != service.common or
            service.root != service.common.parent):
        core._fail("relocation_main_required", "Run from the actual main workspace before or after its same-volume rename.")
    other = target if service.common == source else source
    if os.path.lexists(other.parent):
        core._fail("relocation_destination_exists", "The other main-root location must be absent; copies and aliases are not a rename.")
    if outcome not in {"finish", "rollback"}:
        core._fail("relocation_outcome_invalid", "Select finish or rollback.")
    if expected_state is not None and not _TOKEN.fullmatch(expected_state):
        core._fail("relocation_expectation_required", "Use the exact expected_state from the pre-move preview.")
    if not dry_run and expected_state is None:
        core._fail("relocation_expectation_required", "A mutation requires the exact pre-move expected_state.")
    desired = target if outcome == "finish" else source
    if not dry_run and service.common != desired:
        core._fail("relocation_move_required", "Move the main directory and repair Git through their owners before applying this outcome.")

    def inspect() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        pending = _pending(service)
        if pending and (pending["from_common_dir"] != source.as_posix() or
                        pending["to_common_dir"] != target.as_posix() or
                        (expected_state is not None and pending["expected_state"] != expected_state)):
            core._fail("relocation_conflict", "Another exact relocation is pending; preserve its marker.")
        expected = expected_state or (pending["expected_state"] if pending else None)
        if service.common == target and expected is None:
            core._fail("relocation_expectation_required", "After physical movement, retain the original preview expectation.")
        snapshot, rows, blockers = _snapshot(service, source, target, allow_after=expected is not None)
        token = core._digest(snapshot)
        if expected is not None and token != expected:
            core._fail("relocation_state_changed", "Bindings, retained refs or physical repository identity changed; preserve the current state.")
        result = {"status": "relocation_preview", "expected_state": token,
                  "from_common_dir": source.as_posix(), "to_common_dir": target.as_posix(),
                  "binding_count": len(rows), "bindings": rows, "blockers": blockers,
                  "ready": not blockers, "pending": pending is not None, "network_read": False}
        return snapshot, result, pending

    if dry_run:
        return inspect()[1]
    with core._lock(service.directory / "bindings.lock"):
        snapshot, _, _ = inspect()
        with contextlib.ExitStack() as locks:
            for filename in sorted(snapshot["bindings"]):
                locks.enter_context(core._lock((service.directory / filename).with_suffix(".lock")))
            locked, result, pending = inspect()
            if locked != snapshot:
                core._fail("relocation_state_changed", "The selected binding inventory changed while acquiring locks.")
            if result["blockers"]:
                core._fail("relocation_busy", "Resolve the reported operation or Git repair before binding relocation.",
                           blockers=result["blockers"])
            marker = {"schema": SCHEMA, "repository": service.repository,
                      "from_common_dir": source.as_posix(), "to_common_dir": target.as_posix(),
                      "expected_state": expected_state}
            if pending is None:
                core._atomic_write(_marker(service), marker)
            for filename, before in locked["bindings"].items():
                after = {
                    **before, "git_common_dir": target.as_posix(),
                    "workspace": _mapped(before["workspace"], source.parent, target.parent)}
                replacement = before if outcome == "rollback" else after
                path = service.directory / filename
                current = json.loads(path.read_text(encoding="utf-8"))
                if current not in (before, after):
                    core._fail("relocation_state_changed", "A newer binding appeared before replacement; it is preserved.",
                               binding_file=filename)
                if current != replacement:
                    core._atomic_write(path, replacement)
            # A killed process can resume through the same expected-state check,
            # including an interruption after all writes but before marker removal.
            _, result, _ = inspect()
            _marker(service).unlink()
            with contextlib.suppress(OSError):
                _marker(service).parent.rmdir()
            result.update(status="relocated" if outcome == "finish" else "relocation_rolled_back", pending=False)
            return result
