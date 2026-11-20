use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::ExitStatusExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use apeiros_capacity::*;

fn policy() -> Policy {
    serde_json::from_value(json!({"host_id":"fixture-host","machine_id":machine_id().unwrap(),"resources":{"ram":{"unit":"bytes","capacity":100,"operational_margin":10,"priority_growth_margin":20,"observation_max_age_ms":60000},"gpu":{"unit":"bytes","capacity":100,"operational_margin":10,"priority_growth_margin":20,"observation_max_age_ms":60000}}})).unwrap()
}
fn request(id: &str, system: &str, memory: u64) -> Request {
    serde_json::from_value(json!({"allocation_id":id,"host_id":"fixture-host","system_id":system,"owner_ref":format!("domain/{system}"),"operation_id":format!("operation/{id}"),"attempt_id":"attempt-1","source_ref":"exact-source-1","configuration_ref":"config-1","priority":"priority","demand":{"ram":memory,"gpu":10},"eligibility_ref":"bounded fixture","release_route":"fixture owner"})).unwrap()
}
fn observation() -> Observation {
    Observation {
        host_id: "fixture-host".into(),
        boot_id: boot_id().unwrap(),
        observed_at_ms: now_ms(),
        source_ref: "fixture".into(),
        external_demand: BTreeMap::from([("ram".into(), 0), ("gpu".into(), 0)]),
        accounted_allocations: BTreeMap::new(),
        managed_demand: BTreeMap::new(),
        cpu_sample: None,
        io_sample: None,
        unavailable: BTreeMap::new(),
    }
}
fn state() -> State {
    let mut s = State::new(policy()).unwrap();
    s.observe(observation(), now_ms(), &boot_id().unwrap())
        .unwrap();
    s
}
#[test]
fn policy_reconfiguration_preserves_work_and_rejects_stale_or_rebound_intent() {
    let mut s = state();
    s.admit(request("a", "indexer", 50), now_ms(), &boot_id().unwrap())
        .unwrap();
    s.hold(hold("independent-user-pause")).unwrap();
    let previous_allocation_revision = s.allocations["a"].revision;
    let mut revised = s.policy.clone();
    revised.resources.get_mut("ram").unwrap().operational_margin = 40;
    s.reconfigure_policy(0, revised.clone(), "measured-save-peak".into())
        .unwrap();
    assert_eq!(s.policy_revision, 1);
    assert_eq!(s.allocations["a"].revision, previous_allocation_revision);
    assert_eq!(s.allocations["a"].phase, Phase::Admitted);
    assert!(s.holds.contains_key("independent-user-pause"));
    assert!(!s.resource_observations.contains_key("ram"));
    assert!(s.resource_observations.contains_key("gpu"));
    assert!(
        s.reconfigure_policy(0, revised.clone(), "stale-old-intent".into())
            .is_err()
    );
    revised.resources.get_mut("ram").unwrap().unit = "changed-meaning".into();
    assert!(
        s.reconfigure_policy(1, revised, "rebind-live-resource".into())
            .is_err()
    );
}
fn hold(id: &str) -> Hold {
    serde_json::from_value(json!({"hold_id":id,"owner_ref":"maintenance/one","reason":"fixture maintenance","scope":{"host_id":"fixture-host","resource_ids":["ram"]},"revision":0})).unwrap()
}
fn release(s: &mut State, id: &str) {
    let a = &s.allocations[id];
    let e = ReleaseEvidence {
        owner_ref: a.request.owner_ref.clone(),
        attempt_id: a.request.attempt_id.clone(),
        boot_id: boot_id().unwrap(),
        continuation_ref: None,
        execution_exited_ref: "fixture exited".into(),
        resources_released_ref: "fixture native allocation released".into(),
        no_successor_ref: "fixture serialized successor exclusion".into(),
    };
    s.release(id, a.revision, e).unwrap();
}

#[test]
fn joint_admission_counts_uncertainty_and_external_demand() {
    let mut s = state();
    let boot = boot_id().unwrap();
    assert!(
        s.admit(request("a", "indexer-a", 60), now_ms(), &boot)
            .unwrap()
            .can_admit
    );
    let rev = s.allocations["a"].revision;
    s.begin_step("a", rev, "start", process(std::process::id()).unwrap())
        .unwrap();
    s.finish_step("a", "start", StepOutcome::Uncertain, "lost native reply")
        .unwrap();
    assert!(
        !s.admit(request("b", "indexer-b", 40), now_ms(), &boot)
            .unwrap()
            .can_admit
    );
    // Restart/re-read retains the uncertain commitment, regardless of age.
    let recovered: State = serde_json::from_slice(&serde_json::to_vec(&s).unwrap()).unwrap();
    assert_eq!(
        recovered
            .assess(&request("b", "indexer-b", 40), now_ms(), &boot)
            .unwrap()
            .commitments["ram"],
        60
    );
    let mut obs = observation();
    obs.external_demand.insert("gpu".into(), 85);
    s.observe(obs, now_ms(), &boot).unwrap();
    assert!(
        !s.assess(&request("small", "indexer-c", 1), now_ms(), &boot)
            .unwrap()
            .can_admit
    );
}
#[test]
fn missing_or_old_observation_is_not_free_capacity() {
    let s = State::new(policy()).unwrap();
    assert!(
        !s.assess(&request("a", "a", 1), now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    let s = state();
    assert!(
        !s.assess(&request("a", "a", 1), now_ms() + 60001, &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    assert!(
        !s.assess(&request("a", "a", 1), now_ms(), "different-boot")
            .unwrap()
            .can_admit
    );
}
#[test]
fn hold_blocks_already_admitted_dispatch_and_stale_release_preserves_others() {
    let mut s = state();
    s.admit(request("a", "a", 20), now_ms(), &boot_id().unwrap())
        .unwrap();
    let rev = s.allocations["a"].revision;
    s.hold(hold("maintenance")).unwrap();
    s.hold(hold("user-pause")).unwrap();
    assert!(
        s.begin_step("a", rev, "start", process(std::process::id()).unwrap())
            .unwrap_err()
            .contains("held")
    );
    let h = s.holds["maintenance"].clone();
    assert!(
        s.release_hold(&h.hold_id, &h.owner_ref, h.revision + 1)
            .is_err()
    );
    s.release_hold(&h.hold_id, &h.owner_ref, h.revision)
        .unwrap();
    assert!(s.holds.contains_key("user-pause"));
    assert!(
        s.begin_step("a", rev, "start", process(std::process::id()).unwrap())
            .is_err()
    );
}
#[test]
fn oldest_large_waiter_is_not_bypassed_by_new_children() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(request("incumbent", "a", 70), now_ms(), &boot)
        .unwrap();
    s.admit(request("large", "b", 80), now_ms(), &boot).unwrap();
    s.admit(request("child", "a", 10), now_ms(), &boot).unwrap();
    assert_eq!(s.allocations["child"].phase, Phase::Waiting);
    release(&mut s, "incumbent");
    s.schedule(now_ms(), &boot).unwrap();
    assert_eq!(s.allocations["large"].phase, Phase::Admitted);
}

fn gpu_waiter_with_spare_ram() -> State {
    let mut s = state();
    let boot = boot_id().unwrap();
    let mut incumbent = request("gpu-incumbent", "incumbent", 1);
    incumbent.demand.insert("gpu".into(), 80);
    assert!(s.admit(incumbent, now_ms(), &boot).unwrap().can_admit);
    let mut waiting = request("gpu-waiter", "older", 20);
    waiting.demand.insert("gpu".into(), 20);
    assert!(!s.admit(waiting, now_ms(), &boot).unwrap().can_admit);
    s
}

#[test]
fn cross_aggregate_spare_capacity_does_not_postpone_gpu_waiter() {
    let mut s = gpu_waiter_with_spare_ram();
    let boot = boot_id().unwrap();
    let older = s.allocations["gpu-waiter"].clone();
    for (id, ram, expected) in [
        ("first", 30, true),
        ("boundary", 39, true),
        ("excess", 1, false),
    ] {
        let mut next = request(id, "newer", ram);
        next.demand.remove("gpu");
        assert_eq!(s.admit(next, now_ms(), &boot).unwrap().can_admit, expected);
    }
    assert_eq!(s.allocations["gpu-waiter"].request, older.request);
    assert_eq!(s.allocations["gpu-waiter"].revision, older.revision);
    assert_eq!(
        s.allocations["gpu-waiter"].queued_sequence,
        older.queued_sequence
    );
    assert_eq!(s.allocations["gpu-waiter"].phase, Phase::Waiting);
    let decisions = s.waiting_decisions(now_ms(), &boot).unwrap();
    assert_eq!(
        decisions["excess"].blocked_by_allocations,
        vec!["gpu-waiter"]
    );
    // The RAM-only work remains admitted when GPU capacity returns. It did not
    // consume the RAM required for the older allocation's useful turn.
    release(&mut s, "gpu-incumbent");
    let admitted = s.schedule(now_ms(), &boot).unwrap();
    assert_eq!(admitted.first().unwrap(), "gpu-waiter");
    for id in ["first", "boundary", "gpu-waiter"] {
        assert_eq!(s.allocations[id].phase, Phase::Admitted);
    }
}

#[test]
fn cross_aggregate_spare_capacity_does_not_lend_deficient_gpu() {
    let mut s = gpu_waiter_with_spare_ram();
    let boot = boot_id().unwrap();
    let mut next = request("small-gpu", "newer", 1);
    next.demand.insert("gpu".into(), 5);
    assert!(s.assess(&next, now_ms(), &boot).unwrap().can_admit);
    assert!(!s.admit(next, now_ms(), &boot).unwrap().can_admit);
    assert_eq!(
        s.waiting_decisions(now_ms(), &boot).unwrap()["small-gpu"].blocked_by_allocations,
        vec!["gpu-waiter"]
    );
}

#[test]
fn cross_aggregate_spare_capacity_preserves_normal_margin_and_priority_turn() {
    let mut s = gpu_waiter_with_spare_ram();
    let boot = boot_id().unwrap();
    let mut next = request("normal-spare", "normal", 49);
    next.demand.remove("gpu");
    next.priority = Priority::Normal;
    assert!(s.admit(next, now_ms(), &boot).unwrap().can_admit);
    let mut excess = request("normal-excess", "normal", 1);
    excess.demand.remove("gpu");
    excess.priority = Priority::Normal;
    assert!(!s.admit(excess, now_ms(), &boot).unwrap().can_admit);
    release(&mut s, "gpu-incumbent");
    assert_eq!(
        s.schedule(now_ms(), &boot).unwrap().first().unwrap(),
        "gpu-waiter"
    );
}

#[test]
fn cross_aggregate_spare_capacity_requires_fresh_shared_resource_and_preserves_holds() {
    let mut s = gpu_waiter_with_spare_ram();
    let boot = boot_id().unwrap();
    let mut next = request("ram-only", "newer", 1);
    next.demand.remove("gpu");
    let observed = s.resource_observations.remove("ram").unwrap();
    assert!(!s.admit(next.clone(), now_ms(), &boot).unwrap().can_admit);
    assert_eq!(s.allocations["ram-only"].phase, Phase::Waiting);
    s.resource_observations.insert("ram".into(), observed);
    s.hold(hold("independent-user-pause")).unwrap();
    assert!(s.schedule(now_ms(), &boot).unwrap().is_empty());
    assert_eq!(s.allocations["ram-only"].phase, Phase::Waiting);
    assert!(s.holds.contains_key("independent-user-pause"));
    let h = s.holds["independent-user-pause"].clone();
    s.release_hold(&h.hold_id, &h.owner_ref, h.revision)
        .unwrap();
    assert_eq!(s.schedule(now_ms(), &boot).unwrap(), vec!["ram-only"]);
}

#[test]
fn cross_aggregate_spare_capacity_inspection_is_exact_and_read_only() {
    let mut s = gpu_waiter_with_spare_ram();
    let boot = boot_id().unwrap();
    s.hold(hold("queue-fixture")).unwrap();
    let mut next = request("ram-only", "newer", 10);
    next.demand.remove("gpu");
    s.admit(next, now_ms(), &boot).unwrap();
    let h = s.holds["queue-fixture"].clone();
    s.release_hold(&h.hold_id, &h.owner_ref, h.revision)
        .unwrap();
    let before = serde_json::to_value(&s).unwrap();
    let decisions = s.waiting_decisions(now_ms(), &boot).unwrap();
    assert!(decisions["ram-only"].would_admit);
    assert!(decisions["ram-only"].blocked_by_allocations.is_empty());
    assert!(!decisions["gpu-waiter"].would_admit);
    assert_eq!(before, serde_json::to_value(&s).unwrap());
    assert_eq!(s.schedule(now_ms(), &boot).unwrap(), vec!["ram-only"]);
}

#[test]
fn fitting_same_aggregate_child_preserves_older_unfitting_request() {
    let mut s = state();
    let boot = boot_id().unwrap();
    let mut incumbent = request("incumbent", "other", 50);
    incumbent.priority = Priority::Normal;
    s.admit(incumbent, now_ms(), &boot).unwrap();
    let mut large = request("large", "indexer", 60);
    large.priority = Priority::Normal;
    s.admit(large.clone(), now_ms(), &boot).unwrap();
    let sequence = s.allocations["large"].queued_sequence;
    let revision = s.allocations["large"].revision;
    let mut child = request("child", "indexer", 10);
    child.priority = Priority::Normal;
    assert!(s.admit(child, now_ms(), &boot).unwrap().can_admit);
    assert_eq!(s.allocations["child"].phase, Phase::Admitted);
    assert_eq!(s.allocations["large"].phase, Phase::Waiting);
    assert_eq!(s.allocations["large"].request, large);
    assert_eq!(s.allocations["large"].queued_sequence, sequence);
    assert_eq!(s.allocations["large"].revision, revision);
    assert!(s.allocations["large"].steps.is_empty());
    assert!(s.allocations["large"].runtime.is_none());
}

#[test]
fn same_aggregate_older_fitting_child_keeps_first_choice() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.hold(hold("queue-fixture")).unwrap();
    s.admit(request("large", "indexer", 60), now_ms(), &boot)
        .unwrap();
    s.admit(request("child", "indexer", 40), now_ms(), &boot)
        .unwrap();
    let h = s.holds["queue-fixture"].clone();
    s.release_hold(&h.hold_id, &h.owner_ref, h.revision)
        .unwrap();
    assert_eq!(s.schedule(now_ms(), &boot).unwrap(), vec!["large"]);
    assert_eq!(s.allocations["child"].phase, Phase::Waiting);
}

#[test]
fn same_system_normal_child_cannot_bypass_priority_waiter() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(request("incumbent", "other", 40), now_ms(), &boot)
        .unwrap();
    s.admit(request("priority-large", "indexer", 60), now_ms(), &boot)
        .unwrap();
    let mut child = request("normal-child", "indexer", 10);
    child.priority = Priority::Normal;
    assert!(s.assess(&child, now_ms(), &boot).unwrap().can_admit);
    assert!(!s.admit(child, now_ms(), &boot).unwrap().can_admit);
    let decisions = s.waiting_decisions(now_ms(), &boot).unwrap();
    assert_eq!(
        decisions["normal-child"].blocked_by_allocations,
        vec!["priority-large"]
    );
}

#[test]
fn same_aggregate_fitting_child_reconsiders_previously_blocked_other_system() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(
        request("incumbent", "incumbent-system", 50),
        now_ms(),
        &boot,
    )
    .unwrap();
    s.hold(hold("queue-fixture")).unwrap();
    for (id, system, demand) in [
        ("large", "first", 80),
        ("other-system", "second", 20),
        ("first-fitting", "first", 10),
        ("later-fitting", "first", 20),
    ] {
        s.admit(request(id, system, demand), now_ms(), &boot)
            .unwrap();
    }
    let h = s.holds["queue-fixture"].clone();
    s.release_hold(&h.hold_id, &h.owner_ref, h.revision)
        .unwrap();
    let before = serde_json::to_value(&s).unwrap();
    let decisions = s.waiting_decisions(now_ms(), &boot).unwrap();
    assert!(decisions["first-fitting"].would_admit);
    assert!(decisions["other-system"].would_admit);
    assert!(!decisions["later-fitting"].would_admit);
    assert_eq!(before, serde_json::to_value(&s).unwrap());
    assert_eq!(
        s.schedule(now_ms(), &boot).unwrap(),
        vec!["first-fitting", "other-system"]
    );
    assert_eq!(s.allocations["large"].phase, Phase::Waiting);
    assert_eq!(s.allocations["later-fitting"].phase, Phase::Waiting);
}
#[test]
fn observed_overrun_is_charged_and_stale_exclusion_is_rejected() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(request("a", "a", 20), now_ms(), &boot).unwrap();
    let rev = s.allocations["a"].revision;
    s.begin_step("a", rev, "start", process(std::process::id()).unwrap())
        .unwrap();
    let rev = s.allocations["a"].revision;
    let mut obs = observation();
    obs.accounted_allocations.insert("a".into(), rev);
    obs.managed_demand
        .insert("a".into(), BTreeMap::from([("ram".into(), 80)]));
    s.observe(obs.clone(), now_ms(), &boot).unwrap();
    assert_eq!(
        s.assess(&request("b", "b", 20), now_ms(), &boot)
            .unwrap()
            .commitments["ram"],
        80
    );
    obs.accounted_allocations.insert("a".into(), rev - 1);
    assert!(s.observe(obs, now_ms(), &boot).is_err());
}

