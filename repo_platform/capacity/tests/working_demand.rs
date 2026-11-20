use serde_json::json;
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use apeiros_capacity::demand::*;
use apeiros_capacity::scheduling::*;
use apeiros_capacity::*;

fn policy() -> Policy {
    serde_json::from_value(json!({"host_id":"fixture","machine_id":machine_id().unwrap(),
        "resources":{"gpu":{"unit":"bytes","capacity":100,"operational_margin":0,"priority_growth_margin":0,"observation_max_age_ms":60000,"native":{"kind":"gpu_memory","uuid":"fixture-gpu"}}}})).unwrap()
}
fn request(id: &str, system: &str, demand: u64) -> Request {
    serde_json::from_value(json!({"allocation_id":id,"host_id":"fixture","system_id":system,"priority":"priority","owner_ref":format!("domain/{system}"),"operation_id":id,"attempt_id":"one","source_ref":"source","configuration_ref":"config","demand":{"gpu":demand},"eligibility_ref":"initial-phase","release_route":"owner"})).unwrap()
}
fn observe(s: &mut State, measured: &[(&str, u64)]) {
    let mut revisions = BTreeMap::new();
    let mut managed = BTreeMap::new();
    for (id, value) in measured {
        revisions.insert(id.to_string(), s.allocations[*id].revision);
        managed.insert(
            id.to_string(),
            BTreeMap::from([("gpu".to_string(), *value)]),
        );
    }
    s.observe(
        Observation {
            host_id: "fixture".into(),
            boot_id: boot_id().unwrap(),
            observed_at_ms: now_ms(),
            source_ref: "current-native-fixture".into(),
            external_demand: BTreeMap::from([("gpu".into(), 0)]),
            accounted_allocations: revisions,
            managed_demand: managed,
            cpu_sample: None,
            io_sample: None,
            unavailable: BTreeMap::new(),
        },
        now_ms(),
        &boot_id().unwrap(),
    )
    .unwrap();
}
fn active(s: &mut State, id: &str, system: &str, amount: u64) {
    active_request(s, request(id, system, amount));
}
fn active_request(s: &mut State, r: Request) {
    let id = r.allocation_id.clone();
    assert!(s.admit(r, now_ms(), &boot_id().unwrap()).unwrap().can_admit);
    let rev = s.allocations[&id].revision;
    s.begin_step(&id, rev, "launch", process(std::process::id()).unwrap())
        .unwrap();
    // Pure accounting fixture; the native execution/binding gate has separate
    // real-process coverage in coordination.rs. No physical GPU is consumed.
    let a = s.allocations.get_mut(&id).unwrap();
    a.runtime = Some(RuntimeBinding {
        process: process(std::process::id()).unwrap(),
        cgroup: format!("/fixture/{id}"),
        supervisor_ref: format!("fixture:{id}"),
    });
}
fn change(s: &State, id: &str, name: &str, amount: u64) -> ChangeRequest {
    let a = &s.allocations[id];
    ChangeRequest {
        allocation_id: id.into(),
        change_id: name.into(),
        owner_ref: a.request.owner_ref.clone(),
        expected_allocation: ExpectedAllocation {
            revision: a.revision,
            request: a.request.clone(),
            runtime: a.runtime.clone(),
        },
        expected_booking: s
            .scheduling
            .as_ref()
            .and_then(|p| p.bookings.get(id))
            .cloned(),
        expected_previous_change_id: s.demand_change(id).map(|c| c.request.change_id.clone()),
        demand: BTreeMap::from([("gpu".into(), amount)]),
        eligibility_ref: format!("qualified-phase:{name}"),
    }
}
fn base() -> State {
    let mut s = State::new(policy()).unwrap();
    observe(&mut s, &[]);
    s
}

