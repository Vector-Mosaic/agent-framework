use serde_json::{Value, json};
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use apeiros_capacity::*;
mod observation;

fn field<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("required string: {key}"))
}
fn revision(v: &Value) -> Result<u64> {
    v.get("revision")
        .and_then(Value::as_u64)
        .ok_or("required revision".into())
}
fn decode<T: serde::de::DeserializeOwned>(v: &Value, key: &str) -> Result<T> {
    serde_json::from_value(v.get(key).ok_or_else(|| format!("required {key}"))?.clone())
        .map_err(|e| e.to_string())
}

// Declare each public wire name at its execution boundary. Exhaustive Rust
// matches keep registered operations tied to real handlers.
macro_rules! action_set {
    ($group:ident { $($variant:ident => $wire:literal),+ $(,)? }) => {
        #[derive(Clone, Copy, PartialEq, Eq)]
        enum $group { $($variant),+ }
        impl $group {
            fn parse(name: &str) -> Option<Self> {
                match name {
                    $($wire => Some(Self::$variant),)+
                    _ => None,
                }
            }
            #[cfg(test)]
            const NAMES: &'static [&'static str] = &[$($wire),+];
        }
    }
}

action_set!(IndependentAction {
    Help => "help",
    Overview => "overview",
    Observation => "observation",
    Initialize => "initialize",
    ObserveNative => "observe_native",
    Follow => "follow",
    PressureTick => "pressure_tick",
});
action_set!(StoredAction {
    SucceedPressureControl => "succeed_pressure_control",
    ConfigureResourcePreferences => "configure_resource_preferences",
    ConfigureScheduling => "configure_scheduling",
    ConfigureReservations => "configure_reservations",
    ReconfigurePolicy => "reconfigure_policy",
    CpuPolicy => "cpu_policy",
    IoPolicy => "io_policy",
    ApplyIoPolicy => "apply_io_policy",
    ApplyCpuPolicy => "apply_cpu_policy",
    Inspect => "inspect",
    Assess => "assess",
    Admit => "admit",
    AdmitIfFit => "admit_if_fit",
    RequestDemandChange => "request_demand_change",
    CancelDemandChange => "cancel_demand_change",
    Observe => "observe",
    Schedule => "schedule",
    Hold => "hold",
    ReleaseHold => "release_hold",
    Release => "release",
    ReconcileStep => "reconcile_step",
    BindRuntime => "bind_runtime",
    AdoptRuntime => "adopt_runtime",
    MaintenancePrepare => "maintenance_prepare",
    MaintenanceRecord => "maintenance_record",
    MaintenanceFinish => "maintenance_finish",
});

