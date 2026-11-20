"""Read-only Node input observations for a maintained checkout refresh."""

from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sys
from urllib.parse import urlsplit
from urllib.request import url2pathname


_THREAD = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_LOADER = {"NODE_OPTIONS", "NODE_PATH"}
_REPL = {"CUA_REPL_ENABLED_SURFACES", "CODEX_HOME", "CODEX_CLI_PATH"}
_MAX_REPORT = 64


def current_principal() -> str | None:
    if sys.platform.startswith("linux"):
        return str(os.geteuid())
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        try:
            get_name = ctypes.WinDLL("secur32", use_last_error=True).GetUserNameExW
            get_name.argtypes = [ctypes.c_int, wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG)]
            get_name.restype = ctypes.c_ubyte
            size = wintypes.ULONG()
            get_name(2, None, ctypes.byref(size))  # NameSamCompatible
            if not 0 < size.value <= 32768:
                return None
            buffer = ctypes.create_unicode_buffer(size.value)
            if get_name(2, buffer, ctypes.byref(size)):
                name = buffer.value.casefold()
                return name if "\\" in name else None
        except OSError:
            pass
        return None
    return None


@dataclass(frozen=True)
class _Image:
    pid: int
    started_at_ms: int
    # Keep the full native creation identity for rechecks even when the public
    # report rounds birth to milliseconds.
    birth: int | float
    executable: str
    parent: int
    image_file: tuple[int, int] | None = None


class _Gone(Exception):
    """The process exited during enumeration."""


def _read(path, limit):
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("process read exceeds limit")
    return data


class _Linux:
    platform = "linux"

    def __init__(self):
        self.proc = Path("/proc")
        self.ticks = os.sysconf("SC_CLK_TCK")
        boot = [line for line in _read(self.proc / "stat", 2**20).splitlines() if line.startswith(b"btime ")]
        self.epoch = int(boot[0].split()[1]) * 1000

    def visibility_complete(self):
        # hidepid=invisible suppresses entire foreign entries. Even an empty
        # list cannot establish host-wide absence through a restricted mount.
        mounts = _read(self.proc / "self/mountinfo", 2**20).splitlines()
        proc_mount = [line for line in mounts if line.split()[4] == b"/proc"]
        return bool(proc_mount) and not any(
            opt.startswith(b"hidepid=") and opt not in (b"hidepid=0", b"hidepid=off")
            for line in proc_mount for field in line.split() for opt in field.split(b","))

    def pids(self, scope):
        return [int(path.name) for path in self.proc.iterdir() if path.name.isdecimal()]

    def principal(self, pid):
        # A nondumpable process can have a root-owned proc directory despite
        # running under an ordinary UID. Use the native effective UID field.
        fields = next((row.split() for row in _read(self.proc / str(pid) / "status", 65536).splitlines()
                       if row.startswith(b"Uid:")), [])
        if len(fields) != 5 or not fields[2].isdigit():
            raise ValueError("unknown process principal")
        return fields[2].decode("ascii")

    def image(self, pid):
        path = self.proc / str(pid)
        try:
            fields = _read(path / "stat", 65536).rsplit(b") ", 1)[1].split()
            if fields[0] in (b"Z", b"X") or int(fields[6]) & 0x00200000:
                raise _Gone
            birth = int(fields[19])
            executable = os.readlink(path / "exe")
            info = (path / "exe").stat()
            return _Image(pid, self.epoch + birth * 1000 // self.ticks, birth, executable,
                          int(fields[1]), (info.st_dev, info.st_ino))
        except FileNotFoundError:
            if not path.exists():
                raise _Gone from None
            raise

    def context(self, pid):
        directory = self.proc / str(pid)
        # Report paths only through the filesystem namespace that owns them.
        if (not (directory / "root").samefile(self.proc / "self/root")
                or not (directory / "ns/mnt").samefile(self.proc / "self/ns/mnt")):
            raise ValueError("distinct process filesystem")
        raw = _read(directory / "cmdline", 131072)
        if not raw.endswith(b"\0"):
            raise ValueError("unobservable process arguments")
        return os.readlink(directory / "cwd"), tuple(os.fsdecode(s) for s in raw[:-1].split(b"\0")), self.env(pid, _LOADER)

    def env(self, pid, keys):
        found = {}
        for pair in _read(self.proc / str(pid) / "environ", 2 * 1024**2).split(b"\0"):
            raw_key, separator, raw_value = pair.partition(b"=")
            key = os.fsdecode(raw_key)
            if separator and key in keys:
                if key in found:
                    raise ValueError("ambiguous environment setting")
                found[key] = os.fsdecode(raw_value)
        return found