#[test]
fn settled_envelope_releases_forecast_but_never_observed_use_or_original_identity() {
    let mut s = base();
    active(&mut s, "a", "alpha", 80);
    observe(&mut s, &[("a", 35)]);
    let original = s.allocations["a"].request.clone();
    let runtime = s.allocations["a"].runtime.clone();
    let c = s
        .request_demand_change(
            change(&s, "a", "resident", 25),
            now_ms(),
            &boot_id().unwrap(),
        )
        .unwrap();
    assert_eq!(c.decision, Decision::Granted);
    assert_eq!(
        s.allocation_commitment(&s.allocations["a"], "gpu", now_ms(), &boot_id().unwrap()),
        35
    );
    assert!(
        s.admit(request("b", "beta", 60), now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    assert_eq!(s.allocations["a"].request, original);
    assert_eq!(s.allocations["a"].runtime, runtime);
    assert_eq!(s.allocations["a"].phase, Phase::Active);
}

#[test]
fn growth_beyond_original_estimate_waits_on_same_allocation_and_grants_after_fit() {
    let mut s = base();
    active(&mut s, "a", "alpha", 30);
    active(&mut s, "b", "beta", 50);
    observe(&mut s, &[("a", 25), ("b", 50)]);
    let r = change(&s, "a", "larger-query-family", 70);
    let c = s
        .request_demand_change(r.clone(), now_ms(), &boot_id().unwrap())
        .unwrap();
    assert_eq!(c.decision, Decision::Pending);
    assert_eq!(s.allocations["a"].phase, Phase::Active);
    assert_eq!(
        s.effective_request(&s.allocations["a"].request).demand["gpu"],
        30
    );
    assert!(
        s.waiting_decisions(now_ms(), &boot_id().unwrap()).unwrap()["a"]
            .reasons
            .iter()
            .any(|x| x.contains("insufficient"))
    );
    assert!(!pressure::constrained(&s, now_ms(), &boot_id().unwrap()).is_empty());
    // Existing domain release, not a phase request, establishes this fixture's exit.
    s.allocations.get_mut("b").unwrap().phase = Phase::Released;
    observe(&mut s, &[("a", 25)]);
    assert_eq!(
        s.schedule(now_ms(), &boot_id().unwrap()).unwrap(),
        vec!["a"]
    );
    assert_eq!(
        s.effective_request(&s.allocations["a"].request).demand["gpu"],
        70
    );
    assert_eq!(s.allocations["a"].request.demand["gpu"], 30);
    assert_eq!(
        s.request_demand_change(r, now_ms(), &boot_id().unwrap())
            .unwrap()
            .decision,
        Decision::Granted
    );
    assert!(
        !s.admit(request("c", "gamma", 40), now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
}

#[test]
fn queued_growth_keeps_aggregate_turns_and_holds_at_final_dispatch() {
    let mut s = base();
    active(&mut s, "a", "alpha", 30);
    active(&mut s, "busy", "other", 50);
    observe(&mut s, &[("a", 30), ("busy", 50)]);
    s.admit(request("older", "beta", 40), now_ms(), &boot_id().unwrap())
        .unwrap();
    s.request_demand_change(change(&s, "a", "growth", 70), now_ms(), &boot_id().unwrap())
        .unwrap();
    s.allocations.get_mut("busy").unwrap().phase = Phase::Released;
    observe(&mut s, &[("a", 30)]);
    let admitted = s.schedule(now_ms(), &boot_id().unwrap()).unwrap();
    assert_eq!(admitted, vec!["older"]);
    assert_eq!(s.demand_change("a").unwrap().decision, Decision::Pending);
    s.allocations.get_mut("older").unwrap().phase = Phase::Released;
    observe(&mut s, &[("a", 30)]);
    s.schedule(now_ms(), &boot_id().unwrap()).unwrap();
    s.hold(serde_json::from_value(json!({"hold_id":"independent","owner_ref":"user","reason":"keep stopped","scope":{"host_id":"fixture","operation_ids":["a"]},"revision":0})).unwrap()).unwrap();
    let rev = s.allocations["a"].revision;
    assert!(
        s.begin_step("a", rev, "new-phase", process(std::process::id()).unwrap())
            .is_err()
    );
    assert_eq!(s.demand_change("a").unwrap().decision, Decision::Granted);
}

#[test]
fn reductions_require_exact_fresh_attribution_and_pending_input_cannot_drift() {
    let mut original = base();
    active(&mut original, "a", "alpha", 80);
    observe(&mut original, &[("a", 20)]);
    for case in 0..5 {
        let mut s = original.clone();
        let r = change(&s, "a", "settled", 25);
        match case {
            0 => s
                .resource_observations
                .get_mut("gpu")
                .unwrap()
                .accounted_revisions
                .clear(),
            1 => {
                s.resource_observations
                    .get_mut("gpu")
                    .unwrap()
                    .external_demand = None
            }
            2 => {
                s.resource_observations
                    .get_mut("gpu")
                    .unwrap()
                    .observed_at_ms = 0
            }
            3 => s.allocations.get_mut("a").unwrap().revision += 1,
            _ => {
                s.allocations
                    .get_mut("a")
                    .unwrap()
                    .runtime
                    .as_mut()
                    .unwrap()
                    .process
                    .boot_id = "prior-boot".into()
            }
        }
        assert!(
            s.request_demand_change(r, now_ms(), &boot_id().unwrap())
                .is_err(),
            "{case}"
        );
        assert!(s.scheduling.is_none());
    }
    let mut s = original;
    s.request_demand_change(
        change(&s, "a", "too-large", 110),
        now_ms(),
        &boot_id().unwrap(),
    )
    .unwrap();
    assert_eq!(s.demand_change("a").unwrap().decision, Decision::Pending);
    s.cancel_demand_change("a", "too-large", "domain/alpha")
        .unwrap();
    assert_eq!(
        s.effective_request(&s.allocations["a"].request).demand["gpu"],
        80
    );
    assert!(
        s.cancel_demand_change("a", "foreign", "domain/alpha")
            .is_err()
    );
}

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "capacity-working-demand-{}",
            fs::read_to_string("/proc/sys/kernel/random/uuid")
                .unwrap()
                .trim()
        ));
        fs::create_dir(&p).unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o700)).unwrap();
        Store::initialize(&p, policy()).unwrap();
        Self(p)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn legacy_booking_commit_survives_interrupted_state_save_and_preserves_turn() {
    let f = Fixture::new();
    let mut store = Store::open(&f.0).unwrap();
    store.state = base();
    active(&mut store.state, "a", "alpha", 30);
    observe(&mut store.state, &[("a", 25)]);
    store.save().unwrap();
    let older_state = fs::read(f.0.join("state.json")).unwrap();
    let r = change(&store.state, "a", "grown", 70);
    store
        .state
        .request_demand_change(r, now_ms(), &boot_id().unwrap())
        .unwrap();
    let granted = store
        .state
        .demand_change("a")
        .unwrap()
        .granted_sequence
        .unwrap();
    store.save().unwrap();
    let legacy: serde_json::Value =
        serde_json::from_slice(&fs::read(f.0.join("scheduling.json")).unwrap()).unwrap();
    assert!(legacy.get("demand_changes").is_none());
    assert_eq!(legacy["bookings"]["a"]["demand"]["gpu"], 70);
    // Power loss after scheduling commit but before the state commit, or an
    // immutable legacy writer preserving its known state fields.
    fs::write(f.0.join("state.json"), &older_state).unwrap();
    drop(store);
    let mut store = Store::open(&f.0).unwrap();
    assert_eq!(store.state.last_turn["alpha"], granted);
    assert!(store.state.sequence >= granted);
    assert_eq!(
        store
            .state
            .effective_request(&store.state.allocations["a"].request)
            .demand["gpu"],
        70
    );
    assert_eq!(store.state.allocations["a"].request.demand["gpu"], 30);
    // Another phase replaces the record before a second interrupted state save.
    // The previous committed useful turn must not disappear with that record.
    store
        .state
        .request_demand_change(
            change(&store.state, "a", "later-growth", 110),
            now_ms(),
            &boot_id().unwrap(),
        )
        .unwrap();
    store.save().unwrap();
    fs::write(f.0.join("state.json"), &older_state).unwrap();
    drop(store);
    let store = Store::open(&f.0).unwrap();
    assert_eq!(store.state.last_turn["alpha"], granted);
    assert_eq!(
        store.state.demand_change("a").unwrap().decision,
        Decision::Pending
    );
}