#[test]
fn memory_working_set_attribution_preserves_host_and_reservation_floors() {
    // These are observer outputs on its clean-inactive-cache basis, NOT raw
    // memory.current (which can be much larger due to reclaimable payloads).
    for (host_unavailable, first, second) in
        [(30u64, 1u64, 2u64), (10, 40, 20), (80, 1, 2), (70, 50, 10)]
    {
        let mut s = state();
        let boot = boot_id().unwrap();
        for id in ["first", "second"] {
            s.admit(request(id, id, 20), now_ms(), &boot).unwrap();
            s.begin_step(
                id,
                s.allocations[id].revision,
                "native",
                process(std::process::id()).unwrap(),
            )
            .unwrap();
        }
        let mut obs = observation();
        obs.external_demand.insert(
            "ram".into(),
            host_unavailable.saturating_sub(first + second),
        );
        for (id, working) in [("first", first), ("second", second)] {
            obs.accounted_allocations
                .insert(id.into(), s.allocations[id].revision);
            obs.managed_demand
                .insert(id.into(), BTreeMap::from([("ram".into(), working)]));
        }
        s.observe(obs, now_ms(), &boot).unwrap();
        let a = s
            .assess(&request("next", "next", 20), now_ms(), &boot)
            .unwrap();
        let external = s.resource_observations["ram"].external_demand.unwrap();
        let charged = external + a.commitments["ram"];
        assert!(charged >= host_unavailable);
        assert!(charged >= 40);
        assert_eq!(a.available["ram"], 90u64.saturating_sub(charged));
        assert_eq!(a.can_admit, charged <= 70);
    }
}
#[test]
fn joint_normal_headroom_and_idempotent_admission() {
    let mut s = state();
    let mut req = request("normal", "normal", 71);
    req.priority = Priority::Normal;
    assert!(
        !s.admit(req, now_ms(), &boot_id().unwrap())
            .unwrap()
            .eligible
    );
    let req = request("a", "a", 20);
    s.admit(req.clone(), now_ms(), &boot_id().unwrap()).unwrap();
    let revision = s.revision;
    s.admit(req.clone(), now_ms(), &boot_id().unwrap()).unwrap();
    assert_eq!(s.revision, revision);
    let mut different = req;
    different.attempt_id = "replacement".into();
    assert!(s.admit(different, now_ms(), &boot_id().unwrap()).is_err());
}
#[test]
fn cgroup_overlap_counts_vm_or_children_once() {
    assert!(native::nested("/system.slice/a", "/system.slice/a/child"));
    assert!(native::nested("/system.slice/a/child", "/system.slice/a"));
    assert!(!native::nested("/system.slice/a", "/system.slice/another"));
}

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "apeiros-capacity-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&path).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        Store::initialize(&path, policy()).unwrap();
        let mut store = Store::open(&path).unwrap();
        store.state = state();
        store.save().unwrap();
        drop(store);
        Self(path)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

fn large_state_fixture() -> Fixture {
    let fixture = Fixture::new();
    let mut store = Store::open(&fixture.0).unwrap();
    store.state.hold(hold("independent-pause")).unwrap();
    let mut req = request("allocation-0000", "a", 20);
    req.release_route = "x".repeat(4096);
    store
        .state
        .admit(req, now_ms(), &boot_id().unwrap())
        .unwrap();
    let template = store.state.allocations["allocation-0000"].clone();
    assert_eq!(template.phase, Phase::Waiting);
    // Many individually bounded allocation records, not one oversized request.
    for i in 1..4096 {
        let id = format!("allocation-{i:04}");
        let mut allocation = template.clone();
        allocation.request.allocation_id = id.clone();
        allocation.request.operation_id = format!("operation/{id}");
        allocation.queued_sequence = template.queued_sequence + i;
        store.state.allocations.insert(id, allocation);
    }
    store.state.sequence = template.queued_sequence + 4095;
    store.save().unwrap();
    assert!(fs::metadata(fixture.0.join("state.json")).unwrap().len() > 16 * 1024 * 1024);
    fixture
}

#[test]
fn large_state_roundtrip_preserves_complete_owner_state() {
    let fixture = large_state_fixture();
    let path = fixture.0.join("state.json");
    let original: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    let store = Store::open(&fixture.0).unwrap();
    assert_eq!(store.state.allocations.len(), 4096);
    assert!(store.state.allocations.contains_key("allocation-4095"));
    assert_eq!(serde_json::to_value(&store.state).unwrap(), original);
    store.save().unwrap();
    drop(store);
    let reopened = Store::open(&fixture.0).unwrap();
    assert_eq!(serde_json::to_value(&reopened.state).unwrap(), original);
}

#[test]
fn large_state_malformed_tail_is_refused_without_rewriting() {
    let fixture = large_state_fixture();
    let path = fixture.0.join("state.json");
    let original = fs::read(&path).unwrap();
    let mut malformed = original.clone();
    malformed.extend_from_slice(b"\ninvalid trailing content");
    for bytes in [&malformed[..], &original[..original.len() - 1]] {
        fs::write(&path, bytes).unwrap();
        let error = Store::open(&fixture.0)
            .err()
            .expect("malformed owner state must refuse");
        assert!(error.starts_with("state unavailable:"), "{error}");
        assert_eq!(fs::read(&path).unwrap(), bytes);
    }
}

fn rpc(root: &std::path::Path, value: Value) -> (bool, Value) {
    rpc_command(
        Command::new(env!("CARGO_BIN_EXE_apeiros-capacity")),
        root,
        value,
    )
}

fn rpc_command(mut command: Command, root: &std::path::Path, value: Value) -> (bool, Value) {
    let mut child = command
        .arg("--state-root")
        .arg(root)
        .arg("request")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(&serde_json::to_vec(&value).unwrap())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    (
        output.status.success(),
        serde_json::from_slice(&output.stdout).unwrap(),
    )
}
#[test]
fn independent_processes_cannot_double_commit() {
    let fixture = Fixture::new();
    let jobs:Vec<_>=(0..12).map(|i|{let root=fixture.0.clone();std::thread::spawn(move||rpc(&root,json!({"action":"admit","request":request(&format!("a{i}"),&format!("system{i}"),70)})))}).collect();
    for job in jobs {
        assert!(job.join().unwrap().0);
    }
    let store = Store::open(&fixture.0).unwrap();
    assert_eq!(
        store
            .state
            .allocations
            .values()
            .filter(|a| a.phase == Phase::Admitted)
            .count(),
        1
    );
}
#[test]
fn rejected_dispatch_does_not_run_command() {
    let fixture = Fixture::new();
    let (_, reply) = rpc(
        &fixture.0,
        json!({"action":"admit","request":request("a","a",20)}),
    );
    let rev = reply["data"]["allocation"]["revision"].as_u64().unwrap();
    rpc(&fixture.0, json!({"action":"hold","hold":hold("m")}));
    let marker = fixture.0.join("must-not-exist");
    let output = Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
        .args([
            "--state-root",
            fixture.0.to_str().unwrap(),
            "dispatch",
            "--allocation-id",
            "a",
            "--revision",
            &rev.to_string(),
            "--step-id",
            "launch",
            "--",
            "/usr/bin/touch",
            marker.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(125));
    assert!(output.stdout.is_empty());
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stderr).unwrap()["ok"],
        false
    );
    assert!(!marker.exists());
}

