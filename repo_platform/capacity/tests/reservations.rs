use serde_json::json;
use std::collections::BTreeMap;
use std::fs;
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::process::{Command, Stdio};
use apeiros_capacity::*;

fn fixture() -> State {
    let policy: Policy = serde_json::from_value(json!({"host_id":"fixture", "machine_id":machine_id().unwrap(),
        "resources":{"gpu":{"unit":"bytes","capacity":100,"operational_margin":25,"priority_growth_margin":0,"observation_max_age_ms":15000,"native":{"kind":"gpu_memory","uuid":"fixture-GPU"}},
        "ram":{"unit":"bytes","capacity":100,"operational_margin":0,"priority_growth_margin":0,"observation_max_age_ms":15000}},
        "cpu_aggregates":{"preview":{"cgroup":"/fixture/preview","priority":"priority","control_owner_ref":"fixture"},"batch":{"cgroup":"/fixture/batch","priority":"priority","control_owner_ref":"fixture"}}})).unwrap();
    let mut s = State::new(policy).unwrap();
    observe(&mut s, 0, 0);
    s.scheduling = Some(serde_json::from_value(json!({"schema":"apeiros.capacity.scheduling.v1","instance_id":s.instance_id,
        "host_id":"fixture","revision":1,"change_ref":"explicit user selection","routes":[],"bookings":{},"native_priorities":{},"observe_running_cpu":false,
        "reservations":selection(&s)})).unwrap());
    s.scheduling.as_ref().unwrap().validate(&s).unwrap();
    s
}
fn selection(s: &State) -> reservations::Configuration {
    serde_json::from_value(json!({"policy_revision":s.policy_revision,"resources":{"gpu":{"resource_policy":s.policy.resources["gpu"],
        "original_operational_margin":0,"system_floors":{"preview":25},"selection_ref":"fixture preview reservation"}}})).unwrap()
}
fn observe(s: &mut State, gpu: u64, ram: u64) {
    let o:Observation=serde_json::from_value(json!({"host_id":"fixture","boot_id":boot_id().unwrap(),"observed_at_ms":now_ms(),"source_ref":"fixture observations",
        "external_demand":{"gpu":gpu,"ram":ram},"accounted_allocations":{},"managed_demand":{}})).unwrap();
    s.observe(o, now_ms(), &boot_id().unwrap()).unwrap();
}
fn request(id: &str, system: &str, gpu: u64) -> Request {
    serde_json::from_value(json!({"allocation_id":id,"host_id":"fixture","system_id":system,"owner_ref":format!("fixture/{system}"),"operation_id":id,
        "attempt_id":"original","source_ref":"original workload source","configuration_ref":"original plan","priority":"priority","demand":{"gpu":gpu},
        "eligibility_ref":"fixture","release_route":"fixture"})).unwrap()
}
fn fits(s: &State, id: &str, system: &str, gpu: u64) -> bool {
    s.assess(&request(id, system, gpu), now_ms(), &boot_id().unwrap())
        .unwrap()
        .can_admit
}
fn admit(s: &mut State, id: &str, system: &str, gpu: u64) {
    assert!(
        s.admit(request(id, system, gpu), now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
}

#[test]
fn reservation_idle_active_split_and_above_floor_are_counted_once() {
    let mut s = fixture();
    assert!(fits(&s, "batch", "batch", 75));
    assert!(!fits(&s, "batch", "batch", 76));
    assert!(fits(&s, "preview", "preview", 100));
    admit(&mut s, "batch", "batch", 67);
    admit(&mut s, "preview-a", "preview", 10);
    admit(&mut s, "preview-b", "preview", 15);
    assert!(fits(&s, "batch-more", "batch", 8));
    assert!(!fits(&s, "batch-more", "batch", 9));
    assert_eq!(s.physical_operational_margin("gpu"), 0);
    let mut larger = fixture();
    admit(&mut larger, "preview", "preview", 40);
    assert!(fits(&larger, "batch", "batch", 60));
    assert!(!fits(&larger, "batch", "batch", 61));
    assert!(pressure::constrained(&s, now_ms(), &boot_id().unwrap()).is_empty());
}

#[test]
fn reservation_fairness_protects_only_unreserved_extra_demand() {
    for (preview_gpu, batch_gpu, expected) in [(25, 75, true), (40, 71, false), (40, 60, true)] {
        let mut s = fixture();
        observe(&mut s, 0, 30);
        let mut preview = request("older-preview", "preview", preview_gpu);
        preview.demand.insert("ram".into(), 80);
        assert!(
            !s.admit(preview, now_ms(), &boot_id().unwrap())
                .unwrap()
                .can_admit
        );
        let batch = request("later-batch", "batch", batch_gpu);
        s.admit(batch, now_ms(), &boot_id().unwrap()).unwrap();
        s.schedule(now_ms(), &boot_id().unwrap()).unwrap();
        assert_eq!(
            s.allocations["later-batch"].phase == Phase::Admitted,
            expected,
            "{preview_gpu}/{batch_gpu}"
        );
    }
}

fn prefer_preview(s: &mut State) {
    s.resource_preferences = Some(preferences::Selection {
        instance_id: s.instance_id.clone(),
        host_id: s.policy.host_id.clone(),
        revision: 1,
        change_ref: "user selected preview for GPU contention".into(),
        resources: BTreeMap::from([(
            "gpu".into(),
            preferences::GpuPreference {
                system_id: "preview".into(),
                gpu_uuid: "fixture-GPU".into(),
            },
        )]),
    });
}

#[test]
fn gpu_preference_passes_older_gpu_contender_but_keeps_its_ram_turn() {
    for (preview_ram, batch_ram, expected) in [(10, 10, "preview"), (80, 80, "batch")] {
        let mut s = fixture();
        prefer_preview(&mut s);
        observe(&mut s, 100, 0);
        for (system, gpu, ram) in [("batch", 70, batch_ram), ("preview", 60, preview_ram)] {
            let mut r = request(system, system, gpu);
            r.demand.insert("ram".into(), ram);
            assert!(!s.admit(r, now_ms(), &boot_id().unwrap()).unwrap().can_admit);
        }
        observe(&mut s, 0, 0);
        assert_eq!(
            s.schedule(now_ms(), &boot_id().unwrap()).unwrap(),
            vec![expected]
        );
    }
}

#[test]
fn gpu_preference_allows_surplus_and_preserves_the_floor_and_independent_holds() {
    for (batch_gpu, held, both) in [(30, false, true), (70, true, false)] {
        let mut s = fixture();
        prefer_preview(&mut s);
        observe(&mut s, 100, 0);
        if held {
            s.hold(serde_json::from_value(json!({"hold_id":"pause","owner_ref":"user","reason":"workload paused","revision":0,
                "scope":{"host_id":"fixture","system_ids":["preview"]}})).unwrap()).unwrap();
        }
        for (sys, n) in [("batch", batch_gpu), ("preview", 60)] {
            s.admit(request(sys, sys, n), now_ms(), &boot_id().unwrap())
                .unwrap();
        }
        observe(&mut s, 0, 0);
        s.schedule(now_ms(), &boot_id().unwrap()).unwrap();
        assert_eq!(s.allocations["batch"].phase, Phase::Admitted);
        assert_eq!(s.allocations["preview"].phase == Phase::Admitted, both);
        assert_eq!(
            s.resource_reservation("gpu").unwrap().system_floors["preview"],
            25
        );
    }
}

#[test]
fn optional_warmup_has_no_gpu_preference_against_a_real_waiter() {
    let mut s = fixture();
    prefer_preview(&mut s);
    observe(&mut s, 100, 0);
    s.admit(request("batch", "batch", 70), now_ms(), &boot_id().unwrap())
        .unwrap();
    observe(&mut s, 0, 0);
    assert!(
        !s.admit_if_fit(request("warm", "preview", 60), now_ms(), &boot_id().unwrap())
            .unwrap()
            .can_admit
    );
    assert!(!s.allocations.contains_key("warm"));
    assert_eq!(
        s.schedule(now_ms(), &boot_id().unwrap()).unwrap(),
        vec!["batch"]
    );
}

#[test]
fn reservation_observed_use_external_pressure_holds_and_freshness_remain_real() {
    let mut s = fixture();
    admit(&mut s, "preview", "preview", 10);
    s.resource_observations
        .get_mut("gpu")
        .unwrap()
        .managed_demand
        .insert("preview".into(), 40);
    assert!(fits(&s, "batch", "batch", 60));
    assert!(!fits(&s, "batch", "batch", 61));
    observe(&mut s, 5, 0);
    assert!(fits(&s, "batch", "batch", 70));
    assert!(!fits(&s, "batch", "batch", 71));
    s.hold(serde_json::from_value(json!({"hold_id":"independent","owner_ref":"user","reason":"independent pause","scope":{"host_id":"fixture","system_ids":["preview"]},"revision":0})).unwrap()).unwrap();
    assert!(!fits(&s, "next-preview", "preview", 1));
    assert!(fits(&s, "batch", "batch", 70));
    assert!(
        !s.assess(
            &request("batch", "batch", 1),
            now_ms() + 15001,
            &boot_id().unwrap()
        )
        .unwrap()
        .can_admit
    );
}

#[test]
fn idle_gpu_floor_detects_only_the_shortfall_and_selects_a_fitting_release() {
    let mut s = fixture();
    let boot = boot_id().unwrap();
    for sys in ["batch", "preview"] {
        s.policy.domain_controls.insert(
            sys.into(),
            pressure::DomainControl {
                argv: vec!["/bin/false".into()],
                qualification_ref: "fixture".into(),
                timeout_ms: 1000,
                stable_recovery_ms: 100,
            },
        );
    }
    for (id, system, bytes) in [("large", "batch", 67), ("small", "batch", 8)] {
        admit(&mut s, id, system, bytes);
        s.begin_step(
            id,
            s.allocations[id].revision,
            "run",
            process(std::process::id()).unwrap(),
        )
        .unwrap();
    }
    let hint = |s: &State| {
        pressure::gpu_pressure_context(s, now_ms(), &boot)["gpu_constraints"]["gpu"].clone()
    };
    assert!(hint(&s).is_null());
    observe(&mut s, 3, 0);
    let gpu = hint(&s);
    assert_eq!(gpu["cause"], "reservation_shortfall");
    assert_eq!(
        gpu["reservation_shortfall"],
        json!({"bytes":3,"unused_by_system":{"preview":25}})
    );
    assert!(gpu["waiter"].is_null() && gpu["preferred_waiter"].is_null());
    assert_eq!(pressure::candidates(&s, now_ms(), &boot)[0].0, "small");
    assert!(!fits(&s, "batch-refill", "batch", 1));
    assert!(fits(&s, "preview-part", "preview", 10));

    admit(&mut s, "preview-part", "preview", 10);
    s.begin_step(
        "preview-part",
        s.allocations["preview-part"].revision,
        "run",
        process(std::process::id()).unwrap(),
    )
    .unwrap();
    assert_eq!(
        hint(&s)["reservation_shortfall"],
        json!({"bytes":3,"unused_by_system":{"preview":15}})
    );
    assert!(
        pressure::candidates(&s, now_ms(), &boot)
            .iter()
            .all(|c| c.0 != "preview-part")
    );
    assert!(
        pressure::gpu_pressure_context(&s, now_ms() + 15001, &boot)["gpu_constraints"]["gpu"]
            .is_null()
    );
    observe(&mut s, 0, 0);
    assert!(hint(&s).is_null());
    assert!(fits(&s, "preview-rest", "preview", 15));
    assert!(!fits(&s, "batch-refill", "batch", 1));
}

#[test]
fn gpu_floor_shortfall_preserves_the_actual_waiter_and_physical_driver() {
    let mut s = fixture();
    prefer_preview(&mut s);
    let boot = boot_id().unwrap();
    admit(&mut s, "batch-work", "batch", 75);
    observe(&mut s, 3, 0);
    assert!(
        !s.admit(request("real-waiter", "preview", 25), now_ms(), &boot)
            .unwrap()
            .can_admit
    );
    for (external, cause, short) in [(3, "waiting_demand", 3), (26, "physical_pressure", 25)] {
        observe(&mut s, external, 0);
        let context = pressure::gpu_pressure_context(&s, now_ms(), &boot);
        let gpu = &context["gpu_constraints"]["gpu"];
        assert_eq!(gpu["cause"], cause);
        assert_eq!(gpu["preferred_waiter"]["allocation_id"], "real-waiter");
        assert_eq!(gpu["reservation_shortfall"]["bytes"], short);
        assert!(
            pressure::constrained(&s, now_ms(), &boot)["gpu"]
                .contains("standing GPU reservation short by")
        );
    }
}

#[test]
fn reservation_validation_preserves_original_margins_and_physical_identity() {
    let s = fixture();
    let valid = selection(&s);
    for mut bad in [valid.clone(), valid.clone(), valid.clone(), valid.clone()]
        .into_iter()
        .enumerate()
    {
        match bad.0 {
            0 => bad.1.policy_revision += 1,
            1 => {
                bad.1
                    .resources
                    .get_mut("gpu")
                    .unwrap()
                    .original_operational_margin = 1
            }
            2 => {
                bad.1
                    .resources
                    .get_mut("gpu")
                    .unwrap()
                    .system_floors
                    .insert("unknown".into(), 1);
            }
            _ => {
                bad.1
                    .resources
                    .get_mut("gpu")
                    .unwrap()
                    .resource_policy
                    .capacity = 101
            }
        }
        assert!(bad.1.validate(&s).is_err());
    }
    let mut s = s;
    let mut policy = s.policy.clone();
    policy.resources.get_mut("gpu").unwrap().operational_margin = 0;
    assert!(
        s.reconfigure_policy(0, policy, "unauthorized guard removal".into())
            .is_err()
    );
}

#[test]
fn reservation_multiple_floors_original_margin_and_boot_conserve_capacity() {
    let mut s = fixture();
    let p = s.policy.resources.get_mut("gpu").unwrap();
    p.operational_margin = 40; // 5 byte margin + 25 byte preview floor + 10 byte secondary floor.
    s.policy.cpu_aggregates.insert(
        "other".into(),
        CpuAggregate {
            cgroup: "/fixture/other".into(),
            priority: Priority::Priority,
            control_owner_ref: "fixture".into(),
        },
    );
    let reserve = s
        .scheduling
        .as_mut()
        .unwrap()
        .reservations
        .as_mut()
        .unwrap()
        .resources
        .get_mut("gpu")
        .unwrap();
    reserve.resource_policy = p.clone();
    reserve.original_operational_margin = 5;
    reserve.system_floors.insert("other".into(), 10);
    s.scheduling.as_ref().unwrap().validate(&s).unwrap();
    assert!(fits(&s, "batch", "batch", 60));
    assert!(!fits(&s, "batch", "batch", 61));
    admit(&mut s, "preview", "preview", 25);
    admit(&mut s, "other", "other", 10);
    assert!(fits(&s, "batch", "batch", 60));
    assert!(!fits(&s, "batch", "batch", 61));
    let a = s.allocations.get_mut("preview").unwrap();
    a.phase = Phase::Active;
    a.runtime = Some(RuntimeBinding {
        process: Process {
            boot_id: "ended-original-boot".into(),
            pid: 123,
            start_ticks: 45,
        },
        cgroup: "/fixture/preview/original".into(),
        supervisor_ref: "fixture".into(),
    });
    // Native runtime cannot survive a boot; its now-unused floor still does.
    assert_eq!(
        s.allocation_commitment(&s.allocations["preview"], "gpu", now_ms(), &boot_id().unwrap()),
        0
    );
    assert!(fits(&s, "batch", "batch", 60));
    assert!(!fits(&s, "batch", "batch", 61));
    assert_eq!(s.physical_operational_margin("gpu"), 5);
}

struct Resident(std::process::Child);
impl Drop for Resident {
    fn drop(&mut self) {
        unsafe {
            libc::kill(self.0.id() as i32, libc::SIGTERM);
        }
        let _ = self.0.wait();
    }
}

#[test]
fn reservation_native_resident_restart_retains_same_owner_and_floor() {
    let root = std::env::temp_dir().join(format!(
        "capacity-reservation-resident-{}-{}",
        std::process::id(),
        now_ms()
    ));
    fs::create_dir(&root).unwrap();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
    Store::initialize(&root, fixture().policy).unwrap();
    let mut stored = Store::open(&root).unwrap();
    let state = stored.state.clone();
    stored
        .configure_reservations(
            &state.instance_id,
            0,
            0,
            Some(selection(&state)),
            "actual native restart acceptance".into(),
        )
        .unwrap();
    drop(stored);
    for _ in 0..2 {
        let resident = Resident(
            Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
                .args(["--state-root", root.to_str().unwrap(), "serve"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .unwrap(),
        );
        let mut stream = None;
        for _ in 0..300 {
            if let Ok(s) = UnixStream::connect(root.join("capacity.sock")) {
                stream = Some(s);
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        let mut stream = stream.expect("fixture resident socket");
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(5)))
            .unwrap();
        let body = serde_json::to_vec(&json!({"action":"inspect"})).unwrap();
        write!(
            stream,
            "POST /v1/capacity HTTP/1.1\r\nContent-Length: {}\r\n\r\n",
            body.len()
        )
        .unwrap();
        stream.write_all(&body).unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        let body = response.split_once("\r\n\r\n").unwrap().1;
        let v: serde_json::Value = serde_json::from_str(body).unwrap();
        assert_eq!(v["data"]["state"]["instance_id"], state.instance_id);
        assert_eq!(
            v["data"]["scheduling"]["reservations"]["resources"]["gpu"]["system_floors"]["preview"],
            25
        );
        drop(stream);
        drop(resident);
    }
    fs::remove_dir_all(root).unwrap();
}

fn cli(binary: &str, root: &std::path::Path, v: serde_json::Value) -> serde_json::Value {
    let mut child = Command::new(binary)
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
        .write_all(serde_json::to_vec(&v).unwrap().as_slice())
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
fn reservation_native_cas_restart_legacy_guard_and_interrupted_publication() {
    let root = std::env::temp_dir().join(format!(
        "capacity-reservation-{}-{}",
        std::process::id(),
        now_ms()
    ));
    fs::create_dir(&root).unwrap();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
    let s = fixture();
    Store::initialize(&root, s.policy.clone()).unwrap();
    let binary = env!("CARGO_BIN_EXE_apeiros-capacity");
    let state = Store::snapshot(&root).unwrap();
    let change = json!({"action":"configure_reservations","expected_instance_id":state.instance_id,"expected_policy_revision":0,"revision":0,"reservations":selection(&state),"change_ref":"explicit persistent selection"});
    cli(binary, &root, change);
    // Separate process/read lifetime, no resident-memory-only reservation.
    let mut stored = Store::open(&root).unwrap();
    observe(&mut stored.state, 0, 0);
    stored.save().unwrap();
    drop(stored);
    let reopened = Store::snapshot(&root).unwrap();
    assert!(fits(&reopened, "batch", "batch", 75));
    assert!(!fits(&reopened, "batch", "batch", 76));
    let oldbody: serde_json::Value =
        serde_json::from_slice(&fs::read(root.join("scheduling.json")).unwrap()).unwrap();
    assert!(oldbody.get("reservations").is_none());
    let ext = root.join("scheduling-reservations.json");
    let saved = fs::read(&ext).unwrap();
    let mut parsed: serde_json::Value = serde_json::from_slice(&saved).unwrap();
    parsed["by_revision"] = json!({"2":parsed["by_revision"]["1"]});
    fs::write(&ext, serde_json::to_vec(&parsed).unwrap()).unwrap();
    let guarded = Store::snapshot(&root).unwrap();
    assert!(guarded.scheduling.unwrap().reservations.is_none());
    assert!(!fits(&Store::snapshot(&root).unwrap(), "preview", "preview", 76));
    fs::write(&ext, &saved).unwrap();
    let mut stored = Store::open(&root).unwrap();
    let old = stored.state.clone();
    assert!(
        stored
            .configure_reservations(&old.instance_id, 0, 0, None, "stale clear".into())
            .is_err()
    );
    let mut next = old.scheduling.clone().unwrap();
    next.revision += 1;
    next.reservations = None;
    stored
        .configure_scheduling(&old.instance_id, 0, 1, next, &BTreeMap::new())
        .unwrap();
    drop(stored);
    assert!(
        Store::snapshot(&root)
            .unwrap()
            .scheduling
            .unwrap()
            .reservations
            .is_some()
    );
    if let Ok(legacy) = std::env::var("APEIROS_CAPACITY_LEGACY_BINARY") {
        let response = cli(
            &legacy,
            &root,
            json!({"action":"assess","request":request("batch","batch",76)}),
        );
        assert_eq!(response["data"]["assessment"]["can_admit"], false);
    }
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn pressure_control_succession_pins_original_requests_and_preserves_floor_on_reopen() {
    let root = std::env::temp_dir().join(format!(
        "capacity-control-succession-{}-{}",
        std::process::id(),
        now_ms()
    ));
    fs::create_dir(&root).unwrap();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
    let mut policy = fixture().policy;
    let control = |name: &str| -> pressure::DomainControl {
        serde_json::from_value(json!({"argv":["/bin/sh","-c",format!("echo {name}; cat")],
            "qualification_ref":name,"timeout_ms":5000,"stable_recovery_ms":15000}))
        .unwrap()
    };
    let original = control("original");
    policy.domain_controls.insert("batch".into(), original.clone());
    Store::initialize(&root, policy).unwrap();
    let mut store = Store::open(&root).unwrap();
    let instance = store.state.instance_id.clone();
    let reservation = selection(&store.state);
    store
        .configure_reservations(
            &instance,
            0,
            0,
            Some(reservation.clone()),
            "exact floor".into(),
        )
        .unwrap();
    observe(&mut store.state, 0, 0);
    admit(&mut store.state, "existing-batch", "batch", 1);
    let id = "existing-batch";
    store.state.pressure.insert(
        id.into(),
        serde_json::from_value(
            json!({"allocation_id":id,"request_id":"original-id","reason":"GPU pressure",
      "constrained_resources":["gpu"],"stage":"release_requested","last_check_ms":1}),
        )
        .unwrap(),
    );
    store.save().unwrap();
    drop(store);
    let mut change = json!({"action":"succeed_pressure_control","expected_source":{"instance_id":instance,"host_id":"fixture"},"system_id":"batch",
       "expected_policy_revision":0,"expected_scheduling_revision":1,"expected_reservations":reservation,
       "previous_control":original,"control":control("successor"),"change_ref":"qualified new batch owner"});
    let exe = env!("CARGO_BIN_EXE_apeiros-capacity");
    let receipt = cli(exe, &root, change.clone());
    assert_eq!(receipt["data"]["preserved_pending_requests"], 1);
    let current = Store::snapshot(&root).unwrap();
    assert_eq!(current.policy_revision, 1);
    assert_eq!(current.scheduling.as_ref().unwrap().revision, 2);
    let selected = current
        .scheduling
        .as_ref()
        .unwrap()
        .reservations
        .as_ref()
        .unwrap();
    assert_eq!(selected.policy_revision, 1);
    assert_eq!(selected.resources, reservation.resources);
    let routed = &current.policy.domain_controls["batch"];
    for (request, label) in [
        (
            json!({"action":"inspect","allocation_id":id,"request_id":"original-id"}),
            "original",
        ),
        (
            json!({"action":"request_release","allocation_id":id,"request_id":"original-id"}),
            "original",
        ),
        (
            json!({"action":"request_resume","allocation_id":id,"request_id":"new-resume-id","pressure_request_id":"original-id"}),
            "original",
        ),
        (
            json!({"action":"inspect","allocation_id":id,"request_id":"future-request"}),
            "successor",
        ),
        (
            json!({"action":"inspect","allocation_id":"other","request_id":"original-id"}),
            "successor",
        ),
    ] {
        let mut process = Command::new(&routed.argv[0])
            .args(&routed.argv[1..])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        serde_json::to_writer(process.stdin.take().unwrap(), &request).unwrap();
        let output = process.wait_with_output().unwrap();
        assert!(output.status.success());
        let bytes = String::from_utf8(output.stdout).unwrap();
        let (owner, body) = bytes.split_once('\n').unwrap();
        assert_eq!(owner, label);
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(body).unwrap(),
            request
        );
    }
    let mut locked = Store::open(&root).unwrap();
    assert!(
        locked
            .succeed_pressure_control(&change, std::path::Path::new(exe))
            .is_err()
    );
    change["expected_policy_revision"] = json!(1);
    change["expected_scheduling_revision"] = json!(2);
    change["previous_control"] = json!(routed);
    change["control"] = json!(control("another"));
    assert!(
        locked
            .succeed_pressure_control(&change, std::path::Path::new(exe))
            .is_err()
    ); // stale floor binding
    drop(locked);
    fs::remove_dir_all(root).unwrap();
}