#[test]
fn ended_boot_does_not_grant_retained_pending_growth() {
    let mut s = base();
    active(&mut s, "a", "alpha", 30);
    active(&mut s, "b", "beta", 50);
    observe(&mut s, &[("a", 25), ("b", 50)]);
    s.request_demand_change(
        change(&s, "a", "growth-before-reboot", 70),
        now_ms(),
        &boot_id().unwrap(),
    )
    .unwrap();
    assert_eq!(s.demand_change("a").unwrap().decision, Decision::Pending);
    let next_boot = "positively-observed-new-native-boot";
    s.resource_observations.get_mut("gpu").unwrap().boot_id = next_boot.into();
    assert_eq!(
        s.allocation_commitment(&s.allocations["a"], "gpu", now_ms(), next_boot),
        0
    );
    assert!(s.schedule(now_ms(), next_boot).unwrap().is_empty());
    assert_eq!(s.demand_change("a").unwrap().decision, Decision::Pending);
    let decision = &s.waiting_decisions(now_ms(), next_boot).unwrap()["a"];
    assert!(!decision.assessment.eligible);
    assert!(
        decision
            .reasons
            .iter()
            .any(|s| s.contains("ended native boot"))
    );
    assert_eq!(s.allocations["a"].phase, Phase::Active);
}