// Kill/reap on assertion failure too; these fixtures must never leave a waiter.
struct DispatchChild(Child);
impl DispatchChild {
    fn spawn(fixture: &Fixture, revision: u64, options: &[&str]) -> Self {
        Self::spawn_executable(
            env!("CARGO_BIN_EXE_apeiros-capacity"),
            fixture,
            revision,
            options,
        )
    }
    fn spawn_executable(
        executable: &str,
        fixture: &Fixture,
        revision: u64,
        options: &[&str],
    ) -> Self {
        Self(
            Command::new(executable)
                .arg("--state-root")
                .arg(&fixture.0)
                .args([
                    "dispatch",
                    "--allocation-id",
                    "a",
                    "--revision",
                    &revision.to_string(),
                    "--step-id",
                    "launch",
                    "--wait",
                ])
                .args(options)
                .args([
                    "--",
                    "/bin/sh",
                    "-c",
                    "printf '%s' \"$$\" > \"$1\"",
                    "fixture",
                ])
                .arg(fixture.0.join("effect"))
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
                .unwrap(),
        )
    }
    fn waiting(&mut self) {
        self.waiting_named("a", 5000);
    }
    fn waiting_named(&mut self, id: &str, timeout_ms: i32) {
        let stderr = self.0.stderr.take().unwrap();
        let mut poll = libc::pollfd {
            fd: stderr.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        assert!(
            unsafe { libc::poll(&mut poll, 1, timeout_ms) } > 0,
            "waiter did not report its boundary"
        );
        let mut reader = BufReader::new(stderr);
        let mut line = String::new();
        reader.read_line(&mut line).unwrap();
        self.0.stderr = Some(reader.into_inner());
        assert!(
            line.starts_with(&format!("capacity dispatch waiting for {id};")),
            "{line}"
        );
    }
    fn finish(&mut self) -> std::process::ExitStatus {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        loop {
            if let Some(status) = self.0.try_wait().unwrap() {
                return status;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "dispatch failed to finish"
            );
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
    }
}
impl Drop for DispatchChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn change_fixture(fixture: &Fixture, update: impl FnOnce(&mut State)) {
    let mut store = Store::open(&fixture.0).unwrap();
    update(&mut store.state);
    store.save().unwrap();
}
fn remove_fixture_hold(state: &mut State, id: &str) {
    let hold = state.holds[id].clone();
    state
        .release_hold(id, &hold.owner_ref, hold.revision)
        .unwrap();
}

#[test]
#[ignore = "requires an explicitly selected immutable legacy dispatcher"]
fn legacy_dispatch_wait_accepts_spare_capacity_admission_without_relaunch() {
    let executable =
        std::env::var("APEIROS_CAPACITY_LEGACY_DISPATCHER").expect("pin the legacy dispatcher");
    let fixture = Fixture::new();
    change_fixture(&fixture, |state| {
        *state = gpu_waiter_with_spare_ram();
        state.hold(hold("queue-fixture")).unwrap();
        let mut r = request("a", "newer", 30);
        r.demand.remove("gpu");
        state.admit(r, now_ms(), &boot_id().unwrap()).unwrap();
        remove_fixture_hold(state, "queue-fixture");
    });
    let original = Store::open(&fixture.0).unwrap().state.allocations["a"].clone();
    let mut child = DispatchChild::spawn_executable(&executable, &fixture, 1, &[]);
    child.waiting();
    assert!(!fixture.0.join("effect").exists());
    let (ok, response) = rpc(&fixture.0, json!({"action":"schedule"}));
    assert!(ok, "{response}");
    assert_eq!(response["data"]["newly_admitted"], json!(["a"]));
    assert!(child.finish().success());
    let store = Store::open(&fixture.0).unwrap();
    let actual = &store.state.allocations["a"];
    assert_eq!(actual.request, original.request);
    assert_eq!(actual.queued_sequence, original.queued_sequence);
    assert_eq!(actual.revision, 3);
    assert_eq!(actual.steps.len(), 1);
    assert_eq!(actual.steps["launch"].process.pid, child.0.id());
    assert_eq!(
        fs::read_to_string(fixture.0.join("effect")).unwrap(),
        child.0.id().to_string()
    );
    assert_eq!(store.state.allocations["gpu-waiter"].phase, Phase::Waiting);
}

#[test]
fn dispatch_wait_passive_capture_joins_shared_readers_without_mutation() {
    let fixture = Fixture::new();
    change_fixture(&fixture, |state| {
        state.hold(hold("independent-pause")).unwrap();
        state
            .admit(request("a", "a", 20), now_ms(), &boot_id().unwrap())
            .unwrap();
    });
    let original = fs::read(fixture.0.join("state.json")).unwrap();
    let shared = fs::File::open(fixture.0.join("capacity.lock")).unwrap();
    assert_eq!(unsafe { libc::flock(shared.as_raw_fd(), libc::LOCK_SH) }, 0);
    let mut child = DispatchChild::spawn(&fixture, 1, &[]);
    // The old EX read cannot reach this boundary while another SH owner lives.
    child.waiting();
    assert_eq!(fs::read(fixture.0.join("state.json")).unwrap(), original);
    assert!(!fixture.0.join("effect").exists());
    drop(shared);
    change_fixture(&fixture, |state| {
        remove_fixture_hold(state, "independent-pause");
        state.schedule(now_ms(), &boot_id().unwrap()).unwrap();
    });
    assert!(child.finish().success());
    let snapshot = Store::snapshot(&fixture.0).unwrap();
    assert_eq!(snapshot.allocations["a"].steps.len(), 1);
    assert_eq!(
        snapshot.allocations["a"].steps["launch"].process.pid,
        child.0.id()
    );
}

#[test]
#[ignore = "explicit 64 MiB state and 32 concurrent waiter stress test"]
fn dispatch_wait_scale_keeps_native_observation_fresh() {
    let fixture = Fixture::new();
    let history_count = 16_384_u64;
    let waiter_count = 32;
    change_fixture(&fixture, |state| {
        state.policy.resources.remove("gpu");
        let ram = state.policy.resources.get_mut("ram").unwrap();
        ram.unit = "bytes".into();
        ram.capacity = 1_u64 << 30;
        ram.operational_margin = 0;
        ram.priority_growth_margin = 0;
        ram.observation_max_age_ms = 15000;
        ram.native = Some(native::Resource::Memory);
        let mut r = request("history-00000", "fixture", 1);
        r.demand.remove("gpu");
        state.admit(r, now_ms(), &boot_id().unwrap()).unwrap();
        release(state, "history-00000");
        let template = state.allocations["history-00000"].clone();
        for n in 1..history_count {
            let id = format!("history-{n:05}");
            let mut a = template.clone();
            a.request.allocation_id = id.clone();
            a.request.operation_id = format!("operation/{id}");
            a.queued_sequence = template.queued_sequence + n;
            state.allocations.insert(id, a);
        }
        state.sequence = history_count + 20;
        state.hold(hold("independent-pause")).unwrap();
        for n in 0..waiter_count {
            let mut r = request(&format!("waiter-{n:02}"), "fixture", 1);
            r.demand.remove("gpu");
            state.admit(r, now_ms(), &boot_id().unwrap()).unwrap();
        }
        let current = serde_json::to_vec_pretty(state).unwrap().len();
        let padding = ((64_usize * 1024 * 1024).saturating_sub(current)).div_ceil(history_count as usize);
        for (id, a) in &mut state.allocations {
            if id.starts_with("history-") {
                a.request.release_route.push_str(&"x".repeat(padding));
            }
        }
    });
    let size = fs::metadata(fixture.0.join("state.json")).unwrap().len();
    assert!((64 * 1024 * 1024..65 * 1024 * 1024).contains(&size), "{size}");
    let original = Store::snapshot(&fixture.0).unwrap();
    let mut children = Vec::new();
    for n in 0..waiter_count {
        let id = format!("waiter-{n:02}");
        let child = Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
            .arg("--state-root")
            .arg(&fixture.0)
            .args([
                "dispatch",
                "--allocation-id",
                &id,
                "--revision",
                "1",
                "--step-id",
                "launch",
                "--wait",
                "--",
                "/bin/false",
            ])
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        children.push(DispatchChild(child));
    }
    for (n, child) in children.iter_mut().enumerate() {
        child.waiting_named(&format!("waiter-{n:02}"), 45000);
    }
    // Optional legacy resident comparison uses only this isolated fixture.
    let executable = std::env::var("APEIROS_CAPACITY_SCALE_RESIDENT")
        .unwrap_or_else(|_| env!("CARGO_BIN_EXE_apeiros-capacity").into());
    let resident_log = fixture.0.join("resident.log");
    let resident = Command::new(&executable)
        .arg("--state-root")
        .arg(&fixture.0)
        .arg("serve")
        .stdout(Stdio::null())
        .stderr(fs::File::create(&resident_log).unwrap())
        .spawn()
        .unwrap();
    let mut resident = DispatchChild(resident);
    let mut last_modified = fs::metadata(fixture.0.join("state.json"))
        .unwrap()
        .modified()
        .unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
    let mut observed = 0;
    let mut ages = Vec::new();
    let mut stamps = Vec::new();
    while ages.len() < 3 {
        assert!(
            std::time::Instant::now() < deadline,
            "native observer did not publish three samples"
        );
        let modified = fs::metadata(fixture.0.join("state.json"))
            .unwrap()
            .modified()
            .unwrap();
        if modified == last_modified {
            std::thread::sleep(std::time::Duration::from_millis(25));
            continue;
        }
        last_modified = modified;
        let state = Store::snapshot(&fixture.0).unwrap();
        if let Some(sample) = &state.observation {
            if sample.source_ref.starts_with("native Linux") && sample.observed_at_ms > observed {
                observed = sample.observed_at_ms;
                assert!(
                    !sample.unavailable.contains_key("ram"),
                    "{:?}",
                    sample.unavailable
                );
                ages.push(now_ms() - observed);
                stamps.push(observed);
            }
        }
        assert_eq!(state.instance_id, original.instance_id);
        assert_eq!(state.policy, original.policy);
        assert_eq!(state.holds, original.holds);
        for n in 0..waiter_count {
            let id = format!("waiter-{n:02}");
            assert_eq!(
                serde_json::to_value(&state.allocations[&id]).unwrap(),
                serde_json::to_value(&original.allocations[&id]).unwrap()
            );
        }
    }
    unsafe {
        libc::kill(resident.0.id() as libc::pid_t, libc::SIGTERM);
    }
    resident.finish();
    for child in &mut children {
        unsafe {
            libc::kill(child.0.id() as libc::pid_t, libc::SIGTERM);
        }
    }
    for child in &mut children {
        assert_eq!(child.finish().signal(), Some(libc::SIGTERM));
    }
    println!(
        "scale_bytes={size} waiters={waiter_count} observation_ages_ms={ages:?} observation_stamps_ms={stamps:?} resident={executable}"
    );
    assert!(
        ages.iter().all(|age| *age <= 15000),
        "expired at publication/readback: {ages:?}"
    );
    assert!(
        stamps.windows(2).all(|pair| pair[1] - pair[0] <= 15000),
        "observer cadence exceeds freshness: {stamps:?}"
    );
}

#[test]
fn dispatch_wait_initial_admission_preserves_independent_holds() {
    let fixture = Fixture::new();
    change_fixture(&fixture, |state| {
        state.hold(hold("maintenance")).unwrap();
        state.hold(hold("user-pause")).unwrap();
        state
            .admit(request("a", "a", 20), now_ms(), &boot_id().unwrap())
            .unwrap();
    });
    let original = Store::open(&fixture.0).unwrap().state.allocations["a"].clone();
    let mut child = DispatchChild::spawn(&fixture, 1, &[]);
    child.waiting();
    change_fixture(&fixture, |state| {
        remove_fixture_hold(state, "maintenance");
        state.schedule(now_ms(), &boot_id().unwrap()).unwrap();
        assert!(state.holds.contains_key("user-pause"));
        assert_eq!(state.allocations["a"].phase, Phase::Waiting);
        assert!(state.allocations["a"].steps.is_empty());
    });
    assert!(!fixture.0.join("effect").exists());
    change_fixture(&fixture, |state| {
        remove_fixture_hold(state, "user-pause");
        state.schedule(now_ms(), &boot_id().unwrap()).unwrap();
    });
    assert!(child.finish().success());
    let store = Store::open(&fixture.0).unwrap();
    let allocation = &store.state.allocations["a"];
    assert_eq!(allocation.request, original.request);
    assert_eq!(allocation.queued_sequence, original.queued_sequence);
    assert_eq!(allocation.steps.len(), 1);
    assert_eq!(allocation.revision, 3);
    assert!(allocation.runtime.is_none());
    assert_eq!(
        fs::read_to_string(fixture.0.join("effect")).unwrap(),
        child.0.id().to_string()
    );
}

#[test]
fn dispatch_wait_exact_admitted_and_active_continuation_wait_for_fresh_fit() {
    for active in [false, true] {
        let fixture = Fixture::new();
        change_fixture(&fixture, |state| {
            state
                .admit(request("a", "a", 20), now_ms(), &boot_id().unwrap())
                .unwrap();
            if active {
                state
                    .begin_step(
                        "a",
                        2,
                        "completed-prefix",
                        process(std::process::id()).unwrap(),
                    )
                    .unwrap();
                state
                    .finish_step(
                        "a",
                        "completed-prefix",
                        StepOutcome::Applied,
                        "saved prefix",
                    )
                    .unwrap();
            }
            let mut observed = observation();
            observed.external_demand.insert("ram".into(), 80);
            state
                .observe(observed, now_ms(), &boot_id().unwrap())
                .unwrap();
        });
        let original = Store::open(&fixture.0).unwrap().state.allocations["a"].clone();
        let mut child = DispatchChild::spawn(&fixture, original.revision, &[]);
        child.waiting();
        assert!(!fixture.0.join("effect").exists());
        change_fixture(&fixture, |state| {
            state
                .observe(observation(), now_ms(), &boot_id().unwrap())
                .unwrap();
        });
        assert!(child.finish().success());
        let store = Store::open(&fixture.0).unwrap();
        let allocation = &store.state.allocations["a"];
        assert_eq!(allocation.revision, original.revision + 1);
        assert_eq!(allocation.steps.len(), original.steps.len() + 1);
        if active {
            assert_eq!(
                allocation.steps["completed-prefix"],
                original.steps["completed-prefix"]
            );
        }
    }
}

#[test]
fn dispatch_wait_sigterm_preserves_unstarted_allocation() {
    let fixture = Fixture::new();
    change_fixture(&fixture, |state| {
        state.hold(hold("user-pause")).unwrap();
        state
            .admit(request("a", "a", 20), now_ms(), &boot_id().unwrap())
            .unwrap();
    });
    let original = fs::read(fixture.0.join("state.json")).unwrap();
    let mut child = DispatchChild::spawn(&fixture, 1, &[]);
    child.waiting();
    assert_eq!(
        unsafe { libc::kill(child.0.id() as libc::pid_t, libc::SIGTERM) },
        0
    );
    assert_eq!(child.finish().signal(), Some(libc::SIGTERM));
    assert_eq!(fs::read(fixture.0.join("state.json")).unwrap(), original);
    assert!(!fixture.0.join("effect").exists());
}

#[test]
fn dispatch_wait_refuses_source_request_revision_release_and_duplicate_drift() {
    for drift in ["source", "request", "revision", "release", "duplicate"] {
        let fixture = Fixture::new();
        change_fixture(&fixture, |state| {
            state.hold(hold("user-pause")).unwrap();
            state
                .admit(request("a", "a", 20), now_ms(), &boot_id().unwrap())
                .unwrap();
        });
        let mut child = DispatchChild::spawn(&fixture, 1, &[]);
        child.waiting();
        change_fixture(&fixture, |state| match drift {
            "source" => state.instance_id = "replacement source".into(),
            "request" => {
                state.allocations.get_mut("a").unwrap().request.attempt_id = "replacement".into()
            }
            "revision" => state.allocations.get_mut("a").unwrap().revision += 1,
            "release" => release(state, "a"),
            "duplicate" => {
                remove_fixture_hold(state, "user-pause");
                state.schedule(now_ms(), &boot_id().unwrap()).unwrap();
                state
                    .begin_step("a", 2, "launch", process(std::process::id()).unwrap())
                    .unwrap();
            }
            _ => unreachable!(),
        });
        let changed = fs::read(fixture.0.join("state.json")).unwrap();
        assert_eq!(child.finish().code(), Some(125), "{drift}");
        assert_eq!(
            fs::read(fixture.0.join("state.json")).unwrap(),
            changed,
            "{drift}"
        );
        assert!(!fixture.0.join("effect").exists(), "{drift}");
    }
}

#[test]
fn dispatch_wait_admitted_before_first_read_binds_actual_exec_process() {
    let fixture = Fixture::new();
    change_fixture(&fixture, |state| {
        state
            .admit(request("a", "a", 20), now_ms(), &boot_id().unwrap())
            .unwrap();
    });
    // Initial revision1 is the caller's saved queue state; the ordinary native
    // admission may finish before the one-shot dispatch process is scheduled.
    let mut child = DispatchChild::spawn(
        &fixture,
        1,
        &["--supervisor-ref", "systemd:fixture-index.service"],
    );
    assert!(child.finish().success());
    let store = Store::open(&fixture.0).unwrap();
    let allocation = &store.state.allocations["a"];
    let runtime = allocation.runtime.as_ref().unwrap();
    assert_eq!(allocation.revision, 4);
    assert_eq!(allocation.phase, Phase::Active);
    assert_eq!(runtime.process, allocation.steps["launch"].process);
    assert_eq!(runtime.process.pid, child.0.id());
    assert_eq!(runtime.process.boot_id, boot_id().unwrap());
    assert!(runtime.process.start_ticks > 0);
    assert_eq!(runtime.supervisor_ref, "systemd:fixture-index.service");
    let cgroups = fs::read_to_string("/proc/self/cgroup").unwrap();
    let expected = cgroups
        .lines()
        .find_map(|line| line.strip_prefix("0::"))
        .unwrap();
    assert_eq!(runtime.cgroup, expected);
    assert_eq!(
        fs::read_to_string(fixture.0.join("effect")).unwrap(),
        runtime.process.pid.to_string()
    );
}

#[test]
fn native_dispatch_preserves_framed_peer_stdin_stdout() {
    let fixture = Fixture::new();
    let (_, reply) = rpc(
        &fixture.0,
        json!({"action":"admit","request":request("peer","a",10)}),
    );
    let revision = reply["data"]["allocation"]["revision"].as_u64().unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
        .args([
            "--state-root",
            fixture.0.to_str().unwrap(),
            "dispatch",
            "--allocation-id",
            "peer",
            "--revision",
            &revision.to_string(),
            "--step-id",
            "exact-connection",
            "--",
            "/bin/cat",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let frame = b"\0\0\0\x08peer\0\xff\n\r";
    child.stdin.take().unwrap().write_all(frame).unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    assert_eq!(output.stdout, frame);
    assert!(output.stderr.is_empty());
}
#[test]
fn guard_timeout_preserves_uncertain_commitment() {
    let fixture = Fixture::new();
    let (_, reply) = rpc(
        &fixture.0,
        json!({"action":"admit","request":request("a","a",20)}),
    );
    let rev = reply["data"]["allocation"]["revision"].as_u64().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
        .args([
            "--state-root",
            fixture.0.to_str().unwrap(),
            "guard",
            "--allocation-id",
            "a",
            "--revision",
            &rev.to_string(),
            "--step-id",
            "launch",
            "--target-ref",
            "fixture:bounded-control",
            "--timeout-ms",
            "20",
            "--",
            "/bin/sleep",
            "1",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["data"]["outcome"], "uncertain");
    let store = Store::open(&fixture.0).unwrap();
    assert_eq!(store.state.allocations["a"].phase, Phase::Active);
    assert_eq!(
        store.state.allocations["a"].steps["launch"].outcome,
        StepOutcome::Uncertain
    );
}

#[test]
fn maintenance_hold_precedes_drain_and_preserves_user_pause() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(request("a", "a", 20), now_ms(), &boot).unwrap();
    let rev = s.allocations["a"].revision;
    s.begin_step("a", rev, "launch", process(std::process::id()).unwrap())
        .unwrap();
    let m = Maintenance {
        operation_id: "upgrade".into(),
        owner_ref: "maintenance/one".into(),
        change_ref: "exact package change".into(),
        hold_id: "upgrade-hold".into(),
        scope: hold("unused").scope,
        revision: 0,
        stage: "".into(),
        evidence_refs: BTreeMap::new(),
    };
    s.prepare_maintenance(m).unwrap();
    s.hold(hold("user-pause")).unwrap();
    let proof = BTreeMap::from([("owner-result".into(), "fixture owner result".into())]);
    assert!(
        s.record_maintenance("upgrade", 1, "maintenance/one", "drained", proof.clone())
            .is_err()
    );
    let h = &s.holds["upgrade-hold"];
    assert!(
        s.release_hold("upgrade-hold", "maintenance/one", h.revision)
            .is_err()
    );
    release(&mut s, "a");
    for (i, stage) in ["drained", "installed", "activated", "verified"]
        .into_iter()
        .enumerate()
    {
        s.record_maintenance(
            "upgrade",
            i as u64 + 1,
            "maintenance/one",
            stage,
            proof.clone(),
        )
        .unwrap();
    }
    s.finish_maintenance("upgrade", 5, "maintenance/one")
        .unwrap();
    assert!(s.holds.contains_key("user-pause"));
    assert!(!s.holds.contains_key("upgrade-hold"));
}

#[test]
fn final_dispatch_rechecks_capacity_and_duplicate_step() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(request("a", "a", 50), now_ms(), &boot).unwrap();
    let rev = s.allocations["a"].revision;
    let mut o = observation();
    o.external_demand.insert("ram".into(), 80);
    s.observe(o, now_ms(), &boot).unwrap();
    assert!(
        s.begin_step("a", rev, "launch", process(std::process::id()).unwrap())
            .is_err()
    );
    s.observe(observation(), now_ms(), &boot).unwrap();
    s.begin_step("a", rev, "launch", process(std::process::id()).unwrap())
        .unwrap();
    let rev = s.allocations["a"].revision;
    assert!(
        s.begin_step("a", rev, "launch", process(std::process::id()).unwrap())
            .unwrap_err()
            .contains("already exists")
    );
}

#[test]
fn live_process_cannot_be_reported_as_native_release() {
    let binding = RuntimeBinding {
        process: process(std::process::id()).unwrap(),
        cgroup: "/fixture".into(),
        supervisor_ref: "fixture".into(),
    };
    assert!(native::verify_runtime_exited(&binding).is_err());
}

#[test]
fn passive_follow_returns_changed_snapshot_and_explicit_source_gap() {
    let fixture = Fixture::new();
    let initial = Store::open(&fixture.0).unwrap().state.clone();
    let root = fixture.0.clone();
    let follower = std::thread::spawn(move || {
        rpc(
            &root,
            json!({"action":"follow","after_source":initial.instance_id,"after_revision":initial.revision,"timeout_ms":3000}),
        )
    });
    std::thread::sleep(std::time::Duration::from_millis(50));
    rpc(&fixture.0, json!({"action":"hold","hold":hold("event")}));
    let (ok, response) = follower.join().unwrap();
    assert!(ok);
    assert_eq!(response["data"]["changed"], true);
    assert_eq!(response["data"]["source_changed"], false);
    let (ok, response) = rpc(
        &fixture.0,
        json!({"action":"follow","after_source":"old source","after_revision":0,"timeout_ms":0}),
    );
    assert!(ok);
    assert_eq!(response["data"]["source_changed"], true);
}

#[test]
fn stdio_mcp_and_cli_use_same_passive_handler() {
    let fixture = Fixture::new();
    let _service = Service(
        Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
            .arg("--state-root")
            .arg(&fixture.0)
            .arg("serve")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    wait_file(&fixture.0.join("capacity.sock"));
    let (_, cli) = rpc(&fixture.0, json!({"action":"inspect"}));
    let mut child = Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
        .arg("--state-root")
        .arg(&fixture.0)
        .arg("mcp")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    writeln!(stdin,"{}",json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"capacity","arguments":{"action":"inspect"}}})).unwrap();
    drop(stdin);
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    let response: Value = serde_json::from_slice(&output.stdout).unwrap();
    let result: Value =
        serde_json::from_str(response["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(
        result["data"]["state"]["instance_id"],
        cli["data"]["state"]["instance_id"]
    );
    assert_eq!(
        result["data"]["state"]["policy"],
        cli["data"]["state"]["policy"]
    );
}

fn mcp_client(
    root: &std::path::Path,
) -> (
    Service,
    std::process::ChildStdin,
    std::io::BufReader<std::process::ChildStdout>,
) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
        .arg("--state-root")
        .arg(root)
        .arg("mcp")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let input = child.stdin.take().unwrap();
    let output = std::io::BufReader::new(child.stdout.take().unwrap());
    (Service(child), input, output)
}
fn mcp_call(input: &mut impl Write, output: &mut impl std::io::BufRead, request: Value) -> Value {
    writeln!(input,"{}",json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"capacity","arguments":request}})).unwrap();
    input.flush().unwrap();
    let mut line = String::new();
    output.read_line(&mut line).unwrap();
    let result: Value = serde_json::from_str(&line).unwrap();
    serde_json::from_str(result["result"]["content"][0]["text"].as_str().unwrap()).unwrap()
}
fn read_forwarded(stream: &mut std::os::unix::net::UnixStream) -> Value {
    use std::io::{BufRead, Read};
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(2)))
        .unwrap();
    let mut reader = std::io::BufReader::new(stream);
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    assert_eq!(line, "POST /v1/capacity HTTP/1.1\r\n");
    let mut length = None;
    loop {
        line.clear();
        reader.read_line(&mut line).unwrap();
        if line == "\r\n" {
            break;
        }
        assert!(!line.is_empty());
        if let Some(value) = line.strip_prefix("Content-Length: ") {
            length = Some(value.trim().parse::<usize>().unwrap());
        }
    }
    let mut bytes = vec![0; length.unwrap()];
    reader.read_exact(&mut bytes).unwrap();
    serde_json::from_slice(&bytes).unwrap()
}
#[test]
fn mcp_without_resident_never_falls_back_to_local_state_writer() {
    let fixture = Fixture::new();
    let before = fs::read(fixture.0.join("state.json")).unwrap();
    let (_child, mut input, mut output) = mcp_client(&fixture.0);
    let help = mcp_call(
        &mut input,
        &mut output,
        json!({"action":"help","operation":"overview"}),
    );
    assert_eq!(help["ok"], true);
    assert_eq!(
        help["data"]["input_schema"]["properties"]["action"]["const"],
        "overview"
    );
    assert_eq!(help["data"]["help_requires_live_resident"], false);
    let result = mcp_call(
        &mut input,
        &mut output,
        json!({"action":"hold","hold":hold("no-resident")}),
    );
    assert_eq!(result["ok"], false);
    assert!(
        result["error"]
            .as_str()
            .unwrap()
            .contains("unavailable before request")
    );
    assert_eq!(fs::read(fixture.0.join("state.json")).unwrap(), before);
}
#[test]
fn persistent_mcp_forwards_opaque_fields_across_resident_replacement() {
    let fixture = Fixture::new();
    let before = fs::read(fixture.0.join("state.json")).unwrap();
    let (_child, mut input, mut output) = mcp_client(&fixture.0);
    for generation in [1, 2] {
        let socket = fixture.0.join("capacity.sock");
        let listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
        let request =
            json!({"action":"future-owner-operation","future_selector":{"generation":generation}});
        let expected = request.clone();
        let response = json!({"ok":true,"data":{"future_state":{"adoption_origin":{"generation":generation}},"unknown_to_client":[true,null,13]}});
        let bytes = serde_json::to_vec(&response).unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            assert_eq!(read_forwarded(&mut stream), expected);
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n",
                bytes.len()
            )
            .unwrap();
            stream.write_all(&bytes).unwrap();
        });
        assert_eq!(mcp_call(&mut input, &mut output, request), response);
        server.join().unwrap();
        fs::remove_file(socket).unwrap();
    }
    assert_eq!(fs::read(fixture.0.join("state.json")).unwrap(), before);
}
#[test]
fn mcp_lost_resident_reply_retains_uncertainty_without_retry() {
    let fixture = Fixture::new();
    let before = fs::read(fixture.0.join("state.json")).unwrap();
    let listener = std::os::unix::net::UnixListener::bind(fixture.0.join("capacity.sock")).unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let received = read_forwarded(&mut stream);
        drop(stream);
        (listener, received)
    });
    let (_child, mut input, mut output) = mcp_client(&fixture.0);
    let request = json!({"action":"hold","hold":hold("accepted-but-lost-reply")});
    let result = mcp_call(&mut input, &mut output, request.clone());
    let (listener, received) = server.join().unwrap();
    assert_eq!(received, request);
    assert_eq!(result["ok"], false);
    assert!(
        result["error"]
            .as_str()
            .unwrap()
            .contains("may have applied")
    );
    listener.set_nonblocking(true).unwrap();
    assert!(listener.accept().is_err());
    assert_eq!(fs::read(fixture.0.join("state.json")).unwrap(), before);
}