/// CLI and the resident HTTP owner share this operation handler. Persistent MCP
/// clients forward opaque operations to the resident; they never load state.
fn handle(root: &Path, v: Value) -> Result<Value> {
    let name = field(&v, "action")?;
    if let Some(action) = IndependentAction::parse(name) {
        return match action {
            IndependentAction::Help => observation::help(&v),
            IndependentAction::Overview => observation::direct(root, &v),
            IndependentAction::Observation => observation::read_or_control(root, &v),
            IndependentAction::Initialize => {
                Store::initialize(root, decode(&v, "policy")?)?;
                Ok(json!({"initialized":true,"launches":false}))
            }
            IndependentAction::ObserveNative => {
                let captured = Store::snapshot(root)?;
                let observation = native::observe(&captured);
                handle(root, json!({"action":"observe","observation":observation}))
            }
            IndependentAction::Follow => follow(root, &v),
            IndependentAction::PressureTick => pressure::tick(root),
        };
    }
    let action = StoredAction::parse(name).ok_or("unknown action; use usage")?;
    if action == StoredAction::Inspect {
        if v.get("view").is_some() {
            return observation::direct(root, &v);
        }
        if ["allocation_id", "source", "after_id", "limit"]
            .iter()
            .any(|key| v.get(*key).is_some())
        {
            return Err("inspect selectors require an explicit view; use overview for routine current inspection".into());
        }
    }
    let mut store = Store::open(root)?;
    if let Some(expected) = v.get("expected_source") {
        if expected["instance_id"] != store.state.instance_id
            || expected["host_id"] != store.state.policy.host_id
        {
            return Err("observation source changed; reacquire without replaying control".into());
        }
    }
    if let Some(expected) = v.get("expected_state_revision") {
        if expected.as_u64() != Some(store.state.revision) {
            return Err("stale capacity state revision".into());
        }
    }
    let now = now_ms();
    let boot = boot_id()?;
    let mut changed = true;
    let response = match action {
        StoredAction::SucceedPressureControl => {
            let executable = std::env::current_exe().map_err(|e| e.to_string())?;
            let result = store.succeed_pressure_control(&v, &executable)?;
            changed = false;
            result
        }
        StoredAction::ConfigureResourcePreferences => {
            let source = v
                .get("expected_source")
                .ok_or("exact capacity source required")?;
            let selected = store.configure_resource_preferences(
                field(source, "instance_id")?,
                decode(&v, "expected_policy_revision")?,
                revision(&v)?,
                decode(&v, "resources")?,
                field(&v, "change_ref")?.into(),
            )?;
            changed = false; // The independently revisioned preference file is the commit point.
            json!({"resource_preferences":store.state.resource_preferences,"preference_revision":store.state.preference_revision(),
                "changed":selected,"allocations_and_holds_preserved":true,"launched":false})
        }
        StoredAction::ConfigureScheduling => {
            store.configure_scheduling(
                field(&v, "expected_instance_id")?,
                v["expected_policy_revision"]
                    .as_u64()
                    .ok_or("expected policy revision required")?,
                revision(&v)?,
                decode(&v, "configuration")?,
                &decode(&v, "expected_allocations")?,
            )?;
            changed = false; // The same-lock native policy file owns its revision.
            json!({"scheduling":store.state.scheduling,"immutable_requests_preserved":true,"workers_restarted":false,"launched":false,"native_priority_applies_only_if_selected":true})
        }
        StoredAction::ConfigureReservations => {
            store.configure_reservations(
                field(&v, "expected_instance_id")?,
                v["expected_policy_revision"]
                    .as_u64()
                    .ok_or("expected policy revision required")?,
                revision(&v)?,
                decode(&v, "reservations")?,
                field(&v, "change_ref")?.into(),
            )?;
            changed = false;
            json!({"scheduling":store.state.scheduling,"immutable_requests_preserved":true,
                "workers_restarted":false,"launched":false,"physical_policy_guard_unchanged":true})
        }
        StoredAction::ReconfigurePolicy => {
            store.state.reconfigure_policy(
                revision(&v)?,
                decode(&v, "policy")?,
                field(&v, "change_ref")?.into(),
            )?;
            json!({"policy":store.state.policy,"policy_revision":store.state.policy_revision,"changed":true,"allocations_and_holds_preserved":true,"launched":false,"fresh_affected_observation_required":true})
        }
        StoredAction::CpuPolicy => {
            changed = false;
            native::cpu_policy(&store.state.native_policy(), false)
        }
        StoredAction::IoPolicy => {
            changed = false;
            storage::controls(&store.state.policy, false)
        }
        StoredAction::ApplyIoPolicy => {
            if store.state.policy_revision != revision(&v)? {
                return Err("stale I/O policy revision; inspect before native control".into());
            }
            changed = false;
            storage::controls(&store.state.policy, true)
        }
        StoredAction::ApplyCpuPolicy => {
            if store
                .state
                .scheduling
                .as_ref()
                .is_some_and(|s| v["scheduling_revision"].as_u64() != Some(s.revision))
            {
                return Err(
                    "stale or missing scheduling revision; inspect before native control".into(),
                );
            }
            if store.state.revision != revision(&v)? {
                return Err("stale capacity policy; inspect before native control".into());
            }
            changed = false;
            native::cpu_policy(&store.state.native_policy(), true)
        }
        StoredAction::Inspect => {
            changed = false;
            json!({"scheduling":store.state.scheduling,"resource_preferences":store.state.resource_preferences,"preference_revision":store.state.preference_revision(),"state":store.state,"current_boot_id":boot,"observed_at_ms":now,"passive":true,"coverage":"enrolled capacity references; domain progress/results remain at owner_ref"})
        }
        StoredAction::Assess => {
            changed = false;
            json!({"assessment":store.state.assess(&decode(&v,"request")?,now,&boot)?,"reserved":false,"launched":false})
        }
        StoredAction::Admit => {
            let req: Request = decode(&v, "request")?;
            let assessment = store.state.admit(req.clone(), now, &boot)?;
            json!({"assessment":assessment,"allocation":store.state.allocations.get(&req.allocation_id),"launched":false})
        }
        StoredAction::AdmitIfFit => {
            let req: Request = decode(&v, "request")?;
            let before = store.state.revision;
            let assessment = store.state.admit_if_fit(req.clone(), now, &boot)?;
            changed = store.state.revision != before;
            json!({"assessment":assessment,"allocation":store.state.allocations.get(&req.allocation_id),"launched":false,"new_waiter_created":false})
        }
        StoredAction::RequestDemandChange => {
            if v.get("expected_source").is_none() {
                return Err("working demand requires the exact observed capacity source".into());
            }
            let request: demand::ChangeRequest = decode(&v, "change")?;
            // Replays read their original outcome even when its runtime ended.
            if !store
                .state
                .demand_change(&request.allocation_id)
                .is_some_and(|c| c.request.change_id == request.change_id)
            {
                native::validate_binding(
                    request
                        .expected_allocation
                        .runtime
                        .as_ref()
                        .ok_or("working demand requires a bound native runtime")?,
                )?;
            }
            let change = store.state.request_demand_change(request, now, &boot)?;
            json!({"change":change,"scheduling_revision":store.state.scheduling.as_ref().map(|s|s.revision),"launched":false,"native_limits_changed":false,"original_request_preserved":true,"final_dispatch_required_for_growth":true})
        }
        StoredAction::CancelDemandChange => {
            if v.get("expected_source").is_none() {
                return Err("working demand requires the exact observed capacity source".into());
            }
            let id = field(&v, "allocation_id")?;
            store.state.cancel_demand_change(
                id,
                field(&v, "change_id")?,
                field(&v, "owner_ref")?,
            )?;
            json!({"change":store.state.demand_change(id),"current_booking_preserved":true,"launched":false,"released":false})
        }
        StoredAction::Observe => {
            store
                .state
                .observe(decode(&v, "observation")?, now, &boot)?;
            let newly_admitted = store.state.schedule(now, &boot)?;
            json!({"observation_saved":true,"newly_admitted":newly_admitted,"launched":false})
        }
        StoredAction::Schedule => {
            let ids = store.state.schedule(now, &boot)?;
            changed = !ids.is_empty();
            json!({"newly_admitted":ids,"launched":false})
        }
        StoredAction::Hold => {
            let hold: Hold = decode(&v, "hold")?;
            let active = store.state.hold(hold.clone())?;
            let pending:Vec<_>=store.state.allocations.values().filter(|a|store.state.scope_matches(&hold.scope,&a.request)&&a.phase!=Phase::Released).flat_map(|a|a.steps.iter().filter(|(_,s)|matches!(s.outcome,StepOutcome::Dispatching|StepOutcome::Uncertain)).map(move |(id,_)|json!({"allocation_id":a.request.allocation_id,"step_id":id}))).collect();
            json!({"hold":store.state.holds.get(&hold.hold_id),"active_or_uncertain_allocations":active,"new_admission_blocked":true,"future_guarded_dispatch_blocked":true,"unstarted_dispatch_blocked":pending.is_empty(),"previously_dispatched_pending_readback":pending,"drained":false,"coverage":"participating future dispatch calls are excluded; an earlier native/daemon queue remains uncertain until owner reconciliation; runtime/domain holds must be joined by maintenance owner"})
        }
        StoredAction::ReleaseHold => {
            store.state.release_hold(
                field(&v, "hold_id")?,
                field(&v, "owner_ref")?,
                revision(&v)?,
            )?;
            json!({"hold_released":true,"resumed":false,"other_holds_preserved":true,"automation_may_become_eligible":true})
        }
        StoredAction::Release => {
            if let Some(binding) = store
                .state
                .allocations
                .get(field(&v, "allocation_id")?)
                .and_then(|a| a.runtime.as_ref())
            {
                native::verify_runtime_exited(binding)?;
            }
            store.state.release(
                field(&v, "allocation_id")?,
                revision(&v)?,
                decode(&v, "evidence")?,
            )?;
            let ids = store.state.schedule(now, &boot)?;
            json!({"released":true,"newly_admitted":ids,"launched":false})
        }
        StoredAction::ReconcileStep => {
            let id = field(&v, "allocation_id")?;
            let a = store
                .state
                .allocations
                .get(id)
                .ok_or("allocation not found")?;
            if a.revision != revision(&v)? || a.request.owner_ref != field(&v, "owner_ref")? {
                return Err("stale or foreign step reconciliation".into());
            }
            store.state.finish_step(
                id,
                field(&v, "step_id")?,
                decode(&v, "outcome")?,
                field(&v, "evidence_ref")?,
            )?;
            json!({"allocation":store.state.allocations.get(id),"released":false})
        }
        StoredAction::BindRuntime => {
            let id = field(&v, "allocation_id")?;
            store.state.bind_runtime(
                id,
                revision(&v)?,
                field(&v, "owner_ref")?,
                decode(&v, "binding")?,
            )?;
            json!({"allocation":store.state.allocations.get(id)})
        }
        StoredAction::AdoptRuntime => {
            let request: Request = decode(&v, "request")?;
            let id = request.allocation_id.clone();
            changed = store.state.adopt_runtime(
                field(&v, "expected_instance_id")?,
                decode(&v, "expected_policy_revision")?,
                request,
                decode(&v, "binding")?,
                field(&v, "evidence_ref")?,
            )?;
            let allocation = &store.state.allocations[&id];
            let holds: Vec<_> = store
                .state
                .holds
                .values()
                .filter(|h| store.state.scope_matches(&h.scope, &allocation.request))
                .map(|h| h.hold_id.clone())
                .collect();
            let pending = allocation.phase != Phase::Released
                && allocation.request.demand.keys().any(|key| {
                    let policy = &store.state.policy.resources[key];
                    !store.state.resource_observations.get(key).is_some_and(|o| {
                        o.boot_id == boot
                            && o.observed_at_ms <= now
                            && now - o.observed_at_ms <= policy.observation_max_age_ms
                            && o.unavailable.is_none()
                            && o.accounted_revisions.get(&id) == Some(&allocation.revision)
                            && o.accounted_runtimes.get(&id) == Some(&allocation.runtime)
                            && o.managed_demand.contains_key(&id)
                    })
                });
            json!({"adopted":changed,"existing_original_outcome":!changed,
                "allocation":allocation,"launched":false,"resumed":false,
                "new_work_authorized":false,"matching_hold_ids":holds,
                "fresh_accounting_pending":pending,
                "runtime_present":allocation.runtime.as_ref().is_some_and(|b|native::validate_binding(b).is_ok()),
                "coverage":"Existing authorized native obligation only. No launch/admission receipt; future dispatch and independent domain lifecycle retain their actual owners. Temporary conservative double counting persists until fresh binding observations."})
        }
        StoredAction::MaintenancePrepare => {
            let m: Maintenance = decode(&v, "maintenance")?;
            let id = m.operation_id.clone();
            store.state.prepare_maintenance(m)?;
            json!({"maintenance":store.state.maintenance.get(&id),"drained":false,"coverage":"capacity admission/dispatch; independently controlled runtimes/update actors require their owning holds and evidence"})
        }
        StoredAction::MaintenanceRecord => {
            let id = field(&v, "operation_id")?;
            store.state.record_maintenance(
                id,
                revision(&v)?,
                field(&v, "owner_ref")?,
                field(&v, "stage")?,
                decode(&v, "evidence_refs")?,
            )?;
            json!({"maintenance":store.state.maintenance.get(id)})
        }
        StoredAction::MaintenanceFinish => {
            let id = field(&v, "operation_id")?;
            store
                .state
                .finish_maintenance(id, revision(&v)?, field(&v, "owner_ref")?)?;
            json!({"maintenance":store.state.maintenance.get(id),"resumed":false,"other_holds_preserved":true})
        }
    };
    if changed {
        store.save()?;
    }
    Ok(response)
}
fn envelope(result: Result<Value>) -> Value {
    match result {
        Ok(data) => json!({"ok":true,"data":data}),
        Err(error) => {
            json!({"ok":false,"error":error,"retry":"Inspect the exact original allocation/hold and reconcile; do not fetch a fresh revision and replay old intent."})
        }
    }
}

