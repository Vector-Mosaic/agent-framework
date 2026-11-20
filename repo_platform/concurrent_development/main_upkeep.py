"""Guarded, opt-in refresh of the configured physical primary Git checkout."""
from __future__ import annotations

import json
from pathlib import Path

from . import core
from .refresh_processes import current_principal, observe_refresh_processes, safe_process_report
from .repl_chat_owner import resolve_chat_owners


_SCHEMA = "agent_framework.main_upkeep.v1"


class MainUpkeep:
    def __init__(self, service: core.ConcurrentDevelopment):
        self.service = service
        self.home = service.common / "agent-framework" / "main-upkeep"
        self.path = self.home / "state.json"
        self.state_lock = self.home / "state.lock"
        self.admission = self.home / "admission.lock"

    def _load(self):
        try:
            state = json.loads(self.path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            core._fail("main_upkeep_state_unreadable", "Inspect the existing primary upkeep configuration before continuing.")
        physical = self.service.common.stat()
        checkout = self.service.common.parent.stat()
        if (not isinstance(state, dict) or state.get("schema") != _SCHEMA
            or state.get("common_dir") != self.service.common.as_posix()
            or state.get("physical_identity") != [physical.st_dev, physical.st_ino]
            or state.get("checkout_identity") != [checkout.st_dev, checkout.st_ino]
            or state.get("main_root") != self.service.common.parent.as_posix()
            or state.get("repository") != self.service.repository
            or state.get("remote") != self.service.remote or state.get("shared_ref") != self.service.shared_ref):
            core._fail("main_upkeep_identity_changed", "The physical checkout or configured Git destination differs from its binding.")
        if (state.get("process_scope") not in ("host", "current_user", "cooperative")
            or (state["process_scope"] != "host" and not isinstance(state.get("principal"), str))
            or (state.get("codex_config_path") is not None and
                (not isinstance(state["codex_config_path"], str) or not Path(state["codex_config_path"]).is_absolute()
                 or Path(state["codex_config_path"]).name != "config.toml"))
            or type(state.get("enabled")) is not bool or not isinstance(state.get("holds"), dict)
            or not isinstance(state.get("state_root"), str) or not Path(state["state_root"]).is_absolute()
            or any(oid is not None and (not isinstance(oid, str) or not core._OID.fullmatch(oid))
                   for oid in (state.get("pending_target"), state.get("last_completed")))
            or not isinstance(state.get("last_outcome"), dict)
            or any(not isinstance(hold, dict) or not isinstance(hold.get("owner_id"), str)
                   or not isinstance(hold.get("paths"), list) or any(not isinstance(p, str) or not p for p in hold["paths"])
                   for hold in state["holds"].values())):
            core._fail("main_upkeep_state_unreadable", "Preserve the pending target and holds until the configuration is reconciled.")
        return state

    def _main(self, state):
        main = core.ConcurrentDevelopment(Path(state["main_root"]), Path(state["state_root"]),
                                          remote=self.service.remote, shared_branch=self.service.shared_ref.removeprefix("refs/heads/"))
        if main.common != self.service.common or main.git_dir != main.common or main.repository != self.service.repository:
            core._fail("main_upkeep_identity_changed", "The configured primary is no longer the selected physical repository.")
        return main

    @staticmethod
    def _require_external_implementation(main):
        if Path(__file__).resolve().is_relative_to(main.root):
            core._fail("main_upkeep_external_toolkit_required", "Run the upkeep tool from a separate stable installation outside the primary checkout.")

    def configure(self, *, enabled, main_root=None, process_scope=None, codex_root=None):
        if not enabled and (main_root or process_scope or codex_root):
            core._fail("invalid_main_upkeep_configuration", "Disable the existing exact configuration without new selectors.")
        with core._lock(self.admission), core._lock(self.state_lock, blocking=True):
            self.service._check_relocation()
            state = self._load()
            if state is None:
                if not enabled:
                    return {"status": "disabled", "configured": False}
                if main_root is None:
                    core._fail("main_root_required", "Enabling upkeep requires the exact physical primary checkout.")
                root = Path(main_root).resolve(strict=True)
                if root != self.service.common.parent or self.service.common != root / ".git":
                    core._fail("main_upkeep_identity_changed", "Select this Git common directory's physical primary checkout.")
                identity, checkout = self.service.common.stat(), root.stat()
                if not identity.st_ino or not checkout.st_ino:
                    core._fail("main_upkeep_identity_unavailable", "The physical checkout identity could not be established.")
                state = {"schema": _SCHEMA, "repository": self.service.repository, "remote": self.service.remote,
                         "shared_ref": self.service.shared_ref, "common_dir": self.service.common.as_posix(),
                         "physical_identity": [identity.st_dev, identity.st_ino], "main_root": root.as_posix(),
                         "checkout_identity": [checkout.st_dev, checkout.st_ino],
                         "state_root": self.service.state_root.resolve().as_posix(), "process_scope": "host",
                         "principal": None, "codex_config_path": None, "holds": {}, "pending_target": None,
                         "last_completed": None, "last_outcome": {"status": "configured"}}
            if main_root is not None and Path(main_root).resolve(strict=True).as_posix() != state["main_root"]:
                core._fail("main_upkeep_identity_changed", "The configured physical checkout cannot be replaced.")
            main = self._main(state)
            if enabled:
                self._require_external_implementation(main)
                main._require_maintained_checkout()
                if process_scope is not None:
                    if process_scope not in ("host", "current_user", "cooperative"):
                        core._fail("invalid_process_scope", "Choose host, current_user or cooperative process observation.")
                    principal = current_principal() if process_scope != "host" else None
                    if process_scope != "host" and not principal:
                        core._fail("process_principal_unavailable", "The current operating-system principal could not be established.")
                    state.update(process_scope=process_scope, principal=principal)
                if codex_root is not None:
                    root = Path(codex_root)
                    if not root.is_absolute() or not root.is_dir():
                        core._fail("invalid_codex_root", "Select the actual absolute Codex profile directory.")
                    state["codex_config_path"] = (root.resolve(strict=True) / "config.toml").as_posix()
            state["enabled"] = enabled
            core._atomic_write(self.path, state)
        return self.status()

    def clear(self):
        with core._lock(self.admission), core._lock(self.state_lock, blocking=True):
            state = self._load()
            if state is not None:
                if state["enabled"] or state["holds"] or state["pending_target"] or self._main(state).maintained_refresh_pending():
                    core._fail("main_upkeep_in_use", "Finish pending upkeep and active readers, then disable upkeep before clearing its configuration.")
                self.path.unlink()
        return {"status": "unconfigured"}

    @staticmethod
    def _display(outcome, state):
        if outcome.get("reason") != "runtime_input_boundary":
            return outcome
        report = safe_process_report(outcome, process_scope=state["process_scope"])
        report["blocking_processes"] = resolve_chat_owners(state.get("codex_config_path"), report["blocking_processes"])
        return {"status": "deferred", "reason": "runtime_input_boundary", "target": outcome.get("target"), **report}

    def status(self):
        state = self._load()
        if state is None:
            return {"status": "unconfigured", "configured": False, "enabled": False, "pending_target": None}
        return {**state, "configured": True, "refresh_interrupted": bool(self._main(state).maintained_refresh_pending()),
                "last_outcome": self._display(state["last_outcome"], state)}

    def request_verified(self, target):
        """The publisher or explicit attempt has just read this exact shared head."""
        state = self._load()
        if state is None or not state["enabled"]:
            return {"status": "disabled"}
        with core._lock(self.state_lock, blocking=True):
            state = self._load()
            if state is None or not state["enabled"]:
                return {"status": "disabled"}
            prior = state["pending_target"] or state["last_completed"]
            if prior and self.service._included(target, prior):
                return {"status": "already_requested", "pending_target": state["pending_target"]}
            if prior and not self.service._included(prior, target):
                core._fail("main_upkeep_lineage_changed", "Shared history diverged from the recorded upkeep target.")
            state["pending_target"] = target
            core._atomic_write(self.path, state)
        return {"status": "requested", "pending_target": target}

    def hold(self, hold_id, owner_id, paths):
        core._identifier(hold_id)
        if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 512 or any(ord(ch) < 32 for ch in owner_id):
            core._fail("invalid_owner", "Name the actual owner of the input use.")
        with core._lock(self.admission), core._lock(self.state_lock, blocking=True):
            state = self._load()
            if state is None:
                core._fail("main_upkeep_not_configured", "Configure the exact primary checkout before registering input use.")
            main = self._main(state)
            if main.maintained_refresh_pending():
                core._fail("main_refresh_interrupted", "Finish the interrupted refresh before beginning a new source reader.")
            selected = sorted({core._safe_path(main.root, path) for path in paths})
            record = {"owner_id": owner_id, "paths": selected}
            if state["holds"].get(hold_id, record) != record:
                core._fail("main_input_hold_owned", "This input hold has a different owner or scope.")
            state["holds"][hold_id] = record
            core._atomic_write(self.path, state)
        return {"status": "held", "hold_id": hold_id, "whole_main": not selected}

    def release(self, hold_id, owner_id):
        core._identifier(hold_id)
        with core._lock(self.admission), core._lock(self.state_lock, blocking=True):
            state = self._load()
            if state is None:
                core._fail("main_upkeep_not_configured", "The exact primary checkout is not configured.")
            hold = state["holds"].get(hold_id)
            if hold and hold["owner_id"] != owner_id:
                core._fail("main_input_hold_owned", "Only the recorded owner may release this input hold.")
            if hold:
                del state["holds"][hold_id]
                core._atomic_write(self.path, state)
        return {"status": "released", "hold_id": hold_id, "main_upkeep": self.attempt(discover=False)}

    def _record(self, outcome, *, completed=None):
        with core._lock(self.state_lock, blocking=True):
            state = self._load()
            if state is not None:
                state["last_outcome"] = outcome
                if completed is not None:
                    state["last_completed"] = completed
                    if state["pending_target"] == completed:
                        state["pending_target"] = None
                core._atomic_write(self.path, state)
        return outcome

    @staticmethod
    def _held(state, changed):
        return sorted(key for key, hold in state["holds"].items() if not hold["paths"] or
                      any(left == right or left.startswith(right + "/") or right.startswith(left + "/")
                          for left in hold["paths"] for right in changed))

    def _drain(self, state):
        main = self._main(state)
        self._require_external_implementation(main)
        main._require_maintained_checkout()
        while True:
            state = self._load()
            recovery = main.maintained_refresh_pending()
            target = recovery or state["pending_target"]
            head = main._head()
            if not target:
                last = state["last_completed"]
                if last and head != last:
                    return self._record({"status": "deferred", "reason": "main_checkout_moved", "target": last})
                return {"status": "current" if last else "idle", "target": last}
            visibility = {}
            if head != target or recovery:
                # During interrupted writes HEAD might already equal target.
                # Conservatively protect every held input until recovery ends.
                changed = core._paths(core._git(main.root, ["diff", "--no-renames", "--name-only", "-z", head, target]).stdout)
                held = sorted(state["holds"]) if recovery else self._held(state, changed)
                if held:
                    return self._record({"status": "deferred", "reason": "main_input_hold", "hold_ids": held, "target": target})
                scope = state["process_scope"]
                if scope != "host" and (not state["principal"] or current_principal() != state["principal"]):
                    return self._record({"status": "deferred", "reason": "process_principal_unavailable", "target": target})
                profile = Path(state["codex_config_path"]) if state["codex_config_path"] else None

                def before_install():
                    report = safe_process_report(observe_refresh_processes(main.root, profile, process_scope=scope),
                                                 process_scope=scope)
                    visibility["process_observation_complete"] = report["process_observation_complete"]
                    if report["defer_reasons"]:
                        core._fail("runtime_input_boundary", "Native checkout input admission deferred the refresh.", **report)

                protected = [path for hold in state["holds"].values() for path in hold["paths"]]
                main.refresh_maintained(target, protected_paths=protected, expected_head=head, before_install=before_install)
            outcome = self._record({"status": "current" if head == target and not recovery else "refreshed",
                                    "target": target, **visibility}, completed=target)
            if not self._load()["pending_target"]:
                return outcome

    def attempt(self, *, discover=True):
        state = self._load()
        if state is None or not state["enabled"]:
            return {"status": "disabled"}
        try:
            with core._lock(self.admission):
                state = self._load()
                if state is None or not state["enabled"]:
                    return {"status": "disabled"}
                try:
                    if discover:
                        self.request_verified(self.service._shared())
                    outcome = self._drain(state)
                except (core.ConcurrentDevelopmentError, OSError, ValueError) as error:
                    reason = error.code if isinstance(error, core.ConcurrentDevelopmentError) else "main_upkeep_unavailable"
                    report = (safe_process_report(error.details, process_scope=state["process_scope"])
                              if reason == "runtime_input_boundary" else {})
                    outcome = self._record({"status": "deferred", "reason": reason,
                                            "target": self._load()["pending_target"], **report})
        except (core.ConcurrentDevelopmentError, OSError, ValueError) as error:
            reason = error.code if isinstance(error, core.ConcurrentDevelopmentError) else "main_upkeep_unavailable"
            return {"status": "deferred", "reason": reason}
        return self._display(outcome, state)
