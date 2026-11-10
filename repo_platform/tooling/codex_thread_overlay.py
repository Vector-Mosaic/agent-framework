"""Shared local Codex thread-overlay helpers.

Thread overlays are local-only Markdown notes keyed by Codex thread id.  They
are intentionally outside repo truth; tools use this module for consistent
thread selection, metadata, pinning, and output behavior.
"""

from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence


THREAD_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class ThreadOverlayError(RuntimeError):
    """Raised when a local Codex thread overlay cannot complete safely."""

    def __init__(self, message: str, *, code: str = "overlay_error", **details: Any):
        super().__init__(message)
        self.code, self.details = code, details


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

    with _file_lock(paths.overlay_root / ".registry.lock"):
        yield


@contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
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
                    raise ThreadOverlayError(f"timed out waiting for overlay coordination lock at {lock_path}") from exc
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


def _publish_listings(paths: ThreadOverlayPaths, target: ThreadTarget, metadata: dict[str, Any], *, path_field: str) -> None:
    """Called under document then registry lock; metadata is authoritative."""
    _refresh_index_locked(paths, target.thread_id, metadata, path_field=path_field)
    registry = read_json(pinned_path(paths))
    threads = registry.get("threads")
    if not isinstance(threads, dict):
        threads = {}
    if metadata["pinned"]:
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
            "updated_at": utc_now(),
            "pinned_thread_ids": sorted(threads),
            "threads": threads,
        },
    )


