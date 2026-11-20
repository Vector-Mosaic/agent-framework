use serde_json::{Value, json};
use std::fs;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use apeiros_capacity::*;

/// Read-only native fixture: no cgroup creation, settings, workload or service.
/// The test process must already have a non-root accounting domain.
fn fixture() -> (State, Request, RuntimeBinding) {
    let actual = fs::read_to_string("/proc/self/cgroup").unwrap();
    let actual = actual.lines().find_map(|l| l.strip_prefix("0::")).unwrap();
    let mut group = PathBuf::from(actual);
    loop {
        let native = PathBuf::from(format!("/sys/fs/cgroup{}", group.display()));
        if fs::read_to_string(native.join("cgroup.type"))
            .is_ok_and(|v| matches!(v.trim(), "domain" | "domain threaded"))
            && native.join("memory.current").is_file()
        {
            break;
        }
        assert!(
            group.pop(),
            "the test process needs a delegated cgroup v2 memory accounting domain"
        );
    }
    let parent = group.parent().unwrap().to_str().unwrap();
    assert_ne!(parent, "/", "run native fixture below a real aggregate");
    let idle = fs::read_to_string(format!("/sys/fs/cgroup{parent}/cpu.idle")).unwrap();
    let priority = if idle.trim() == "1" {
        "normal"
    } else {
        "priority"
    };
    let cpus: Vec<u32> = fs::read_to_string("/sys/devices/system/cpu/online")
        .unwrap()
        .trim()
        .split(',')
        .flat_map(|part| {
            if let Some((a, b)) = part.split_once('-') {
                (a.parse().unwrap()..=b.parse().unwrap()).collect::<Vec<u32>>()
            } else {
                vec![part.parse().unwrap()]
            }
        })
        .collect();
    let ram: u64 = fs::read_to_string("/proc/meminfo")
        .unwrap()
        .lines()
        .find(|l| l.starts_with("MemTotal:"))
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap()
        .parse::<u64>()
        .unwrap()
        * 1024;
    let policy = serde_json::from_value(json!({
        "host_id":"fixture-host","machine_id":machine_id().unwrap(),
        "resources":{
            "ram":{"unit":"bytes","capacity":ram,"operational_margin":1,"priority_growth_margin":1,"observation_max_age_ms":60000,"native":{"kind":"memory"}},
            "cpu":{"unit":"millicores","capacity":cpus.len()*1000,"operational_margin":1,"priority_growth_margin":1,"observation_max_age_ms":60000,"native":{"kind":"cpu","eligible_cpus":cpus}}
        },
        "cpu_aggregates":{"fixture":{"cgroup":parent,"priority":priority,"control_owner_ref":"native fixture parent"}}
    })).unwrap();
    let request = serde_json::from_value(json!({
        "allocation_id":"existing-native","host_id":"fixture-host","system_id":"fixture",
        "owner_ref":"native fixture owner","operation_id":"existing test invocation",
        "attempt_id":format!("pid-{}", std::process::id()),
        "source_ref":"capacity test build","configuration_ref":"native test invocation",
        "priority":priority,"demand":{"ram":1024,"cpu":1},
        "eligibility_ref":"read-only native bookkeeping fixture","release_route":"actual native exit"
    })).unwrap();
    let binding = RuntimeBinding {
        process: process(std::process::id()).unwrap(),
        cgroup: group.to_str().unwrap().into(),
        supervisor_ref: "existing native test invocation".into(),
    };
    (State::new(policy).unwrap(), request, binding)
}

fn adopt(state: &mut State, request: Request, binding: RuntimeBinding) -> Result<bool> {
    let instance = state.instance_id.clone();
    state.adopt_runtime(
        &instance,
        state.policy_revision,
        request,
        binding,
        "native owner observation",
    )
}

#[test]
fn adoption_records_real_obligation_under_hold_and_pressure_without_a_grant() {
    let (mut state, mut request, binding) = fixture();
    request
        .demand
        .insert("ram".into(), state.policy.resources["ram"].capacity + 1);
    state.hold(serde_json::from_value(json!({
        "hold_id":"independent-hold","owner_ref":"fixture maintenance","reason":"do not dispatch",
        "scope":{"host_id":"fixture-host"},"revision":0
    })).unwrap()).unwrap();
    let holds = serde_json::to_value(&state.holds).unwrap();
    assert!(adopt(&mut state, request, binding).unwrap());
    let a = &state.allocations["existing-native"];
    assert_eq!(a.phase, Phase::Active);
    assert!(a.steps.is_empty());
    assert!(a.admitted_sequence.is_none());
    assert!(a.adoption.is_some());
    assert!(state.last_turn.is_empty());
    assert_eq!(serde_json::to_value(&state.holds).unwrap(), holds);
    assert!(
        state
            .begin_step(
                "existing-native",
                a.revision,
                "held-new-work",
                process(std::process::id()).unwrap()
            )
            .is_err()
    );
    assert!(state.allocations["existing-native"].steps.is_empty());
}

