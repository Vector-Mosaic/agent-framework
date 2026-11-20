"""Best-effort, unprivileged owner hints for already established REPL blockers."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat
import time


_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_KINDS = {"computer_use_repl", "node_repl", "repl"}
# Native process start times can precede the recorded invocation by a fraction
# of a second (Linux boot-time epoch has whole-second resolution). A match in
# this window is a correlation, never a native process-to-chat binding.
_WINDOW_MS = 1500
_MAX_THREADS = 128
_PREFIX_BYTES = 1024
_STAMP = re.compile(rb'^\{"timestamp":"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z)"')
_CALL = re.compile(rb'(?:,"ordinal":\d+)?,"type":"response_item","payload":\{"type":"(function_call|custom_tool_call)"')
_FIELD = re.compile(rb',"(id|call_id|status|name|namespace)":"([A-Za-z0-9_.-]{1,200})"')
# Read only the dispatch at the start of a native functions.exec wrapper. A
# tool name in comments, strings, results, or arbitrary user JS supplies no hint.
_NODE_DISPATCH = re.compile(
    rb',"input":"(?:// @exec: \{(?:[^\\}\r\n]|\\")*\}\\n)?(?:\s|\\n)*'
    rb'(?:(?:const|let|var) [A-Za-z_$][\w$]*\s*=\s*|text\(\s*)?\(?\s*'
    rb'await\s+tools\.mcp__node_repl__js\s*\('
)


def _windows_user_owns(path: Path) -> bool:
    """Compare the file owner to the process token without reading user profile contents."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.windll.advapi32
    kernel = ctypes.windll.kernel32
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(wintypes.HANDLE(-1), 8, ctypes.byref(token)):
        return False
    descriptor = ctypes.c_void_p()
    try:
        length = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        if not 0 < length.value < 65536:
            return False
        data = ctypes.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, data, length, ctypes.byref(length)):
            return False
        user_sid = ctypes.c_void_p.from_buffer(data)
        # LocalSystem, LocalService and NetworkService own no interactive profile here.
        if any(advapi.IsWellKnownSid(user_sid, kind) for kind in (22, 23, 24)):
            return False
        owner = ctypes.c_void_p()
        error = advapi.GetNamedSecurityInfoW(wintypes.LPWSTR(str(path)), 1, 1, ctypes.byref(owner),
                                             None, None, None, ctypes.byref(descriptor))
        return error == 0 and bool(advapi.EqualSid(user_sid, owner))
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        kernel.CloseHandle(token)


def _ordinary(path: Path, *, directory=False) -> os.stat_result:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if path.resolve(strict=True) != path or not expected(info.st_mode):
        raise ValueError("unexpected profile path")
    owned = info.st_uid == os.geteuid() if os.name == "posix" else _windows_user_owns(path)
    if not owned or (not directory and info.st_nlink != 1):
        raise ValueError("unexpected profile owner")
    return info


def _profile(config_path: str | Path | None) -> Path:
    if config_path is None:
        raise ValueError("Codex profile not configured")
    config = Path(config_path)
    if config.name != "config.toml" or not config.is_absolute():
        raise ValueError("unbound Codex profile")
    if os.name == "posix":
        if os.geteuid() == 0 or os.getuid() != os.geteuid():
            raise ValueError("profile lookup requires its ordinary user")
    else:
        # A service callback must not open the interactive user's private
        # state. Restrict Windows to its unprivileged current-user profile.
        import ctypes
        if os.name != "nt" or ctypes.windll.shell32.IsUserAnAdmin():
            raise ValueError("profile lookup requires its ordinary user")
    _ordinary(config.parent, directory=True)
    _ordinary(config)
    return config.parent