struct Service(std::process::Child);
impl Drop for Service {
    fn drop(&mut self) {
        if self.0.try_wait().is_ok_and(|s| s.is_none()) {
            let _ = self.0.kill();
        }
        let _ = self.0.wait();
    }
}
fn wait_file(path: &std::path::Path) {
    let end = std::time::Instant::now() + std::time::Duration::from_secs(2);
    while !path.exists() {
        assert!(
            std::time::Instant::now() < end,
            "task-owned fixture failed to become ready"
        );
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
}

#[test]
fn hold_waits_for_native_effect_boundary_and_blocks_next_step() {
    let fixture = Fixture::new();
    let (_, reply) = rpc(
        &fixture.0,
        json!({"action":"admit","request":request("a","a",20)}),
    );
    let rev = reply["data"]["allocation"]["revision"].as_u64().unwrap();
    let ready = fixture.0.join("ready");
    let finish = fixture.0.join("finish");
    let mut guard = Service(
        Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
            .args([
                "--state-root",
                fixture.0.to_str().unwrap(),
                "guard",
                "--allocation-id",
                "a",
                "--revision",
                &rev.to_string(),
                "--step-id",
                "native-start",
                "--target-ref",
                "fixture:bounded-control",
                "--timeout-ms",
                "3000",
                "--",
                "/bin/sh",
                "-c",
                "printf ready > \"$1\"; while [ ! -e \"$2\" ]; do sleep 0.01; done",
                "fixture",
                ready.to_str().unwrap(),
                finish.to_str().unwrap(),
            ])
            .stdout(Stdio::null())
            .spawn()
            .unwrap(),
    );
    wait_file(&ready);
    let root = fixture.0.clone();
    let holder =
        std::thread::spawn(move || rpc(&root, json!({"action":"hold","hold":hold("maintenance")})));
    std::thread::sleep(std::time::Duration::from_millis(50));
    assert!(!holder.is_finished());
    fs::write(&finish, b"finish").unwrap();
    assert!(guard.0.wait().unwrap().success());
    assert!(holder.join().unwrap().0);
    let mut store = Store::open(&fixture.0).unwrap();
    let rev = store.state.allocations["a"].revision;
    assert_eq!(
        store.state.allocations["a"].steps["native-start"].outcome,
        StepOutcome::Applied
    );
    assert!(
        store
            .state
            .begin_step("a", rev, "next-step", process(std::process::id()).unwrap())
            .unwrap_err()
            .contains("held")
    );
}

#[test]
fn unix_http_uses_shared_handler_without_network_listener() {
    use std::io::Read;
    let fixture = Fixture::new();
    let _service = Service(
        Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
            .arg("--state-root")
            .arg(&fixture.0)
            .arg("serve")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let socket = fixture.0.join("capacity.sock");
    wait_file(&socket);
    let mut stream = std::os::unix::net::UnixStream::connect(socket).unwrap();
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(3)))
        .unwrap();
    stream
        .write_all(b"GET /v1/capacity HTTP/1.1\r\nHost: localhost\r\n\r\n")
        .unwrap();
    stream.shutdown(std::net::Shutdown::Write).unwrap();
    let mut output = String::new();
    stream.read_to_string(&mut output).unwrap();
    let (headers, body) = output.split_once("\r\n\r\n").unwrap();
    assert!(headers.starts_with("HTTP/1.1 200 OK"));
    let result: Value = serde_json::from_str(body).unwrap();
    assert_eq!(result["data"]["passive"], true);
    assert_eq!(result["data"]["state"]["policy"]["host_id"], "fixture-host");
}