#[test]
fn pending_growth_does_not_pressure_unload_its_own_resident() {
    let mut s = base();
    active(&mut s, "a", "alpha", 30);
    active(&mut s, "b", "beta", 50);
    observe(&mut s, &[("a", 25), ("b", 50)]);
    for system in ["alpha", "beta"] {
        s.policy.domain_controls.insert(
            system.into(),
            pressure::DomainControl {
                argv: vec!["/bin/true".into()],
                qualification_ref: "fixture-inspection-only".into(),
                timeout_ms: 1000,
                stable_recovery_ms: 1000,
            },
        );
    }
    s.request_demand_change(change(&s, "a", "growth", 70), now_ms(), &boot_id().unwrap())
        .unwrap();
    let ids: Vec<_> = pressure::candidates(&s, now_ms(), &boot_id().unwrap())
        .into_iter()
        .map(|c| c.0)
        .collect();
    assert_eq!(ids, vec!["b"]);
    s.resource_observations
        .get_mut("gpu")
        .unwrap()
        .external_demand = Some(30);
    assert!(
        pressure::candidates(&s, now_ms(), &boot_id().unwrap())
            .iter()
            .any(|c| c.0 == "a")
    );
}

#[test]
fn ended_released_inspection_cannot_fence_a_live_waiter() {
    let mut s = base();
    let boot = boot_id().unwrap();
    active(&mut s, "old", "alpha", 10);
    active(&mut s, "resident", "alpha", 80);
    s.policy.domain_controls.insert(
        "alpha".into(),
        pressure::DomainControl {
            argv: vec!["/bin/true".into()],
            qualification_ref: "fixture-inspection-only".into(),
            timeout_ms: 1000,
            stable_recovery_ms: 1000,
        },
    );
    observe(&mut s, &[("old", 10), ("resident", 80)]);
    assert!(
        !s.admit(request("waiter", "beta", 50), now_ms(), &boot)
            .unwrap()
            .can_admit
    );
    s.pressure.insert(
        "old".into(),
        serde_json::from_value(json!({
            "allocation_id":"old", "request_id":"prior-inspection", "stage":"inspecting",
            "reason":"waiting for GPU", "constrained_resources":["gpu"], "last_check_ms":0
        }))
        .unwrap(),
    );
    let candidates = |s: &State| {
        pressure::candidates(s, now_ms(), &boot)
            .into_iter()
            .map(|c| c.0)
            .collect::<Vec<_>>()
    };
    let swap_control = |s: &mut State| {
        let mut policy = s.policy.clone();
        policy.domain_controls.get_mut("alpha").unwrap().argv = vec!["/bin/false".into()];
        s.reconfigure_policy(s.policy_revision, policy, "qualified successor".into())
    };
    assert!(candidates(&s).is_empty());
    assert!(swap_control(&mut s).is_err());
    s.allocations.get_mut("old").unwrap().phase = Phase::Released;
    observe(&mut s, &[("resident", 80)]);
    assert!(pressure::constrained(&s, now_ms(), &boot).contains_key("gpu"));
    assert!(candidates(&s).is_empty()); // Current-boot native evidence still governs.
    let old = s.allocations.get_mut("old").unwrap();
    old.runtime.as_mut().unwrap().process.boot_id = "prior-boot".into();
    assert!(candidates(&s).is_empty()); // The launch step is still from this boot.
    assert!(swap_control(&mut s).is_err());
    for step in s.allocations.get_mut("old").unwrap().steps.values_mut() {
        step.process.boot_id = "prior-boot".into();
    }
    assert_eq!(candidates(&s), vec!["resident"]);
    let evidence = serde_json::to_value(&s.pressure).unwrap();
    let mut eligible = s.clone();
    swap_control(&mut eligible).unwrap();
    assert_eq!(eligible.policy_revision, s.policy_revision + 1);
    assert_eq!(serde_json::to_value(&eligible.pressure).unwrap(), evidence);
    assert_eq!(eligible.allocations["old"].phase, Phase::Released);
    s.pressure.get_mut("old").unwrap().resume_request_id = Some("existing-resume".into());
    assert!(swap_control(&mut s).is_err());
    s.pressure.get_mut("old").unwrap().resume_request_id = None;
    s.pressure.get_mut("old").unwrap().expected_control_revision = Some(2);
    assert!(candidates(&s).is_empty()); // An admitted handoff still requires reconciliation.
    assert!(swap_control(&mut s).is_err());
}

