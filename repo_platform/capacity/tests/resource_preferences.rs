use serde_json::{Value, json};
use std::fs;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::{Command, Stdio};
use apeiros_capacity::*;

fn invoke(root: &Path, request: &Value) -> Value {
    let mut child = Command::new(env!("CARGO_BIN_EXE_apeiros-capacity"))
        .arg("--state-root")
        .arg(root)
        .arg("request")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(&serde_json::to_vec(request).unwrap())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    let response: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(output.status.success(), response["ok"].as_bool().unwrap());
    response
}

#[test]
fn gpu_preference_is_conditional_and_independent_of_other_capacity_writers() {
    let root = std::env::temp_dir().join(format!("capacity-preference-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
    let policy: Policy=serde_json::from_value(json!({"host_id":"fixture","machine_id":machine_id().unwrap(),
        "resources":{"gpu":{"unit":"bytes","capacity":100,"operational_margin":25,"priority_growth_margin":0,"observation_max_age_ms":15000,"native":{"kind":"gpu_memory","uuid":"selected-gpu"}},
        "ram":{"unit":"bytes","capacity":100,"operational_margin":0,"priority_growth_margin":0,"observation_max_age_ms":15000}},
        "cpu_aggregates":{"preferred":{"priority":"priority","cgroup":"/fixture/preferred","control_owner_ref":"fixture"}}})).unwrap();
    Store::initialize(&root, policy.clone()).unwrap();
    let instance = Store::snapshot(&root).unwrap().instance_id;
    let resources = json!({"gpu":{"system_id":"preferred","gpu_uuid":"selected-gpu"}});
    let req = json!({"action":"configure_resource_preferences","expected_source":{"instance_id":instance,"host_id":"fixture"},
        "expected_policy_revision":0,"revision":0,"change_ref":"selected by fixture owner","resources":resources});
    for (key, bad) in [
        ("expected_policy_revision", json!(1)),
        ("revision", json!(1)),
        (
            "expected_source",
            json!({"host_id":"other","instance_id":instance}),
        ),
        (
            "resources",
            json!({"gpu":{"system_id":"unknown","gpu_uuid":"selected-gpu"}}),
        ),
        (
            "resources",
            json!({"gpu":{"system_id":"preferred","gpu_uuid":"other-device"}}),
        ),
        (
            "resources",
            json!({"ram":{"system_id":"preferred","gpu_uuid":"selected-gpu"}}),
        ),
    ] {
        let mut rejected = req.clone();
        rejected[key] = bad;
        assert_eq!(invoke(&root, &rejected)["ok"], false);
        assert_eq!(Store::snapshot(&root).unwrap().preference_revision(), 0);
    }
    let activated = invoke(&root, &req);
    assert_eq!(activated["data"]["preference_revision"], 1);
    assert_eq!(invoke(&root, &req)["ok"], false);
    let mut same = req.clone();
    same["revision"] = json!(1);
    assert_eq!(invoke(&root, &same)["data"]["changed"], false);
    let snapshot = Store::snapshot(&root).unwrap();
    assert_eq!(
        snapshot.preferred_system_for_resource("gpu"),
        Some("preferred")
    );
    assert_eq!(snapshot.preferred_system_for_resource("ram"), None);
    assert_eq!(snapshot.policy, policy);
    let mut writer = Store::open(&root).unwrap();
    let mut different = policy.clone();
    different.resources.get_mut("gpu").unwrap().native = Some(native::Resource::GpuMemory {
        uuid: "another-gpu".into(),
    });
    assert!(
        writer
            .state
            .reconfigure_policy(0, different.clone(), "would rebind preference".into())
            .is_err()
    );
    // A legacy writer persists only its own state. Its rebind cannot give the
    // retained preference authority over another physical device.
    writer.state.resource_preferences = None;
    writer.state.policy = different.clone();
    writer.save().unwrap();
    drop(writer);
    let view = invoke(&root, &json!({"action":"inspect","view":"policy"}));
    assert_eq!(view["data"]["resource_preferences"]["resources"], resources);
    assert_eq!(view["data"]["effective_resource_preferences"], json!({}));
    assert_eq!(view["data"]["cursor"]["preference_revision"], 1);
    let mut clear = same;
    clear["resources"] = json!({});
    clear["change_ref"] = json!("explicitly cleared");
    assert_eq!(invoke(&root, &clear)["data"]["preference_revision"], 2);
    assert_eq!(Store::snapshot(&root).unwrap().policy, different);
    fs::remove_dir_all(root).unwrap();
}