#[test]
fn failed_refresh_retains_original_observation_without_renewing_admission() {
    let mut s = state();
    let before = s.retained_resource_observations["ram"].observed_at_ms;
    let mut o = observation();
    o.external_demand.remove("ram");
    o.unavailable
        .insert("ram".into(), "native reader failed".into());
    s.observe(o, now_ms(), &boot_id().unwrap()).unwrap();
    assert_eq!(
        s.retained_resource_observations["ram"].observed_at_ms,
        before
    );
    assert!(
        !s.assess(&request("a", "a", 1), now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
}

#[test]
fn foreign_physical_host_policy_is_rejected() {
    let mut p = policy();
    p.machine_id = "foreign-machine".into();
    assert!(State::new(p).unwrap_err().contains("machine identity"));
}

#[test]
fn duplicate_physical_resource_aliases_cannot_double_capacity() {
    let mut p = policy();
    p.resources.get_mut("ram").unwrap().native = Some(native::Resource::GpuMemory {
        uuid: "GPU-fixture".into(),
    });
    p.resources.get_mut("gpu").unwrap().native = Some(native::Resource::GpuMemory {
        uuid: "GPU-fixture".into(),
    });
    assert!(State::new(p).unwrap_err().contains("overlap physical"));
}

#[test]
fn cpu_requests_require_real_system_aggregate_binding() {
    let mut p = policy();
    p.resources.get_mut("ram").unwrap().native = Some(native::Resource::Cpu {
        eligible_cpus: vec![0],
    });
    p.resources.get_mut("ram").unwrap().unit = "millicores".into();
    let s = State::new(p).unwrap();
    assert!(
        s.assess(&request("a", "a", 10), now_ms(), &boot_id().unwrap())
            .unwrap_err()
            .contains("system aggregate")
    );
}

#[test]
fn repeat_waiting_admission_does_not_claim_reservation() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(request("incumbent", "a", 70), now_ms(), &boot)
        .unwrap();
    s.admit(request("large", "b", 80), now_ms(), &boot).unwrap();
    let small = request("child", "a", 10);
    s.admit(small.clone(), now_ms(), &boot).unwrap();
    assert!(!s.admit(small, now_ms(), &boot).unwrap().can_admit);
}

#[test]
fn partial_refresh_keeps_other_resource_source_time_and_use() {
    let mut s = state();
    let original = s.resource_observations["ram"].observed_at_ms;
    let mut o = observation();
    o.external_demand.remove("ram");
    o.external_demand.insert("gpu".into(), 85);
    s.observe(o, now_ms(), &boot_id().unwrap()).unwrap();
    assert_eq!(s.resource_observations["ram"].observed_at_ms, original);
    assert_eq!(s.resource_observations["gpu"].external_demand, Some(85));
    let native = native::observe(&s);
    s.observe(native, now_ms(), &boot_id().unwrap()).unwrap();
    assert_eq!(s.resource_observations["ram"].observed_at_ms, original);
    assert_eq!(s.resource_observations["gpu"].external_demand, Some(85));
}

#[test]
fn queued_children_do_not_take_two_turns_before_another_system() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(request("incumbent", "c", 90), now_ms(), &boot)
        .unwrap();
    s.admit(request("a1", "a", 45), now_ms(), &boot).unwrap();
    s.admit(request("a2", "a", 45), now_ms(), &boot).unwrap();
    s.admit(request("b", "b", 45), now_ms(), &boot).unwrap();
    release(&mut s, "incumbent");
    let admitted = s.schedule(now_ms(), &boot).unwrap();
    assert_eq!(admitted, vec!["a1", "b"]);
    assert_eq!(s.allocations["a2"].phase, Phase::Waiting);
}

#[test]
fn bounded_control_step_persists_prepared_target_separately_from_controller() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(request("a", "a", 20), now_ms(), &boot).unwrap();
    let rev = s.allocations["a"].revision;
    let controller = process(std::process::id()).unwrap();
    s.begin_control_step(
        "a",
        rev,
        "daemon-start",
        controller.clone(),
        "container:exact-id".into(),
    )
    .unwrap();
    let step = &s.allocations["a"].steps["daemon-start"];
    assert_eq!(step.process, controller);
    assert_eq!(step.target_ref.as_deref(), Some("container:exact-id"));
    assert!(s.allocations["a"].runtime.is_none());
}

#[test]
fn final_step_charges_its_own_observed_overrun() {
    let mut s = state();
    let boot = boot_id().unwrap();
    s.admit(request("a", "a", 20), now_ms(), &boot).unwrap();
    s.admit(request("b", "b", 30), now_ms(), &boot).unwrap();
    let rev = s.allocations["a"].revision;
    s.begin_step("a", rev, "first", process(std::process::id()).unwrap())
        .unwrap();
    let rev = s.allocations["a"].revision;
    let mut o = observation();
    o.accounted_allocations.insert("a".into(), rev);
    o.managed_demand
        .insert("a".into(), BTreeMap::from([("ram".into(), 80)]));
    s.observe(o, now_ms(), &boot).unwrap();
    assert!(
        !s.assess(&s.allocations["a"].request, now_ms(), &boot)
            .unwrap()
            .can_admit
    );
    assert!(
        s.begin_step("a", rev, "next", process(std::process::id()).unwrap())
            .unwrap_err()
            .contains("prerequisites changed")
    );
}

#[test]
fn native_cpu_expansion_does_not_claim_another_aggregates_useful_minimum() {
    let mut p = policy();
    p.resources.get_mut("ram").unwrap().unit = "millicores".into();
    p.resources.get_mut("ram").unwrap().native = Some(native::Resource::Cpu {
        eligible_cpus: vec![0],
    });
    for system in ["a", "b"] {
        p.cpu_aggregates.insert(
            system.into(),
            CpuAggregate {
                cgroup: format!("/fixture.slice/{system}"),
                priority: Priority::Priority,
                control_owner_ref: "fixture-native-owner".into(),
            },
        );
    }
    let mut s = State::new(p).unwrap();
    s.observe(observation(), now_ms(), &boot_id().unwrap())
        .unwrap();
    s.admit(request("a", "a", 10), now_ms(), &boot_id().unwrap())
        .unwrap();
    s.allocations.get_mut("a").unwrap().phase = Phase::Active;
    let mut observed = observation();
    observed
        .accounted_allocations
        .insert("a".into(), s.allocations["a"].revision);
    observed
        .managed_demand
        .insert("a".into(), BTreeMap::from([("ram".into(), 100)]));
    s.observe(observed, now_ms(), &boot_id().unwrap()).unwrap();
    let assessment = s
        .assess(&request("b", "b", 10), now_ms(), &boot_id().unwrap())
        .unwrap();
    assert!(assessment.can_admit);
    assert_eq!(assessment.commitments["ram"], 10);
    assert_eq!(s.resource_observations["ram"].managed_demand["a"], 100);
}