#[test]
fn prepared_extension_does_not_activate_before_legacy_commit() {
    let f = Fixture::new();
    let mut store = Store::open(&f.0).unwrap();
    store.state = base();
    active(&mut store.state, "a", "alpha", 80);
    observe(&mut store.state, &[("a", 20)]);
    store
        .state
        .request_demand_change(
            change(&store.state, "a", "resident", 25),
            now_ms(),
            &boot_id().unwrap(),
        )
        .unwrap();
    store.save().unwrap();
    let previous = fs::read(f.0.join("scheduling.json")).unwrap();
    store
        .state
        .request_demand_change(
            change(&store.state, "a", "query", 70),
            now_ms(),
            &boot_id().unwrap(),
        )
        .unwrap();
    store.save().unwrap();
    fs::write(f.0.join("scheduling.json"), previous).unwrap();
    drop(store);
    let store = Store::open(&f.0).unwrap();
    assert_eq!(
        store.state.demand_change("a").unwrap().request.change_id,
        "resident"
    );
    assert_eq!(
        store
            .state
            .effective_request(&store.state.allocations["a"].request)
            .demand["gpu"],
        25
    );
}

#[test]
fn ended_native_boot_has_no_volatile_commitment_but_keeps_storage_and_recovery() {
    let mut s = base();
    active(&mut s, "a", "alpha", 80);
    observe(&mut s, &[("a", 20)]);
    let boot = boot_id().unwrap();
    let a = s.allocations.get_mut("a").unwrap();
    a.runtime.as_mut().unwrap().process.boot_id = "ended-boot".into();
    for step in a.steps.values_mut() {
        step.process.boot_id = "ended-boot".into();
    }
    let original = s.allocations["a"].request.clone();
    assert_eq!(
        s.allocation_commitment(&s.allocations["a"], "gpu", now_ms(), &boot),
        0
    );
    assert_eq!(s.allocations["a"].phase, Phase::Active);
    assert_eq!(s.allocations["a"].request, original);
    assert!(s.allocations["a"].release_evidence.is_none());
    // Current unmanaged use still constrains fitting new work.
    s.resource_observations
        .get_mut("gpu")
        .unwrap()
        .external_demand = Some(40);
    assert!(
        !s.assess(&request("new", "beta", 70), now_ms(), &boot)
            .unwrap()
            .can_admit
    );
    // A future dispatch asks for its full effective envelope, never zero.
    assert!(!s.assess(&original, now_ms(), &boot).unwrap().can_admit);
    s.policy.resources.get_mut("gpu").unwrap().native = None;
    assert_eq!(
        s.allocation_commitment(&s.allocations["a"], "gpu", now_ms(), &boot),
        80
    );
    s.policy.resources.get_mut("gpu").unwrap().native = Some(native::Resource::GpuMemory {
        uuid: "fixture-gpu".into(),
    });
    s.allocations.get_mut("a").unwrap().steps.insert(
        "possible-new-effect".into(),
        Step {
            process: process(std::process::id()).unwrap(),
            target_ref: None,
            outcome: StepOutcome::Uncertain,
            evidence_ref: None,
        },
    );
    assert_eq!(
        s.allocation_commitment(&s.allocations["a"], "gpu", now_ms(), &boot),
        80
    );
    s.allocations
        .get_mut("a")
        .unwrap()
        .steps
        .remove("possible-new-effect");
    s.allocations
        .get_mut("a")
        .unwrap()
        .runtime
        .as_mut()
        .unwrap()
        .process
        .boot_id = boot.clone();
    s.allocations
        .get_mut("a")
        .unwrap()
        .runtime
        .as_mut()
        .unwrap()
        .process
        .pid = u32::MAX;
    assert_eq!(
        s.allocation_commitment(&s.allocations["a"], "gpu", now_ms(), &boot),
        80
    );
}

