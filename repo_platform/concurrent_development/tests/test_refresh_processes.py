from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from repo_platform.concurrent_development import refresh_processes as guard


class NativeFixture:
    platform = "linux"

    def __init__(self):
        self.images = {}
        self.commands = {}
        self.environment = {}
        self.owners = {}
        self.nodes = []
        self.blocked = set()

    def add(self, pid, exe, cwd, *args, parent=1, owner="42", env=None):
        self.images[pid] = guard._Image(pid, pid * 1000, pid * 1000000, str(exe), parent)
        self.commands[pid] = (str(cwd), (str(exe), *map(str, args)))
        self.environment[pid] = env or {}
        self.owners[pid] = owner
        if Path(exe).name == "node":
            self.nodes.append(pid)

    def visibility_complete(self):
        return True

    def principal(self, pid):
        return self.owners[pid]

    def pids(self, scope):
        return self.nodes.copy()

    def image(self, pid):
        if pid not in self.images:
            raise guard._Gone
        return self.images[pid]

    def context(self, pid):
        if pid in self.blocked:
            raise PermissionError
        return (*self.commands[pid], self.env(pid, guard._LOADER))

    def env(self, pid, keys):
        return {key: val for key, val in self.environment[pid].items() if key in keys}


class ProcessGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.files = Path(temporary.name).resolve()
        self.checkout = self.files / "checkout"
        self.checkout.mkdir()
        self.bin = self.files / "resources/cua_node/bin"
        self.bin.mkdir(parents=True)
        self.node = self.bin / "node"
        self.node.touch()
        self.script = self.files / "external-script.js"
        self.script.touch()
        self.profile = self.files / "profile"
        self.profile.mkdir()
        self.config = self.profile / "config.toml"
        self.config.touch()
        self.native = NativeFixture()

    def observe(self, scope="host"):
        return guard._observe(self.native, self.checkout, self.config, scope, "42")

    def test_disjoint_script_and_unclassifiable_loading_and_checkout_entries(self):
        self.native.add(3, self.node, self.files, self.script)
        self.native.add(4, self.node, self.checkout, self.script)
        self.native.add(5, self.node, self.files, "--eval", "private-expression")
        self.native.add(6, self.node, self.files, self.script, env={"NODE_OPTIONS": "private-loader"})
        consumer = self.checkout / "script.js"
        consumer.touch()
        self.native.add(7, self.node, self.files, consumer)
        result = self.observe()
        self.assertEqual(result["defer_reasons"], ["unmanaged_node_consumer"])
        self.assertEqual([r["pid"] for r in result["blocking_processes"]], [4, 5, 6, 7])
        self.assertEqual(result["blocking_process_count"], 4)
        self.assertEqual(result["blocking_processes"][0]["working_directory_scope"], "maintained_checkout")
        self.assertNotIn("private", str(result))
        self.assertNotIn(str(self.files), str(result))

    def test_external_script_declaring_checkout_paths_in_arguments_is_blocked(self):
        target = self.checkout / "tsconfig.json"
        for pid, args in enumerate((
                ("--project", str(target)), ("--project=" + str(target),),
                ("--file", target.as_uri()), ("checkout/tsconfig.json",),
                ("--project=" + str(self.files / "disjoint/config.json"),)), start=3):
            self.native.add(pid, self.node, self.files, self.script, *args)
        result = self.observe()
        self.assertEqual([r["pid"] for r in result["blocking_processes"]], [3, 4, 5, 6])
        self.assertEqual(result["defer_reasons"], ["unmanaged_node_consumer"])

    def test_closed_report_rejects_unknown_reasons_and_preserves_positive_deferral(self):
        row = {"pid": 8, "started_at_ms": 200, "kind": "repl", "role": "worker", "working_directory_scope": "outside",
               "parent_pid": 7, "parent_started_at_ms": 100, "owner_lookup_eligible": True,
               "thread_id": "private-invalid-id", "command": "private-command",
               "chat_owner": {"title": "private-prior-value"}}
        report = guard.safe_process_report({"defer_reasons": ["unmanaged_node_consumer", "private-reason"],
            "blocking_process_count": 2, "blocking_processes": [row, {**row, "pid": True}], "secret": "private"})
        self.assertEqual(report["defer_reasons"], ["unmanaged_node_consumer", "process_observation_unavailable"])
        self.assertEqual(report["blocking_process_count"], 2)
        self.assertEqual([r["pid"] for r in report["blocking_processes"]], [8])
        self.assertNotIn("private", str(report))
        self.assertNotIn("chat_owner", report["blocking_processes"][0])
        self.assertEqual(guard.safe_process_report(None)["defer_reasons"], ["process_observation_unavailable"])
        inconsistent = guard.safe_process_report({"defer_reasons": [], "blocking_process_count": 1,
                                                  "blocking_processes": []})
        self.assertIn("unmanaged_node_consumer", inconsistent["defer_reasons"])

    def test_report_kernel_worker_and_same_account_chat_evidence(self):
        thread = "00000000-0000-0000-0000-000000000001"
        self.native.add(10, self.files / "resources/codex", self.files)
        self.native.add(11, self.bin / "node_repl", self.files, parent=10, env={
            "CODEX_HOME": str(self.profile), "CODEX_CLI_PATH": str(self.files / "resources/codex"),
            "CUA_REPL_ENABLED_SURFACES": "private-surface"})
        self.native.add(12, self.node, self.checkout, "--experimental-vm-modules", self.files / "kernel.js",
                        "--session-id", "private-session", "--working-dir", self.checkout, parent=11,
                        env={"CODEX_THREAD_ID": thread})
        worker = self.files / "trusted-worker.js"
        worker.touch()
        self.native.add(13, self.node, self.files, worker, self.checkout, parent=11)
        result = self.observe()
        kernel, worker = result["blocking_processes"]
        self.assertEqual((kernel["role"], worker["role"]), ("kernel", "worker"))
        self.assertEqual((kernel["parent_pid"], kernel["parent_started_at_ms"]), (11, 11000))
        self.assertEqual(kernel["kind"], "computer_use_repl")
        self.assertEqual(kernel["thread_id"], thread)
        self.assertTrue(worker["owner_lookup_eligible"])
        self.assertNotIn("thread_id", worker)
        self.assertNotIn("private", str(result))
        self.native.owners[11] = "999"
        self.assertTrue(all("owner_lookup_eligible" not in r for r in self.observe()["blocking_processes"]))

    def test_unreadable_process_and_explicitly_selected_principal(self):
        self.native.add(12, self.node, self.checkout, self.script)
        self.native.add(13, self.node, self.files, self.script, owner="123")
        self.native.blocked.add(13)
        full = self.observe()
        self.assertIn("process_observation_unavailable", full["defer_reasons"])
        self.assertEqual(full["blocking_processes"][1]["working_directory_scope"], "unknown")
        scoped = self.observe("current_user")
        self.assertEqual(scoped["defer_reasons"], ["unmanaged_node_consumer"])
        self.assertEqual([r["pid"] for r in scoped["blocking_processes"]], [12])
        with mock.patch.object(self.native, "principal", side_effect=PermissionError):
            self.assertEqual(self.observe("current_user")["defer_reasons"], ["process_observation_unavailable"])

    def test_cooperative_scope_holds_cover_invisible_inputs_but_never_a_known_node(self):
        with mock.patch.object(self.native, "visibility_complete", return_value=False):
            advisory = self.observe("cooperative")
            self.assertFalse(advisory["process_observation_complete"])
            self.assertEqual(advisory["defer_reasons"], [])
            self.assertEqual(guard.safe_process_report(advisory, process_scope="cooperative"), advisory)
            self.assertIn("process_observation_unavailable", guard.safe_process_report(advisory)["defer_reasons"])
            self.native.add(12, self.node, self.checkout, self.script)
            self.native.blocked.add(12)
            report = self.observe("cooperative")
            self.assertEqual(report["defer_reasons"], ["unmanaged_node_consumer"])
            self.assertEqual(report["blocking_processes"][0]["working_directory_scope"], "unknown")
            self.assertFalse(report["process_observation_complete"])
        with mock.patch.object(self.native, "pids", side_effect=([12], PermissionError())):
            interrupted = self.observe("cooperative")
            self.assertEqual(interrupted["defer_reasons"], ["unmanaged_node_consumer"])
            self.assertFalse(interrupted["process_observation_complete"])
        with mock.patch.object(self.native, "pids", side_effect=([], [12])):
            arriving = self.observe("cooperative")
            self.assertEqual(arriving["blocking_process_count"], 1)
            self.assertEqual(arriving["defer_reasons"], ["unmanaged_node_consumer"])

    def test_changing_incarnation_and_invalid_scope_defer(self):
        self.native.add(3, self.node, self.checkout, self.script)
        with mock.patch.object(self.native, "pids", side_effect=[[3], []]):
            self.assertEqual(self.observe()["defer_reasons"], ["process_observation_unavailable"])
        with mock.patch.object(guard, "current_principal", return_value=None):
            for scope in ("unsupported", "current_user"):
                self.assertEqual(guard.observe_refresh_processes(self.checkout, process_scope=scope)["defer_reasons"],
                                 ["process_observation_unavailable"])

    def test_windows_packaged_pair_and_native_bootstrap_match(self):
        self.native.platform = "windows"
        base = r"C:\Users\Example\AppData\Local\OpenAI\Codex\runtimes"
        node = guard._Image(9, 200, 200, base + r"\cua_node\aaaaaaaaaaaa\bin\node.exe", 8)
        parent = guard._Image(8, 100, 100, base + r"\node_repl\bbbbbbbbbbbb\bin\node_repl.exe", 1)
        command = (node.executable, "--eval", "private-bootstrap", r"C:\runtime\trusted-worker.js", r"C:\checkout")
        self.assertEqual(guard._repl_role(self.native, node, parent, command), "worker")
        spoof = guard._Image(8, 100, 100, r"C:\other\node_repl.exe", 1)
        self.assertIsNone(guard._repl_role(self.native, node, spoof, command))

    def test_windows_known_node_survives_denied_birth_and_missing_optional_reader_is_explicit(self):
        process = SimpleNamespace(username=lambda: r"DOMAIN\User", name=lambda: "node.exe",
                                  create_time=mock.Mock(side_effect=PermissionError))
        api = SimpleNamespace(pids=lambda: [0, 4, 8], Process=lambda pid: process,
                              NoSuchProcess=ProcessLookupError, Error=OSError)
        with mock.patch.dict(sys.modules, {"psutil": api}):
            native = guard._Windows()
            result = guard._observe(native, self.checkout, None, "cooperative", r"domain\user")
        self.assertEqual(result, {"defer_reasons": ["unmanaged_node_consumer"], "blocking_processes": [],
                                  "blocking_process_count": 1, "process_observation_complete": False})
        with mock.patch.object(guard.sys, "platform", "win32"), mock.patch.object(guard, "os", SimpleNamespace(name="nt")), \
             mock.patch.object(guard, "current_principal", return_value=r"domain\user"), \
             mock.patch.object(guard, "_Windows", side_effect=ImportError):
            strict = guard.observe_refresh_processes(self.checkout, process_scope="current_user")
            cooperative = guard.observe_refresh_processes(self.checkout, process_scope="cooperative")
        self.assertEqual(strict["defer_reasons"], ["process_observation_unavailable"])
        self.assertFalse(cooperative["process_observation_complete"])
        self.assertEqual(cooperative["defer_reasons"], [])

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("node"), "requires a local Linux Node")
    def test_native_proc_reader_detects_real_checkout_consumer(self):
        consumer = self.checkout / "consumer.js"
        consumer.write_text("setInterval(() => {}, 10000);", encoding="utf-8")
        with subprocess.Popen([shutil.which("node"), str(consumer)], cwd=self.checkout,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as child:
            try:
                native = guard._Linux()
                with mock.patch.object(native, "pids", return_value=[child.pid]):
                    report = guard._observe(native, self.checkout, None, "current_user", str(os.geteuid()))
                self.assertEqual(report["blocking_process_count"], 1)
                self.assertEqual(report["blocking_processes"][0]["pid"], child.pid)
                self.assertEqual(report["blocking_processes"][0]["working_directory_scope"], "maintained_checkout")
            finally:
                child.terminate()