// This is an inert domain-interface fixture, not workload checkpoint or native
// memory-release qualification. Its real child process tests durable request
// ordering, bounded frontend loss and exact selectors through the installed CLI.
fn pressure_fixture() -> Fixture {
    let fixture = Fixture::new();
    let script = fixture.0.join("domain.py");
    fs::write(&script,r#"import json,sys,time
from pathlib import Path
root=Path(__file__).parent
request=json.load(sys.stdin)
p=root/'domain.json'
data=json.loads(p.read_text())
if request['action']!='inspect':
    with (root/'effects.jsonl').open('a') as f:f.write(json.dumps(request)+'\n')
    assert request['expected_attempt']==1
    prior=data['pressure_request']
    repeated_release=request['action']=='request_release' and prior is not None and prior['request_id']==request['request_id']
    repeated_resume=request['action']=='request_resume' and prior is not None and prior.get('resume') is not None and prior['resume']['request_id']==request['request_id']
    if repeated_release:assert request['expected_control_revision']==7
    elif repeated_resume:assert request['expected_control_revision']==8
    else:
        assert request['expected_control_revision']==data['control_revision']
        data['control_revision']+=1
    if request['action']=='request_release':
        data['pressure_request']={'request_id':request['request_id'],'origin':'capacity_pressure','state':'accepted','effect_started':True,'processes_stopped':False,'fresh_checkpoint':None,'resources_released':False,'resume':None}
    else:
        assert request['pressure_request_id']==data['pressure_request']['request_id']
        data['pressure_request']['state']='resumed'
        data['pressure_request']['resume']={'request_id':request['request_id'],'pressure_request_id':request['pressure_request_id'],'state':'accepted','successor':{'attempt_id':'2','allocation_id':'successor','owner_ref':'fixture/successor','state':'waiting'}}
    p.write_text(json.dumps(data))
    if (root/'delay').exists():time.sleep(1)
print(json.dumps({'status':'ok','data':data}))
"#).unwrap();
    let mut store = Store::open(&fixture.0).unwrap();
    store.state.policy.domain_controls.insert(
        "a".into(),
        pressure::DomainControl {
            argv: vec!["/usr/bin/python3".into(), script.to_string_lossy().into()],
            qualification_ref: "inert owner-contract fixture only".into(),
            timeout_ms: 2000,
            stable_recovery_ms: 1,
        },
    );
    let mut req = request("a", "a", 50);
    req.attempt_id = "1".into();
    store
        .state
        .admit(req, now_ms(), &boot_id().unwrap())
        .unwrap();
    let revision = store.state.allocations["a"].revision;
    store
        .state
        .begin_step(
            "a",
            revision,
            "fixture-start",
            process(std::process::id()).unwrap(),
        )
        .unwrap();
    let mut observed = observation();
    observed.external_demand.insert("ram".into(), 50);
    store
        .state
        .observe(observed, now_ms(), &boot_id().unwrap())
        .unwrap();
    store.save().unwrap();
    fs::write(fixture.0.join("domain.json"),serde_json::to_vec(&json!({"allocation_id":"a","owner_ref":"domain/a","operation_id":"operation/a","attempt_id":"1","control_revision":7,"release_eligible":true,"resume_eligible":false,"qualification_refs":["inert fixture"],"limitation":null,"pressure_request":null})).unwrap()).unwrap();
    drop(store);
    fixture
}
fn effects(fixture: &Fixture) -> Vec<Value> {
    fs::read_to_string(fixture.0.join("effects.jsonl"))
        .unwrap_or_default()
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect()
}
#[test]
fn pressure_joins_exact_owner_release_and_preserves_independent_hold_before_resume() {
    let fixture = pressure_fixture();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let first = effects(&fixture);
    assert_eq!(first.len(), 1);
    assert_eq!(first[0]["action"], "request_release");
    assert_eq!(first[0]["expected_control_revision"], 7);
    assert_eq!(first[0]["request_id"].as_str().unwrap().len(), 32);
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    assert_eq!(effects(&fixture).len(), 1);
    let mut store = Store::open(&fixture.0).unwrap();
    assert_eq!(store.state.allocations["a"].phase, Phase::Active);
    release(&mut store.state, "a");
    store
        .state
        .observe(observation(), now_ms(), &boot_id().unwrap())
        .unwrap();
    store.state.hold(hold("independent-user-pause")).unwrap();
    store.save().unwrap();
    drop(store);
    let path = fixture.0.join("domain.json");
    let mut data: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    data["pressure_request"]["state"] = json!("released");
    data["pressure_request"]["processes_stopped"] = json!(true);
    data["pressure_request"]["resources_released"] = json!(true);
    data["pressure_request"]["fresh_checkpoint"] = json!({"ref":"inert"});
    data["resume_eligible"] = json!(true);
    fs::write(&path, serde_json::to_vec(&data).unwrap()).unwrap();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    assert_eq!(effects(&fixture).len(), 1);
    let mut store = Store::open(&fixture.0).unwrap();
    let h = store.state.holds["independent-user-pause"].clone();
    store
        .state
        .release_hold(&h.hold_id, &h.owner_ref, h.revision)
        .unwrap();
    store.save().unwrap();
    drop(store);
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    std::thread::sleep(std::time::Duration::from_millis(3));
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let actual = effects(&fixture);
    assert_eq!(actual.len(), 2);
    assert_eq!(actual[1]["action"], "request_resume");
    assert_eq!(actual[1]["pressure_request_id"], actual[0]["request_id"]);
    assert_ne!(actual[1]["request_id"], actual[0]["request_id"]);
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    assert_eq!(effects(&fixture).len(), 2);
}
#[test]
fn pressure_lost_acceptance_is_reconciled_without_duplicate_or_uncharged_work() {
    let fixture = pressure_fixture();
    fs::write(fixture.0.join("delay"), b"fixture delay").unwrap();
    let mut store = Store::open(&fixture.0).unwrap();
    store
        .state
        .policy
        .domain_controls
        .get_mut("a")
        .unwrap()
        .timeout_ms = 100;
    store.save().unwrap();
    drop(store);
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    assert_eq!(effects(&fixture).len(), 1);
    let store = Store::open(&fixture.0).unwrap();
    assert!(store.state.pressure["a"].last_error.is_some());
    assert_eq!(store.state.allocations["a"].phase, Phase::Active);
    drop(store);
    fs::remove_file(fixture.0.join("delay")).unwrap();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    assert_eq!(effects(&fixture).len(), 1);
}
#[test]
fn pressure_requires_current_useful_owner_eligibility_and_never_refreshes_stale_intent() {
    let fixture = pressure_fixture();
    let path = fixture.0.join("domain.json");
    let mut data: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    data["release_eligible"] = json!(false);
    fs::write(&path, serde_json::to_vec(&data).unwrap()).unwrap();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    assert!(effects(&fixture).is_empty());
    let mut store = Store::open(&fixture.0).unwrap();
    assert!(!store.state.pressure.contains_key("a"));
    // A prospective busy owner creates no intent. Separately retained original
    // intent must still preserve its exact expected revision rather than refresh.
    store.state.pressure.insert(
        "a".into(),
        pressure::Transition {
            allocation_id: "a".into(),
            request_id: "0123456789abcdef0123456789abcdef".into(),
            reason: "retained fixture pressure request".into(),
            constrained_resources: std::collections::BTreeSet::from(["ram".into()]),
            stage: "blocked".into(),
            expected_control_revision: Some(6),
            resume_request_id: None,
            resume_control_revision: None,
            last_observation: None,
            last_error: None,
            recovery_since_ms: None,
            recovery_boot_id: None,
            last_check_ms: 0,
        },
    );
    store.save().unwrap();
    drop(store);
    data["release_eligible"] = json!(true);
    fs::write(&path, serde_json::to_vec(&data).unwrap()).unwrap();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    assert!(effects(&fixture).is_empty());
    assert_eq!(
        Store::open(&fixture.0).unwrap().state.pressure["a"].stage,
        "blocked"
    );
}

#[test]
fn pressure_retries_only_owner_verified_missing_handoff_with_original_selectors() {
    let fixture = pressure_fixture();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let path = fixture.0.join("domain.json");
    let mut data: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    data["pressure_request"]["effect_started"] = json!(false);
    data["pressure_request"]["retry_eligible"] = json!(true);
    fs::write(&path, serde_json::to_vec(&data).unwrap()).unwrap();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let requests = effects(&fixture);
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0], requests[1]);
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    assert_eq!(effects(&fixture).len(), 2);
}

#[test]
fn io_interval_counts_external_work_and_rejects_changed_backend_or_incarnation() {
    let mut s = state();
    s.admit(request("a", "a", 10), now_ms(), &boot_id().unwrap())
        .unwrap();
    let process = process(std::process::id()).unwrap();
    let runtime = RuntimeBinding {
        process: process.clone(),
        cgroup: "/fixture-only".into(),
        supervisor_ref: "inert-counter-fixture".into(),
    };
    let device = storage::Device {
        filesystem_device: "fixture-partition".into(),
        physical_device: "fixture-backend".into(),
    };
    let previous = storage::Sample {
        boot_id: boot_id().unwrap(),
        monotonic_ms: 1000,
        devices: BTreeMap::from([("ram".into(), device)]),
        total_bytes: BTreeMap::from([("ram".into(), 10000)]),
        managed_bytes: BTreeMap::from([("a".into(), BTreeMap::from([("ram".into(), 1000)]))]),
        managed_processes: BTreeMap::from([("a".into(), process)]),
        unavailable: BTreeMap::new(),
    };
    let mut current = previous.clone();
    current.monotonic_ms = 3000;
    current.total_bytes.insert("ram".into(), 14000);
    current
        .managed_bytes
        .get_mut("a")
        .unwrap()
        .insert("ram".into(), 2000);
    s.native_io_counters = Some(previous);
    let id = "a".to_string();
    let bindings = vec![(&id, &s.allocations["a"], &runtime)];
    let mut accounted = BTreeMap::new();
    let mut managed = BTreeMap::new();
    assert_eq!(
        storage::rate(&s, &current, "ram", &bindings, &mut accounted, &mut managed).unwrap(),
        1500
    );
    assert_eq!(managed["a"]["ram"], 500);
    current.managed_processes.get_mut("a").unwrap().start_ticks += 1;
    accounted.clear();
    managed.clear();
    assert_eq!(
        storage::rate(&s, &current, "ram", &bindings, &mut accounted, &mut managed).unwrap(),
        2000
    );
    assert!(accounted.is_empty());
    current.devices.get_mut("ram").unwrap().physical_device = "replacement".into();
    assert!(storage::rate(&s, &current, "ram", &bindings, &mut accounted, &mut managed).is_err());
}
#[test]
fn io_limits_cannot_be_forgotten_by_policy_removal_or_backend_rebinding() {
    let mut p = policy();
    p.resources.get_mut("ram").unwrap().unit = "bytes_per_second".into();
    p.resources.get_mut("ram").unwrap().native = Some(native::Resource::IoBandwidth {
        device: storage::DeviceBinding {
            mountpoint: "/fixture-not-mounted".into(),
            filesystem_uuid: "inert-fixture".into(),
        },
        capacity_evidence_ref: "inert-fixture-only".into(),
    });
    p.cpu_aggregates.insert(
        "a".into(),
        CpuAggregate {
            cgroup: "/fixture".into(),
            priority: Priority::Priority,
            control_owner_ref: "fixture-owner".into(),
        },
    );
    p.io_limits.insert(
        "a".into(),
        BTreeMap::from([(
            "ram".into(),
            storage::Limit {
                read_bytes_per_second: Some(10),
                write_bytes_per_second: Some(10),
                change_ref: "inert fixture".into(),
                ending_condition: "exact fixture ending".into(),
            },
        )]),
    );
    let mut s = State::new(p).unwrap();
    let mut replacement = s.policy.clone();
    replacement.io_limits.clear();
    assert!(
        s.reconfigure_policy(0, replacement, "remove-owner".into())
            .unwrap_err()
            .contains("clear selected native I/O")
    );
    let mut replacement = s.policy.clone();
    replacement
        .io_limits
        .get_mut("a")
        .unwrap()
        .get_mut("ram")
        .unwrap()
        .read_bytes_per_second = Some(0);
    assert!(State::new(replacement).is_err());
}
#[test]
fn io_requires_actual_mount_and_cannot_double_count_backend_aliases() {
    let binding = storage::DeviceBinding {
        mountpoint: "/fixture-not-mounted".into(),
        filesystem_uuid: "inert-fixture".into(),
    };
    assert!(storage::device(&binding).is_err());
    let mut p = policy();
    for resource in p.resources.values_mut() {
        resource.unit = "bytes_per_second".into();
        resource.native = Some(native::Resource::IoBandwidth {
            device: binding.clone(),
            capacity_evidence_ref: "inert fixture only".into(),
        });
    }
    assert!(
        State::new(p)
            .unwrap_err()
            .contains("overlap physical capacity")
    );
}

#[test]
fn completed_result_or_independent_successor_closes_obsolete_pressure_without_resume() {
    for complete in [true, false] {
        let fixture = pressure_fixture();
        assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
        let mut store = Store::open(&fixture.0).unwrap();
        release(&mut store.state, "a");
        store.save().unwrap();
        drop(store);
        let path = fixture.0.join("domain.json");
        let mut data: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        data["pressure_request"]["state"] = json!("released");
        data["pressure_request"]["processes_stopped"] = json!(true);
        data["pressure_request"]["resources_released"] = json!(true);
        if complete {
            data["original_attempt_state"] = json!("completed");
        } else {
            data["pressure_request"]["fresh_checkpoint"] = json!({"ref":"inert"});
            data["independent_successor"] = json!({"origin":"manual","request_id":null,"attempt_id":"2","allocation_id":"manual-successor","owner_ref":"fixture/manual-successor","state":"waiting"});
        }
        fs::write(&path, serde_json::to_vec(&data).unwrap()).unwrap();
        assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
        assert_eq!(effects(&fixture).len(), 1);
        assert_eq!(
            Store::open(&fixture.0).unwrap().state.pressure["a"].stage,
            "completed"
        );
    }
}

#[test]
fn waiting_explanation_uses_actual_queue_precedence_without_mutation() {
    let mut s = state();
    let now = now_ms();
    let boot = boot_id().unwrap();
    s.admit(request("holder", "first", 60), now, &boot).unwrap();
    s.admit(request("older-large", "second", 50), now, &boot)
        .unwrap();
    s.admit(request("newer-small", "third", 10), now, &boot)
        .unwrap();
    let before = serde_json::to_value(&s).unwrap();
    let decisions = s.waiting_decisions(now, &boot).unwrap();
    assert!(!decisions["older-large"].would_admit);
    assert!(
        decisions["older-large"]
            .assessment
            .reasons
            .iter()
            .any(|s| s.contains("insufficient"))
    );
    assert!(decisions["newer-small"].assessment.can_admit);
    assert!(!decisions["newer-small"].would_admit);
    assert_eq!(
        decisions["newer-small"].blocked_by_allocations,
        vec!["older-large"]
    );
    assert_eq!(before, serde_json::to_value(&s).unwrap());
    release(&mut s, "holder");
    let decisions = s.waiting_decisions(now, &boot).unwrap();
    let expected: std::collections::BTreeSet<_> = decisions
        .iter()
        .filter(|(_, d)| d.would_admit)
        .map(|(id, _)| id.clone())
        .collect();
    let admitted: std::collections::BTreeSet<_> =
        s.schedule(now, &boot).unwrap().into_iter().collect();
    assert_eq!(admitted, expected);
    assert_eq!(admitted.len(), 2);
}