class _Windows:
    platform = "windows"

    def __init__(self):
        import psutil
        self.api = psutil

    def _read(self, pid, call):
        try:
            return call(self.api.Process(pid))
        except self.api.NoSuchProcess:
            raise _Gone from None
        except self.api.Error:
            raise OSError("process read unavailable") from None

    def visibility_complete(self):
        return True

    def pids(self, scope):
        try:
            return [pid for pid in self.api.pids() if pid not in (0, 4)]
        except self.api.Error:
            raise OSError("native process inventory unavailable") from None

    def principal(self, pid):
        username = self._read(pid, lambda p: p.username().casefold())
        if "\\" not in username:
            raise ValueError("unknown process principal")
        return username

    def candidate(self, pid):
        # psutil on Windows obtains a native process image name, not Linux comm.
        return self._read(pid, lambda p: p.name().casefold() in ("node.exe", "nodejs.exe"))

    def image(self, pid):
        def get(p):
            birth = p.create_time()
            return _Image(pid, int(birth * 1000), birth, p.exe(), p.ppid())
        return self._read(pid, get)

    def context(self, pid):
        return self._read(pid, lambda p: (p.cwd(), tuple(p.cmdline()), self.env(pid, _LOADER)))

    def env(self, pid, keys):
        return self._read(pid, lambda p: {k.upper(): v for k, v in p.environ().items() if k.upper() in keys})


def _inventory(native, scope, principal):
    images, unidentified = {}, set()
    try:
        unavailable = not native.visibility_complete()
    except (OSError, ValueError, IndexError):
        unavailable = True
    aliases = set()
    if native.platform == "linux":
        for name in ("node", "nodejs"):
            located = shutil.which(name)
            if located:
                try:
                    info = Path(located).stat()
                    aliases.add((info.st_dev, info.st_ino))
                except OSError:
                    unavailable = True
    for pid in native.pids(scope):
        identified = False
        try:
            if native.platform == "windows" and not native.candidate(pid):
                continue
            if scope != "host" and native.principal(pid) != principal:
                continue
            identified = native.platform == "windows"
            image = native.image(pid)
            native_path = image.executable.removesuffix(" (deleted)")
            is_node = (ntpath.basename(native_path).casefold() in ("node.exe", "nodejs.exe") if native.platform == "windows"
                       else Path(native_path).name in ("node", "nodejs") or image.image_file in aliases)
            if is_node:
                images[pid] = image
        except (_Gone, ProcessLookupError):
            continue
        except FileNotFoundError:
            # A PID observed by readdir may disappear before the initial UID
            # lookup. A persistent missing image is unobservable.
            if native.platform != "linux" or (native.proc / str(pid)).exists():
                unavailable = True
        except (OSError, ValueError, IndexError):
            unavailable = True
            if identified:
                unidentified.add(pid)
    return images, unavailable, unidentified


def _argument_inputs_disjoint(arguments, directory, checkout):
    for argument in arguments:
        if argument.startswith("-"):
            _, equals, value = argument.partition("=")
            if not equals:
                continue
        else:
            value = argument
        if not value:
            continue
        if value.startswith("file:"):
            uri = urlsplit(value)
            if uri.netloc not in ("", "localhost") or uri.query or uri.fragment:
                return False
            value = url2pathname(uri.path)
        path = Path(value)
        if os.name == "nt" and not path.is_absolute() and (path.drive or path.root):
            return False
        # Positional inputs and --flag=value may be relative to the runtime
        # cwd. Resolve existing aliases even when the final input is missing.
        location = (directory / path).resolve(strict=False)
        if location.is_relative_to(checkout) or checkout.is_relative_to(location):
            return False
    return True


