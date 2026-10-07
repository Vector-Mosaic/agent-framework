"""Isolated Git candidate preparation, conditional publication and safe incorporation.

The caller owns semantic readiness and the workspace's safe execution boundary.
This module never creates a worktree, stashes source, or provides backup history.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence


SCHEMA = "agent_framework.concurrent_binding.v1"
CANDIDATE_SCHEMA = "agent_framework.concurrent_candidate.v1"
DEFAULT_SHARED_BRANCH = "main"
DEFAULT_REMOTE = "origin"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class ConcurrentDevelopmentError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def _fail(code: str, message: str, **details: Any) -> None:
    raise ConcurrentDevelopmentError(code, message, **details)


def _json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail("invalid_identity", "Use an exact bounded owner/binding/job identifier.")
    return value


def _git(root: Path, args: Sequence[str], *, data: bytes | None = None,
         index: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        environment.pop(key, None)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    try:
        result = subprocess.run(["git", "--literal-pathspecs", "-C", str(root), *args],
                                input=data, capture_output=True, env=environment, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail("git_unavailable", "Git could not complete the selected operation.", operation=args[0])
    if check and result.returncode:
        # Provider diagnostics can contain credential-bearing remote URLs.
        _fail("git_failed", "Git refused the selected operation; original source is preserved.",
              operation=args[0], exit_code=result.returncode)
    return result


def _text(root: Path, args: Sequence[str], **kwargs: Any) -> str:
    return _git(root, args, **kwargs).stdout.decode("utf-8", "surrogateescape").strip()


def _oid(root: Path, value: str, kind: str = "commit") -> str:
    if not isinstance(value, str) or not _OID.fullmatch(value):
        _fail("exact_oid_required", "An exact commit/tree identity is required.")
    actual = _text(root, ["rev-parse", "--verify", f"{value}^{{{kind}}}"])
    if not _OID.fullmatch(actual):
        _fail("invalid_object", "Git object identity could not be established.")
    return actual


def _tree(root: Path, value: str) -> str:
    return _oid(root, value, "tree")


def _paths(raw: bytes) -> list[str]:
    return [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]


def _changed(root: Path, old: str, new: str) -> list[str]:
    return _paths(_git(root, ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", old, new]).stdout)


def _entries(root: Path, tree: str) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for raw in _git(root, ["ls-tree", "-r", "-z", tree]).stdout.split(b"\0"):
        if not raw:
            continue
        metadata, path = raw.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        entries[path.decode("utf-8", "surrogateescape")] = (mode, oid)
    return entries


def _safe_path(root: Path, raw: str) -> str:
    if not isinstance(raw, str) or "\\" in raw or "\0" in raw or "\n" in raw:
        _fail("invalid_path", "Source paths must be literal repository-relative file paths.")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(p.lower() in {".", "..", ".git"} for p in path.parts):
        _fail("invalid_path", "Source paths must stay inside the selected workspace.")
    if any(":" in part for part in path.parts):
        _fail("invalid_path", "Absolute or pathspec source selectors are not supported.")
    candidate = root.joinpath(*path.parts)
    for parent in candidate.parents:
        if parent == root:
            break
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            _fail("unsafe_path", "Source paths cannot traverse symbolic-link or junction parents.")
    if candidate.is_dir() and not candidate.is_symlink():
        _fail("file_paths_required", "Select exact files; directory expansion is not implicit.")
    return path.as_posix()


def _merge(root: Path, base: str, ours: str, theirs: str, *, role: str,
           resolved: str | None = None) -> str:
    # Older supported real-merge implementations accept commits, not arbitrary
    # trees or an explicit --merge-base option. Give merge-ort exactly our three
    # trees through a synthetic common parent, without changing real history,
    # the index, or the worktree. Fixed metadata makes these temporary plumbing
    # objects deterministic; they are not task checkpoints or backup refs.
    def merge_input(treeish: str, label: str, parent: str | None = None) -> str:
        tree = _tree(root, treeish)
        body = f"tree {tree}\n"
        if parent is not None:
            body += f"parent {parent}\n"
        body += ("author Concurrent Development <concurrent-development@example.invalid> 0 +0000\n"
                 "committer Concurrent Development <concurrent-development@example.invalid> 0 +0000\n\n"
                 f"Exact merge {label}\n")
        return _text(root, ["hash-object", "-t", "commit", "-w", "--stdin"], data=body.encode())

    common = merge_input(base, "base")
    left = merge_input(ours, "ours", common)
    right = merge_input(theirs, "theirs", common)
    result = _git(root, ["merge-tree", "--write-tree", "-z", "--name-only",
                         left, right], check=False)
    first = result.stdout.split(b"\0", 1)[0].decode("ascii", "replace").strip()
    if result.returncode == 0 and _OID.fullmatch(first):
        if resolved is not None and _tree(root, resolved) != first:
            _fail("unexpected_resolution", "An explicit resolution cannot replace a clean merge.", role=role)
        return first
    if result.returncode == 1 and _OID.fullmatch(first):
        if resolved is not None:
            return _tree(root, resolved)
        _fail("merge_conflict", "Resolve this exact combination before publication or refresh.",
              role=role, base=base, ours=ours, theirs=theirs, conflicted_tree=first)
    diagnostic = result.stderr.decode("utf-8", "replace").lower()
    error_class = ("unsupported_merge_option" if "unknown option" in diagnostic or "unrecognized option" in diagnostic else
                   "invalid_merge_input" if "not something we can merge" in diagnostic or "bad object" in diagnostic else
                   "git_merge_execution_failure")
    version_text = _text(root, ["--version"], check=False)
    version = version_text if re.fullmatch(r"git version [0-9A-Za-z._ +()-]{1,100}", version_text) else "unavailable"
    _fail("merge_failed", f"Git could not construct the selected combination ({version}; exit {result.returncode}; {error_class}).", role=role,
          git_exit_code=result.returncode, git_version=version, git_error_class=error_class,
          stderr_bytes=len(result.stderr), stderr_sha256=hashlib.sha256(result.stderr).hexdigest())


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".binding-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _fail("binding_busy", "Another operation currently owns this integration binding.")
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class ConcurrentDevelopment:
    def __init__(self, root: Path, state_root: Path | None = None, *,
                 remote: str = DEFAULT_REMOTE, shared_branch: str = DEFAULT_SHARED_BRANCH):
        self.root = Path(_text(Path(root).resolve(), ["rev-parse", "--show-toplevel"])).resolve()
        self.common = Path(_text(self.root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])).resolve()
        self.git_dir = Path(_text(self.root, ["rev-parse", "--path-format=absolute", "--git-dir"])).resolve()
        if not isinstance(remote, str) or not _ID.fullmatch(remote):
            _fail("invalid_remote", "Select a configured remote name, not a URL or command option.")
        if (not isinstance(shared_branch, str) or shared_branch.startswith(("-", "refs/")) or
                _git(self.root, ["check-ref-format", f"refs/heads/{shared_branch}"], check=False).returncode):
            _fail("invalid_shared_branch", "Select a valid branch name, without a refs/heads/ prefix.")
        if remote not in _text(self.root, ["remote"]).splitlines():
            _fail("remote_missing", "The selected remote name must already be configured in the workspace.")
        self.remote = remote
        self.shared_ref = f"refs/heads/{shared_branch}"
        urls = _text(self.root, ["remote", "get-url", "--all", remote]).splitlines()
        push_urls = _text(self.root, ["remote", "get-url", "--push", "--all", remote]).splitlines()
        if len(urls) != 1 or push_urls != urls:
            _fail("remote_endpoint_mismatch", "Integration requires one shared fetch/push endpoint so publication readback observes the destination.")
        url = urls[0]
        normalized = re.sub(r"^git@([^:]+):", r"ssh://\1/", url).rstrip("/")
        normalized = re.sub(r"^(?:https?|ssh)://(?:git@)?", "", normalized)
        normalized = normalized.removesuffix(".git")
        self.repository = hashlib.sha256(normalized.encode()).hexdigest()
        if state_root is not None:
            self.state_root = Path(state_root)
        else:
            configured = os.environ.get("AGENT_FRAMEWORK_STATE_HOME")
            if configured:
                state_home = Path(configured)
            elif os.name == "nt":
                state_home = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "AgentFramework"
            else:
                state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "agent-framework"
            self.state_root = state_home / "concurrent-development"
        self.directory = self.state_root.resolve() / self.repository

    def _file(self, binding_id: str) -> Path:
        return self.directory / f"{_identifier(binding_id)}.json"

    @contextlib.contextmanager
    def _locked(self, binding_id: str, *, allow_refresh_reconciliation: bool = False) -> Iterator[dict[str, Any]]:
        with _lock(self._file(binding_id).with_suffix(".lock")):
            self._check_relocation()
            binding = self._load(binding_id)
            if binding.get("refresh_operation") and not allow_refresh_reconciliation:
                _fail("refresh_interrupted", "Reconcile the exact unfinished refresh before another binding operation.",
                      binding_id=binding_id, command="reconcile-refresh")
            pending = binding.get("pending_local_ref")
            if pending:
                self._advance_local_refs(binding)
            yield binding

    def _advance_local_refs(self, binding: dict[str, Any]) -> None:
        pending = binding["pending_local_ref"]
        commands = ["start"]
        for suffix, old_key, new_key in (("candidates", "old", "new"), ("integration-state", "old_state", "new_state")):
            ref = f"refs/agent-framework/{suffix}/{binding['binding_id']}"
            observed = _text(self.root, ["rev-parse", "--verify", ref], check=False) or None
            if observed not in {pending[old_key], pending[new_key]}:
                _fail("candidate_ref_changed", "An interrupted local ref update meets an unexpected owner.")
            if observed != pending[new_key]:
                commands.append(f"update {ref} {pending[new_key]} {observed or '0' * len(pending[new_key])}")
        commands.extend(["prepare", "commit", ""])
        _git(self.root, ["update-ref", "--stdin"], data="\n".join(commands).encode())
        binding.pop("pending_local_ref")
        self._save(binding)

    def _load(self, binding_id: str) -> dict[str, Any]:
        try:
            value = json.loads(self._file(binding_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _fail("binding_missing", "The exact integration binding is missing or unreadable.")
        if (value.get("schema") != SCHEMA or value.get("binding_id") != binding_id or
                value.get("repository") != self.repository or
                value.get("workspace") != self.root.as_posix() or
                value.get("git_common_dir") != self.common.as_posix() or
                value.get("remote") != self.remote or value.get("shared_ref") != self.shared_ref):
            _fail("binding_mismatch", "The binding does not belong to this exact workspace and repository.")
        return value

    def _save(self, binding: dict[str, Any]) -> None:
        _atomic_write(self._file(binding["binding_id"]), binding)

    def _check_relocation(self) -> None:
        from .relocation import check_pending
        check_pending(self)

    def relocate_common_dir(self, from_common_dir: str, to_common_dir: str, *,
                            dry_run: bool = False, expected_state: str | None = None,
                            outcome: str = "finish") -> dict[str, Any]:
        from .relocation import relocate_common_dir
        return relocate_common_dir(self, from_common_dir, to_common_dir, dry_run=dry_run,
                                   expected_state=expected_state, outcome=outcome)

    def _head(self) -> str:
        return _text(self.root, ["rev-parse", "HEAD"])

    def _index_path(self) -> Path:
        return Path(_text(self.root, ["rev-parse", "--path-format=absolute", "--git-path", "index"]))

    def _check_source_operation(self) -> None:
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "index.lock"):
            if (self.git_dir / name).exists():
                _fail("workspace_operation_active", "Finish the existing Git operation before integration.", marker=name)
        if _git(self.root, ["ls-files", "--unmerged", "-z"]).stdout:
            _fail("unmerged_index", "Resolve the actual index before source integration.")
        if _text(self.root, ["config", "--bool", "core.sparseCheckout"], check=False) == "true":
            _fail("sparse_workspace_unsupported", "Sparse workspace incorporation is not supported by this owner.")

    def _capture(self, base: str, paths: Sequence[str] | None, extra: Sequence[str] = ()) -> str:
        baseline_paths = set(_entries(self.root, _tree(self.root, base)))
        indexed_paths = set(_paths(_git(self.root, ["ls-files", "--cached", "-z"]).stdout))
        selected = ([_safe_path(self.root, p) for p in paths] if paths is not None else
                    sorted(baseline_paths |
                           indexed_paths |
                           {_safe_path(self.root, p) for p in extra}))
        if paths is None:
            # An owned new file can subsequently be removed or its deletion
            # incorporated. Absence outside the baseline is already represented
            # by read-tree; asking git add to delete a never-present path fails.
            selected = [path for path in selected if path in baseline_paths or os.path.lexists(self.root / path)]
        selected = [_safe_path(self.root, path) for path in selected]
        with tempfile.TemporaryDirectory(prefix="concurrent-index-") as temporary:
            index = Path(temporary) / "index"
            _git(self.root, ["read-tree", base], index=index)
            tracked = baseline_paths | indexed_paths
            for force, group in ((True, [p for p in selected if p in tracked]),
                                 (False, [p for p in selected if p not in tracked])):
                if group:
                    # The incorporated baseline may not yet know a file already
                    # tracked by the actual workspace. Preserve normal Git's
                    # tracked-file behavior even under newly ignored folders;
                    # never force-add an untracked ignored neighbor or secret.
                    flags = ["--force"] if force else []
                    _git(self.root, ["add", "-A", *flags, "--pathspec-from-file=-", "--pathspec-file-nul"],
                         index=index, data=b"\0".join(p.encode("utf-8", "surrogateescape") for p in group) + b"\0")
            tree = _text(self.root, ["write-tree"], index=index)
        if any(mode == "160000" for mode, _ in _entries(self.root, tree).values()):
            _fail("submodule_unsupported", "Source integration containing gitlinks needs its owning submodule procedure.")
        return tree

    def _remote_oid(self, ref: str) -> str | None:
        result = _git(self.root, ["ls-remote", "--refs", self.remote, ref], check=False)
        if result.returncode:
            _fail("remote_unavailable", "The exact remote ref could not be read.")
        rows = [line.split() for line in result.stdout.decode().splitlines()]
        if not rows:
            return None
        if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != ref or not _OID.fullmatch(rows[0][0]):
            _fail("remote_ref_ambiguous", "The remote returned an unexpected ref identity.")
        return rows[0][0]

    def _shared(self) -> str:
        oid = self._remote_oid(self.shared_ref)
        if oid is None:
            _fail("shared_ref_missing", "The configured remote shared branch must exist before integration.")
        _git(self.root, ["fetch", "--no-tags", "--no-write-fetch-head", self.remote, oid])
        return _oid(self.root, oid)

    def bind(self, binding_id: str, owner_id: str, base: str, lifecycle_owner: str = "external_workspace",
             owned_paths: Sequence[str] = ()) -> dict[str, Any]:
        _identifier(binding_id)
        if not owner_id or len(owner_id) > 512 or any(ord(c) < 32 for c in owner_id):
            _fail("invalid_owner", "An actual task, person or worker attempt identity is required.")
        if lifecycle_owner not in {"external_workspace", "codex_native", "runner"}:
            _fail("invalid_lifecycle_owner", "Select the actual workspace lifecycle owner.")
        base = _oid(self.root, base)
        if not self._included(base, self._head()):
            _fail("base_not_incorporated", "The declared shared base is not in this workspace's ordinary Git history.")
        paths = sorted({_safe_path(self.root, p) for p in owned_paths})
        with _lock(self.directory / "bindings.lock"):
            self._check_relocation()
            if self._file(binding_id).exists():
                value = self._load(binding_id)
                if value["owner_id"] != owner_id or value["lifecycle_owner"] != lifecycle_owner:
                    _fail("binding_owned", "This integration binding already belongs to another owner.")
                return value
            for path in self.directory.glob("*.json"):
                try:
                    other = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    _fail("binding_unreadable", "An existing workspace binding needs repair before adding another.")
                if other.get("workspace") == self.root.as_posix():
                    _fail("workspace_already_bound", "This mutable workspace already has an integration owner.")
            value = {"schema": SCHEMA, "binding_id": binding_id, "owner_id": owner_id,
                     "repository": self.repository, "workspace": self.root.as_posix(),
                     "remote": self.remote, "shared_ref": self.shared_ref,
                     "git_common_dir": self.common.as_posix(), "lifecycle_owner": lifecycle_owner,
                     "workspace_ref": _text(self.root, ["symbolic-ref", "-q", "HEAD"], check=False) or None,
                     "base": base, "generation": 0, "owned_paths": paths, "candidate": None}
            self._save(value)
            return value

    def status(self, binding_id: str) -> dict[str, Any]:
        value = self._load(binding_id)
        return {"binding": value, "head": self._head(), "network_read": False}

    def release_binding(self, binding_id: str, owner_id: str) -> dict[str, Any]:
        with self._locked(binding_id) as binding:
            if binding["owner_id"] != owner_id:
                _fail("binding_owned", "Only the exact lifecycle owner may release this workspace binding.")
            if binding.get("candidate") is not None:
                _fail("candidate_unfinished", "Close the candidate and its actual consumers before releasing the workspace binding.")
            self._file(binding_id).unlink()
            return {"status": "binding_released", "binding_id": binding_id, "workspace_removed": False}

    def _candidate(self, binding: Mapping[str, Any], candidate_id: str,
                   *, require_current: bool = True) -> dict[str, Any]:
        candidate = binding.get("candidate")
        if not isinstance(candidate, dict) or candidate.get("descriptor", {}).get("candidate_id") != candidate_id:
            _fail("candidate_mismatch", "The exact candidate is not owned by this binding.")
        descriptor = validate_candidate_descriptor(candidate["descriptor"])
        if (descriptor["binding_id"] != binding["binding_id"] or descriptor["owner_id"] != binding["owner_id"] or
                descriptor["repository"] != binding["repository"] or
                descriptor["remote"] != binding["remote"] or descriptor["shared_ref"] != binding["shared_ref"]):
            _fail("candidate_owner_mismatch", "The candidate descriptor belongs to a different binding or owner.")
        if require_current and (candidate.get("retracted") or descriptor["generation"] != binding["generation"]):
            _fail("candidate_retracted", "The candidate no longer has the current ready selection.")
        observed = _text(self.root, ["rev-parse", "--verify", f"refs/agent-framework/candidates/{binding['binding_id']}"], check=False)
        if (observed and observed != descriptor["commit"]) or (require_current and not observed):
            _fail("candidate_ref_changed", "The local candidate ref no longer identifies the owned operation.")
        parents = _text(self.root, ["rev-list", "--parents", "-n", "1", descriptor["commit"]]).split()
        if parents != [descriptor["commit"], descriptor["parent"]] or _tree(self.root, descriptor["commit"]) != descriptor["tree"]:
            _fail("candidate_object_mismatch", "The candidate's exact tree and single parent do not match its binding.")
        return candidate

    def _selection_current(self, binding: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
        current = self._capture(candidate["source_base"], candidate["selection_paths"])
        if current != candidate["source_selection_tree"]:
            _fail("ready_selection_changed", "A selected source file changed; explicitly prepare the still-ready selection again.")

    def _require_reconciled(self, candidate: Mapping[str, Any] | None) -> None:
        if candidate and (candidate.get("publication_uncertain") or candidate.get("exposure_uncertain")):
            _fail("remote_outcome_uncertain", "Reconcile the exact interrupted publish or expose operation before changing its candidate.")

    def prepare(self, binding_id: str, paths: Sequence[str], message: str, expected_generation: int,
                ready_tree: str | None = None, resolved_candidate_tree: str | None = None,
                resolved_residual_tree: str | None = None, resolved_carry_tree: str | None = None,
                expected_shared: str | None = None, expected_base: str | None = None,
                expected_source_tree: str | None = None) -> dict[str, Any]:
        if not message.strip() or "\0" in message:
            _fail("message_required", "Provide the intended integration commit message.")
        if bool(paths) == bool(ready_tree):
            _fail("ready_selection_required", "Select exact ready paths or an explicit ready tree, exclusively.")
        if any((resolved_candidate_tree, resolved_residual_tree, resolved_carry_tree)):
            if expected_shared is None or expected_base is None or ready_tree is None:
                _fail("resolution_inputs_required", "Explicit preparation resolutions require exact expected shared/base identities and an immutable ready-tree selection.")
            if (resolved_residual_tree or resolved_carry_tree) and expected_source_tree is None:
                _fail("resolution_inputs_required", "A carry or residual resolution also requires the exact complete source tree it resolved.")
        with self._locked(binding_id) as binding:
            if binding["generation"] != expected_generation:
                _fail("generation_changed", "The task's ready selection changed; inspect and recombine rather than reusing an older resolution.",
                      expected=expected_generation, observed=binding["generation"])
            previous = binding.get("candidate")
            self._require_reconciled(previous)
            if previous and previous.get("consumers"):
                _fail("candidate_in_use", "Release the candidate's actual remote consumers before replacing it.")
            if previous and previous.get("published"):
                _fail("refresh_required", "Incorporate the published result before selecting further integration.")
            self._check_source_operation()
            base = binding["base"]
            if expected_base is not None and _oid(self.root, expected_base) != base:
                _fail("stale_resolution", "The incorporated source base changed; resolve the current combination.",
                      input="base", expected=expected_base, observed=base)
            selected = [_safe_path(self.root, p) for p in paths]
            index_before = self._index_path().read_bytes()
            ready = _tree(self.root, ready_tree) if ready_tree else self._capture(base, selected)
            selection_paths = sorted(set(selected) | set(_changed(self.root, base, ready)))
            owned = sorted(set(binding["owned_paths"]) | set(selection_paths))
            complete = self._capture(base, None, owned)
            original_ready, original_complete = ready, complete
            source_selection_tree = self._capture(base, selection_paths)
            shared = self._shared()
            if expected_shared is not None and _oid(self.root, expected_shared) != shared:
                _fail("stale_resolution", "Shared source advanced after this resolution; combine the new shared contribution before preparing.",
                      input="shared", expected=expected_shared, observed=shared)
            if expected_source_tree is not None and _tree(self.root, expected_source_tree) != complete:
                _fail("stale_resolution", "Complete local source changed after this resolution; preserve and combine its newer edits.",
                      input="source_tree", expected=expected_source_tree, observed=complete)
            # If repeating the identical selection after a ref race, carry the
            # already resolved C/D forward rather than replaying an older patch.
            try:
                if (previous and not previous.get("retracted") and
                        previous.get("original_ready") == ready):
                    base = previous["descriptor"]["parent"]
                    ready = previous["descriptor"]["tree"]
                    complete = _merge(self.root, previous["original_complete"], previous["residual_tree"],
                                      complete, role="new_residual_edits", resolved=resolved_carry_tree)
                elif resolved_carry_tree is not None:
                    _fail("unexpected_resolution", "A carry resolution applies only when retaining a previous ready selection.")
                combined = _merge(self.root, base, shared, ready, role="ready",
                                  resolved=resolved_candidate_tree)
                residual = _merge(self.root, ready, combined, complete, role="residual",
                                  resolved=resolved_residual_tree)
            except ConcurrentDevelopmentError as error:
                if error.code == "merge_conflict":
                    error.details.update(expected_base=binding["base"], expected_shared=shared,
                                         ready_tree=original_ready, expected_source_tree=original_complete,
                                         expected_generation=expected_generation)
                raise
            if self._index_path().read_bytes() != index_before or self._capture(binding["base"], None, owned) != original_complete:
                _fail("workspace_changed", "The workspace or index changed during candidate capture; retry from current source.")
            commit = _text(self.root, ["commit-tree", combined, "-p", shared], data=(message.rstrip() + "\n").encode())
            generation = binding["generation"] + 1
            descriptor = {"schema": CANDIDATE_SCHEMA, "repository": self.repository,
                          "binding_id": binding_id, "owner_id": binding["owner_id"],
                          "generation": generation, "commit": commit, "tree": combined,
                          "parent": shared, "selected_tree": ready, "remote": self.remote,
                          "shared_ref": self.shared_ref,
                          "remote_ref": f"refs/heads/agent-framework/candidates/{binding_id}"}
            descriptor["candidate_id"] = _digest(descriptor)
            local_ref = f"refs/agent-framework/candidates/{binding_id}"
            old = _text(self.root, ["rev-parse", "--verify", local_ref], check=False)
            if old and (not previous or old != previous["descriptor"]["commit"]):
                _fail("candidate_ref_changed", "The local candidate ref no longer belongs to the expected operation.")
            # These are live operation inputs, not checkpoint history. A tree
            # ref keeps their exact objects reachable until the operation closes.
            operation_tree = _text(self.root, ["mktree"], data="".join(
                f"040000 tree {oid}\t{name}\n" for name, oid in sorted({
                    "candidate": combined, "selected": ready, "complete": complete,
                    "residual": residual, "original_ready": original_ready,
                    "original_complete": original_complete, "source_selection": source_selection_tree,
                }.items())).encode())
            state_ref = f"refs/agent-framework/integration-state/{binding_id}"
            old_state = _text(self.root, ["rev-parse", "--verify", state_ref], check=False) or None
            if old_state and (not previous or old_state != previous.get("operation_tree")):
                _fail("candidate_ref_changed", "The operation-input ref belongs to an unexpected source generation.")
            binding.update(generation=generation, owned_paths=owned, candidate={
                "descriptor": descriptor, "selection_base": base, "complete_tree": complete,
                "operation_tree": operation_tree,
                "residual_tree": residual, "original_ready": original_ready,
                "original_complete": original_complete,
                "source_base": binding["base"], "selection_paths": selection_paths,
                "source_selection_tree": source_selection_tree,
                "retracted": False, "consumers": [], "published": False, "exposed": None,
                "prior_exposed": (previous.get("exposed") or previous.get("prior_exposed")) if previous else None})
            binding["pending_local_ref"] = {"old": old or None, "new": commit,
                                            "old_state": old_state, "new_state": operation_tree}
            self._save(binding)
            self._advance_local_refs(binding)
            return {"status": "prepared", "candidate": descriptor, "residual_tree": residual,
                    "changed_paths": _changed(self.root, shared, combined), "assurance_claimed": False}

    def expose(self, binding_id: str, candidate_id: str, expected_remote: str | None) -> dict[str, Any]:
        with self._locked(binding_id) as binding:
            candidate = self._candidate(binding, candidate_id)
            descriptor = candidate["descriptor"]
            if expected_remote is not None:
                _oid(self.root, expected_remote)
            observed = self._remote_oid(descriptor["remote_ref"])
            if candidate.pop("exposure_uncertain", None):
                self._save(binding)
            if observed == descriptor["commit"]:
                candidate["exposed"] = observed
                candidate.pop("exposure_uncertain", None)
                self._save(binding)
                return {"status": "already_exposed", "candidate": descriptor}
            self._selection_current(binding, candidate)
            if candidate["consumers"]:
                _fail("candidate_in_use", "An issued job owns the exact exposed source ref.")
            if observed != expected_remote:
                _fail("candidate_ref_changed", "The candidate ref moved; reconcile its actual owner before replacing it.")
            if expected_remote != candidate.get("prior_exposed"):
                _fail("candidate_ref_unowned", "The expected replaced ref is not a prior source exposed by this binding.")
            candidate["exposure_uncertain"] = True
            self._save(binding)
            try:
                result = _git(self.root, ["push", "--porcelain", "--no-follow-tags", "--recurse-submodules=no",
                    f"--force-with-lease={descriptor['remote_ref']}:{expected_remote or ''}", self.remote,
                    f"{descriptor['commit']}:{descriptor['remote_ref']}"], check=False)
                transport_exit = result.returncode
            except ConcurrentDevelopmentError:
                transport_exit = -1
            try:
                after = self._remote_oid(descriptor["remote_ref"])
            except ConcurrentDevelopmentError:
                candidate["exposure_uncertain"] = True
                self._save(binding)
                return {"status": "uncertain", "candidate": descriptor}
            if after != descriptor["commit"]:
                candidate.pop("exposure_uncertain", None)
                self._save(binding)
                return {"status": "not_exposed", "observed": after, "exit_code": transport_exit}
            candidate["exposed"] = after
            candidate.pop("exposure_uncertain", None)
            self._save(binding)
            return {"status": "exposed", "candidate": descriptor}

    def publish(self, binding_id: str, candidate_id: str, expected_shared: str) -> dict[str, Any]:
        with self._locked(binding_id) as binding:
            candidate = self._candidate(binding, candidate_id)
            descriptor = candidate["descriptor"]
            if _oid(self.root, expected_shared) != descriptor["parent"]:
                _fail("candidate_base_mismatch", "Publication must name the candidate's exact shared parent.")
            current = self._shared()
            if candidate.pop("publication_uncertain", None):
                self._save(binding)
            if self._included(descriptor["commit"], current):
                candidate["published"] = True
                candidate.pop("publication_uncertain", None)
                self._save(binding)
                return {"status": "already_included", "commit": descriptor["commit"],
                        "observed": current, "refresh_required": not candidate.get("incorporated", False)}
            if current != expected_shared:
                candidate.pop("publication_uncertain", None)
                self._save(binding)
                return {"status": "stale", "expected": expected_shared, "observed": current,
                        "recombine_required": True}
            self._selection_current(binding, candidate)
            candidate["publication_uncertain"] = True
            self._save(binding)
            try:
                result = _git(self.root, ["push", "--porcelain", "--no-follow-tags", "--recurse-submodules=no",
                             f"--force-with-lease={self.shared_ref}:{expected_shared}", self.remote,
                             f"{descriptor['commit']}:{self.shared_ref}"], check=False)
                transport_exit = result.returncode
            except ConcurrentDevelopmentError:
                transport_exit = -1
            try:
                observed = self._shared()
            except ConcurrentDevelopmentError:
                candidate["publication_uncertain"] = True
                self._save(binding)
                return {"status": "uncertain", "commit": descriptor["commit"], "refresh_required": False}
            if self._included(descriptor["commit"], observed):
                candidate["published"] = True
                candidate.pop("publication_uncertain", None)
                self._save(binding)
                return {"status": "published", "commit": descriptor["commit"], "observed": observed,
                        "refresh_required": True, "transport_exit_code": transport_exit}
            candidate.pop("publication_uncertain", None)
            self._save(binding)
            return {"status": "stale" if observed != expected_shared else "not_published",
                    "observed": observed, "recombine_required": observed != expected_shared}

    def _included(self, commit: str, observed: str) -> bool:
        return _git(self.root, ["merge-base", "--is-ancestor", commit, observed], check=False).returncode == 0

    def retract(self, binding_id: str, candidate_id: str) -> dict[str, Any]:
        with self._locked(binding_id) as binding:
            candidate = self._candidate(binding, candidate_id, require_current=False)
            self._require_reconciled(candidate)
            if candidate["published"]:
                _fail("already_published", "Retraction cannot undo shared publication; incorporate or author a corrective change.")
            if not candidate["retracted"]:
                binding["generation"] += 1
            candidate["retracted"] = True
            self._save(binding)
            return {"status": "retracted", "consumers_retained": list(candidate["consumers"])}

    def retain(self, binding_id: str, candidate_id: str, job_id: str) -> dict[str, Any]:
        _identifier(job_id)
        with self._locked(binding_id) as binding:
            candidate = self._candidate(binding, candidate_id)
            self._selection_current(binding, candidate)
            if self._remote_oid(candidate["descriptor"]["remote_ref"]) != candidate["descriptor"]["commit"]:
                _fail("candidate_not_exposed", "The exact candidate must remain exposed before a remote consumer can retain it.")
            candidate["consumers"] = sorted(set(candidate["consumers"]) | {job_id})
            self._save(binding)
            return candidate["descriptor"]

    def release(self, binding_id: str, candidate_id: str, job_id: str) -> dict[str, Any]:
        with self._locked(binding_id) as binding:
            candidate = self._candidate(binding, candidate_id, require_current=False)
            candidate["consumers"] = [item for item in candidate["consumers"] if item != _identifier(job_id)]
            self._save(binding)
            return {"status": "released", "remaining_consumers": candidate["consumers"]}

    def refresh(self, binding_id: str, target: str, expected_generation: int,
                resolved_source_tree: str | None = None, resolved_index_tree: str | None = None,
                resolved_head_tree: str | None = None, expected_base: str | None = None,
                expected_source_tree: str | None = None, expected_head: str | None = None,
                expected_index_tree: str | None = None) -> dict[str, Any]:
        if any((resolved_source_tree, resolved_index_tree, resolved_head_tree)) and expected_base is None:
            _fail("resolution_inputs_required", "Every explicit refresh resolution requires its exact incorporated base.")
        if resolved_source_tree is not None and expected_source_tree is None:
            _fail("resolution_inputs_required", "An explicit source resolution requires its exact complete source input.")
        if (resolved_head_tree or resolved_index_tree) and expected_head is None:
            _fail("resolution_inputs_required", "A HEAD or index resolution requires its exact existing HEAD input.")
        if resolved_index_tree is not None and expected_index_tree is None:
            _fail("resolution_inputs_required", "An index resolution requires the exact staged tree it resolved.")
        with self._locked(binding_id) as binding:
            if binding["generation"] != expected_generation:
                _fail("generation_changed", "Inspect and recombine the current binding before refreshing its workspace.",
                      expected=expected_generation, observed=binding["generation"])
            self._check_source_operation()
            shared = self._shared()
            target = _oid(self.root, target)
            if not self._included(target, shared):
                _fail("target_not_shared", "Refresh target must be an exact revision retained by the configured remote shared branch.")
            if not self._included(binding["base"], target):
                _fail("refresh_would_rewind", "Refresh must retain the shared base already incorporated by this workspace.")
            symbolic = _text(self.root, ["symbolic-ref", "-q", "HEAD"], check=False)
            if (symbolic or None) != binding["workspace_ref"] or symbolic == self.shared_ref:
                _fail("workspace_branch_protected", "Refresh requires the originally bound detached HEAD or owned feature branch; the shared branch is protected.")
            head = self._head()
            index_path = self._index_path()
            original_index = index_path.read_bytes()
            before = self._capture(binding["base"], None, binding["owned_paths"])
            with tempfile.TemporaryDirectory(prefix="concurrent-staged-") as staged_temporary:
                staged_index = Path(staged_temporary) / "index"
                staged_index.write_bytes(original_index)
                staged = _text(self.root, ["write-tree"], index=staged_index)
            inputs = {"expected_base": binding["base"], "expected_source_tree": before,
                      "expected_head": head, "expected_index_tree": staged,
                      "expected_generation": expected_generation}
            for name, expected, observed, kind in (
                ("base", expected_base, binding["base"], "commit"),
                ("source_tree", expected_source_tree, before, "tree"),
                ("head", expected_head, head, "commit"),
                ("index_tree", expected_index_tree, staged, "tree"),
            ):
                if expected is not None and _oid(self.root, expected, kind) != observed:
                    _fail("stale_resolution", "A local refresh input changed after this resolution; preserve and resolve its current meaning.",
                          input=name, expected=expected, observed=observed)
            candidate = binding.get("candidate")
            self._require_reconciled(candidate)
            if candidate and not candidate.get("published") and not candidate.get("retracted"):
                _fail("candidate_pending", "Publish or retract the prepared selection before changing its source base.")
            try:
                if candidate and candidate.get("published") and not candidate.get("incorporated"):
                    descriptor = candidate["descriptor"]
                    if not self._included(descriptor["commit"], target):
                        _fail("published_source_missing", "Refresh must retain this task's published contribution.")
                    carried = _merge(self.root, candidate["original_complete"], candidate["complete_tree"], before,
                                     role="new_local_edits")
                    residual = _merge(self.root, descriptor["selected_tree"], descriptor["tree"], carried,
                                      role="published_residual")
                    after = _merge(self.root, descriptor["commit"], target, residual, role="later_shared_changes")
                else:
                    after = _merge(self.root, binding["base"], target, before, role="refresh")
            except ConcurrentDevelopmentError as exc:
                if exc.code == "merge_conflict":
                    exc.details.update(inputs)
                if exc.code != "merge_conflict" or resolved_source_tree is None:
                    raise
                after = _tree(self.root, resolved_source_tree)
            else:
                if resolved_source_tree is not None and _tree(self.root, resolved_source_tree) != after:
                    _fail("unexpected_resolution", "An explicit source resolution cannot replace a clean refresh.")
            with tempfile.TemporaryDirectory(prefix="concurrent-refresh-") as temporary:
                operation = Path(temporary)
                private_index = operation / "index"
                private_index.write_bytes(original_index)
                # Preserve ordinary local commits in normal Git ancestry. The
                # shared base remains target; unpublished source is not pushed.
                if head == binding["base"] or self._included(head, target):
                    new_head = target
                    if resolved_head_tree is not None and _tree(self.root, resolved_head_tree) != _tree(self.root, target):
                        _fail("unexpected_resolution", "An explicit HEAD resolution cannot replace retained shared history.")
                else:
                    try:
                        head_tree = _merge(self.root, binding["base"], target, head, role="local_commits", resolved=resolved_head_tree)
                    except ConcurrentDevelopmentError as error:
                        if error.code == "merge_conflict":
                            error.details.update(inputs)
                        raise
                    new_head = _text(self.root, ["commit-tree", head_tree, "-p", head, "-p", target],
                                     data=b"Merge shared source into isolated workspace\n")
                try:
                    next_index_tree = _merge(self.root, head, new_head, staged, role="staged_intent", resolved=resolved_index_tree)
                except ConcurrentDevelopmentError as error:
                    if error.code == "merge_conflict":
                        error.details.update(inputs)
                    raise
                # Copying and editing only changed entries preserves unrelated
                # index flags and stage selections, rather than rebuilding it.
                old_entries = _entries(self.root, staged)
                new_entries = _entries(self.root, next_index_tree)
                changes = []
                for path in sorted(set(old_entries) | set(new_entries)):
                    if old_entries.get(path) == new_entries.get(path):
                        continue
                    mode, oid = new_entries.get(path, ("0", "0" * len(head)))
                    changes.append(f"{mode} {oid}\t{path}".encode("utf-8", "surrogateescape"))
                if changes:
                    _git(self.root, ["update-index", "-z", "--index-info"], index=private_index,
                         data=b"\0".join(changes) + b"\0")
                replacement_index = private_index.read_bytes()
                self._install_source(before, after, original_index, replacement_index,
                                     head, new_head, binding, target, operation)
            return {"status": "refreshed", "base": target, "workspace_head": new_head,
                    "source_tree": after, "index_tree": next_index_tree}

    def _install_source(self, before: str, after: str, index_before: bytes, index_after: bytes,
                        head_before: str, head_after: str, binding: dict[str, Any], target: str, operation: Path) -> None:
        previous, following = _entries(self.root, before), _entries(self.root, after)
        changed = sorted(path for path in set(previous) | set(following)
                         if previous.get(path) != following.get(path))
        for path in changed:
            _safe_path(self.root, path)
            if following.get(path, (None, None))[0] in {"120000", "160000"} or previous.get(path, (None, None))[0] in {"120000", "160000"}:
                _fail("special_path_refresh_unsupported", "Changed symlink/gitlink incorporation needs its owning Git procedure.", path=path)
            file = self.root / path
            if path not in previous and os.path.lexists(file):
                _fail("untracked_collision", "An unselected local path would be overwritten by refresh.", path=path)
        output = operation / "checkout"
        output.mkdir()
        checkout_index = operation / "checkout-index"
        _git(self.root, ["read-tree", after], index=checkout_index)
        installs = [path for path in changed if path in following]
        if installs:
            _git(self.root, ["checkout-index", "--force", f"--prefix={output.as_posix()}/", "-z", "--stdin"],
                 index=checkout_index, data=b"\0".join(p.encode("utf-8", "surrogateescape") for p in installs) + b"\0")
        # Exactly one active source mutation transaction is retained across a
        # killed process. It is not a periodic checkpoint or task backup.
        durable = self._refresh_directory(binding["binding_id"])
        if durable.exists():
            _fail("refresh_operation_exists", "Reconcile or remove the completed exact refresh transaction before starting another.")
        durable.mkdir(parents=True)
        shutil.copytree(output, durable / "checkout")
        originals = durable / "originals"
        originals.mkdir()
        fingerprints = {}
        for path in changed:
            source = self.root / path
            prior = originals / path
            if os.path.lexists(source):
                prior.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, prior)
                self._sync_file(prior)
            installed = durable / "checkout" / path
            if installed.exists():
                self._sync_file(installed)
            fingerprints[path] = {"before": self._fingerprint(prior), "after": self._fingerprint(installed)}
        for name, content in (("index_before", index_before), ("index_after", index_after)):
            (durable / name).write_bytes(content)
            self._sync_file(durable / name)
        retained_tree = _text(self.root, ["mktree"], data=(
            f"040000 tree {before}\tbefore\n040000 tree {after}\tafter\n").encode())
        retained_commit = _text(self.root, ["commit-tree", retained_tree, "-p", head_after],
                                data=b"Retain exact active refresh inputs\n")
        transaction = {"schema": "agent_framework.concurrent_refresh.v1", "binding_id": binding["binding_id"],
                       "before": before, "after": after, "base": binding["base"], "target": target,
                       "head_before": head_before, "head_after": head_after,
                       "symbolic_head": _text(self.root, ["symbolic-ref", "-q", "HEAD"], check=False),
                       "index_before": hashlib.sha256(index_before).hexdigest(),
                       "index_after": hashlib.sha256(index_after).hexdigest(), "paths": fingerprints,
                       "retained_commit": retained_commit}
        _atomic_write(durable / "transaction.json", transaction)
        if (self._head() != head_before or self._index_path().read_bytes() != index_before or
                self._capture(binding["base"], None, binding["owned_paths"]) != before):
            self._remove_refresh_directory(binding["binding_id"])
            _fail("workspace_changed", "Source or staging changed before refresh; no files were installed.")
        binding["refresh_operation"] = transaction
        self._save(binding)
        self._reconcile_refresh(binding, "finish")

    def _refresh_directory(self, binding_id: str) -> Path:
        return self.git_dir / "concurrent-development" / f"refresh-{_identifier(binding_id)}"

    def _remove_refresh_directory(self, binding_id: str) -> None:
        directory = self._refresh_directory(binding_id)
        parent = (self.git_dir / "concurrent-development").resolve()
        if directory.is_symlink() or directory.resolve().parent != parent or parent.parent != self.git_dir.resolve():
            _fail("unsafe_operation_path", "The refresh operation no longer resolves inside its owning Git metadata directory.")
        shutil.rmtree(directory)

    @staticmethod
    def _sync_file(path: Path) -> None:
        with path.open("r+b") as stream:
            os.fsync(stream.fileno())

    @staticmethod
    def _fingerprint(path: Path) -> dict[str, Any] | None:
        if not os.path.lexists(path):
            return None
        status = path.lstat()
        if not stat.S_ISREG(status.st_mode):
            return {"kind": "nonregular", "mode": stat.S_IMODE(status.st_mode)}
        return {"kind": "file", "mode": stat.S_IMODE(status.st_mode),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def reconcile_refresh(self, binding_id: str, outcome: str) -> dict[str, Any]:
        if outcome not in {"finish", "rollback"}:
            _fail("invalid_refresh_outcome", "Choose finish or rollback for the exact interrupted refresh.")
        with self._locked(binding_id, allow_refresh_reconciliation=True) as binding:
            if not binding.get("refresh_operation"):
                directory = self._refresh_directory(binding_id)
                if directory.exists():
                    # Only a completed transaction is disposable. Preparation
                    # interrupted before metadata binding never changed source.
                    transaction_file = directory / "transaction.json"
                    if not transaction_file.exists():
                        _fail("refresh_preparation_incomplete", "An incomplete preparation directory requires explicit inspection.", operation_path=directory.as_posix())
                    transaction = json.loads(transaction_file.read_text())
                    if transaction.get("schema") == "agent_framework.concurrent_refresh.v1" and transaction.get("binding_id") == binding_id:
                        self._release_refresh_ref(binding_id, transaction)
                        self._remove_refresh_directory(binding_id)
                return {"status": "no_pending_refresh", "workspace_removed": False}
            return self._reconcile_refresh(binding, outcome)

    def _reconcile_refresh(self, binding: dict[str, Any], outcome: str) -> dict[str, Any]:
        transaction = binding["refresh_operation"]
        directory = self._refresh_directory(binding["binding_id"])
        manifest = json.loads((directory / "transaction.json").read_text())
        if {k: v for k, v in manifest.items() if k != "completed"} != transaction:
            _fail("refresh_transaction_changed", "The exact in-progress refresh description changed; inspect its owner before proceeding.")
        ref = f"refs/agent-framework/refresh-state/{binding['binding_id']}"
        retained_commit = transaction["retained_commit"]
        observed = _text(self.root, ["rev-parse", "--verify", ref], check=False)
        if observed and observed != retained_commit:
            _fail("refresh_ref_changed", "The live refresh input ref belongs to an unexpected operation.")
        if not observed:
            _git(self.root, ["update-ref", ref, retained_commit, "0" * len(retained_commit)])
        desired = "after" if outcome == "finish" else "before"
        for side in ("before", "after"):
            if hashlib.sha256((directory / f"index_{side}").read_bytes()).hexdigest() != transaction[f"index_{side}"]:
                _fail("refresh_transaction_changed", "The retained index input for this source operation changed.")
        symbolic = _text(self.root, ["symbolic-ref", "-q", "HEAD"], check=False)
        if symbolic != transaction["symbolic_head"] or self._head() not in {transaction["head_before"], transaction["head_after"]}:
            _fail("refresh_head_changed", "Another Git operation changed this workspace's HEAD; reconcile it before resuming refresh.")
        index_path = self._index_path()
        if hashlib.sha256(index_path.read_bytes()).hexdigest() not in {transaction["index_before"], transaction["index_after"]}:
            _fail("refresh_index_changed", "Another writer changed staging; retain that staging while reconciling the refresh.")
        for path, fingerprints in transaction["paths"].items():
            _safe_path(self.root, path)
            current = self._fingerprint(self.root / path)
            if current not in (fingerprints["before"], fingerprints["after"]):
                _fail("refresh_source_changed", "A newer file or mode change must be preserved and reconciled explicitly.", path=path, operation_path=directory.as_posix())
            retained = directory / ("checkout" if desired == "after" else "originals") / path
            if self._fingerprint(retained) != fingerprints[desired]:
                _fail("refresh_transaction_changed", "The retained source input for this operation changed.", path=path)
        lock_path = index_path.with_name(index_path.name + ".lock")
        # A same-volume hard link publishes the complete lock bytes atomically;
        # its identity remains provable even if the process dies immediately.
        if os.path.lexists(lock_path):
            if not os.path.samefile(lock_path, directory / "index_after"):
                _fail("index_busy", "Another Git operation owns the real index lock; its lock will not be removed.")
        else:
            try:
                os.link(directory / "index_after", lock_path)
            except FileExistsError:
                _fail("index_busy", "Another Git operation acquired the real index lock.")
        # A cooperating Git writer could finish between the first observation
        # and our lock acquisition. Never bless that newer HEAD or index merely
        # because it is the value most recently observed.
        expected_head = self._head()
        if (_text(self.root, ["symbolic-ref", "-q", "HEAD"], check=False) != transaction["symbolic_head"] or
                expected_head not in {transaction["head_before"], transaction["head_after"]} or
                hashlib.sha256(index_path.read_bytes()).hexdigest() not in {transaction["index_before"], transaction["index_after"]}):
            _fail("refresh_workspace_changed", "HEAD or staging changed before the refresh acquired its index lock; the newer state is preserved.")
        for path, fingerprints in transaction["paths"].items():
            destination = self.root / path
            current = self._fingerprint(destination)
            if current == fingerprints[desired]:
                continue
            if current not in (fingerprints["before"], fingerprints["after"]):
                _fail("refresh_source_changed", "A concurrent writer changed source after the refresh check.", path=path)
            source = directory / ("checkout" if desired == "after" else "originals") / path
            if fingerprints[desired] is None:
                destination.unlink(missing_ok=True)
            else:
                staging = directory / "install-file"
                shutil.copy2(source, staging)
                self._sync_file(staging)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, destination)
        desired_head = transaction[f"head_{desired}"]
        if _text(self.root, ["symbolic-ref", "-q", "HEAD"], check=False) != transaction["symbolic_head"]:
            _fail("refresh_head_changed", "The workspace changed its attached branch during refresh; preserve it for reconciliation.")
        _git(self.root, ["update-ref", "-m", "concurrent development refresh", "HEAD", desired_head, expected_head])
        index_install = directory / "install-index"
        shutil.copy2(directory / f"index_{desired}", index_install)
        self._sync_file(index_install)
        os.replace(index_install, index_path)
        lock_path.unlink()
        binding["base"] = transaction["target"] if outcome == "finish" else transaction["base"]
        if binding.get("candidate") and binding["candidate"].get("published") and outcome == "finish":
            binding["candidate"]["incorporated"] = True
        _atomic_write(directory / "transaction.json", {**transaction, "completed": outcome})
        binding.pop("refresh_operation")
        self._save(binding)
        self._release_refresh_ref(binding["binding_id"], transaction)
        self._remove_refresh_directory(binding["binding_id"])
        return {"status": "refresh_finished" if outcome == "finish" else "refresh_rolled_back",
                "base": binding["base"], "workspace_head": desired_head, "workspace_removed": False}

    def _release_refresh_ref(self, binding_id: str, transaction: Mapping[str, Any]) -> None:
        ref = f"refs/agent-framework/refresh-state/{binding_id}"
        if _text(self.root, ["rev-parse", "--verify", ref], check=False):
            _git(self.root, ["update-ref", "-d", ref, transaction["retained_commit"]])

    def close(self, binding_id: str, candidate_id: str) -> dict[str, Any]:
        with self._locked(binding_id) as binding:
            candidate = self._candidate(binding, candidate_id, require_current=False)
            self._require_reconciled(candidate)
            if candidate["consumers"]:
                _fail("candidate_in_use", "Actual candidate consumers must finish before closeout.")
            if candidate.get("published") and not candidate.get("incorporated"):
                _fail("refresh_required", "Incorporate the published result before releasing its operation state.")
            if not candidate.get("published") and not candidate.get("retracted"):
                _fail("candidate_unfinished", "Publish the candidate or explicitly retract its ready selection before closeout.")
            descriptor = candidate["descriptor"]
            retained_remote = candidate.get("exposed") or candidate.get("prior_exposed")
            if retained_remote:
                observed = self._remote_oid(descriptor["remote_ref"])
                if observed and observed != retained_remote:
                    _fail("candidate_ref_changed", "Closeout cannot delete a ref now owned by another source generation.")
                if observed:
                    _git(self.root, ["push", "--porcelain", "--no-follow-tags", "--recurse-submodules=no",
                         f"--force-with-lease={descriptor['remote_ref']}:{observed}", self.remote,
                         f":{descriptor['remote_ref']}"])
                    if self._remote_oid(descriptor["remote_ref"]) is not None:
                        _fail("candidate_close_uncertain", "Candidate ref removal is not yet established.")
            local_ref = f"refs/agent-framework/candidates/{binding_id}"
            observed_local = _text(self.root, ["rev-parse", "--verify", local_ref], check=False)
            if observed_local:
                _git(self.root, ["update-ref", "-d", local_ref, descriptor["commit"]])
            state_ref = f"refs/agent-framework/integration-state/{binding_id}"
            if _text(self.root, ["rev-parse", "--verify", state_ref], check=False):
                _git(self.root, ["update-ref", "-d", state_ref, candidate["operation_tree"]])
            binding["candidate"] = None
            self._save(binding)
            return {"status": "closed", "workspace_removed": False, "binding_id": binding_id}


def validate_candidate_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    keys = {"schema", "repository", "binding_id", "owner_id", "generation", "candidate_id",
            "commit", "tree", "parent", "selected_tree", "remote", "shared_ref", "remote_ref"}
    if not isinstance(value, Mapping) or set(value) != keys or value["schema"] != CANDIDATE_SCHEMA:
        _fail("candidate_descriptor_invalid", "The candidate descriptor must have its exact versioned shape.")
    result = dict(value)
    _identifier(result["binding_id"])
    if (not isinstance(result["owner_id"], str) or not result["owner_id"] or len(result["owner_id"]) > 512 or
            any(ord(c) < 32 for c in result["owner_id"]) or type(result["generation"]) is not int or result["generation"] < 1 or
            not isinstance(result["remote"], str) or not _ID.fullmatch(result["remote"]) or
            not isinstance(result["shared_ref"], str) or not result["shared_ref"].startswith("refs/heads/") or result["remote_ref"] != f"refs/heads/agent-framework/candidates/{result['binding_id']}"):
        _fail("candidate_descriptor_invalid", "The candidate owner, generation or closed source route is invalid.")
    for key in ("commit", "tree", "parent", "selected_tree"):
        if not isinstance(result[key], str) or not _OID.fullmatch(result[key]):
            _fail("candidate_descriptor_invalid", "Candidate source fields must be exact Git identities.")
    if not isinstance(result["repository"], str) or not re.fullmatch(r"[0-9a-f]{64}", result["repository"]):
        _fail("candidate_descriptor_invalid", "Candidate repository identity is invalid.")
    expected = _digest({key: item for key, item in result.items() if key != "candidate_id"})
    if result["candidate_id"] != expected:
        _fail("candidate_descriptor_invalid", "Candidate descriptor identity does not match its content.")
    return result


def validate_current_candidate(repo_root: Path, binding_id: str, candidate_id: str,
                               *, state_root: Path | None = None, remote: str = DEFAULT_REMOTE,
                               shared_branch: str = DEFAULT_SHARED_BRANCH) -> dict[str, Any]:
    service = ConcurrentDevelopment(repo_root, state_root, remote=remote, shared_branch=shared_branch)
    with service._locked(binding_id) as binding:
        candidate = service._candidate(binding, candidate_id)
        service._selection_current(binding, candidate)
        return dict(candidate["descriptor"])


def retain_candidate(repo_root: Path, binding_id: str, candidate_id: str, *, job_id: str,
                     state_root: Path | None = None, remote: str = DEFAULT_REMOTE,
                     shared_branch: str = DEFAULT_SHARED_BRANCH) -> dict[str, Any]:
    return ConcurrentDevelopment(repo_root, state_root, remote=remote, shared_branch=shared_branch).retain(binding_id, candidate_id, job_id)


def release_candidate(repo_root: Path, binding_id: str, candidate_id: str, *, job_id: str,
                      state_root: Path | None = None, remote: str = DEFAULT_REMOTE,
                     shared_branch: str = DEFAULT_SHARED_BRANCH) -> dict[str, Any]:
    return ConcurrentDevelopment(repo_root, state_root, remote=remote, shared_branch=shared_branch).release(binding_id, candidate_id, job_id)