def _revision(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _token(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")


def _untoken(value: str) -> dict[str, Any]:
    try:
        if not isinstance(value, str) or len(value) > 16384:
            raise ValueError()
        result = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if not isinstance(result, dict) or result.get("version") != 1:
            raise ValueError()
        return result
    except (ValueError, UnicodeError) as exc:
        raise ThreadOverlayError("Invalid overlay token; reacquire with read or outline.", code="invalid_token") from exc


def _binding(paths: ThreadOverlayPaths, target: ThreadTarget, artifact_path: Path) -> dict[str, str]:
    return {"store": paths.overlay_root.resolve().as_posix(), "codex_root": paths.codex_root.resolve().as_posix(),
            "thread_id": target.thread_id, "document": artifact_path.name}


def _capture_locked(paths: ThreadOverlayPaths, target: ThreadTarget, artifact_path: Path, path_field: str) -> dict[str, Any]:
    try:
        raw = artifact_path.read_bytes()
    except FileNotFoundError:
        raw = None
    meta_path = metadata_path(paths, target.thread_id)
    metadata = load_metadata(paths, target.thread_id)
    meta_exists = meta_path.exists()
    valid = bool(metadata and metadata.get("thread_id") == target.thread_id
                 and isinstance(metadata.get("pinned"), bool)
                 and (metadata.get("body_revision") is None or isinstance(metadata["body_revision"], str))
                 and (metadata.get("pin_revision") is None or isinstance(metadata["pin_revision"], str))
                 and metadata.get(path_field) == artifact_path.as_posix())
    revision = _revision(raw) if raw is not None else None
    if raw is None and not meta_exists:
        consistency = "absent"
    elif raw is None or not valid:
        consistency = "inconsistent"
    elif metadata.get("body_revision") not in {None, revision}:
        consistency = "revision_mismatch"
    else:
        consistency = "consistent"
    pin_revision = metadata.get("pin_revision") or _revision(json.dumps(
        {k: metadata.get(k) for k in ("pinned", "pinned_at", "unpinned_at")}, sort_keys=True).encode())
    basis = {"version": 1, "kind": "mutation", **_binding(paths, target, artifact_path),
             "body_revision": revision, "pin_revision": pin_revision}
    return {"raw": raw, "metadata": metadata, "consistency": consistency,
            "body_revision": revision, "pin_revision": pin_revision, "mutation_token": _token(basis)}


def capture_overlay(paths: ThreadOverlayPaths, target: ThreadTarget, *, artifact_path: Path, path_field: str) -> dict[str, Any]:
    try:
        with _file_lock(artifact_path.parent / ".document.lock"):
            return _capture_locked(paths, target, artifact_path, path_field)
    except OSError as exc:
        raise ThreadOverlayError("The selected overlay store could not be read or coordinated; recover its actual access.", code="overlay_unavailable") from exc


def _decode_body(raw: bytes) -> str:
    try:
        return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise ThreadOverlayError("The selected overlay is not valid UTF-8; preserve its bytes and reconcile encoding.", code="invalid_body_encoding") from exc


def _atomic_body(path: Path, raw: bytes) -> None:
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + 2.0
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        temporary.unlink(missing_ok=True)


def mutate_overlay(paths: ThreadOverlayPaths, target: ThreadTarget, *, artifact_path: Path, path_field: str,
                   action: str, change: Callable[[str, dict[str, Any]], str] | None = None,
                   pin: bool | None = None, expected: str | None = None, expect_absent: bool = False,
                   repair: bool = False, allow_create: bool = False) -> dict[str, Any]:
    """One conditional mutation path for body, pin, item and repair operations.

    Body, metadata and derived listings are separate atomic publications. A
    failure reports the reached boundary and never replays the intended change.
    """
    if bool(expected) == bool(expect_absent):
        raise ThreadOverlayError("Supply --expected from the observed read, or --expect-absent for creation; no unconditional mutation is supported.",
                                 code="precondition_required", effect="none")
    with ExitStack() as coordination:
        try:
            coordination.enter_context(_file_lock(artifact_path.parent / ".document.lock"))
            current = _capture_locked(paths, target, artifact_path, path_field)
        except OSError as exc:
            raise ThreadOverlayError("The selected overlay could not be coordinated or read; recover its actual access before editing.",
                                     code="overlay_unavailable", effect="none") from exc
        if expect_absent:
            if current["consistency"] != "absent" or change is None or repair or not allow_create:
                raise ThreadOverlayError("Creation expected both body and metadata absent; read and reconcile the existing state.",
                                         code="creation_conflict", effect="none")
        else:
            basis = _untoken(expected)
            if basis.get("kind") != "mutation" or any(basis.get(k) != v for k, v in _binding(paths, target, artifact_path).items()):
                raise ThreadOverlayError("Mutation token belongs to another document/store/task; read this exact target.", code="token_binding_mismatch", effect="none")
            if current["consistency"] not in ({"consistent", "revision_mismatch"} if repair else {"consistent"}):
                raise ThreadOverlayError("Body and metadata need reconciliation; read their consistency and use repair only with known pin state.", code="inconsistent_overlay", effect="none")
            if ((change is not None or repair) and basis.get("body_revision") != current["body_revision"]
                    or pin is not None and basis.get("pin_revision") != current["pin_revision"]):
                raise ThreadOverlayError("Observed mutation basis changed; reread and reconcile the intended edit. No requested change was applied.",
                                         code="revision_conflict", effect="none", mutation_token=current["mutation_token"])
        metadata = dict(current["metadata"])
        old_raw = current["raw"]
        body = _decode_body(old_raw) if old_raw is not None else ""
        new_raw = change(body, metadata).encode("utf-8") if change is not None else old_raw
        now = utc_now()
        metadata.update({"version": 1, "thread_id": target.thread_id,
                         "thread_name": target.thread_name or metadata.get("thread_name", ""),
                         "created_at": metadata.get("created_at") or now, "updated_at": now,
                         "pinned": metadata.get("pinned", False) if pin is None else pin,
                         "last_action": action, "matched_by": target.matched_by,
                         path_field: artifact_path.as_posix(), "body_revision": _revision(new_raw)})
        if pin is not None:
            metadata["pin_revision"] = uuid.uuid4().hex
            metadata["pinned_at" if pin else "unpinned_at"] = now
        effects = {"body": "unchanged", "metadata": "unchanged", "listings": "unchanged"}
        try:
            if new_raw != old_raw:
                effects["body"] = "pending_readback"
                _atomic_body(artifact_path, new_raw)
                effects["body"] = "committed"
            effects["metadata"] = "pending_readback"
            write_json(metadata_path(paths, target.thread_id), metadata)
            effects["metadata"] = "committed"
            with _registry_lock(paths):
                effects["listings"] = "possibly_partial"
                _publish_listings(paths, target, metadata, path_field=path_field)
                effects["listings"] = "committed"
            result = _capture_locked(paths, target, artifact_path, path_field)
        except (OSError, ThreadOverlayError) as exc:
            raise ThreadOverlayError("Overlay publication did not finish. Read the authoritative body/metadata before retrying; repair listings/revision without replaying the edit.",
                                     code="publication_partial", effects=effects, repair="read then repair with the observed token; missing/invalid metadata needs explicit reconciliation") from exc
        return {"metadata": metadata, "mutation": {"status": "applied", "effects": effects,
                "body_revision": result["body_revision"], "mutation_token": result["mutation_token"]}}


def mutation_options(args: argparse.Namespace) -> dict[str, Any]:
    return {"expected": getattr(args, "expected", None), "expect_absent": bool(getattr(args, "expect_absent", False))}


def add_mutation_options(parser: argparse.ArgumentParser) -> None:
    precondition = parser.add_mutually_exclusive_group()
    precondition.add_argument("--expected", help="Opaque mutation_token from an actual read/check/status of this exact document; stale relevant state conflicts.")
    precondition.add_argument("--expect-absent", action="store_true", help="Creation only: require both body and metadata absent. Never use to replace or repair existing state.")


def overlay_status(
    paths: ThreadOverlayPaths,
    target: ThreadTarget,
    *,
    artifact_path: Path,
    path_field: str,
    captured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    captured = captured if captured is not None else capture_overlay(paths, target, artifact_path=artifact_path, path_field=path_field)
    metadata = captured["metadata"]
    return {
        "thread_id": target.thread_id,
        "thread_name": target.thread_name or metadata.get("thread_name", ""),
        "matched_by": target.matched_by,
        "exists": captured["raw"] is not None,
        "pinned": bool(metadata.get("pinned")) if metadata else False,
        path_field: artifact_path.as_posix(),
        "metadata": metadata,
        "source": {**_binding(paths, target, artifact_path), "path": artifact_path.as_posix(),
                   "revision": captured["body_revision"], "bytes": len(captured["raw"]) if captured["raw"] is not None else None},
        "consistency": captured["consistency"],
        "mutation_token": captured["mutation_token"],
    }


PARSER_REVISION = "overlay-atx-v2"
PARSER_SUPPORT = "Top-level ATX # headings with up to three leading spaces; fenced/indented code and block quotes excluded. HTML comments, declarations, processing instructions, CDATA, pre/script/style/textarea and blocks opened by standard block tags or standalone complete tags are literal; standalone tags conservatively start a block even after paragraph text. Setext headings remain ordinary content."
_HTML_COMPLETE_TAG = re.compile(r'''^ {0,3}(?:</[A-Za-z][A-Za-z0-9-]*\s*>|<[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:[^\s"'=<>`]+|'[^']*'|"[^"]*"))?)*\s*/?>)\s*$''')


def section_outline(body: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    stack: list[int] = []
    fence: tuple[str, int] | None = None
    literal_end: str | None = None
    html_block = False
    offset = 0
    for line in body.splitlines(keepends=True):
        start = offset
        offset += len(line)
        if literal_end:
            if literal_end in line.lower():
                literal_end = None
            continue
        if html_block:
            if not line.strip():
                html_block = False
            continue
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            continue
        if marker and (marker[1][0] != "`" or "`" not in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            continue
        literal = re.match(r"^ {0,3}(<!--|<\?|<!\[CDATA\[|<![A-Z]|<(pre|script|style|textarea)(?:\s|>))", line, re.I)
        if literal:
            opening = literal[1].lower()
            end = "-->" if opening == "<!--" else "?>" if opening == "<?" else "]]>" if opening == "<![cdata[" else ">" if opening.startswith("<!") else f"</{literal[2].lower()}>"
            if end not in line.lower():
                literal_end = end
            continue
        if re.match(r"^ {0,3}</?(?:address|article|aside|base|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:\s|/?>)", line, re.I):
            html_block = True
            continue
        if _HTML_COMPLETE_TAG.match(line):
            html_block = True
            continue
        heading = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)[\r\n]*$", line)
        if not heading:
            continue
        level = len(heading[1])
        while stack and sections[stack[-1]]["level"] >= level:
            sections[stack.pop()]["end"] = start
        if stack and sections[stack[-1]]["intro_end"] == len(body):
            sections[stack[-1]]["intro_end"] = start
        title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2] or "").strip()
        sections.append({"title": title, "level": level, "start": start, "end": len(body),
                         "intro_end": len(body), "parent": stack[-1] if stack else None})
        stack.append(len(sections) - 1)
    # Leaf introductory text ends at its section boundary too.
    for section in sections:
        section["intro_end"] = min(section["intro_end"], section["end"])
    return sections


def overlay_view(captured: dict[str, Any], transform: Callable[[str], str], view: str) -> tuple[str, dict[str, str]]:
    raw = captured["raw"]
    if raw is None:
        raise ThreadOverlayError("No body exists for this exact document.", code="missing_overlay")
    body = transform(_decode_body(raw))
    return body, {"name": view, "revision": _revision(body.encode("utf-8"))}


def navigate_overlay(paths: ThreadOverlayPaths, target: ThreadTarget, *, artifact_path: Path, path_field: str,
                     transform: Callable[[str], str], view: str, args: argparse.Namespace) -> dict[str, Any]:
    captured = capture_overlay(paths, target, artifact_path=artifact_path, path_field=path_field)
    status = overlay_status(paths, target, artifact_path=artifact_path, path_field=path_field, captured=captured)
    if getattr(args, "pinned_only", False) and not status["pinned"]:
        return {"active": False, **status}
    body, view_identity = overlay_view(captured, transform, view)
    binding = {"version": 1, **_binding(paths, target, artifact_path), "source_revision": captured["body_revision"],
               "view": view_identity, "parser_revision": PARSER_REVISION}
    sections = section_outline(body)
    preamble_end = sections[0]["start"] if sections else len(body)
    def reference(index: int | None) -> str:
        return _token({**binding, "kind": "section", "index": index})
    if args.command == "outline":
        return {"active": True, **status, "view": view_identity, "parser_revision": PARSER_REVISION, "parser_support": PARSER_SUPPORT,
                "preamble": {"section_ref": reference(None), "start": 0, "end": preamble_end},
                "sections": [{**section, "section_ref": reference(index),
                              "parent_ref": reference(section["parent"]) if section["parent"] is not None else None}
                             for index, section in enumerate(sections)]}
    expected_source = getattr(args, "expected_source", None)
    if expected_source is not None and expected_source != captured["body_revision"]:
        raise ThreadOverlayError("Stored bytes changed; reacquire this document with read or outline.", code="stale_selection")
    selector = getattr(args, "section_ref", None) or getattr(args, "cursor", None)
    selected, start, end, position = None, 0, len(body), 0
    context_mode = getattr(args, "context", "include")
    if selector:
        selection = _untoken(selector)
        if any(selection.get(k) != v for k, v in binding.items()):
            raise ThreadOverlayError("Selection source, view or exact document changed; reacquire with read or outline.", code="stale_selection")
        expected_kind = "cursor" if getattr(args, "cursor", None) else "section"
        if selection.get("kind") != expected_kind:
            raise ThreadOverlayError("Wrong selection token kind; use outline section_ref or returned next_cursor.", code="invalid_token")
        selected = selection.get("index")
        if selected is not None and (type(selected) is not int or not 0 <= selected < len(sections)):
            raise ThreadOverlayError("Invalid section occurrence.", code="invalid_token")
        if selection.get("selection") == "document":
            start, end = 0, len(body)
        elif selected is None:
            start, end = 0, preamble_end
        else:
            start, end = sections[selected]["start"], sections[selected]["end"]
        position = selection.get("next", start) if expected_kind == "cursor" else start
        if expected_kind == "cursor":
            context_mode = selection.get("context")
            if context_mode not in {"include", "references"}:
                raise ThreadOverlayError("Invalid cursor context mode.", code="invalid_token")
    if type(position) is not int or not start <= position <= end:
        raise ThreadOverlayError("Invalid continuation offset.", code="invalid_token")
    limit = getattr(args, "max_chars", None)
    delivered_end = min(end, position + limit) if limit is not None else end
    contexts = []
    if selected is not None:
        ancestors = []
        ancestor = sections[selected]["parent"]
        while ancestor is not None:
            ancestors.append(ancestor)
            ancestor = sections[ancestor]["parent"]
        for index in [None, *reversed(ancestors)]:
            context_start, context_end = (0, preamble_end) if index is None else (sections[index]["start"], sections[index]["intro_end"])
            context = {"section_ref": reference(index), "start": context_start, "end": context_end}
            if context_mode == "include":
                context["body"] = body[context_start:context_end]
            contexts.append(context)
    next_cursor = None
    if delivered_end < end:
        next_cursor = _token({**binding, "kind": "cursor", "index": selected, "next": delivered_end,
                              "selection": "document" if not selector or selection.get("selection") == "document" else "section",
                              "context": context_mode})
    return {"active": True, **status, "view": view_identity, "parser_revision": PARSER_REVISION, "parser_support": PARSER_SUPPORT,
            "selection": {"start": start, "end": end, "chars": end - start},
            "delivered": {"start": position, "end": delivered_end, "chars": delivered_end - position,
                          "complete_selection": position == start and delivered_end == end},
            "context": contexts, "next_cursor": next_cursor, "body": body[position:delivered_end]}


def add_navigation_options(parser: argparse.ArgumentParser) -> None:
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--section-ref", help="Exact occurrence reference returned by outline for this source/view revision.")
    selection.add_argument("--cursor", help="Continue the same captured selection/view; stale source is rejected.")
    parser.add_argument("--context", choices=("include", "references"), default="include", help="Section context: preamble and ancestor introductory text, or references only; cursors retain their original mode.")
    parser.add_argument("--max-chars", type=positive_int, help="Bound the delivered selection in Unicode characters; omit for the full selection.")
    parser.add_argument("--expected-source", help="Require the exact source revision emitted by an earlier check; never splice changed bytes.")


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
    source_revision: str | None = None,
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
    if source_revision is not None:
        root_args.extend(["--expected-source", source_revision])
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