def _ordinary_locations(image, checkout, cwd, command, environment):
    """Resolve the ordinary Node script launch. Unknown loader semantics block."""
    directory = Path(cwd)
    if not directory.is_absolute():
        raise ValueError("unknown working directory")
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("unknown working directory")
    scope = "maintained_checkout" if directory.is_relative_to(checkout) else "outside"
    if (scope != "outside" or image.executable.endswith(" (deleted)") or any(environment.values())
            or not 2 <= len(command) <= 512 or len(command[0]) == 0
            or ntpath.basename(command[0]).casefold() not in ("node", "nodejs", "node.exe", "nodejs.exe")
            or Path(image.executable).resolve(strict=True).is_relative_to(checkout)):
        return scope, False
    if any(s.split("=", 1)[0] in ("--env-file", "--env-file-if-exists") for s in command[1:]):
        return scope, False
    i = 1
    while i < len(command) and command[i] in ("--no-warnings", "--trace-warnings", "--enable-source-maps", "--experimental-vm-modules"):
        i += 1
    if i < len(command) and command[i] == "--":
        i += 1
    if i == len(command) or not command[i] or command[i].startswith("-"):
        return scope, False
    script = Path(command[i])
    if os.name == "nt" and not script.is_absolute() and (script.drive or script.root or ":" in command[i]):
        return scope, False
    script = (directory / script).resolve(strict=True)
    if not script.is_file() or script.is_relative_to(checkout):
        return scope, False
    if script.name == "trusted-worker.js":
        # The native worker accepts its target directory as a positional
        # argument. It can differ from the process's inherited cwd.
        args = command[i + 1:]
        if len(args) != 1 or not Path(args[0]).is_absolute():
            return scope, False
    return scope, _argument_inputs_disjoint(command[i + 1:], directory, checkout)


def _repl_role(native, image, parent, command):
    if native.platform == "windows":
        child_path = PureWindowsPath(image.executable.casefold())
        host_path = PureWindowsPath(parent.executable.casefold())
        if (len(child_path.parts) < 8 or len(host_path.parts) < 8
                or host_path.parts[-7:-3] != ("openai", "codex", "runtimes", "node_repl")
                or host_path.name != "node_repl.exe" or child_path.name != "node.exe"
                or child_path.parents[3] != host_path.parents[3]
                or (child_path.parent != host_path.parent and child_path.parts[-4] != "cua_node")
                or any(p.parent.name != "bin" or not re.fullmatch(r"[0-9a-f]{12,64}", p.parts[-3])
                       for p in (child_path, host_path))):
            return None
        if len(command) > 3 and command[1] == "--eval":
            command = (command[0], *command[3:])
        path = PureWindowsPath
    else:
        host_path, child_path = Path(parent.executable), Path(image.executable)
        if (host_path.parts[-4:] != ("resources", "cua_node", "bin", "node_repl")
                or child_path != host_path.with_name("node")):
            return None
        path = Path
    if (len(command) == 7 and command[1] == "--experimental-vm-modules"
            and path(command[2]).is_absolute() and path(command[2]).name == "kernel.js"
            and command[3] == "--session-id" and command[5] == "--working-dir"):
        return "kernel"
    if len(command) >= 2 and path(command[1]).is_absolute() and path(command[1]).name == "trusted-worker.js":
        return "worker"
    return None