struct StateChanges(fs::File);
impl StateChanges {
    fn open(root: &Path) -> Result<Self> {
        let fd = unsafe { libc::inotify_init1(libc::IN_CLOEXEC | libc::IN_NONBLOCK) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        let file: fs::File = unsafe { std::os::fd::FromRawFd::from_raw_fd(fd) };
        let path = std::ffi::CString::new(root.as_os_str().as_encoded_bytes())
            .map_err(|e| e.to_string())?;
        if unsafe {
            libc::inotify_add_watch(
                file.as_raw_fd(),
                path.as_ptr(),
                libc::IN_MOVED_TO | libc::IN_DELETE_SELF | libc::IN_MOVE_SELF,
            )
        } < 0
        {
            return Err(std::io::Error::last_os_error().to_string());
        }
        Ok(Self(file))
    }

    fn wait(&self, timeout: Option<Duration>) -> Result<()> {
        let mut poll = libc::pollfd {
            fd: self.0.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        let timeout = timeout
            .map(|t| t.as_millis().min(i32::MAX as u128) as i32)
            .unwrap_or(-1);
        let result = unsafe { libc::poll(&mut poll, 1, timeout) };
        if result < 0 && std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted {
            return Err(std::io::Error::last_os_error().to_string());
        }
        if result > 0 {
            let mut bytes = [0u8; 4096];
            unsafe {
                libc::read(self.0.as_raw_fd(), bytes.as_mut_ptr().cast(), bytes.len());
            }
        }
        Ok(())
    }
}

fn follow(root: &Path, v: &Value) -> Result<Value> {
    let after_source = field(v, "after_source")?;
    let after_revision = v
        .get("after_revision")
        .and_then(Value::as_u64)
        .ok_or("required after_revision")?;
    let timeout = v
        .get("timeout_ms")
        .and_then(Value::as_u64)
        .ok_or("required timeout_ms")?;
    if timeout > 30000 {
        return Err("follow timeout must be 0..30000ms".into());
    }
    let changes = StateChanges::open(root)?;
    let start = Instant::now();
    loop {
        let store = Store::open(root)?;
        let source_changed = store.state.instance_id != after_source;
        let changed = store.state.revision != after_revision;
        if source_changed || changed || start.elapsed() >= Duration::from_millis(timeout) {
            return Ok(
                json!({"scheduling":store.state.scheduling,"resource_preferences":store.state.resource_preferences,"preference_revision":store.state.preference_revision(),"state":store.state,"source_changed":source_changed,"changed":changed,"timed_out":!source_changed&&!changed,"coverage":"current complete capacity snapshot, not a historical event log; source replacement requires reacquisition","passive":true}),
            );
        }
        drop(store);
        changes.wait(Some(
            Duration::from_millis(timeout).saturating_sub(start.elapsed()),
        ))?;
    }
}
fn read_json(mut input: impl Read) -> Result<Value> {
    let mut data = String::new();
    input
        .by_ref()
        .take(1024 * 1024 + 1)
        .read_to_string(&mut data)
        .map_err(|e| e.to_string())?;
    if data.len() > 1024 * 1024 {
        return Err("request exceeds 1MiB".into());
    }
    serde_json::from_str(&data).map_err(|e| e.to_string())
}
fn text(result: &Value) -> String {
    serde_json::to_string_pretty(result).unwrap()
}
fn state_root(args: &mut Vec<String>) -> Result<PathBuf> {
    let i = args
        .iter()
        .take_while(|s| *s != "--")
        .position(|s| s == "--state-root");
    let root = if let Some(i) = i {
        if i + 1 >= args.len() {
            return Err("missing --state-root".into());
        }
        let p = args.remove(i + 1);
        args.remove(i);
        p
    } else {
        std::env::var("APEIROS_CAPACITY_STATE_ROOT")
            .map_err(|_| "explicit --state-root or APEIROS_CAPACITY_STATE_ROOT required")?
    };
    let root = PathBuf::from(root);
    if !root.is_absolute() {
        return Err("state root must be absolute".into());
    }
    Ok(root)
}
fn arg(args: &[String], name: &str) -> Result<String> {
    let i = args
        .iter()
        .take_while(|s| *s != "--")
        .position(|v| v == name)
        .ok_or_else(|| format!("missing {name}"))?;
    args.get(i + 1)
        .cloned()
        .ok_or_else(|| format!("missing {name} value"))
}

#[derive(Default)]
struct DispatchWaitBinding {
    binding: Option<(String, Value)>,
    admitted_sequence: Option<u64>,
}
impl DispatchWaitBinding {
    // The same exact identity rules apply both to passive readiness and to the
    // fresh exclusive recheck. Only that second call can authorize dispatch.
    fn ready(
        &mut self,
        state: &State,
        id: &str,
        revision: u64,
        step: &str,
        boot: &str,
    ) -> Result<Option<u64>> {
        let a = state.allocations.get(id).ok_or("allocation not found")?;
        if a.request.allocation_id != id || a.steps.contains_key(step) {
            return Err("dispatch identity or step changed; reconcile original effect".into());
        }
        let mut identity = serde_json::to_value(a).map_err(|e| e.to_string())?;
        if revision == 1 {
            let untouched = a.steps.is_empty()
                && a.runtime.is_none()
                && a.adoption.is_none()
                && a.release_evidence.is_none();
            match (a.revision, &a.phase, a.admitted_sequence) {
                (1, Phase::Waiting, None) if untouched && self.admitted_sequence.is_none() => {}
                (2, Phase::Admitted, Some(sequence))
                    if untouched
                        && sequence > a.queued_sequence
                        && self.admitted_sequence.is_none_or(|known| known == sequence) =>
                {
                    self.admitted_sequence = Some(sequence);
                }
                _ => return Err("waiting allocation changed outside initial admission 1->2".into()),
            }
            let fields = identity
                .as_object_mut()
                .ok_or("invalid allocation identity")?;
            fields.remove("revision");
            fields.remove("phase");
            fields.remove("admitted_sequence");
        } else if a.revision != revision || !matches!(a.phase, Phase::Admitted | Phase::Active) {
            return Err("stale or non-dispatchable allocation while waiting".into());
        }
        match &self.binding {
            Some((source, original)) if *source != state.instance_id || *original != identity => {
                return Err("capacity source or allocation changed while waiting".into());
            }
            None => self.binding = Some((state.instance_id.clone(), identity)),
            _ => {}
        }
        let assessment = state.assess(&a.request, now_ms(), &boot)?;
        if !assessment.eligible {
            return Err(format!(
                "waiting request is ineligible: {}",
                assessment.reasons.join("; ")
            ));
        }
        if matches!(a.phase, Phase::Admitted | Phase::Active) && assessment.can_admit {
            return Ok(Some(a.revision));
        }
        Ok(None)
    }
}

fn dispatch_wait_store(root: &Path, id: &str, revision: u64, step: &str) -> Result<(Store, u64)> {
    if step.trim().is_empty() {
        return Err("empty step_id".into());
    }
    // Subscribe before capture: admission between observation and sleep cannot
    // be missed. Waiting is passive; it never schedules or records a step.
    let changes = StateChanges::open(root)?;
    let boot = boot_id()?;
    let mut binding = DispatchWaitBinding::default();
    let mut reported = false;
    loop {
        let captured = Store::snapshot(root)?;
        let ready = binding.ready(&captured, id, revision, step, &boot)?;
        drop(captured);
        if ready.is_some() {
            let store = Store::open(root)?;
            // Another decision may have changed holds, fit, identity or the
            // original admission while this process joined the exclusive lock.
            if let Some(actual_revision) = binding.ready(&store.state, id, revision, step, &boot)? {
                // Keep this lock through begin_step and close-on-exec. Later
                // failures retain the original effect; they never re-enter wait.
                return Ok((store, actual_revision));
            }
        }
        if !reported {
            eprintln!("capacity dispatch waiting for {id}; no step or runtime binding recorded");
            reported = true;
        }
        // Default SIGTERM/SIGINT stop the unmutated waiter. No new attempt,
        // release, repeated effect or elapsed-time permission follows.
        changes.wait(None)?;
    }
}

fn dispatch(root: &Path, args: &[String], guard: bool) -> Result<Value> {
    let id = arg(args, "--allocation-id")?;
    let rev: u64 = arg(args, "--revision")?
        .parse()
        .map_err(|_| "invalid revision")?;
    let step = arg(args, "--step-id")?;
    let split = args
        .iter()
        .position(|v| v == "--")
        .ok_or("dispatch requires -- and actual owner command")?;
    let argv = &args[split + 1..];
    if argv.is_empty() {
        return Err("empty owner command".into());
    }
    let waiting = args[..split].iter().any(|arg| arg == "--wait");
    let supervisor = args[..split]
        .iter()
        .position(|arg| arg == "--supervisor-ref")
        .map(|position| {
            args[..split]
                .get(position + 1)
                .filter(|value| !value.trim().is_empty() && !value.starts_with("--"))
                .cloned()
                .ok_or_else(|| "missing --supervisor-ref value".to_string())
        })
        .transpose()?;
    if guard && (waiting || supervisor.is_some()) {
        return Err("--wait and --supervisor-ref belong only to native exec dispatch".into());
    }
    let timeout = if guard {
        let t: u64 = arg(args, "--timeout-ms")?
            .parse()
            .map_err(|_| "invalid timeout")?;
        if t == 0 || t > 10_000 {
            return Err("guard timeout must be 1..10000ms".into());
        }
        t
    } else {
        0
    };
    let (mut store, rev) = if waiting {
        dispatch_wait_store(root, &id, rev, &step)?
    } else {
        (Store::open(root)?, rev)
    };
    let owner_process = process(std::process::id())?;
    if guard {
        store.state.begin_control_step(
            &id,
            rev,
            &step,
            owner_process,
            arg(args, "--target-ref")?,
        )?;
    } else {
        store
            .state
            .begin_step(&id, rev, &step, owner_process.clone())?;
        if let Some(supervisor_ref) = supervisor {
            let cgroup = fs::read_to_string("/proc/self/cgroup")
                .map_err(|e| e.to_string())?
                .lines()
                .find_map(|line| line.strip_prefix("0::"))
                .ok_or("native exec requires an actual unified cgroup")?
                .to_string();
            let allocation = &store.state.allocations[&id];
            let revision = allocation.revision;
            let owner = allocation.request.owner_ref.clone();
            store.state.bind_runtime(
                &id,
                revision,
                &owner,
                RuntimeBinding {
                    process: owner_process,
                    cgroup,
                    supervisor_ref,
                },
            )?;
        }
    }
    store.save()?;
    if !guard {
        // The lock is close-on-exec. Holding it until exec prevents hold/launch
        // check-use races; failure leaves the persisted attempt for reconciliation.
        let error = Command::new(&argv[0]).args(&argv[1..]).exec();
        store.state.finish_step(
            &id,
            &step,
            StepOutcome::Failed,
            "native exec returned before program replacement",
        )?;
        store.save()?;
        return Err(format!("exec did not replace owner process: {error}"));
    }
    // Only bounded local native control effects belong here, never computation,
    // checkpoint drains or remote waits. A timeout never releases the allocation.
    let log_dir = root.join("control-output");
    fs::create_dir_all(&log_dir).map_err(|e| e.to_string())?;
    fs::set_permissions(&log_dir, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    let log_path = log_dir.join(format!("{}-{}.log", std::process::id(), now_ms()));
    let log = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(&log_path)
        .map_err(|e| e.to_string())?;
    let mut child = match Command::new(&argv[0])
        .args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log)
        .spawn()
    {
        Ok(child) => child,
        Err(e) => {
            store.state.finish_step(
                &id,
                &step,
                StepOutcome::Failed,
                "native control child did not spawn",
            )?;
            store.save()?;
            return Err(e.to_string());
        }
    };
    let started = Instant::now();
    let (outcome, code) = loop {
        let status = match child.try_wait() {
            Ok(status) => status,
            Err(_) => break (StepOutcome::Uncertain, None),
        };
        if let Some(status) = status {
            break (
                if status.success() {
                    StepOutcome::Applied
                } else {
                    StepOutcome::Uncertain
                },
                status.code(),
            );
        }
        if started.elapsed() >= Duration::from_millis(timeout) {
            let _ = child.kill();
            let _ = child.wait();
            break (StepOutcome::Uncertain, None);
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let persistence_error = store
        .state
        .finish_step(
            &id,
            &step,
            outcome.clone(),
            log_path.to_string_lossy().as_ref(),
        )
        .and_then(|()| store.save())
        .err();
    Ok(
        json!({"child_launched":true,"outcome":outcome,"exit_code":code,"evidence_ref":log_path,"allocation":store.state.allocations.get(&id),"resources_released":false,"domain_readback_required":true,"state_persistence_error":persistence_error}),
    )
}

fn serve(root: &Path) -> Result<()> {
    // Singleton lifetime is independent of short state/dispatch locks.
    let lock = OpenOptions::new()
        .create(true)
        .write(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(root.join("service.lock"))
        .map_err(|e| e.to_string())?;
    if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err("capacity service already owns this state".into());
    }
    let socket = root.join("capacity.sock");
    if socket.exists() {
        if UnixStream::connect(&socket).is_ok() {
            return Err("live socket exists without matching service lock".into());
        }
        fs::remove_file(&socket).map_err(|e| e.to_string())?;
    }
    let listener = UnixListener::bind(&socket).map_err(|e| e.to_string())?;
    fs::set_permissions(&socket, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    let observer_root = root.to_path_buf();
    std::thread::spawn(move || {
        let mut previous_cpu_policy = Value::Null;
        let mut previous_io_policy = Value::Null;
        loop {
            if let Ok(store) = Store::open(&observer_root) {
                let observed = native::cpu_policy(&store.state.native_policy(), true);
                if observed != previous_cpu_policy {
                    eprintln!("capacity selected CPU policy: {}", observed);
                    previous_cpu_policy = observed;
                }
                let io = storage::controls(&store.state.policy, true);
                if io != previous_io_policy {
                    eprintln!("capacity selected I/O policy: {}", io);
                    previous_io_policy = io;
                }
            }
            if let Err(e) = handle(&observer_root, json!({"action":"observe_native"})) {
                eprintln!("capacity native observation unavailable: {e}");
            }
            std::thread::sleep(Duration::from_secs(5));
        }
    });
    let pressure_root = root.to_path_buf();
    std::thread::spawn(move || {
        loop {
            if let Err(error) = pressure::tick(&pressure_root) {
                eprintln!("capacity domain pressure reconciliation: {error}");
            }
            std::thread::sleep(Duration::from_secs(5));
        }
    });
    // A private local interface with a bounded request lifetime and worker count.
    let clients = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    for stream in listener.incoming() {
        match stream {
            Ok(mut stream) => {
                if clients.fetch_add(1, std::sync::atomic::Ordering::SeqCst) >= 16 {
                    clients.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
                    continue;
                }
                let clients = clients.clone();
                let root = root.to_path_buf();
                std::thread::spawn(move || {
                    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
                    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
                    let response = serve_request(&root, &mut stream);
                    if let Err(error) = response {
                        eprintln!("capacity request: {error}");
                    }
                    clients.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
                });
            }
            Err(e) => return Err(e.to_string()),
        }
    }
    Ok(())
}
fn serve_request(root: &Path, stream: &mut UnixStream) -> Result<()> {
    let mut reader = BufReader::new(stream.try_clone().map_err(|e| e.to_string())?);
    let mut first = String::new();
    reader.read_line(&mut first).map_err(|e| e.to_string())?;
    if first != "POST /v1/capacity HTTP/1.1\r\n" && first != "GET /v1/capacity HTTP/1.1\r\n" {
        return Err("only GET/POST /v1/capacity HTTP/1.1 supported".into());
    }
    let mut length = 0;
    let mut header_bytes = first.len();
    loop {
        let mut line = String::new();
        reader.read_line(&mut line).map_err(|e| e.to_string())?;
        header_bytes += line.len();
        if header_bytes > 8192 {
            return Err("headers too large".into());
        }
        if line == "\r\n" {
            break;
        }
        if line.is_empty() {
            return Err("incomplete HTTP headers".into());
        }
        if let Some((name, value)) = line.split_once(':') {
            if name.eq_ignore_ascii_case("Content-Length") {
                length = value
                    .trim()
                    .parse::<usize>()
                    .map_err(|_| "invalid content-length")?;
            }
            if name.eq_ignore_ascii_case("Transfer-Encoding") {
                return Err("transfer encoding unsupported".into());
            }
        }
    }
    if length > 1024 * 1024 {
        return Err("request too large".into());
    }
    let request = if first.starts_with("GET ") {
        json!({"action":"inspect"})
    } else {
        let mut bytes = vec![0; length];
        reader.read_exact(&mut bytes).map_err(|e| e.to_string())?;
        serde_json::from_slice(&bytes).map_err(|e| e.to_string())?
    };
    let result = envelope(handle(root, request));
    let bytes = serde_json::to_vec(&result).unwrap();
    write!(stream,"HTTP/1.1 {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",if result["ok"]==true{"200 OK"}else{"409 Conflict"},bytes.len()).map_err(|e|e.to_string())?;
    stream.write_all(&bytes).map_err(|e| e.to_string())
}
fn resident_request(root: &Path, request: &Value) -> Result<Value> {
    // A long-lived MCP executable can outlive several resident upgrades. Never
    // deserialize State here or fall back to the embedded operation handler.
    let metadata = fs::symlink_metadata(root).map_err(|e| e.to_string())?;
    if !metadata.is_dir() || metadata.permissions().mode() & 0o077 != 0 {
        return Err("resident state directory must be private mode 0700".into());
    }
    let bytes = serde_json::to_vec(request).map_err(|e| e.to_string())?;
    if bytes.len() > 1024 * 1024 {
        return Err("request exceeds 1MiB".into());
    }
    let mut stream = UnixStream::connect(root.join("capacity.sock"))
        .map_err(|e| format!("capacity resident unavailable before request: {e}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(35)))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .map_err(|e| e.to_string())?;
    let response = (|| -> Result<Value> {
        write!(stream, "POST /v1/capacity HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", bytes.len()).map_err(|e|e.to_string())?;
        stream.write_all(&bytes).map_err(|e| e.to_string())?;
        stream
            .shutdown(std::net::Shutdown::Write)
            .map_err(|e| e.to_string())?;
        let mut reader = BufReader::new(stream);
        let mut header_bytes = 0;
        let mut length = None;
        let mut status = None;
        loop {
            let mut line = String::new();
            reader
                .by_ref()
                .take((8193 - header_bytes) as u64)
                .read_line(&mut line)
                .map_err(|e| e.to_string())?;
            header_bytes += line.len();
            if header_bytes > 8192 || line.is_empty() {
                return Err("invalid/incomplete resident HTTP headers".into());
            }
            if status.is_none() {
                status = Some(match line.as_str() {
                    "HTTP/1.1 200 OK\r\n" => true,
                    "HTTP/1.1 409 Conflict\r\n" => false,
                    _ => return Err("unexpected resident HTTP status".into()),
                });
            } else if line == "\r\n" {
                break;
            } else if let Some((name, value)) = line.split_once(':') {
                if name.eq_ignore_ascii_case("Content-Length") {
                    if length.is_some() {
                        return Err("duplicate resident content-length".into());
                    }
                    length = Some(
                        value
                            .trim()
                            .parse::<usize>()
                            .map_err(|_| "invalid resident content-length")?,
                    );
                }
                if name.eq_ignore_ascii_case("Transfer-Encoding") {
                    return Err("resident transfer encoding unsupported".into());
                }
            }
        }
        let length = length.ok_or("resident content-length required")?;
        if length > 32 * 1024 * 1024 {
            return Err("resident response exceeds 32MiB".into());
        }
        let mut body = vec![0; length];
        reader.read_exact(&mut body).map_err(|e| e.to_string())?;
        let result: Value = serde_json::from_slice(&body).map_err(|e| e.to_string())?;
        if result["ok"].as_bool() != status {
            return Err("resident response envelope/status mismatch".into());
        }
        Ok(result)
    })();
    response.map_err(|e| format!("capacity resident reply unavailable; original operation may have applied: {e}. Reconcile original identities before any retry; no local fallback or automatic replay."))
}

fn mcp(root: &Path) -> Result<()> {
    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let line = line.map_err(|e| e.to_string())?;
        if line.len() > 1024 * 1024 {
            return Err("MCP request too large".into());
        }
        let message: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
        let Some(id) = message.get("id") else {
            continue;
        };
        let result = match message["method"].as_str().unwrap_or("") {
            "initialize" => {
                json!({"protocolVersion":"2025-03-26","capabilities":{"tools":{}},"serverInfo":{"name":"apeiros-capacity","version":env!("CARGO_PKG_VERSION")}})
            }
            "ping" => json!({}),
            "tools/list" => {
                json!({"tools":[{"name":"capacity","description":"Start with action=overview for focused current capacity, waiting causes and exact detail routes, or action=help for offline action discovery. inspect with an explicit view reads bounded current/history/allocation/policy/holds/pressure/gpu_pressure/maintenance details. inspect without view and legacy follow are complete-state machine snapshots, not routine overview. Assess/admit enrolled capacity; coordinate exact holds/maintenance through the existing owner. Qualified pressure controllers retain domain save/release/resume ownership. Reads never launch work; other effects/prerequisites are in action help. Never replay stale intent with a fresh revision; actual native/domain evidence is required for release.","inputSchema":serde_json::from_str::<Value>(include_str!("../request.schema.json")).expect("capacity input schema")}]})
            }
            "tools/call" if message["params"]["name"] == "capacity" => {
                let request = &message["params"]["arguments"];
                let result = if request["action"] == "help" {
                    envelope(observation::help(request))
                } else {
                    resident_request(root, request).unwrap_or_else(|error| envelope(Err(error)))
                };
                json!({"content":[{"type":"text","text":text(&result)}],"isError":result["ok"]!=true})
            }
            _ => {
                println!(
                    "{}",
                    json!({"jsonrpc":"2.0","id":id,"error":{"code":-32601,"message":"method not found"}})
                );
                continue;
            }
        };
        println!("{}", json!({"jsonrpc":"2.0","id":id,"result":result}));
    }
    Ok(())
}
fn usage() -> Value {
    serde_json::from_str(include_str!("../usage.json")).expect("embedded capacity usage")
}

fn usage_requested(args: &mut Vec<String>) -> Result<bool> {
    let mut help = args.is_empty();
    let mut saw_verb = false;
    let mut index = 0;
    while let Some(token) = args.get(index) {
        match token.as_str() {
            "--" => break,
            "--help" | "-h" => help = true,
            "--state-root" | "--allocation-id" | "--revision" | "--step-id" | "--target-ref"
            | "--timeout-ms" | "--supervisor-ref" => {
                if args.get(index + 1).is_some_and(|value| value != "--") {
                    index += 1;
                }
            }
            value if value == "--format" || value.starts_with("--format=") => {
                let (format, count) = match value.strip_prefix("--format=") {
                    Some(inline) => (Some(inline), 1),
                    None => (args.get(index + 1).map(String::as_str), 2),
                };
                if !matches!(format, Some("json" | "text")) {
                    return Err("--format requires json or text".into());
                }
                args.drain(index..index + count);
                continue;
            }
            verb if !saw_verb && !verb.starts_with('-') => {
                saw_verb = true;
                help |= verb == "usage";
            }
            _ => {}
        }
        index += 1;
    }
    Ok(help)
}

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let result = (|| {
        if usage_requested(&mut args)? {
            println!("{}", text(&usage()));
            return Ok(Value::Null);
        }
        if let [verb, selection] = args.as_slice()
            && verb == pressure_succession::ROUTER
        {
            // exec preserves the callback PID and the existing timeout/kill
            // contract. Replay only the very same validated JSON request on stdin.
            use std::io::{Seek, SeekFrom};
            use std::os::fd::FromRawFd;
            let request = read_json(std::io::stdin())?;
            let argv = pressure_succession::destination(selection, &request)?;
            let fd = unsafe {
                libc::memfd_create(c"capacity-pressure-request".as_ptr(), libc::MFD_CLOEXEC)
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().to_string());
            }
            let mut body = unsafe { std::fs::File::from_raw_fd(fd) };
            serde_json::to_writer(&mut body, &request).map_err(|e| e.to_string())?;
            body.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
            return Err(Command::new(&argv[0])
                .args(&argv[1..])
                .stdin(body)
                .exec()
                .to_string());
        }
        let root = state_root(&mut args)?;
        match args.first().map(String::as_str) {
            Some("request") => handle(&root, read_json(std::io::stdin())?),
            Some("serve") => {
                serve(&root)?;
                Ok(json!({"stopped":true}))
            }
            Some("mcp") => {
                mcp(&root)?;
                return Ok(Value::Null);
            }
            Some("dispatch") => dispatch(&root, &args, false),
            Some("guard") => dispatch(&root, &args, true),
            _ => Err("unknown verb; use usage".into()),
        }
    })();
    let failed = result.is_err();
    let result = envelope(result);
    if failed || result["data"] != Value::Null {
        if args.first().is_some_and(|s| s == "dispatch") {
            eprintln!("{}", text(&result));
        } else {
            println!("{}", text(&result));
        }
    }
    if failed {
        std::process::exit(
            if args
                .first()
                .is_some_and(|s| s == "dispatch" || s == "guard")
            {
                125
            } else {
                2
            },
        );
    }
}