def _database(profile: Path) -> sqlite3.Connection:
    db = profile / "state_5.sqlite"
    _ordinary(db)
    # SQLite in read-only WAL mode may try to create missing sidecars. An
    # incomplete live WAL binding is best left unread for this optional hint.
    with db.open("rb") as source:
        wal = source.read(20)[18:20] == b"\x02\x02"
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = db.with_name(db.name + suffix)
        if (wal and suffix in ("-wal", "-shm")) or os.path.lexists(sidecar):
            _ordinary(sidecar)
    connection = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=0.1)
    connection.row_factory = sqlite3.Row
    if hasattr(connection, "setlimit"):
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024)
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tool(header: bytes, stamp_end: int) -> str | None:
    call = _CALL.match(header, stamp_end)
    if call is None:
        return None
    cursor = call.end()
    fields = {}
    while match := _FIELD.match(header, cursor):
        fields[match[1]] = match[2]
        cursor = match.end()
    name, ns = fields.get(b"name"), fields.get(b"namespace")
    if call[1] == b"function_call":
        for kind, tool in (("computer_use_repl", b"mcp__cua_repl"), ("node_repl", b"mcp__node_repl")):
            if (name == b"js" and ns == tool) or (name == tool + b"__js" and ns is None):
                return kind
    elif name == b"exec" and ns in (None, b"functions") and _NODE_DISPATCH.match(header, cursor):
        return "node_repl"
    return None


def _next_line(source, end: int, deadline: float) -> int:
    while source.tell() < end:
        if time.monotonic() >= deadline:
            raise TimeoutError
        part = source.readline(min(64 * 1024, end - source.tell()))
        if not part:
            raise ValueError("rollout truncated")
        if part.endswith(b"\n"):
            break
    return source.tell()