#[test]
fn surplus_admission_protects_next_phase_instead_of_current_small_residency() {
    let mut s = base();
    active(&mut s, "a", "alpha", 5);
    active(&mut s, "busy", "other", 50);
    observe(&mut s, &[("a", 5), ("busy", 50)]);
    s.request_demand_change(
        change(&s, "a", "next-phase", 70),
        now_ms(),
        &boot_id().unwrap(),
    )
    .unwrap();
    let next = request("later", "beta", 40);
    assert!(
        s.assess(&next, now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    assert!(
        !s.admit(next, now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    s.allocations.get_mut("busy").unwrap().phase = Phase::Released;
    observe(&mut s, &[("a", 5)]);
    assert_eq!(
        s.schedule(now_ms(), &boot_id().unwrap()).unwrap(),
        vec!["a"]
    );
    assert_eq!(s.allocations["later"].phase, Phase::Waiting);
}

#[test]
fn surplus_admission_counts_existing_residency_once_when_protecting_growth() {
    let mut s = base();
    s.policy.resources.insert(
        "ram".into(),
        ResourcePolicy {
            unit: "bytes".into(),
            capacity: 100,
            operational_margin: 0,
            priority_growth_margin: 0,
            observation_max_age_ms: 60000,
            native: Some(native::Resource::Memory),
        },
    );
    s.resource_observations
        .insert("ram".into(), s.resource_observations["gpu"].clone());
    let mut a = request("a", "alpha", 30);
    a.demand.insert("ram".into(), 10);
    active_request(&mut s, a);
    let mut busy = request("busy", "other", 70);
    busy.demand.insert("ram".into(), 1);
    active_request(&mut s, busy);
    observe(&mut s, &[("a", 30), ("busy", 70)]);
    let mut ram = s.resource_observations["gpu"].clone();
    ram.managed_demand = BTreeMap::from([("a".into(), 10), ("busy".into(), 1)]);
    s.resource_observations.insert("ram".into(), ram);
    let mut growth = change(&s, "a", "gpu-and-ram-growth", 60);
    growth.demand.insert("ram".into(), 20);
    assert_eq!(
        s.request_demand_change(growth, now_ms(), &boot_id().unwrap())
            .unwrap()
            .decision,
        Decision::Pending
    );
    let mut later = request("ram-only", "beta", 1);
    later.demand = BTreeMap::from([("ram".into(), 79)]);
    // 10 existing + 10 additional for the older phase + 79 useful spare work
    // + the incumbent's 1 fits exactly. Reserving another 20 would double count.
    assert!(
        s.admit(later, now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    s.allocations.get_mut("busy").unwrap().phase = Phase::Released;
    observe(&mut s, &[("a", 30)]);
    let mut ram = s.resource_observations["gpu"].clone();
    ram.managed_demand = BTreeMap::from([("a".into(), 10)]);
    s.resource_observations.insert("ram".into(), ram);
    assert_eq!(
        s.schedule(now_ms(), &boot_id().unwrap()).unwrap(),
        vec!["a"]
    );
    assert_eq!(s.demand_change("a").unwrap().decision, Decision::Granted);
}

#[test]
fn phase_change_after_resource_policy_removal_refuses_without_effect() {
    let mut s = base();
    active(&mut s, "a", "alpha", 80);
    observe(&mut s, &[("a", 20)]);
    let r = change(&s, "a", "settled", 25);
    s.policy.resources.remove("gpu");
    assert!(
        s.request_demand_change(r, now_ms(), &boot_id().unwrap())
            .unwrap_err()
            .contains("absent from current policy")
    );
    assert!(s.scheduling.is_none());
}