#[test]
fn adoption_rejects_changed_identity_and_overlap_without_partial_state() {
    let (initial, request, binding) = fixture();
    let instance = initial.instance_id.clone();
    for case in 0..8 {
        let mut state = initial.clone();
        let mut r = request.clone();
        let mut b = binding.clone();
        let expected = if case == 0 {
            "foreign instance"
        } else {
            &instance
        };
        let revision = if case == 1 { 1 } else { 0 };
        match case {
            2 => b.process.boot_id = "another boot".into(),
            3 => b.process.start_ticks += 1,
            4 => r.host_id = "another host".into(),
            5 => {
                r.demand.insert("unknown".into(), 1);
            }
            6 => {
                r.demand.remove("cpu");
            }
            7 => b.cgroup = state.policy.cpu_aggregates["fixture"].cgroup.clone(),
            _ => (),
        }
        let before = serde_json::to_value(&state).unwrap();
        assert!(
            state
                .adopt_runtime(expected, revision, r, b, "native owner observation")
                .is_err(),
            "case {case}"
        );
        assert_eq!(serde_json::to_value(&state).unwrap(), before);
    }
    let mut state = initial;
    assert!(adopt(&mut state, request.clone(), binding.clone()).unwrap());
    let before = serde_json::to_value(&state).unwrap();
    let mut duplicate = request;
    duplicate.allocation_id = "double-counted-native".into();
    assert!(
        adopt(&mut state, duplicate, binding)
            .unwrap_err()
            .contains("counted twice")
    );
    assert_eq!(serde_json::to_value(&state).unwrap(), before);
}

#[test]
fn adoption_replay_preserves_original_policy_and_terminal_disposition() {
    let (mut state, request, binding) = fixture();
    let instance = state.instance_id.clone();
    assert!(adopt(&mut state, request.clone(), binding.clone()).unwrap());
    state
        .reconfigure_policy(0, state.policy.clone(), "later policy decision".into())
        .unwrap();
    let before = serde_json::to_value(&state).unwrap();
    assert!(
        !state
            .adopt_runtime(
                &instance,
                0,
                request.clone(),
                binding.clone(),
                "native owner observation"
            )
            .unwrap()
    );
    assert_eq!(serde_json::to_value(&state).unwrap(), before);
    assert!(
        state
            .adopt_runtime(
                &instance,
                1,
                request.clone(),
                binding.clone(),
                "native owner observation"
            )
            .is_err()
    );
    assert!(
        state
            .adopt_runtime(
                &instance,
                0,
                request.clone(),
                binding.clone(),
                "different evidence"
            )
            .is_err()
    );
    // Synthetic already-reconciled terminal record, not a native exit claim.
    state.allocations.get_mut("existing-native").unwrap().phase = Phase::Released;
    let terminal = serde_json::to_value(&state).unwrap();
    assert!(
        !state
            .adopt_runtime(&instance, 0, request, binding, "native owner observation")
            .unwrap()
    );
    assert_eq!(serde_json::to_value(&state).unwrap(), terminal);
}

#[test]
fn adopted_runtime_cannot_be_rebound_or_released_while_live() {
    let (mut state, request, binding) = fixture();
    assert!(adopt(&mut state, request.clone(), binding.clone()).unwrap());
    let a = &state.allocations["existing-native"];
    let revision = a.revision;
    let mut changed = binding.clone();
    changed.supervisor_ref = "replacement native owner".into();
    assert!(
        state
            .bind_runtime("existing-native", revision, &request.owner_ref, changed)
            .unwrap_err()
            .contains("immutable")
    );
    let evidence = ReleaseEvidence {
        owner_ref: request.owner_ref,
        attempt_id: request.attempt_id,
        boot_id: binding.process.boot_id,
        continuation_ref: None,
        execution_exited_ref: "false exit assertion".into(),
        resources_released_ref: "false release assertion".into(),
        no_successor_ref: "fixture".into(),
    };
    assert!(
        state
            .release("existing-native", revision, evidence)
            .unwrap_err()
            .contains("still exists")
    );
    assert_eq!(state.allocations["existing-native"].phase, Phase::Active);
    assert_eq!(state.allocations["existing-native"].revision, revision);
}

struct PrivateState(PathBuf);
impl Drop for PrivateState {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn rpc(root: &Path, request: Value) -> Value {
    let mut child = Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
        .args(["--state-root", root.to_str().unwrap(), "request"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(&serde_json::to_vec(&request).unwrap())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stdout)
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

#[test]
fn shared_handler_persists_adoption_and_reconciles_exact_duplicate() {
    let (state, request, binding) = fixture();
    let nonce = fs::read_to_string("/proc/sys/kernel/random/uuid").unwrap();
    let path = std::env::temp_dir().join(format!("adopt-{}", nonce.trim()));
    fs::create_dir(&path).unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
    let root = PrivateState(path);
    Store::initialize(&root.0, state.policy).unwrap();
    let initialized = Store::open(&root.0).unwrap().state.clone();
    let input = json!({"action":"adopt_runtime","expected_instance_id":initialized.instance_id,
        "expected_policy_revision":0,"request":request,"binding":binding,"evidence_ref":"native owner observation"});
    let result = rpc(&root.0, input.clone());
    assert_eq!(result["data"]["adopted"], true);
    assert_eq!(result["data"]["new_work_authorized"], false);
    assert_eq!(result["data"]["runtime_present"], true);
    assert_eq!(result["data"]["fresh_accounting_pending"], true);
    let saved = Store::open(&root.0).unwrap().state.clone();
    assert!(saved.allocations["existing-native"].steps.is_empty());
    let result = rpc(&root.0, input);
    assert_eq!(result["data"]["existing_original_outcome"], true);
    assert_eq!(Store::open(&root.0).unwrap().state.revision, saved.revision);
}
