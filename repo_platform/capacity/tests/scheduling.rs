use serde_json::json;
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use apeiros_capacity::scheduling::*;
use apeiros_capacity::*;

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "capacity-scheduling-{}-{}",
            std::process::id(),
            fs::read_to_string("/proc/sys/kernel/random/uuid")
                .unwrap()
                .trim()
        ));
        fs::create_dir(&root).unwrap();
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
        Store::initialize(&root, policy()).unwrap();
        Self(root)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn policy() -> Policy {
    serde_json::from_value(json!({"host_id":"fixture","machine_id":machine_id().unwrap(),
        "resources":{"ram":{"unit":"bytes","capacity":100,"operational_margin":0,"priority_growth_margin":50,"observation_max_age_ms":60000}},
        "cpu_aggregates":{"old":{"cgroup":"/fixture.slice/old.slice","priority":"normal","control_owner_ref":"fixture"},
            "indexer":{"cgroup":"/fixture.slice/indexer.slice","priority":"priority","control_owner_ref":"fixture"}}})).unwrap()
}
fn request(id: &str) -> Request {
    serde_json::from_value(json!({"allocation_id":id,"host_id":"fixture","system_id":"old","priority":"normal","owner_ref":"domain/indexer","operation_id":id,"attempt_id":"one","source_ref":"source","configuration_ref":"config","demand":{"ram":60},"eligibility_ref":"peak","release_route":"owner"})).unwrap()
}
fn observe(s: &mut State) {
    s.observe(serde_json::from_value(json!({"host_id":"fixture","boot_id":boot_id().unwrap(),"observed_at_ms":now_ms(),"source_ref":"fixture","external_demand":{"ram":0},"accounted_allocations":{},"managed_demand":{}})).unwrap(),now_ms(),&boot_id().unwrap()).unwrap();
}
fn config(s: &State) -> Configuration {
    Configuration {
        schema: "apeiros.capacity.scheduling.v1".into(),
        instance_id: s.instance_id.clone(),
        host_id: s.policy.host_id.clone(),
        revision: 1,
        change_ref: "explicit-user-priority-and-phase-evidence".into(),
        routes: vec![Route {
            owner_ref: "domain/indexer".into(),
            original_system_id: "old".into(),
            original_priority: Priority::Normal,
            system_id: "indexer".into(),
            priority: Priority::Priority,
            execution_system_id: "old".into(),
        }],
        bookings: BTreeMap::new(),
        native_priorities: BTreeMap::new(),
        observe_running_cpu: false,
        observe_running_memory: false,
        demand_changes: BTreeMap::new(),
        reservations: None,
    }
}
fn expected(s: &State, id: &str) -> BTreeMap<String, ExpectedAllocation> {
    let a = &s.allocations[id];
    BTreeMap::from([(
        id.into(),
        ExpectedAllocation {
            revision: a.revision,
            request: a.request.clone(),
            runtime: a.runtime.clone(),
        },
    )])
}
#[test]
fn effective_class_preserves_original_request_and_both_hold_scopes() {
    let f = Fixture::new();
    let mut st = Store::open(&f.0).unwrap();
    observe(&mut st.state);
    let r = request("a");
    st.state
        .admit(r.clone(), now_ms(), &boot_id().unwrap())
        .unwrap();
    // Ineligible requests are not queued, so use a useful admitted original.
    let mut r = r;
    r.demand.insert("ram".into(), 40);
    st.state
        .admit(r.clone(), now_ms(), &boot_id().unwrap())
        .unwrap();
    st.save().unwrap();
    let c = config(&st.state);
    let e = expected(&st.state, "a");
    let instance = st.state.instance_id.clone();
    st.configure_scheduling(&instance, 0, 0, c, &e).unwrap();
    assert_eq!(st.state.allocations["a"].request, r);
    assert_eq!(st.state.effective_request(&r).system_id, "indexer");
    assert!(
        st.state
            .admit(r.clone(), now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    for system in ["old", "indexer"] {
        let scope: Scope =
            serde_json::from_value(json!({"host_id":"fixture","system_ids":[system]})).unwrap();
        assert!(st.state.scope_matches(&scope, &r));
    }
    st.state.hold(serde_json::from_value(json!({"hold_id":"pause","owner_ref":"user","reason":"independent","scope":{"host_id":"fixture","system_ids":["indexer"]},"revision":0})).unwrap()).unwrap();
    assert!(
        !st.state
            .assess(&r, now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    assert_eq!(st.state.allocations["a"].request, r);
}
#[test]
fn separate_policy_survives_legacy_state_writer_and_pins_original_request() {
    let f = Fixture::new();
    let mut st = Store::open(&f.0).unwrap();
    observe(&mut st.state);
    let mut r = request("a");
    r.demand.insert("ram".into(), 40);
    st.state
        .admit(r.clone(), now_ms(), &boot_id().unwrap())
        .unwrap();
    st.save().unwrap();
    let instance = st.state.instance_id.clone();
    let c = config(&st.state);
    let e = expected(&st.state, "a");
    st.configure_scheduling(&instance, 0, 0, c, &e).unwrap();
    drop(st);
    // Simulate the unchanged v1 serializer used by an old direct owner.
    let old: State = serde_json::from_slice(&fs::read(f.0.join("state.json")).unwrap()).unwrap();
    assert!(old.scheduling.is_none());
    fs::write(f.0.join("state.json"), serde_json::to_vec(&old).unwrap()).unwrap();
    let st = Store::open(&f.0).unwrap();
    assert_eq!(st.state.effective_request(&r).priority, Priority::Priority);
    drop(st);
    let mut old = old;
    old.allocations.get_mut("a").unwrap().request.source_ref = "replacement".into();
    // Class routes belong to the declared owner, while a per-allocation booking
    // additionally requires the exact original request. No replacement is reset.
    let mut st = Store::open(&f.0).unwrap();
    let mut c = st.state.scheduling.clone().unwrap();
    c.revision = 2;
    c.bookings.insert(
        "a".into(),
        Booking {
            original_request: r.clone(),
            demand: BTreeMap::from([("ram".into(), 20)]),
            eligibility_ref: "measured simultaneous phase peak".into(),
        },
    );
    let e = expected(&st.state, "a");
    st.configure_scheduling(&instance, 0, 1, c, &e).unwrap();
    drop(st);
    fs::write(f.0.join("state.json"), serde_json::to_vec(&old).unwrap()).unwrap();
    assert!(Store::open(&f.0).is_err());
}
#[test]
fn scheduling_correction_rejects_stale_or_uncovered_allocation_without_effect() {
    let f = Fixture::new();
    let mut st = Store::open(&f.0).unwrap();
    observe(&mut st.state);
    let mut r = request("a");
    r.demand.insert("ram".into(), 40);
    st.state.admit(r, now_ms(), &boot_id().unwrap()).unwrap();
    st.save().unwrap();
    let instance = st.state.instance_id.clone();
    let c = config(&st.state);
    let mut e = expected(&st.state, "a");
    assert!(
        st.configure_scheduling(&instance, 0, 0, c.clone(), &BTreeMap::new())
            .is_err()
    );
    e.get_mut("a").unwrap().revision += 1;
    assert!(
        st.configure_scheduling(&instance, 0, 0, c.clone(), &e)
            .is_err()
    );
    e = expected(&st.state, "a");
    assert!(
        st.configure_scheduling("foreign", 0, 0, c.clone(), &e)
            .is_err()
    );
    assert!(!f.0.join("scheduling.json").exists());
    st.configure_scheduling(&instance, 0, 0, c.clone(), &e)
        .unwrap();
    assert!(st.configure_scheduling(&instance, 0, 0, c, &e).is_err());
}
#[test]
fn native_priority_refuses_unrelated_work_and_keeps_execution_placement() {
    let f = Fixture::new();
    let mut st = Store::open(&f.0).unwrap();
    observe(&mut st.state);
    let mut a = request("a");
    a.demand.insert("ram".into(), 20);
    st.state
        .admit(a.clone(), now_ms(), &boot_id().unwrap())
        .unwrap();
    let mut b = request("b");
    b.owner_ref = "unrelated".into();
    b.demand.insert("ram".into(), 20);
    st.state.admit(b, now_ms(), &boot_id().unwrap()).unwrap();
    let mut c = config(&st.state);
    c.native_priorities.insert("old".into(), Priority::Priority);
    let e = expected(&st.state, "a");
    let instance = st.state.instance_id.clone();
    assert!(st.configure_scheduling(&instance, 0, 0, c, &e).is_err());
    let c = config(&st.state);
    st.configure_scheduling(&instance, 0, 0, c, &e).unwrap();
    assert_eq!(st.state.execution_system(&a), "old");
    assert_eq!(
        st.state.native_policy().cpu_aggregates["old"].priority,
        Priority::Normal
    );
}
#[test]
fn actual_cpu_use_replaces_running_peak_but_ram_and_unknown_cpu_stay_charged() {
    let mut s = State::new(policy()).unwrap();
    let cpu:ResourcePolicy=serde_json::from_value(json!({"unit":"millicores","capacity":10000,"operational_margin":0,"priority_growth_margin":0,"observation_max_age_ms":60000,"native":{"kind":"cpu","eligible_cpus":[0]}})).unwrap();
    s.policy.resources.insert("cpu".into(), cpu);
    observe(&mut s);
    let mut r = request("a");
    r.demand.insert("ram".into(), 40);
    s.admit(r.clone(), now_ms(), &boot_id().unwrap()).unwrap();
    let a = s.allocations.get_mut("a").unwrap();
    a.phase = Phase::Active;
    a.request.demand.insert("cpu".into(), 6000);
    a.runtime = Some(RuntimeBinding {
        process: process(std::process::id()).unwrap(),
        cgroup: "/fixture".into(),
        supervisor_ref: "fixture".into(),
    });
    let mut c = config(&s);
    c.observe_running_cpu = true;
    s.scheduling = Some(c);
    let a = s.allocations["a"].clone();
    let o = ResourceObservation {
        boot_id: boot_id().unwrap(),
        observed_at_ms: now_ms(),
        source_ref: "native-attributed".into(),
        external_demand: Some(0),
        unavailable: None,
        accounted_revisions: BTreeMap::from([("a".into(), a.revision)]),
        accounted_runtimes: BTreeMap::from([("a".into(), a.runtime.clone())]),
        managed_demand: BTreeMap::from([("a".into(), 100)]),
    };
    s.resource_observations.insert("cpu".into(), o.clone());
    assert_eq!(
        s.allocation_commitment(&a, "cpu", now_ms(), &boot_id().unwrap()),
        100
    );
    assert_eq!(
        s.allocation_commitment(&a, "cpu", now_ms() + 60001, &boot_id().unwrap()),
        6000
    );
    s.resource_observations
        .get_mut("cpu")
        .unwrap()
        .accounted_revisions
        .insert("a".into(), 999);
    assert_eq!(
        s.allocation_commitment(&a, "cpu", now_ms(), &boot_id().unwrap()),
        6000
    );
    let mut memory = o;
    memory.managed_demand.insert("a".into(), 10);
    s.resource_observations.insert("ram".into(), memory);
    assert_eq!(
        s.allocation_commitment(&a, "ram", now_ms(), &boot_id().unwrap()),
        40
    );
}

fn running_memory() -> (State, u64, String) {
    let mut s = State::new(policy()).unwrap();
    s.policy.resources.get_mut("ram").unwrap().native = Some(native::Resource::Memory);
    let now = now_ms();
    let boot = boot_id().unwrap();
    observe(&mut s);
    let mut r = request("a");
    r.demand.insert("ram".into(), 40);
    s.admit(r, now, &boot).unwrap();
    let a = s.allocations.get_mut("a").unwrap();
    a.phase = Phase::Active;
    a.request.demand.insert("ram".into(), 90);
    a.runtime = Some(RuntimeBinding {
        process: process(std::process::id()).unwrap(),
        cgroup: "/fixture".into(),
        supervisor_ref: "fixture".into(),
    });
    let a = a.clone();
    let mut c = config(&s);
    c.observe_running_memory = true;
    s.scheduling = Some(c);
    s.resource_observations.insert(
        "ram".into(),
        ResourceObservation {
            boot_id: boot.clone(),
            observed_at_ms: now,
            source_ref: "native-attributed".into(),
            external_demand: Some(20),
            unavailable: None,
            accounted_revisions: BTreeMap::from([("a".into(), a.revision)]),
            accounted_runtimes: BTreeMap::from([("a".into(), a.runtime.clone())]),
            managed_demand: BTreeMap::from([("a".into(), 10)]),
        },
    );
    (s, now, boot)
}

#[test]
fn running_memory_uses_host_working_demand_but_dispatch_keeps_forecast() {
    let (mut s, now, boot) = running_memory();
    let original = s.allocations["a"].request.clone();
    let mut b = request("b");
    b.demand.insert("ram".into(), 60);
    assert_eq!(s.assess(&b, now, &boot).unwrap().available["ram"], 70);
    assert!(s.assess(&b, now, &boot).unwrap().can_admit);
    // Without the selection, a 90-byte lifetime forecast blocks this fit even
    // though the whole host uses only 30 and the next dispatch needs 60.
    s.scheduling.as_mut().unwrap().observe_running_memory = false;
    assert!(!s.assess(&b, now, &boot).unwrap().can_admit);
    s.scheduling.as_mut().unwrap().observe_running_memory = true;
    // The active allocation's own next dispatch still needs its full 90, not
    // its observed 10. Changing accounting never changes that original request.
    assert!(!s.assess(&original, now, &boot).unwrap().can_admit);
    let revision = s.allocations["a"].revision;
    assert!(
        s.begin_step("a", revision, "next", process(std::process::id()).unwrap())
            .is_err()
    );
    assert!(s.allocations["a"].steps.is_empty());
    assert_eq!(s.allocations["a"].request, original);
    // A newly admitted, unstarted 60 is fully committed atomically. A second
    // contender cannot spend the same 70 bytes of current headroom.
    assert!(s.admit(b, now, &boot).unwrap().can_admit);
    let mut c = request("c");
    c.demand.insert("ram".into(), 20);
    assert!(!s.admit(c, now, &boot).unwrap().can_admit);
    // Real host pressure, rather than static forecasts, still prevents work.
    s.resource_observations
        .get_mut("ram")
        .unwrap()
        .external_demand = Some(95);
    assert!(!pressure::constrained(&s, now, &boot).is_empty());
}

#[test]
fn running_memory_never_credits_unknown_stale_or_uncertain_work() {
    let (s, now, boot) = running_memory();
    for case in 0..11 {
        let mut s = s.clone();
        let mut at = now;
        match case {
            0 => at += 60001,
            1 => s.resource_observations.get_mut("ram").unwrap().boot_id = "other".into(),
            2 => s
                .resource_observations
                .get_mut("ram")
                .unwrap()
                .accounted_revisions
                .clear(),
            3 => s
                .resource_observations
                .get_mut("ram")
                .unwrap()
                .accounted_runtimes
                .clear(),
            4 => {
                s.resource_observations.get_mut("ram").unwrap().unavailable =
                    Some("read failed".into())
            }
            5 => s
                .resource_observations
                .get_mut("ram")
                .unwrap()
                .managed_demand
                .clear(),
            6 => {
                s.resource_observations
                    .get_mut("ram")
                    .unwrap()
                    .external_demand = None
            }
            7 => s.allocations.get_mut("a").unwrap().runtime = None,
            8 => s.allocations.get_mut("a").unwrap().phase = Phase::Admitted,
            9 => s.allocations.get_mut("a").unwrap().phase = Phase::ReleasePending,
            _ => {
                s.allocations.get_mut("a").unwrap().steps.insert(
                    "unknown".into(),
                    Step {
                        process: process(std::process::id()).unwrap(),
                        target_ref: None,
                        outcome: StepOutcome::Uncertain,
                        evidence_ref: None,
                    },
                );
            }
        }
        assert_eq!(
            s.allocation_commitment(&s.allocations["a"], "ram", at, &boot),
            90,
            "case {case}"
        );
    }
    let mut s = s;
    s.resource_observations
        .get_mut("ram")
        .unwrap()
        .managed_demand
        .insert("a".into(), 110);
    assert_eq!(
        s.allocation_commitment(&s.allocations["a"], "ram", now, &boot),
        110
    );
}

#[test]
fn memory_selection_has_one_revision_and_a_legacy_readable_policy() {
    let f = Fixture::new();
    let mut st = Store::open(&f.0).unwrap();
    st.state.policy.resources.get_mut("ram").unwrap().native = Some(native::Resource::Memory);
    observe(&mut st.state);
    let mut r = request("a");
    r.demand.insert("ram".into(), 40);
    st.state.admit(r, now_ms(), &boot_id().unwrap()).unwrap();
    st.save().unwrap();
    let instance = st.state.instance_id.clone();
    let c = config(&st.state);
    st.configure_scheduling(&instance, 0, 0, c, &expected(&st.state, "a"))
        .unwrap();
    let mut c = st.state.scheduling.clone().unwrap();
    c.revision = 2;
    c.observe_running_memory = true;
    assert!(
        st.configure_scheduling(&instance, 0, 1, c.clone(), &BTreeMap::new())
            .is_err()
    );
    let e = expected(&st.state, "a");
    st.configure_scheduling(&instance, 0, 1, c, &e).unwrap();
    drop(st);
    let v1 = fs::read(f.0.join("scheduling.json")).unwrap();
    let mut v: serde_json::Value = serde_json::from_slice(&v1).unwrap();
    assert!(v.get("observe_running_memory").is_none());
    assert!(
        Store::open(&f.0)
            .unwrap()
            .state
            .scheduling
            .unwrap()
            .observe_running_memory
    );
    // Interrupted publication preserves the committed selection; only the v1
    // revision switches it. This is still one policy commit, not two owners.
    let memory_path = f.0.join("scheduling-memory.json");
    let mut memory: serde_json::Value =
        serde_json::from_slice(&fs::read(&memory_path).unwrap()).unwrap();
    memory["by_revision"] = json!({"2":true,"3":false});
    fs::write(&memory_path, serde_json::to_vec(&memory).unwrap()).unwrap();
    assert!(
        Store::open(&f.0)
            .unwrap()
            .state
            .scheduling
            .unwrap()
            .observe_running_memory
    );
    v["revision"] = json!(3);
    fs::write(f.0.join("scheduling.json"), serde_json::to_vec(&v).unwrap()).unwrap();
    assert!(
        !Store::open(&f.0)
            .unwrap()
            .state
            .scheduling
            .unwrap()
            .observe_running_memory
    );
    // A legacy writer's unmatched new revision keeps conservative accounting.
    v["revision"] = json!(4);
    fs::write(f.0.join("scheduling.json"), serde_json::to_vec(&v).unwrap()).unwrap();
    assert!(
        !Store::open(&f.0)
            .unwrap()
            .state
            .scheduling
            .unwrap()
            .observe_running_memory
    );
}