def _rollout_kinds(path: Path, start: bytes, stop: bytes, deadline: float) -> set[str]:
    expected = _ordinary(path)
    kinds = set()
    with path.open("rb") as source:
        if not os.path.samestat(expected, os.fstat(source.fileno())):
            raise ValueError("rollout replaced")
        end = expected.st_size
        lo, hi = 0, end
        # Rollouts are chronological JSONL. Seek to a preceding line by time,
        # then project only invocation metadata in the narrow start window.
        while hi - lo > 64 * 1024:
            source.seek((lo + hi) // 2)
            boundary = _next_line(source, end, deadline)
            if boundary >= hi:
                break
            stamp = _STAMP.match(source.read(48))
            if stamp is None:
                raise ValueError("unrecognized rollout timestamp")
            if stamp[1] < start:
                lo = boundary
            else:
                hi = boundary
        pos = lo
        while pos < end:
            source.seek(pos)
            header = source.readline(min(_PREFIX_BYTES, end - pos))
            stamp = _STAMP.match(header)
            if stamp is None:
                raise ValueError("unrecognized rollout timestamp")
            if stamp[1] > stop:
                break
            if stamp[1] >= start:
                kind = _tool(header, stamp.end())
                if kind:
                    kinds.add(kind)
            if time.monotonic() >= deadline:
                raise TimeoutError
            pos = source.tell() if header.endswith(b"\n") else _next_line(source, end, deadline)
        if not os.path.samestat(expected, path.stat()) or os.fstat(source.fileno()).st_size < end:
            raise ValueError("rollout changed during lookup")
    return kinds


def _utc_stamp(milliseconds: int) -> bytes:
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z").encode("ascii")


def _correlate(db: sqlite3.Connection, profile: Path, process: dict, deadline: float) -> str | None:
    birth = process["started_at_ms"]
    if type(birth) is not int or birth <= 0:
        return None
    low, high = birth - _WINDOW_MS, birth + _WINDOW_MS
    rows = db.execute(
        "SELECT id, rollout_path FROM threads WHERE created_at <= ? AND updated_at >= ? LIMIT ?",
        (high // 1000, low // 1000, _MAX_THREADS + 1)
    ).fetchall()
    if len(rows) > _MAX_THREADS:
        return None
    start, stop = _utc_stamp(low), _utc_stamp(high)
    matches = set()
    for thread, location in rows:
        if time.monotonic() >= deadline:
            raise TimeoutError
        path = Path(location)
        if (not _UUID.fullmatch(thread) or not path.is_absolute()
                or not any(path.is_relative_to(profile / d) for d in ("sessions", "archived_sessions"))
                or not re.fullmatch(r"rollout-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-"
                                    + thread + r"(?:_[0-9a-f-]{36})?\.jsonl", path.name)):
            return None
        for parent in path.parents:
            if parent == profile:
                break
            _ordinary(parent, directory=True)
        kinds = _rollout_kinds(path, start, stop, deadline)
        if (kinds and process["kind"] == "repl") or process["kind"] in kinds:
            matches.add(thread)
            if len(matches) > 1:
                return None
    return next(iter(matches), None)


def _name(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 240 and value.isprintable()


def _identity(db: sqlite3.Connection, thread: str, status: str) -> dict:
    # 'title' can be a stored first-message excerpt. An earlier native schema
    # without a dedicated display name can still confirm the thread ID.
    columns = {column[1] for column in db.execute("PRAGMA table_info(threads)")}
    projection = ", ".join(f"t.{field}" if field in columns else "NULL"
                           for field in ("name", "agent_path"))
    row = db.execute(f"SELECT {projection} FROM threads t WHERE t.id=?", (thread,)).fetchone()
    if row is None:
        return {"status": "unknown"}
    owner = {"status": status, "thread_id": thread}
    name, agent_path = row
    if _name(name):
        owner["name"] = name
    if _name(agent_path):
        owner["agent_path"] = agent_path
        visited = {thread}
        while len(visited) < 8:
            try:
                parents = db.execute(f"SELECT t.id,{projection} FROM thread_spawn_edges e "
                                     "JOIN threads t ON t.id=e.parent_thread_id WHERE e.child_thread_id=? LIMIT 2",
                                     (thread,)).fetchall()
            except sqlite3.Error:
                break
            if len(parents) != 1:
                break
            thread, parent_name, parent_agent = parents[0]
            if not isinstance(thread, str) or not _UUID.fullmatch(thread) or thread in visited:
                break
            if not parent_agent:
                owner["parent_thread_id"] = thread
                if _name(parent_name):
                    owner["parent_name"] = parent_name
                break
            visited.add(thread)
    return owner


def resolve_chat_owners(codex_config_path: str | Path | None, processes: list[dict]) -> list[dict]:
    """Project display-only chat ownership. Only same-principal REPLs can qualify."""
    rows = [dict(process) for process in processes]
    eligible = []
    for row in rows:
        if row.get("kind") in _KINDS:
            row["chat_owner"] = {"status": "unknown"}
            if row.get("owner_lookup_eligible") is True:
                eligible.append(row)
    if not eligible:
        return rows
    try:
        deadline = time.monotonic() + 3
        profile = _profile(codex_config_path)
        with closing(_database(profile)) as db:
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            db.execute("BEGIN")
            for row in eligible:
                if time.monotonic() >= deadline:
                    break
                try:
                    thread = row.get("thread_id")
                    if isinstance(thread, str) and _UUID.fullmatch(thread):
                        row["chat_owner"] = _identity(db, thread, "direct")
                    elif row.get("role") == "kernel":
                        thread = _correlate(db, profile, row, deadline)
                        if thread:
                            row["chat_owner"] = _identity(db, thread, "correlated")
                except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError, KeyError):
                    continue
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError, RuntimeError):
        pass

    # Only correlate a worker with a sibling from the same native parent
    # incarnation. A long-lived parent can launch more than one session.
    kernels = {}
    for row in eligible:
        if (row.get("role") == "kernel" and type(row.get("started_at_ms")) is int
                and type(row.get("parent_pid")) is int and type(row.get("parent_started_at_ms")) is int):
            group = (row.get("parent_pid"), row.get("parent_started_at_ms"), row["kind"])
            kernels.setdefault(group, []).append(row)
    for worker in eligible:
        if worker.get("role") != "worker" or worker["chat_owner"]["status"] != "unknown":
            continue
        if (worker.get("thread_id") or type(worker.get("parent_pid")) is not int
                or type(worker.get("parent_started_at_ms")) is not int
                or type(worker.get("started_at_ms")) is not int):
            continue
        group = (worker["parent_pid"], worker["parent_started_at_ms"], worker["kind"])
        siblings = [r for r in kernels.get(group, ()) if abs(r["started_at_ms"] - worker["started_at_ms"]) <= 100]
        if len(siblings) == 1 and siblings[0]["chat_owner"]["status"] != "unknown":
            worker["chat_owner"] = {**siblings[0]["chat_owner"], "status": "correlated"}
    return rows