def _repl_details(native, image, command, principal, config):
    parent = native.image(image.parent)
    if parent.started_at_ms > image.started_at_ms:
        return {}
    role = _repl_role(native, image, parent, command)
    if role is None:
        return {}
    row = {"role": role, "kind": "repl", "parent_pid": parent.pid, "parent_started_at_ms": parent.started_at_ms}
    try:
        settings = native.env(parent.pid, _REPL)
        row["kind"] = "computer_use_repl" if settings.get("CUA_REPL_ENABLED_SURFACES") else "node_repl"
        if (config is not None and principal is not None and config.name == "config.toml"
                and config.is_absolute() and config.resolve(strict=True) == config
                and native.principal(image.pid) == principal == native.principal(parent.pid)):
            home = settings.get("CODEX_HOME")
            if not home and native.platform == "windows" and os.name == "nt":
                # Use the token's profile. A callback's USERPROFILE can have
                # been overridden independently of its account or the REPL.
                import ctypes
                buffer = ctypes.create_unicode_buffer(260)
                if ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, buffer) == 0:
                    home = str(Path(buffer.value) / ".codex")
            # The chat reader independently checks the actual config/profile
            # owner and refuses titles outside the selected account.
            if home and Path(home).is_absolute() and Path(home).resolve(strict=True) == config.parent:
                if native.platform == "linux":
                    app = native.image(parent.parent)
                    expected = str(Path(parent.executable).parents[2] / "codex")
                    if (app.executable != settings.get("CODEX_CLI_PATH") or app.executable != expected
                            or native.principal(app.pid) != principal or native.image(app.pid) != app):
                        return row
                label = native.env(image.pid, {"CODEX_THREAD_ID"}).get("CODEX_THREAD_ID")
                if native.env(parent.pid, _REPL) == settings:
                    row["owner_lookup_eligible"] = True
                    if (isinstance(label, str) and _THREAD.fullmatch(label)
                            and native.env(image.pid, {"CODEX_THREAD_ID"}).get("CODEX_THREAD_ID") == label):
                        row["thread_id"] = label
    except (_Gone, OSError, ValueError, RuntimeError):
        pass
    return row if native.image(parent.pid) == parent else {}


def _observe(native, checkout, config, scope, principal):
    before, unavailable, prior_unidentified = _inventory(native, scope, principal)
    rows = []
    for pid, image in before.items():
        row = {"pid": pid, "started_at_ms": image.started_at_ms, "kind": "node", "role": "process",
               "working_directory_scope": "unknown"}
        try:
            context = native.context(pid)
            try:
                location, disjoint = _ordinary_locations(image, checkout, *context)
            except (OSError, ValueError, RuntimeError):
                unavailable, disjoint = True, False
                cwd = Path(context[0])
                if cwd.is_absolute() and cwd.is_dir():
                    location = "maintained_checkout" if cwd.resolve(strict=True).is_relative_to(checkout) else "outside"
                else:
                    location = "unknown"
            row["working_directory_scope"] = location
            if not disjoint:
                try:
                    row.update(_repl_details(native, image, context[1], principal, config))
                except (_Gone, OSError, ValueError, IndexError):
                    pass
            if (native.image(pid) != image or native.context(pid) != context
                    or (disjoint and _ordinary_locations(image, checkout, *context) != (location, True))):
                unavailable = True
                row = {"pid": pid, "started_at_ms": image.started_at_ms, "kind": "node",
                       "role": "process", "working_directory_scope": "unknown"}
            elif disjoint:
                continue
        except (_Gone, OSError, ValueError, RuntimeError, IndexError):
            unavailable = True
        rows.append(row)
    try:
        fresh, failed, unidentified = _inventory(native, scope, principal)
    except (OSError, ValueError, IndexError):
        # Losing the second read must not erase a blocker already observed.
        fresh, failed, unidentified = before, True, prior_unidentified
    if fresh != before or failed:
        unavailable = True
    rows = [row for row in rows if fresh.get(row["pid"]) == before[row["pid"]]]
    for pid, image in fresh.items():
        if before.get(pid) != image:
            # A known Node appeared too late to evaluate its input closure.
            rows.append({"pid": pid, "started_at_ms": image.started_at_ms, "kind": "node",
                         "role": "process", "working_directory_scope": "unknown"})
    rows.sort(key=lambda row: (row["role"] == "process", row["started_at_ms"], row["pid"]))
    count = len(rows) + len(unidentified)
    reasons = ["unmanaged_node_consumer"] if count else []
    if unavailable and scope != "cooperative":
        reasons.append("process_observation_unavailable")
    return {"defer_reasons": reasons, "blocking_process_count": count, "blocking_processes": rows[:_MAX_REPORT],
            "process_observation_complete": not unavailable}