#[test]
fn admit_if_fit_refusals_leave_no_allocation_or_scheduler_mutation() {
    let boot = boot_id().unwrap();
    for cause in [
        "capacity",
        "ineligible",
        "hold",
        "missing",
        "stale",
        "fairness",
    ] {
        let mut s = state();
        let mut candidate = request("opportunistic", "new-system", 20);
        match cause {
            "capacity" => {
                s.admit(request("active", "existing", 80), now_ms(), &boot)
                    .unwrap();
            }
            "ineligible" => {
                candidate.demand.insert("ram".into(), 101);
            }
            "hold" => {
                s.hold(hold("independent-user-pause")).unwrap();
            }
            "missing" => {
                s = State::new(policy()).unwrap();
            }
            "fairness" => {
                s.admit(request("active", "existing", 40), now_ms(), &boot)
                    .unwrap();
                s.admit(request("older-waiter", "older-system", 60), now_ms(), &boot)
                    .unwrap();
                candidate.priority = Priority::Normal;
                candidate.demand.insert("ram".into(), 10);
                assert!(s.assess(&candidate, now_ms(), &boot).unwrap().can_admit);
            }
            _ => {}
        }
        let observed_before = serde_json::to_value(&s).unwrap();
        let now = now_ms() + if cause == "stale" { 60001 } else { 0 };
        let assessment = s.admit_if_fit(candidate, now, &boot).unwrap();
        assert!(!assessment.can_admit, "{cause}");
        assert!(!s.allocations.contains_key("opportunistic"), "{cause}");
        assert_eq!(
            serde_json::to_value(&s).unwrap(),
            observed_before,
            "{cause}"
        );
    }
}

#[test]
fn admit_if_fit_success_uses_normal_scheduler_and_duplicate_identity_remains_exact() {
    let mut s = state();
    let mut normal = s.clone();
    let boot = boot_id().unwrap();
    let now = now_ms();
    let admitted = request("opportunistic", "indexer", 50);
    assert!(
        normal
            .admit(admitted.clone(), now, &boot)
            .unwrap()
            .can_admit
    );
    assert!(
        s.admit_if_fit(admitted.clone(), now, &boot)
            .unwrap()
            .can_admit
    );
    assert_eq!(
        serde_json::to_value(&s).unwrap(),
        serde_json::to_value(&normal).unwrap()
    );
    let before = serde_json::to_value(&s).unwrap();
    assert!(
        s.admit_if_fit(admitted.clone(), now, &boot)
            .unwrap()
            .can_admit
    );
    assert_eq!(serde_json::to_value(&s).unwrap(), before);
    let mut rebound = admitted;
    rebound.configuration_ref = "changed-configuration".into();
    assert!(s.admit_if_fit(rebound, now, &boot).is_err());
    assert_eq!(serde_json::to_value(&s).unwrap(), before);

    let waiting = request("existing-waiter", "other", 50);
    assert!(!s.admit(waiting.clone(), now, &boot).unwrap().can_admit);
    let before = serde_json::to_value(&s).unwrap();
    assert!(!s.admit_if_fit(waiting, now, &boot).unwrap().can_admit);
    assert_eq!(serde_json::to_value(&s).unwrap(), before);
}

#[test]
fn admit_if_fit_cli_decline_has_no_durable_waiter_and_success_reports_allocation() {
    let fixture = Fixture::new();
    let mut store = Store::open(&fixture.0).unwrap();
    store.state.hold(hold("independent-user-pause")).unwrap();
    store.save().unwrap();
    drop(store);
    let before = fs::read(fixture.0.join("state.json")).unwrap();
    let req = request("on-demand", "indexer", 20);
    let (ok, declined) = rpc(&fixture.0, json!({"action":"admit_if_fit","request":req}));
    assert!(ok);
    assert_eq!(declined["data"]["assessment"]["can_admit"], false);
    assert!(declined["data"]["allocation"].is_null());
    assert_eq!(fs::read(fixture.0.join("state.json")).unwrap(), before);
    let mut store = Store::open(&fixture.0).unwrap();
    let h = store.state.holds["independent-user-pause"].clone();
    store
        .state
        .release_hold(&h.hold_id, &h.owner_ref, h.revision)
        .unwrap();
    store.save().unwrap();
    drop(store);
    let (ok, accepted) = rpc(&fixture.0, json!({"action":"admit_if_fit","request":req}));
    assert!(ok);
    assert_eq!(accepted["data"]["assessment"]["can_admit"], true);
    assert_eq!(
        accepted["data"]["allocation"]["request"]["allocation_id"],
        "on-demand"
    );
    assert_eq!(accepted["data"]["launched"], false);
}

// Reuse the inert protocol fixture while giving each domain its own observation.
// No native workload, checkpoint or memory-release claim is established here.
fn idle_pressure_fixture(idle_release_eligible: bool) -> Fixture {
    let fixture = pressure_fixture();
    let script = fixture.0.join("domain.py");
    let routed = fs::read_to_string(&script).unwrap().replace(
        "p=root/'domain.json'",
        "p=root/('domain-'+request['allocation_id']+'.json')",
    );
    fs::write(&script, routed).unwrap();
    let template: Value =
        serde_json::from_slice(&fs::read(fixture.0.join("domain.json")).unwrap()).unwrap();
    let mut store = Store::open(&fixture.0).unwrap();
    let control = store.state.policy.domain_controls["a"].clone();
    store.state = state();
    for (id, priority, memory) in [
        ("render", Priority::Normal, 30),
        ("preview-cache", Priority::Priority, 20),
    ] {
        store
            .state
            .policy
            .domain_controls
            .insert(id.into(), control.clone());
        let mut req = request(id, id, memory);
        req.attempt_id = "1".into();
        req.priority = priority;
        assert!(
            store
                .state
                .admit(req, now_ms(), &boot_id().unwrap())
                .unwrap()
                .can_admit
        );
        let revision = store.state.allocations[id].revision;
        store
            .state
            .begin_step(
                id,
                revision,
                "fixture-start",
                process(std::process::id()).unwrap(),
            )
            .unwrap();
        let mut domain = template.clone();
        domain["allocation_id"] = json!(id);
        domain["owner_ref"] = json!(format!("domain/{id}"));
        domain["operation_id"] = json!(format!("operation/{id}"));
        if id == "preview-cache" {
            domain["reclaimable_idle"] = json!(true);
            domain["release_eligible"] = json!(idle_release_eligible);
        }
        fs::write(
            fixture.0.join(format!("domain-{id}.json")),
            serde_json::to_vec(&domain).unwrap(),
        )
        .unwrap();
    }
    let mut observed = observation();
    observed.external_demand.insert("ram".into(), 50);
    store
        .state
        .observe(observed, now_ms(), &boot_id().unwrap())
        .unwrap();
    store.save().unwrap();
    drop(store);
    fixture
}

#[test]
fn pressure_prefers_eligible_idle_owner_before_older_normal_render() {
    let fixture = idle_pressure_fixture(true);
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let actual = effects(&fixture);
    assert_eq!(actual.len(), 1);
    assert_eq!(actual[0]["allocation_id"], "preview-cache");
    assert_eq!(actual[0]["action"], "request_release");
    assert_eq!(
        Store::open(&fixture.0).unwrap().state.allocations["render"].phase,
        Phase::Active
    );
}

#[test]
fn pressure_idle_claim_without_current_release_eligibility_does_not_displace_render() {
    let fixture = idle_pressure_fixture(false);
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let actual = effects(&fixture);
    assert_eq!(actual.len(), 1);
    assert_eq!(actual[0]["allocation_id"], "render");
    assert_eq!(actual[0]["action"], "request_release");
}

fn preferred_gpu_fixture() -> Fixture {
    let f = idle_pressure_fixture(true);
    let mut store = Store::open(&f.0).unwrap();
    let state = &mut store.state;
    state.policy.resources.get_mut("gpu").unwrap().native = Some(native::Resource::GpuMemory {
        uuid: "fixture-gpu".into(),
    });
    state.policy.cpu_aggregates.insert(
        "preview-cache".into(),
        serde_json::from_value(
            json!({"cgroup":"/fixture/preview","priority":"priority","control_owner_ref":"fixture"}),
        )
        .unwrap(),
    );
    state
        .observe(observation(), now_ms(), &boot_id().unwrap())
        .unwrap();
    let select = json!({"action":"configure_resource_preferences","revision":0,
        "expected_policy_revision":state.policy_revision, "expected_source":{"host_id":"fixture-host","instance_id":state.instance_id},
        "change_ref":"fixture GPU precedence","resources":{"gpu":{"system_id":"preview-cache","gpu_uuid":"fixture-gpu"}}});
    store.save().unwrap();
    drop(store);
    assert!(rpc(&f.0, select).0);
    f
}

