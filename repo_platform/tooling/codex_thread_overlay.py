"""Shared local Codex thread-overlay helpers.

Thread overlays are local-only Markdown notes keyed by Codex thread id.  They
are intentionally outside repo truth; tools use this module for consistent
thread selection, metadata, pinning, and output behavior.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


THREAD_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class ThreadOverlayError(RuntimeError):
    """Raised when a local Codex thread overlay cannot complete safely."""


@dataclass(frozen=True)
class ThreadTarget:
    thread_id: str
    thread_name: str
    matched_by: str
    session_index_entry: dict[str, Any] | None


@dataclass(frozen=True)
class ThreadOverlayPaths:
    codex_root: Path
    backup_root: Path
    overlay_root: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_codex_root() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser().resolve()


def default_backup_root() -> Path:
    explicit = os.environ.get("AGENT_FRAMEWORK_STATE_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt":
        return (Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "AgentFramework").resolve()
    return (Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "agent-framework").expanduser().resolve()


def resolve_paths(args: argparse.Namespace, overlay_root_name: str) -> ThreadOverlayPaths:
    codex_root = Path(args.codex_root).expanduser().resolve() if args.codex_root else default_codex_root()
    backup_root = Path(args.backup_root).expanduser().resolve() if args.backup_root else default_backup_root()
    return ThreadOverlayPaths(
        codex_root=codex_root,
        backup_root=backup_root,
        overlay_root=backup_root / overlay_root_name,
    )


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    deadline = time.monotonic() + 2.0
    while True:
        try:
            text = path.read_text(encoding="utf-8")
            break
        except PermissionError:
            # A Windows replacement may briefly make a pathname unavailable to
            # a new reader. Retry the open, never malformed JSON or wrong shape.
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ThreadOverlayError(f"failed to parse JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ThreadOverlayError(f"expected JSON object at {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish complete bytes on the same filesystem. Readers see either complete
    # version, and a failed serialization/replacement preserves the old file.
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + 2.0
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows readers/AV can briefly hold a handle without delete
                # sharing. Keep the old complete version while that handle closes.
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _registry_lock(paths: ThreadOverlayPaths) -> Iterator[None]:
    """Serialize shared registry updates; the OS releases ownership on exit."""

    paths.overlay_root.mkdir(parents=True, exist_ok=True)
    lock_path = paths.overlay_root / ".registry.lock"
    with lock_path.open("a+b") as handle:
        # Windows permits a byte-range lock beyond EOF. Do not write a sentinel
        # before acquisition: concurrent first-open writers would race there.
        deadline = time.monotonic() + 30.0
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise ThreadOverlayError(f"timed out waiting for overlay registry lock at {lock_path}") from exc
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_session_index(codex_root: Path) -> list[dict[str, Any]]:
    index_path = codex_root / "session_index.jsonl"
    if not index_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with index_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            thread_id = str(payload.get("id", "")).strip().lower()
            if not THREAD_ID_RE.match(thread_id):
                continue
            entries.append(
                {
                    "thread_id": thread_id,
                    "thread_name": str(payload.get("thread_name", "")).strip(),
                    "updated_at": payload.get("updated_at"),
                    "index_path": index_path.as_posix(),
                }
            )
    return entries


def parse_dt(raw_value: Any) -> datetime:
    raw = str(raw_value or "").strip()
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def env_thread_id() -> str:
    for key in ("CODEX_THREAD_ID", "CODEX_THREAD_UUID", "OPENAI_CODEX_THREAD_ID"):
        value = os.environ.get(key, "").strip().lower()
        if THREAD_ID_RE.match(value):
            return value
    return ""


def resolve_thread_target(
    args: argparse.Namespace,
    paths: ThreadOverlayPaths,
    *,
    required: bool,
) -> ThreadTarget | None:
    raw_thread_id = str(getattr(args, "thread_id", "") or "").strip().lower()
    title_query = str(getattr(args, "thread_title_contains", "") or "").strip()
    latest_thread = bool(getattr(args, "latest_thread", False))

    if raw_thread_id:
        if not THREAD_ID_RE.match(raw_thread_id):
            raise ThreadOverlayError(f"invalid thread id: {raw_thread_id!r}")
        # Exact identities do not depend on a private application's index format.
        return ThreadTarget(raw_thread_id, "", "thread_id", None)

    env_id = env_thread_id()
    if env_id and not title_query and not latest_thread:
        return ThreadTarget(env_id, "", "environment", None)

    session_index = load_session_index(paths.codex_root) if title_query or latest_thread else []
    if title_query:
        matches = [
            entry for entry in session_index if title_query.lower() in str(entry.get("thread_name", "")).lower()
        ]
        if not matches:
            raise ThreadOverlayError(f"no Codex thread matched title fragment {title_query!r}")
        if len(matches) > 1:
            names = ", ".join(f"{entry['thread_name']} ({entry['thread_id']})" for entry in matches[:5])
            raise ThreadOverlayError(
                f"title fragment {title_query!r} matched multiple threads; use --thread-id. Matches: {names}"
            )
        match = matches[0]
        return ThreadTarget(
            thread_id=match["thread_id"],
            thread_name=str(match.get("thread_name", "")),
            matched_by="thread_title_contains",
            session_index_entry=match,
        )

    if latest_thread:
        if not session_index:
            raise ThreadOverlayError(f"no session index entries found under {paths.codex_root}")
        match = max(session_index, key=lambda entry: parse_dt(entry.get("updated_at")))
        env_id = env_thread_id()
        if env_id and env_id != match["thread_id"]:
            raise ThreadOverlayError(
                "--latest-thread would select "
                f"{match['thread_id']} ({match.get('thread_name') or 'unnamed'}), "
                f"but the current Codex thread environment is {env_id}; "
                "use no selector for the current thread or pass --thread-id explicitly"
            )
        return ThreadTarget(
            thread_id=match["thread_id"],
            thread_name=str(match.get("thread_name", "")),
            matched_by="latest_thread",
            session_index_entry=match,
        )

    env_id = env_thread_id()
    if env_id:
        match = next((entry for entry in session_index if entry["thread_id"] == env_id), None)
        return ThreadTarget(
            thread_id=env_id,
            thread_name=str(match.get("thread_name", "")) if match else "",
            matched_by="environment",
            session_index_entry=match,
        )

    if required:
        raise ThreadOverlayError(
            "select a thread with CODEX_THREAD_ID, --thread-id, --thread-title-contains, or --latest-thread"
        )
    return None


def thread_dir(paths: ThreadOverlayPaths, thread_id: str) -> Path:
    return paths.overlay_root / thread_id


def body_path(paths: ThreadOverlayPaths, thread_id: str, filename: str) -> Path:
    return thread_dir(paths, thread_id) / filename


def metadata_path(paths: ThreadOverlayPaths, thread_id: str) -> Path:
    return thread_dir(paths, thread_id) / "metadata.json"


def index_path(paths: ThreadOverlayPaths) -> Path:
    return paths.overlay_root / "index.json"


def pinned_path(paths: ThreadOverlayPaths) -> Path:
    return paths.overlay_root / "pinned.json"


def load_metadata(paths: ThreadOverlayPaths, thread_id: str) -> dict[str, Any]:
    return read_json(metadata_path(paths, thread_id))


def refresh_index(
    paths: ThreadOverlayPaths,
    thread_id: str,
    metadata: dict[str, Any],
    *,
    path_field: str,
) -> None:
    with _registry_lock(paths):
        _refresh_index_locked(paths, thread_id, metadata, path_field=path_field)


def _refresh_index_locked(
    paths: ThreadOverlayPaths,
    thread_id: str,
    metadata: dict[str, Any],
    *,
    path_field: str,
) -> None:
    path = index_path(paths)
    payload = read_json(path)
    threads = payload.get("threads")
    if not isinstance(threads, dict):
        threads = {}
    threads[thread_id] = {
        "thread_id": thread_id,
        "thread_name": metadata.get("thread_name", ""),
        "pinned": bool(metadata.get("pinned")),
        "updated_at": metadata.get("updated_at"),
        path_field: metadata.get(path_field),
    }
    write_json(
        path,
        {
            "version": 1,
            "updated_at": utc_now(),
            "threads": threads,
        },
    )


def write_metadata(
    paths: ThreadOverlayPaths,
    thread_id: str,
    payload: dict[str, Any],
    *,
    path_field: str,
) -> None:
    with _registry_lock(paths):
        write_json(metadata_path(paths, thread_id), payload)
        _refresh_index_locked(paths, thread_id, payload, path_field=path_field)


def set_pinned(
    paths: ThreadOverlayPaths,
    target: ThreadTarget,
    *,
    pinned: bool,
    artifact_path: Path,
    path_field: str,
    missing_noun: str,
) -> dict[str, Any]:
    with _registry_lock(paths):
        return _set_pinned_locked(
            paths, target, pinned=pinned, artifact_path=artifact_path,
            path_field=path_field, missing_noun=missing_noun,
        )


def _set_pinned_locked(
    paths: ThreadOverlayPaths,
    target: ThreadTarget,
    *,
    pinned: bool,
    artifact_path: Path,
    path_field: str,
    missing_noun: str,
) -> dict[str, Any]:
    metadata = load_metadata(paths, target.thread_id)
    if not metadata and pinned:
        raise ThreadOverlayError(f"cannot pin missing {missing_noun} for thread {target.thread_id}")
    now = utc_now()
    metadata.update(
        {
            "thread_id": target.thread_id,
            "thread_name": target.thread_name or metadata.get("thread_name", ""),
            "pinned": pinned,
            "updated_at": now,
            path_field: artifact_path.as_posix(),
        }
    )
    if pinned:
        metadata["pinned_at"] = now
    else:
        metadata["unpinned_at"] = now
    write_json(metadata_path(paths, target.thread_id), metadata)
    _refresh_index_locked(paths, target.thread_id, metadata, path_field=path_field)

    registry = read_json(pinned_path(paths))
    threads = registry.get("threads")
    if not isinstance(threads, dict):
        threads = {}
    if pinned:
        threads[target.thread_id] = {
            "thread_id": target.thread_id,
            "thread_name": metadata.get("thread_name", ""),
            "pinned_at": metadata.get("pinned_at"),
            path_field: metadata.get(path_field),
        }
    else:
        threads.pop(target.thread_id, None)
    write_json(
        pinned_path(paths),
        {
            "version": 1,
            "updated_at": now,
            "pinned_thread_ids": sorted(threads),
            "threads": threads,
        },
    )
    return metadata


def overlay_status(
    paths: ThreadOverlayPaths,
    target: ThreadTarget,
    *,
    artifact_path: Path,
    path_field: str,
) -> dict[str, Any]:
    metadata = load_metadata(paths, target.thread_id)
    return {
        "thread_id": target.thread_id,
        "thread_name": target.thread_name or metadata.get("thread_name", ""),
        "matched_by": target.matched_by,
        "exists": artifact_path.exists(),
        "pinned": bool(metadata.get("pinned")) if metadata else False,
        path_field: artifact_path.as_posix(),
        "metadata": metadata,
    }


def pinned_registry_status(paths: ThreadOverlayPaths, *, root_field: str) -> dict[str, Any]:
    registry = read_json(pinned_path(paths))
    threads = registry.get("threads")
    if not isinstance(threads, dict):
        threads = {}
    return {
        root_field: paths.overlay_root.as_posix(),
        "pinned_thread_ids": sorted(threads),
        "threads": threads,
    }


def read_body_arg(args: argparse.Namespace) -> str | None:
    if getattr(args, "stdin", False):
        return sys.stdin.read()
    body_file = getattr(args, "body_file", None)
    if body_file:
        return Path(body_file).read_text(encoding="utf-8")
    text = getattr(args, "text", None)
    if text is not None:
        return str(text)
    return None


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def bounded_check_excerpt(
    body: str,
    *,
    active: bool,
    max_chars: int,
    offset: int | None,
    tail: bool,
    entrypoint_argv: Sequence[str],
    thread_id: str,
    codex_root: Path,
    backup_root: Path,
    noun: str,
    as_json: bool,
) -> tuple[str, dict[str, Any]]:
    if not active:
        return "", {
            "mode": "inactive",
            "total_chars": None,
            "total_lines": None,
            "range": None,
            "truncated": False,
            "truncated_before": False,
            "truncated_after": False,
            "next_offset": None,
            "commands": {"next": None, "tail": None, "full": None},
        }

    total_chars = len(body)
    if offset is not None and offset > total_chars:
        raise ThreadOverlayError(
            f"--offset {offset} exceeds the active {noun} length of {total_chars} characters"
        )
    if tail:
        start_char = max(0, total_chars - max_chars)
        mode = "tail"
    elif offset is not None:
        start_char = offset
        mode = "offset"
    else:
        start_char = 0
        mode = "head"
    end_char = min(total_chars, start_char + max_chars)
    excerpt = body[start_char:end_char]
    truncated_before = start_char > 0
    truncated_after = end_char < total_chars
    output_args = ["--json"] if as_json else []
    root_args = [
        "--codex-root",
        str(codex_root),
        "--backup-root",
        str(backup_root),
    ]
    next_command = None
    if truncated_after:
        next_command = [
            *entrypoint_argv,
            "check",
            "--thread-id",
            thread_id,
            "--max-chars",
            str(max_chars),
            "--offset",
            str(end_char),
            *root_args,
            *output_args,
        ]
    tail_command = None
    if (truncated_before or truncated_after) and not tail:
        tail_command = [
            *entrypoint_argv,
            "check",
            "--thread-id",
            thread_id,
            "--max-chars",
            str(max_chars),
            "--tail",
            *root_args,
            *output_args,
        ]
    return excerpt, {
        "mode": mode,
        "total_chars": total_chars,
        "total_lines": len(body.splitlines()),
        "range": {
            "start_char": start_char,
            "end_char_exclusive": end_char,
            "returned_chars": len(excerpt),
        },
        "truncated": truncated_before or truncated_after,
        "truncated_before": truncated_before,
        "truncated_after": truncated_after,
        "next_offset": end_char if truncated_after else None,
        "commands": {
            "next": next_command,
            "tail": tail_command,
            "full": [
                *entrypoint_argv,
                "read",
                "--thread-id",
                thread_id,
                *root_args,
                *output_args,
            ],
        },
    }


def emit_kv(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, sort_keys=True)}")
        else:
            print(f"{key}: {value}")


def add_common(parser: argparse.ArgumentParser, *, defaults: bool) -> None:
    default = None if defaults else argparse.SUPPRESS
    parser.add_argument(
        "--codex-root",
        default=default,
        help="Override Codex home; defaults to CODEX_HOME, then ~/.codex.",
    )
    parser.add_argument("--backup-root", default=default, help="Override continuity storage; defaults to AGENT_FRAMEWORK_STATE_HOME, then the platform user-state directory.")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text" if defaults else argparse.SUPPRESS,
        help="Output format.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Alias for --format json.",
    )


def add_selector(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--thread-id", help="Exact Codex thread UUID.")
    group.add_argument("--thread-title-contains", help="Case-insensitive thread title fragment from session_index.jsonl.")
    group.add_argument("--latest-thread", action="store_true", help="Use the newest session_index.jsonl entry.")