def observe_refresh_processes(checkout: Path, codex_config_path: Path | None = None, *, process_scope: str = "host") -> dict:
    """Observe a stable native inventory. Incomplete observations defer refresh."""
    try:
        principal = current_principal()
        if process_scope not in ("host", "current_user", "cooperative") or (process_scope != "host" and principal is None):
            raise ValueError("unobservable process scope")
        if sys.platform.startswith("linux"):
            native = _Linux()
        elif os.name == "nt":
            native = _Windows()
        else:
            raise ValueError("unsupported native process reader")
        checkout = Path(checkout).resolve(strict=True)
        if not checkout.is_dir():
            raise ValueError("unknown checkout")
        return _observe(native, checkout, Path(codex_config_path) if codex_config_path else None, process_scope, principal)
    except (ImportError, OSError, ValueError, RuntimeError, IndexError):
        return {"defer_reasons": [] if process_scope == "cooperative" else ["process_observation_unavailable"],
                "blocking_process_count": 0, "blocking_processes": [], "process_observation_complete": False}


def safe_process_report(report: object, *, process_scope: str = "host") -> dict:
    """Retain the guard decision and only the public, typed incarnation fields."""
    source = report if isinstance(report, dict) else {}
    allowed_reasons = ("unmanaged_node_consumer", "process_observation_unavailable")
    raw_reasons = source.get("defer_reasons")
    reasons = [r for r in allowed_reasons if isinstance(raw_reasons, list) and r in raw_reasons]
    invalid = not isinstance(raw_reasons, list) or any(r not in allowed_reasons for r in raw_reasons)
    complete = source.get("process_observation_complete")
    if type(complete) is not bool:
        complete, invalid = False, True
    if process_scope not in ("host", "current_user", "cooperative") or (not complete and process_scope != "cooperative"):
        invalid = True
    count = source.get("blocking_process_count")
    if type(count) is not int or not 0 <= count < 2**31:
        count, invalid = 0, True
    supplied = source.get("blocking_processes")
    if not isinstance(supplied, list):
        supplied, invalid = [], True
    if len(supplied) > min(count, _MAX_REPORT):
        invalid = True
    rows, seen = [], set()
    for raw in supplied[:_MAX_REPORT]:
        if not isinstance(raw, dict):
            continue
        pid, started = raw.get("pid"), raw.get("started_at_ms")
        kind, role, scope = raw.get("kind"), raw.get("role"), raw.get("working_directory_scope")
        if (type(pid) is not int or not 0 < pid < 2**32 or pid in seen
                or type(started) is not int or not 0 < started <= 2**53 - 1
                or kind not in ("node", "node_repl", "computer_use_repl", "repl")
                or role not in ("process", "worker", "kernel") or (kind == "node") != (role == "process")
                or scope not in ("outside", "maintained_checkout", "unknown")):
            continue
        seen.add(pid)
        row = {"pid": pid, "started_at_ms": started, "kind": kind, "role": role, "working_directory_scope": scope}
        ppid, parent_birth = raw.get("parent_pid"), raw.get("parent_started_at_ms")
        if (type(ppid) is int and 0 < ppid < 2**32 and ppid != pid
                and type(parent_birth) is int and 0 < parent_birth <= started):
            row.update(parent_pid=ppid, parent_started_at_ms=parent_birth)
            if kind != "node" and raw.get("owner_lookup_eligible") is True:
                row["owner_lookup_eligible"] = True
                label = raw.get("thread_id")
                if isinstance(label, str) and _THREAD.fullmatch(label):
                    row["thread_id"] = label
        rows.append(row)
    if count or supplied:
        if "unmanaged_node_consumer" not in reasons:
            reasons.insert(0, "unmanaged_node_consumer")
            invalid = True
    if invalid and "process_observation_unavailable" not in reasons:
        reasons.append("process_observation_unavailable")
    return {"defer_reasons": reasons, "blocking_process_count": max(count, len(rows)), "blocking_processes": rows,
            "process_observation_complete": complete and not invalid and "process_observation_unavailable" not in reasons}