fn queue_gpu_waiter(f: &Fixture, id: &str, system: &str, ram: u64) {
    let mut req = request(id, system, ram);
    if ram == 0 {
        req.demand.remove("ram");
    }
    req.demand.insert("gpu".into(), 85);
    let mut store = Store::open(&f.0).unwrap();
    assert!(
        !store
            .state
            .admit(req, now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    store.save().unwrap();
}

fn set_domain_field(f: &Fixture, id: &str, key: &str, value: bool) {
    let path = f.0.join(format!("domain-{id}.json"));
    let mut domain: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    domain[key] = json!(value);
    fs::write(path, serde_json::to_vec(&domain).unwrap()).unwrap();
}

#[test]
fn gpu_pressure_context_reaches_only_prospective_inspects_from_one_snapshot() {
    let f = preferred_gpu_fixture();
    queue_gpu_waiter(&f, "older", "other", 80);
    queue_gpu_waiter(&f, "preferred", "preview-cache", 0);
    let script = f.0.join("domain.py");
    let owner = fs::read_to_string(&script).unwrap().replace(
        "request=json.load(sys.stdin)",
        r#"request=json.load(sys.stdin)
import os
context=os.environ.get('APEIROS_CAPACITY_PRESSURE_CONTEXT_V1')
with (root/'callbacks.jsonl').open('a') as f:
    f.write(json.dumps({'request':request,'context':context})+'\n')"#,
    );
    fs::write(script, owner).unwrap();
    let state = Store::open(&f.0).unwrap().state.clone();
    let mut cli = Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"));
    cli.env(
        "APEIROS_CAPACITY_PRESSURE_CONTEXT_V1",
        "ambient untrusted context",
    );
    assert!(rpc_command(cli, &f.0, json!({"action":"pressure_tick"})).0);
    let callbacks: Vec<Value> = fs::read_to_string(f.0.join("callbacks.jsonl"))
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(callbacks.len(), 4);
    assert_eq!(callbacks[0]["request"]["action"], "inspect");
    assert_eq!(callbacks[1]["request"]["action"], "inspect");
    assert_eq!(callbacks[0]["context"], callbacks[1]["context"]);
    let hint: Value = serde_json::from_str(callbacks[0]["context"].as_str().unwrap()).unwrap();
    let now = hint["captured_at_ms"].as_u64().unwrap();
    assert_eq!(
        hint,
        pressure::gpu_pressure_context(&state, now, &boot_id().unwrap())
    );
    assert_eq!(hint["schema"], "apeiros.capacity.pressure_inspection.v1");
    assert_eq!(
        hint["source"],
        json!({"host_id":"fixture-host","instance_id":state.instance_id,"boot_id":boot_id().unwrap()})
    );
    assert_eq!(hint["state_revision"], state.revision);
    assert_eq!(hint["policy_revision"], state.policy_revision);
    assert_eq!(hint["preference_revision"], 1);
    let waiter = json!({"allocation_id":"preferred","system_id":"preview-cache"});
    let sample = state.resource_observations["gpu"].observed_at_ms;
    let valid = sample + state.policy.resources["gpu"].observation_max_age_ms;
    assert!((sample..=valid).contains(&now));
    assert_eq!(
        hint["gpu_constraints"],
        json!({"gpu":{"gpu_uuid":"fixture-gpu","observed_at_ms":sample,"valid_through_ms":valid,
        "cause":"waiting_demand","waiter":waiter,"preferred_system":"preview-cache","preferred_waiter":waiter}})
    );
    for c in &callbacks[..3] {
        assert_eq!(c["request"].as_object().unwrap().len(), 4); // Closed inspect stdin is unchanged.
    }
    assert_eq!(callbacks[2]["request"]["action"], "inspect");
    assert!(callbacks[2]["context"].is_null()); // Fresh rejoin.
    assert_eq!(callbacks[3]["request"]["action"], "request_release");
    assert!(callbacks[3]["context"].is_null());
}

#[test]
fn gpu_pressure_context_separates_physical_pressure_from_eligible_short_waiters() {
    let f = preferred_gpu_fixture();
    queue_gpu_waiter(&f, "preferred", "preview-cache", 0);
    queue_gpu_waiter(&f, "ordinary", "other", 0);
    let mut store = Store::open(&f.0).unwrap();
    let s = &mut store.state;
    let boot = boot_id().unwrap();
    let mut obs = observation();
    obs.external_demand.insert("gpu".into(), 75);
    let now = obs.observed_at_ms;
    s.observe(obs, now, &boot).unwrap();
    let snapshot = |s: &State| pressure::gpu_pressure_context(s, now, &boot);
    let gpu = &snapshot(s)["gpu_constraints"]["gpu"];
    assert_eq!(gpu["cause"], "physical_pressure");
    assert!(gpu["waiter"].is_null());
    assert_eq!(
        gpu["preferred_waiter"],
        json!({"allocation_id":"preferred","system_id":"preview-cache"})
    );
    let mut pause = hold("owner-pause");
    pause.scope.resource_ids.clear();
    pause
        .scope
        .operation_ids
        .insert("operation/preferred".into());
    s.hold(pause).unwrap();
    let gpu = &snapshot(s)["gpu_constraints"]["gpu"];
    assert_eq!(gpu["cause"], "physical_pressure");
    assert!(gpu["preferred_waiter"].is_null());
    assert_eq!(gpu["preferred_system"], "preview-cache");
    assert_eq!(
        pressure::gpu_pressure_context(s, now + 60001, &boot)["gpu_constraints"],
        json!({})
    );

    // A newly fitting preferred waiter can still be queued between a native
    // observation and scheduling. Its presence alone must not excuse a drain.
    let mut small = request("small", "preview-cache", 1);
    small.demand = BTreeMap::from([("gpu".into(), 30)]);
    assert!(!s.admit(small, now, &boot).unwrap().can_admit);
    let recovered = observation();
    let now = recovered.observed_at_ms;
    s.observe(recovered, now, &boot).unwrap();
    assert_eq!(s.allocations["small"].phase, Phase::Waiting);
    assert_eq!(s.preferred_gpu_waiters(now, &boot)["gpu"], "small");
    let gpu = &pressure::gpu_pressure_context(s, now, &boot)["gpu_constraints"]["gpu"];
    assert_eq!(gpu["cause"], "waiting_demand");
    assert_eq!(
        gpu["waiter"],
        json!({"allocation_id":"ordinary","system_id":"other"})
    );
    assert!(gpu["preferred_waiter"].is_null());
}

#[test]
fn gpu_pressure_preference_protects_useful_work_and_releases_idle_weights() {
    let f = preferred_gpu_fixture();
    queue_gpu_waiter(&f, "ordinary", "other", 1);
    set_domain_field(&f, "render", "release_eligible", false);
    set_domain_field(&f, "preview-cache", "reclaimable_idle", false);
    assert!(rpc(&f.0, json!({"action":"pressure_tick"})).0);
    assert!(effects(&f).is_empty());
    set_domain_field(&f, "preview-cache", "reclaimable_idle", true);
    assert!(rpc(&f.0, json!({"action":"pressure_tick"})).0);
    let sent = effects(&f);
    assert_eq!(sent.len(), 1);
    assert_eq!(sent[0]["allocation_id"], "preview-cache");
}

#[test]
fn gpu_pressure_preference_rechecks_idle_before_effect_and_allows_physical_release() {
    let f = preferred_gpu_fixture();
    queue_gpu_waiter(&f, "ordinary", "other", 1);
    set_domain_field(&f, "render", "release_eligible", false);
    let script = f.0.join("domain.py");
    let owner = fs::read_to_string(&script).unwrap().replace(
        "data=json.loads(p.read_text())",
        "data=json.loads(p.read_text())\nif request['allocation_id']=='preview-cache' and request['action']=='inspect':\n    p.write_text(json.dumps(dict(data,reclaimable_idle=False)))",
    );
    fs::write(script, owner).unwrap();
    assert!(rpc(&f.0, json!({"action":"pressure_tick"})).0);
    assert!(effects(&f).is_empty()); // Work began between selection and release.
    let mut store = Store::open(&f.0).unwrap();
    assert_eq!(store.state.pressure["preview-cache"].stage, "blocked");
    let mut obs = observation();
    obs.external_demand.insert("gpu".into(), 75);
    store
        .state
        .observe(obs, now_ms(), &boot_id().unwrap())
        .unwrap();
    store.save().unwrap();
    drop(store);
    assert!(rpc(&f.0, json!({"action":"pressure_tick"})).0);
    let sent = effects(&f);
    assert_eq!(sent.len(), 1);
    assert_eq!(sent[0]["allocation_id"], "preview-cache");
}

#[test]
fn gpu_pressure_preference_honors_the_floor_and_only_promotes_gpu_demand() {
    let f = preferred_gpu_fixture();
    let mut store = Store::open(&f.0).unwrap();
    let st = &store.state;
    let floor = json!({"action":"configure_reservations", "expected_instance_id":st.instance_id, "expected_policy_revision":st.policy_revision, "revision":0,
        "change_ref":"fixture selected floor","reservations":{"policy_revision":st.policy_revision,"resources":{"gpu":{
            "resource_policy":st.policy.resources["gpu"],"original_operational_margin":0,"system_floors":{"preview-cache":10},"selection_ref":"protected GPU floor"}}}});
    store.state.policy.domain_controls.remove("render");
    store.save().unwrap();
    drop(store);
    assert!(rpc(&f.0, floor).0);
    queue_gpu_waiter(&f, "ordinary", "other", 1);
    assert!(rpc(&f.0, json!({"action":"pressure_tick"})).0);
    assert!(effects(&f).is_empty()); // Releasing these idle weights cannot increase the other system's usable capacity.

    let f = preferred_gpu_fixture();
    queue_gpu_waiter(&f, "older", "other", 80);
    queue_gpu_waiter(&f, "preferred", "preview-cache", 0);
    let store = Store::open(&f.0).unwrap();
    let reasons = pressure::constrained(&store.state, now_ms(), &boot_id().unwrap());
    assert!(reasons["gpu"].ends_with(": preferred"));
    assert!(reasons["ram"].ends_with(": older"));
    drop(store);
    set_domain_field(&f, "preview-cache", "reclaimable_idle", false);
    assert!(rpc(&f.0, json!({"action":"pressure_tick"})).0);
    let sent = effects(&f);
    assert_eq!(sent.len(), 1);
    assert_eq!(sent[0]["allocation_id"], "render");
}

#[test]
fn standing_floor_deficit_requests_one_owner_release_without_a_waiter() {
    let f = preferred_gpu_fixture();
    let mut store = Store::open(&f.0).unwrap();
    let st = &mut store.state;
    st.policy
        .resources
        .get_mut("gpu")
        .unwrap()
        .operational_margin = 25;
    let configure = json!({"action":"configure_reservations","expected_instance_id":st.instance_id,
        "expected_policy_revision":st.policy_revision,"revision":0,"change_ref":"standing floor",
        "reservations":{"policy_revision":st.policy_revision,"resources":{"gpu":{
        "resource_policy":st.policy.resources["gpu"],"original_operational_margin":0,
        "system_floors":{"preview-cache":25},"selection_ref":"fixture floor"}}}});
    store.save().unwrap();
    drop(store);
    assert!(rpc(&f.0, configure).0);
    let mut store = Store::open(&f.0).unwrap();
    let mut sample = observation();
    sample.external_demand.insert("gpu".into(), 66);
    store
        .state
        .observe(sample, now_ms(), &boot_id().unwrap())
        .unwrap();
    store.save().unwrap();
    drop(store);
    assert!(rpc(&f.0, json!({"action":"pressure_tick"})).0);
    let sent = effects(&f);
    assert_eq!(sent.len(), 1);
    assert_eq!(sent[0]["allocation_id"], "render");
    assert_eq!(sent[0]["action"], "request_release");
    assert!(rpc(&f.0, json!({"action":"pressure_tick"})).0);
    assert_eq!(effects(&f).len(), 1);
    let mut store = Store::open(&f.0).unwrap();
    assert_eq!(store.state.allocations["preview-cache"].phase, Phase::Active);
    release(&mut store.state, "render");
    assert!(pressure::constrained(&store.state, now_ms(), &boot_id().unwrap()).is_empty());
    let original = &store.state.allocations["render"].request;
    assert!(
        !store
            .state
            .assess(original, now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
}

#[test]
fn busy_owner_creates_no_pressure_intent_and_can_be_selected_when_later_idle() {
    let fixture = pressure_fixture();
    let path = fixture.0.join("domain.json");
    let mut data: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    data["release_eligible"] = json!(false);
    data["reclaimable_idle"] = json!(true);
    fs::write(&path, serde_json::to_vec(&data).unwrap()).unwrap();
    let (ok, blocked) = rpc(&fixture.0, json!({"action":"pressure_tick"}));
    assert!(ok);
    let blocker = blocked["data"]["observed_blockers"]
        .as_array()
        .unwrap()
        .iter()
        .find(|value| value["allocation_id"] == "a")
        .unwrap();
    assert_eq!(blocker["control_revision"], 7);
    assert_eq!(blocker["release_eligible"], false);
    assert_eq!(blocker["effect_requested"], false);
    assert!(effects(&fixture).is_empty());
    assert!(
        !Store::open(&fixture.0)
            .unwrap()
            .state
            .pressure
            .contains_key("a")
    );
    data["release_eligible"] = json!(true);
    data["control_revision"] = json!(9);
    fs::write(&path, serde_json::to_vec(&data).unwrap()).unwrap();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let actual = effects(&fixture);
    assert_eq!(actual.len(), 1);
    assert_eq!(actual[0]["action"], "request_release");
    assert_eq!(actual[0]["expected_control_revision"], 9);
}

fn stateless_release_observation(fixture: &Fixture) -> Value {
    let path = fixture.0.join("domain.json");
    let mut data: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    data["reclaimable_idle"] = json!(true);
    data["pressure_request"]["state"] = json!("released");
    data["pressure_request"]["processes_stopped"] = json!(true);
    data["pressure_request"]["resources_released"] = json!(true);
    data["pressure_request"]["fresh_checkpoint"] = Value::Null;
    data["pressure_request"]["continuation_preserved"] = json!(true);
    data["pressure_request"]["continuation_ref"] =
        json!("exact immutable model recipe; no in-flight request");
    data["resume_eligible"] = json!(true);
    data
}

#[test]
fn stateless_pressure_release_rehydrates_optional_evidence_and_preserves_independent_hold() {
    let fixture = pressure_fixture();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let mut store = Store::open(&fixture.0).unwrap();
    release(&mut store.state, "a");
    store
        .state
        .observe(observation(), now_ms(), &boot_id().unwrap())
        .unwrap();
    store.state.hold(hold("independent-user-pause")).unwrap();
    store.save().unwrap();
    drop(store);
    let data = stateless_release_observation(&fixture);
    fs::write(
        fixture.0.join("domain.json"),
        serde_json::to_vec(&data).unwrap(),
    )
    .unwrap();
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    assert_eq!(effects(&fixture).len(), 1);
    let mut store = Store::open(&fixture.0).unwrap();
    assert_eq!(store.state.pressure["a"].stage, "released");
    assert!(store.state.pressure["a"].last_error.is_none());
    // A previous binary can round-trip state while dropping unknown fields.
    // The next reconciliation must inspect the owner again, not act on defaults.
    let mut legacy = serde_json::to_value(&store.state).unwrap();
    let observed = legacy["pressure"]["a"]["last_observation"]
        .as_object_mut()
        .unwrap();
    observed.remove("reclaimable_idle");
    let workflow = observed
        .get_mut("pressure_request")
        .unwrap()
        .as_object_mut()
        .unwrap();
    workflow.remove("continuation_preserved");
    workflow.remove("continuation_ref");
    store.state = serde_json::from_value(legacy).unwrap();
    store.save().unwrap();
    drop(store);
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let mut store = Store::open(&fixture.0).unwrap();
    let fresh = serde_json::to_value(&store.state.pressure["a"].last_observation).unwrap();
    assert_eq!(fresh["reclaimable_idle"], true);
    assert_eq!(fresh["pressure_request"]["continuation_preserved"], true);
    assert_eq!(fresh["pressure_request"]["save_acknowledged"], false);
    assert!(
        fresh["pressure_request"]["continuation_ref"]
            .as_str()
            .is_some_and(|s| !s.is_empty())
    );
    assert!(store.state.holds.contains_key("independent-user-pause"));
    assert_eq!(effects(&fixture).len(), 1);
    let h = store.state.holds["independent-user-pause"].clone();
    store
        .state
        .release_hold(&h.hold_id, &h.owner_ref, h.revision)
        .unwrap();
    store.save().unwrap();
    drop(store);
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    std::thread::sleep(std::time::Duration::from_millis(3));
    assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
    let actual = effects(&fixture);
    assert_eq!(actual.len(), 2);
    assert_eq!(actual[1]["action"], "request_resume");
    assert_eq!(actual[1]["pressure_request_id"], actual[0]["request_id"]);
}

#[test]
fn stateless_continuation_never_substitutes_for_release_identity_or_qualification() {
    for missing in [
        "reference",
        "blank_reference",
        "preserved",
        "stopped",
        "resources",
        "request",
        "owner",
        "qualification",
    ] {
        let fixture = pressure_fixture();
        assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
        let mut store = Store::open(&fixture.0).unwrap();
        release(&mut store.state, "a");
        store
            .state
            .observe(observation(), now_ms(), &boot_id().unwrap())
            .unwrap();
        store.save().unwrap();
        drop(store);
        let mut data = stateless_release_observation(&fixture);
        match missing {
            "reference" => {
                data["pressure_request"]
                    .as_object_mut()
                    .unwrap()
                    .remove("continuation_ref");
            }
            "blank_reference" => {
                data["pressure_request"]["continuation_ref"] = json!(" ");
            }
            "preserved" => {
                data["pressure_request"]["continuation_preserved"] = json!(false);
            }
            "stopped" => {
                data["pressure_request"]["processes_stopped"] = json!(false);
            }
            "resources" => {
                data["pressure_request"]["resources_released"] = json!(false);
            }
            "request" => {
                data["pressure_request"]["request_id"] = json!("another-workflow");
            }
            "owner" => {
                data["owner_ref"] = json!("another-owner");
            }
            "qualification" => {
                data["qualification_refs"] = json!([]);
            }
            _ => unreachable!(),
        }
        fs::write(
            fixture.0.join("domain.json"),
            serde_json::to_vec(&data).unwrap(),
        )
        .unwrap();
        assert!(rpc(&fixture.0, json!({"action":"pressure_tick"})).0);
        let store = Store::open(&fixture.0).unwrap();
        assert!(store.state.pressure["a"].last_error.is_some(), "{missing}");
        assert_ne!(store.state.pressure["a"].stage, "resumed", "{missing}");
        assert_eq!(effects(&fixture).len(), 1, "{missing}");
    }
}
