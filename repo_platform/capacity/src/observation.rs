//! Focused projections of the existing owner. No second capacity registry.
use super::*;
use std::collections::BTreeMap;
const VIEWS: &[&str] = &[
    "current",
    "history",
    "allocation",
    "policy",
    "holds",
    "pressure",
    "gpu_pressure",
    "maintenance",
];

fn parameters(v: &Value, keys: &[&str]) -> Result<()> {
    if v.is_null() {
        return Ok(());
    }
    let p = v.as_object().ok_or("parameters must be an object")?;
    if p.keys().any(|k| !keys.contains(&k.as_str())) {
        return Err("unknown observation parameter; use help for the selected action".into());
    }
    Ok(())
}

/// Direct MCP/CLI focused reads use the same projection as the nested observation action.
/// The legacy inspect-without-view and full-state follow contracts stay intact.
pub(super) fn direct(root: &Path, v: &Value) -> Result<Value> {
    parameters(
        v,
        &[
            "action",
            "view",
            "allocation_id",
            "source",
            "after_id",
            "limit",
        ],
    )?;
    let state = Store::snapshot(root)?;
    let host = state.policy.host_id.clone();
    let operation = if v["action"] == "overview" {
        "overview"
    } else {
        "inspect"
    };
    let mut p = serde_json::Map::new();
    for key in ["view", "source", "after_id"] {
        if let Some(value) = v.get(key) {
            p.insert(key.into(), value.clone());
        }
    }
    read_or_control_snapshot(
        root,
        &json!({"operation":operation,"host_id":host,"allocation_id":v.get("allocation_id"),"limit":v.get("limit").cloned().unwrap_or(json!(25)),"timeout_ms":5000,"parameters":p}),
        Some(state),
    )
}

pub(super) fn help(v: &Value) -> Result<Value> {
    parameters(v, &["action", "operation"])?;
    let schema: Value = serde_json::from_str(include_str!("../request.schema.json")).unwrap();
    let usage = usage();
    if let Some(operation) = v.get("operation") {
        let operation = operation
            .as_str()
            .ok_or("operation must be an action name")?;
        let selected = schema["oneOf"]
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["properties"]["action"]["const"] == operation)
            .ok_or("unknown operation; call help without operation to list actions")?;
        let mut input = selected.clone();
        include_definitions(&mut input, &schema);
        return Ok(
            json!({"operation":operation,"input_schema":input,"semantics":usage["operation_details"][operation],"passive":true,"help_requires_live_resident":false}),
        );
    }
    Ok(
        json!({"purpose":usage["purpose"],"start":{"action":"overview"},"actions":usage["operation_details"],"detail":{"action":"help","operation":"exact action name"},"views":VIEWS,"passive":true,"help_requires_live_resident":false,"full_snapshot":"inspect without view is the existing complete-state machine contract. Routine overview and explicit views avoid full history/counters."}),
    )
}

fn referenced_definitions(value: &Value, schema: &Value) -> Value {
    fn visit(value: &Value, schema: &Value, found: &mut serde_json::Map<String, Value>) {
        match value {
            Value::Object(object) => {
                if let Some(name) = object
                    .get("$ref")
                    .and_then(Value::as_str)
                    .and_then(|r| r.strip_prefix("#/$defs/"))
                {
                    if !found.contains_key(name) {
                        let definition = schema["$defs"][name].clone();
                        found.insert(name.into(), definition.clone());
                        visit(&definition, schema, found);
                    }
                }
                for value in object.values() {
                    visit(value, schema, found);
                }
            }
            Value::Array(values) => {
                for value in values {
                    visit(value, schema, found);
                }
            }
            _ => (),
        }
    }
    let mut found = serde_json::Map::new();
    visit(value, schema, &mut found);
    Value::Object(found)
}

fn include_definitions(input: &mut Value, schema: &Value) {
    let definitions = referenced_definitions(input, schema);
    if !definitions.as_object().unwrap().is_empty() {
        input["$defs"] = definitions;
    }
}

fn controls(state: &State, source: &Value) -> Value {
    let schema: Value = serde_json::from_str(include_str!("../request.schema.json")).unwrap();
    let operations = [
        (
            "hold",
            state.revision,
            true,
            "Block participating future admission/dispatch; no drain. Supply a new exact hold identity, owner, reason and scope. Final owner checks the original source/state revision.",
        ),
        (
            "release_hold",
            0,
            !state.holds.is_empty(),
            "Release only the exact hold owner/revision; no domain start. Existing authorized automation may become eligible; other holds remain.",
        ),
        (
            "reconfigure_policy",
            state.policy_revision,
            true,
            "Conditional full policy replacement under its reviewed change reference; preserve allocations and holds. Does not grant new limits or native effects.",
        ),
    ];
    Value::Array(operations.into_iter().map(|(action, revision, available, effect)| {
        let input = schema["oneOf"].as_array().unwrap().iter().find(|s| s["properties"]["action"]["const"] == action).unwrap();
        let mut shared=input.clone();
        let properties=shared["properties"].as_object_mut().unwrap();
        properties.remove("expected_source"); properties.remove("expected_state_revision");
        properties.insert("source".into(),json!({"type":"object","properties":{"instance_id":{"type":"string"},"host_id":{"type":"string"}},"required":["instance_id","host_id"],"additionalProperties":false}));
        properties.insert("revision".into(),json!({"type":"integer","minimum":0}));
        let required=shared["required"].as_array_mut().unwrap();
        required.retain(|r|r!="expected_source" && r!="expected_state_revision");
        for key in ["source","revision"] {if !required.contains(&json!(key)){required.push(json!(key));}}
        include_definitions(&mut shared,&schema);
        json!({"action":action,"available":available,"unavailable_reason":if available{Value::Null}else{json!("No retained hold to select")},"source":source,"revision":if action=="release_hold"{Value::Null}else{json!(revision)},"effect":effect,"route":"capacity observation operation=control parameters","required_inputs":shared["required"],"input_schema":shared,"help":{"action":"help","operation":action,"route":"Direct capacity action schema; the observation action accepts the source/revision fields above"},"hold_details":if action=="release_hold"{json!({"view":"holds","source":source})}else{Value::Null}})
    }).collect())
}

fn committed(state: &State, id: &str, key: &str, now: u64, boot: &str) -> Option<u64> {
    let a = &state.allocations[id];
    if !matches!(
        a.phase,
        Phase::Admitted | Phase::Active | Phase::ReleasePending
    ) {
        return None;
    }
    Some(state.allocation_commitment(a, key, now, boot))
}

/// Explain only what retained evidence establishes. In particular, an omitted
/// counter is never zero, nor proof of which native qualification check failed.
fn attribution(state: &State, id: &str, key: &str, now: u64, boot: &str) -> Value {
    let a = &state.allocations[id];
    let obs = state.resource_observations.get(key);
    let Some(policy) = state.policy.resources.get(key) else {
        return json!({"status":"resource_no_longer_enrolled","requested":a.request.demand.get(key),"effective_requested":state.effective_request(&a.request).demand.get(key),"committed":Value::Null,"observed":obs.and_then(|o|o.managed_demand.get(id)),"reason":"This retained allocation references a resource absent from current policy. No current unit, capacity or attribution is inferred."});
    };
    let matching = obs.is_some_and(|o| {
        o.accounted_revisions.get(id) == Some(&a.revision)
            && o.accounted_runtimes.get(id) == Some(&a.runtime)
    });
    let measured = obs.and_then(|o| o.managed_demand.get(id)).copied();
    let current = obs.is_some_and(|o| {
        o.boot_id == boot
            && o.observed_at_ms <= now
            && now - o.observed_at_ms <= policy.observation_max_age_ms
    });
    let (status, reason) = if a.phase == Phase::Released {
        (
            "not_current",
            "Allocation is released; retained evidence is historical.",
        )
    } else if state.native_runtime_ended_at_boot(a, boot)
        && matches!(
            policy.native,
            Some(
                native::Resource::Cpu { .. }
                    | native::Resource::Memory
                    | native::Resource::GpuMemory { .. }
            )
        )
    {
        (
            "ended_native_boot",
            "The exact bound incarnation ended at host reboot; physical contribution is zero, while logical work and owner recovery remain unresolved.",
        )
    } else if obs.is_none() {
        ("unavailable", "No retained observation for this resource.")
    } else if obs.is_some_and(|o| o.unavailable.is_some()) {
        (
            "unavailable",
            "The resource observation reports a failure; its original reason and time remain attached.",
        )
    } else if !current {
        (
            "stale",
            "Observation does not establish current same-boot use within the selected resource lifetime.",
        )
    } else if matching && measured.is_some() {
        (
            "attributed",
            "Exact allocation revision and native binding match the resource sample.",
        )
    } else if measured.is_some() {
        (
            "binding_changed",
            "Retained measured use does not match the current allocation revision/binding; fresh attribution is required.",
        )
    } else if a.runtime.is_none() {
        (
            "not_bound",
            "No native runtime binding was recorded; observed host use remains external demand.",
        )
    } else if matches!(policy.native, Some(native::Resource::Cpu { .. })) {
        (
            "unattributed",
            "Native sampling did not establish an eligible same-incarnation CPU interval. Possible checks include prior counter identity, eligible CPU hierarchy and counter availability; the retained sample does not identify which failed. Use the exact native owner/binding for diagnosis. Host use remains external demand.",
        )
    } else {
        (
            "unattributed",
            "The resource sample did not attribute use to this recorded binding. This is not zero use or released capacity; inspect the resource failure/binding through its native owner.",
        )
    };
    json!({"status":status,"reason":reason,"requested":a.request.demand.get(key),"effective_requested":state.effective_request(&a.request).demand.get(key),"committed":committed(state,id,key,now,boot),"observed":measured,"observation_at_ms":obs.map(|o|o.observed_at_ms),"observation_source":obs.map(|o|&o.source_ref),"resource_error":obs.and_then(|o|o.unavailable.as_ref()),"sample_binding_matches":matching,"fresh":current,"unit":policy.unit})
}

fn allocation_row(
    state: &State,
    id: &str,
    queue: &BTreeMap<String, QueueDecision>,
    now: u64,
    boot: &str,
    source: &Value,
    full: bool,
) -> Value {
    let a = &state.allocations[id];
    let resources: BTreeMap<_, _> = a
        .request
        .demand
        .keys()
        .map(|key| (key, attribution(state, id, key, now, boot)))
        .collect();
    let mut row = json!({"allocation_id":id,"system_id":state.effective_request(&a.request).system_id,"priority":state.effective_request(&a.request).priority,"original_system_id":a.request.system_id,"original_priority":a.request.priority,"execution_system_id":state.execution_system(&a.request),"operation_id":a.request.operation_id,"attempt_id":a.request.attempt_id,"owner_ref":a.request.owner_ref,"phase":a.phase,"revision":a.revision,"resources":resources,"waiting":queue.get(id),"inspect":{"action":"inspect","view":"allocation","allocation_id":id,"source":source},"domain_progress":"Read the exact owner_ref; capacity phase/resource use is not domain progress or completion."});
    if full {
        row["allocation"] = json!(a);
        row["working_demand_change"] = json!(state.demand_change(id));
    }
    row["native_incarnation_ended_at_boot"] = json!(state.native_runtime_ended_at_boot(a, boot));
    if state.native_runtime_ended_at_boot(a, boot) {
        row["physical_accounting_note"] = json!(
            "Ended native boot: no current CPU/RAM/VRAM contribution. Logical allocation/recovery remains unresolved; storage remains charged and unmanaged current successors remain external demand."
        );
    }
    row
}

fn resource_summaries(state: &State, now: u64, boot: &str) -> Value {
    let resources: BTreeMap<_,_> = state.policy.resources.iter().map(|(key, policy)| {
        let observed = state.resource_observations.get(key);
        let total: u64 = state.allocations.keys().filter_map(|id| committed(state,id,key,now,boot)).fold(0,u64::saturating_add);
        let attributed = state.allocations.iter().filter(|(_,a)| matches!(a.phase,Phase::Admitted|Phase::Active|Phase::ReleasePending)).filter(|(id,_)| attribution(state,id,key,now,boot)["status"] == "attributed").count();
        let charged = state.allocations.values().filter(|a| matches!(a.phase,Phase::Admitted|Phase::Active|Phase::ReleasePending) && a.request.demand.contains_key(key)).count();
        (key, json!({"unit":policy.unit,"capacity":policy.capacity,"operational_margin":policy.operational_margin,"physical_operational_margin":state.physical_operational_margin(key),"system_reservation":state.resource_reservation(key),"priority_growth_margin":policy.priority_growth_margin,"commitments":total,"external_demand":observed.and_then(|o|o.external_demand),"observed_at_ms":observed.map(|o|o.observed_at_ms),"source_ref":observed.map(|o|&o.source_ref),"error":observed.and_then(|o|o.unavailable.as_ref()),"fresh":observed.is_some_and(|o|o.boot_id==boot && o.observed_at_ms<=now && now-o.observed_at_ms<=policy.observation_max_age_ms),"charged_allocations":charged,"attributed_allocations":attributed,"attribution_gap":"Unattributed native use remains external demand; commitments and observation are distinct. Exact allocation inspection explains retained attribution evidence."}))
    }).collect();
    json!(resources)
}

pub(super) fn read_or_control(root: &Path, v: &Value) -> Result<Value> {
    read_or_control_snapshot(root, v, None)
}

fn read_or_control_snapshot(root: &Path, v: &Value, mut captured: Option<State>) -> Result<Value> {
    let operation = field(v, "operation")?;
    let host = field(v, "host_id")?;
    let p = &v["parameters"];
    let limit = v["limit"]
        .as_u64()
        .filter(|v| *v > 0 && *v <= 100)
        .ok_or("limit must be 1..100")? as usize;
    if operation == "control" {
        parameters(
            p,
            &[
                "action",
                "source",
                "revision",
                "hold",
                "hold_id",
                "owner_ref",
                "policy",
                "change_ref",
            ],
        )?;
        let action = field(p, "action")?;
        if !["hold", "release_hold", "reconfigure_policy"].contains(&action) {
            return Err("shared capacity control is hold/release_hold/reconfigure_policy; domain execution stays at its owner".into());
        }
        if p["source"]["host_id"] != host || !p["source"]["instance_id"].is_string() {
            return Err("exact observed capacity source required".into());
        }
        let rev = revision(p)?;
        let mut request = p.clone();
        let object = request.as_object_mut().ok_or("invalid control")?;
        object.remove("source");
        object.insert("expected_source".into(), p["source"].clone());
        if action == "hold" {
            object.insert("expected_state_revision".into(), json!(rev));
        }
        return handle(root, request);
    }
    if operation == "diagnostic" {
        parameters(p, &["action"])?;
        let action = field(p, "action")?;
        if !["cpu_policy", "io_policy"].contains(&action) {
            return Err("only explicit passive native policy readback is exposed here".into());
        }
        let state = Store::snapshot(root)?;
        if state.policy.host_id != host {
            return Err("selected capacity host mismatch".into());
        }
        return Ok(if action == "cpu_policy" {
            native::cpu_policy(&state.native_policy(), false)
        } else {
            storage::controls(&state.policy, false)
        });
    }
    if !["overview", "inspect", "follow", "evidence", "controls"].contains(&operation) {
        return Err("unknown observation operation".into());
    }
    parameters(
        p,
        &[
            "view",
            "source",
            "after_id",
            "after_allocation_id",
            "cursor",
        ],
    )?;
    let selected = v.get("allocation_id").and_then(Value::as_str);
    let view = p
        .get("view")
        .map(|x| x.as_str().ok_or("view must be a string"))
        .transpose()?
        .unwrap_or(if selected.is_some() {
            "allocation"
        } else {
            "current"
        });
    if !VIEWS.contains(&view) {
        return Err(format!("unknown view; choose one of {}", VIEWS.join(", ")));
    }
    if (view == "allocation") != selected.is_some() {
        return Err("allocation view requires its exact allocation_id; other views do not accept allocation_id".into());
    }
    if operation == "evidence" && selected.is_none() {
        return Err("capacity evidence requires exact allocation_id; use an explicit history/pressure/maintenance view for other retained records".into());
    }
    if p.get("after_id").is_some() && p.get("after_allocation_id").is_some() {
        return Err("select only one page position".into());
    }
    let after = p
        .get("after_id")
        .or_else(|| p.get("after_allocation_id"))
        .map(|x| x.as_str().ok_or("after_id must be a string"))
        .transpose()?;
    if after.is_some() && (p.get("source").is_none() || matches!(view, "policy" | "gpu_pressure")) {
        return Err(
            "paging requires the returned source and a collection or allocation-commitment view"
                .into(),
        );
    }
    let previous = p.get("cursor");
    if operation == "follow" {
        let cursor = previous.ok_or("exact original cursor required")?;
        if after.is_some() || cursor["view"] != view || cursor["allocation_id"] != json!(selected) {
            return Err("follow cursor belongs to a different view/allocation; reuse its original selection".into());
        }
    } else if previous.is_some() {
        return Err("cursor is only valid for follow".into());
    }
    let timeout = v["timeout_ms"]
        .as_u64()
        .filter(|n| *n <= 30000)
        .ok_or("bounded timeout required")?;
    let deadline = Instant::now() + Duration::from_millis(timeout.saturating_sub(50));
    loop {
        let snapshot = match captured.take() {
            Some(state) => state,
            None => Store::snapshot(root)?,
        };
        let state = &snapshot;
        if state.policy.host_id != host {
            return Err("selected capacity host mismatch".into());
        }
        let source = json!({"instance_id":state.instance_id,"host_id":state.policy.host_id});
        if p.get("source").is_some_and(|expected| expected != &source) {
            return Err("capacity source changed; reacquire without replaying old intent".into());
        }
        let cursor = json!({"source":source,"revision":state.revision,"scheduling_revision":state.scheduling.as_ref().map_or(0,|s|s.revision),"preference_revision":state.preference_revision(),"view":view,"allocation_id":selected});
        let changed = previous.is_some_and(|c| c != &cursor);
        if operation == "follow" && !changed && Instant::now() < deadline {
            drop(snapshot);
            std::thread::sleep(Duration::from_millis(100));
            continue;
        }
        let now = now_ms();
        let boot = boot_id()?;
        let mut result = json!({"source":source,"cursor":cursor,"view":view,"state_revision":state.revision,"policy_revision":state.policy_revision,"scheduling_revision":state.scheduling.as_ref().map_or(0,|s|s.revision),"observed_at_ms":now,"current_boot_id":boot,"source_changed":previous.is_some_and(|c|c["source"]!=source),"changed":changed,"timed_out":operation=="follow"&&!changed,"passive":true,"coverage":"Current captured owner state, not an event-history guarantee. Pages are source-bound keyset reads and may observe later state revisions. Resource sample times remain independent. Capacity never establishes domain completion.","enforcement":"Requested demand and charged commitments are not native limits. This view does not probe effective enforcement; use explicit cpu_policy/io_policy diagnostics for their enrolled native controls.","detail_views":VIEWS});
        if operation == "controls" {
            result["controls"] = controls(state, &source);
            result["diagnostics"] = json!(["cpu_policy", "io_policy"]);
            return Ok(result);
        }
        if view == "gpu_pressure" {
            result["gpu_pressure"] = pressure::gpu_pressure_context(state, now, &boot);
            return Ok(result);
        }
        if view == "policy" {
            result["policy"] = json!(state.policy);
            result["scheduling"] = json!(state.scheduling);
            result["preference_revision"] = json!(state.preference_revision());
            result["resource_preferences"] = json!(state.resource_preferences);
            result["effective_resource_preferences"] = json!(
                state
                    .policy
                    .resources
                    .keys()
                    .filter_map(|k| state
                        .preferred_system_for_resource(k)
                        .map(|system| (k, system)))
                    .collect::<BTreeMap<_, _>>()
            );
            result["effective_native_policy"] = json!(state.native_policy());
            return Ok(result);
        }
        let queue = if ["current", "allocation"].contains(&view) {
            state.waiting_decisions(now, &boot)?
        } else {
            BTreeMap::new()
        };
        if let Some(id) = selected {
            if !state.allocations.contains_key(id) {
                return Err("selected allocation is unknown; no latest substitution".into());
            }
            result["allocation"] = allocation_row(state, id, &queue, now, &boot, &source, true);
            let selected = &state.allocations[id];
            let contributions: Vec<_> = state.allocations.keys().filter(|other| other.as_str() != id).filter_map(|other| {
                let quantities: BTreeMap<_,_> = selected.request.demand.keys().filter_map(|key| committed(state,other,key,now,&boot).filter(|amount|*amount>0).map(|amount|(key.clone(),amount))).collect();
                if quantities.is_empty() { return None; }
                let a = &state.allocations[other];
                Some((other.clone(),json!({"allocation_id":other,"owner_ref":a.request.owner_ref,"operation_id":a.request.operation_id,"attempt_id":a.request.attempt_id,"phase":a.phase,"quantities":quantities,"inspect":{"action":"inspect","view":"allocation","allocation_id":other,"source":source}})))
            }).collect();
            let total = contributions.len();
            let entries: Vec<_> = contributions
                .into_iter()
                .filter(|(id, _)| after.is_none_or(|a| id.as_str() > a))
                .take(limit + 1)
                .collect();
            let more = entries.len() > limit;
            let entries: BTreeMap<_, _> = entries.into_iter().take(limit).collect();
            result["commitments"] = json!({"items":entries,"page":{"returned":entries.len(),"total_matching":total,"limit":limit,"more":more,"next_parameters":if more{json!({"view":view,"source":source,"after_id":entries.keys().last()})}else{Value::Null}},"meaning":"Current charged commitments contributing to this allocation's requested resources, excluding itself. Current totals can differ from a scheduler simulation after hypothetical earlier admissions; neither view is a reservation."});
            result["diagnostic_routes"] = json!({"domain_owner_ref":selected.request.owner_ref,"native_binding":selected.runtime,"capacity_cpu_policy":{"action":"cpu_policy"},"capacity_io_policy":{"action":"io_policy"},"meaning":"Native policy reads are passive explicit diagnostics. Diagnose the exact retained binding with its owner; later native evidence must retain its own time and cannot explain an earlier skip as fact."});
            return Ok(result);
        }
        let mut counts = BTreeMap::<String, usize>::new();
        for a in state.allocations.values() {
            *counts
                .entry(
                    serde_json::to_value(&a.phase)
                        .unwrap()
                        .as_str()
                        .unwrap()
                        .into(),
                )
                .or_default() += 1;
        }
        let candidates: Vec<_> = match view {
            "current" | "history" => state
                .allocations
                .iter()
                .filter(|(_, a)| (a.phase == Phase::Released) == (view == "history"))
                .map(|(id, _)| id.clone())
                .collect(),
            "holds" => state.holds.keys().cloned().collect(),
            "pressure" => state.pressure.keys().cloned().collect(),
            "maintenance" => state.maintenance.keys().cloned().collect(),
            _ => unreachable!(),
        };
        let total = candidates.len();
        let ids: Vec<_> = candidates
            .into_iter()
            .filter(|id| after.is_none_or(|a| id.as_str() > a))
            .take(limit + 1)
            .collect();
        let more = ids.len() > limit;
        let ids: Vec<_> = ids.into_iter().take(limit).collect();
        let rows: BTreeMap<_, _> = ids
            .iter()
            .map(|id| {
                (
                    id.clone(),
                    match view {
                        "current" | "history" => {
                            allocation_row(state, id, &queue, now, &boot, &source, false)
                        }
                        "holds" => json!(state.holds[id]),
                        "pressure" => json!(state.pressure[id]),
                        "maintenance" => json!(state.maintenance[id]),
                        _ => unreachable!(),
                    },
                )
            })
            .collect();
        result["items"] = json!(rows);
        result["page"] = json!({"returned":ids.len(),"total_matching":total,"limit":limit,"more":more,"next_parameters":if more{json!({"view":view,"source":source,"after_id":ids.last()})}else{Value::Null}});
        if view == "current" {
            result["allocation_counts"] = json!(counts);
            result["resources"] = resource_summaries(state, now, &boot);
            result["related_counts"] = json!({"holds":state.holds.len(),"pressure":state.pressure.len(),"maintenance":state.maintenance.len()});
            result["control_discovery"] =
                json!({"operation":"controls","host_id":host,"parameters":{"source":source}});
        }
        return Ok(result);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;
    struct Fixture(std::path::PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "capacity-view-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            let policy:Policy=serde_json::from_value(json!({"host_id":"fixture-host","machine_id":machine_id().unwrap(),"resources":{"ram":{"unit":"bytes","capacity":100,"operational_margin":10,"priority_growth_margin":10,"observation_max_age_ms":60000}}})).unwrap();
            Store::initialize(&root, policy).unwrap();
            Self(root)
        }
        fn populate(&self) {
            let mut store = Store::open(&self.0).unwrap();
            for i in 0..201 {
                let id = format!("allocation-{i:03}");
                let request:Request=serde_json::from_value(json!({"allocation_id":id,"host_id":"fixture-host","system_id":"science","owner_ref":"domain/exact","operation_id":format!("op-{i}"),"attempt_id":"one","source_ref":"full-source-detail","configuration_ref":"config","eligibility_ref":"reviewed","release_route":"native-owner","priority":"priority","demand":{"ram":1}})).unwrap();
                store.state.allocations.insert(
                    id,
                    Allocation {
                        request,
                        phase: if i == 200 {
                            Phase::Active
                        } else {
                            Phase::Released
                        },
                        revision: 1,
                        queued_sequence: i,
                        admitted_sequence: None,
                        steps: BTreeMap::new(),
                        release_evidence: None,
                        runtime: None,
                        adoption: None,
                    },
                );
            }
            store.save().unwrap();
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }
    fn read(
        f: &Fixture,
        operation: &str,
        allocation: Option<&str>,
        p: Value,
        limit: usize,
    ) -> Result<Value> {
        read_or_control(
            &f.0,
            &json!({"operation":operation,"host_id":"fixture-host","allocation_id":allocation,"parameters":p,"limit":limit,"timeout_ms":0}),
        )
    }
    #[test]
    fn captured_projection_reuses_one_state_without_holding_or_reopening_decision_lock() {
        let f = Fixture::new();
        f.populate();
        let captured = Store::snapshot(&f.0).unwrap();
        let revision = captured.revision;
        let source = captured.instance_id.clone();
        let mut writer = Store::open(&f.0).unwrap();
        writer.state.revision += 1;
        writer.save().unwrap();
        let root = f.0.clone();
        let (tx, rx) = std::sync::mpsc::channel();
        let reader = std::thread::spawn(move || {
            tx.send(read_or_control_snapshot(&root,
                &json!({"operation":"inspect","host_id":"fixture-host","allocation_id":"allocation-200",
                    "parameters":{"view":"allocation"},"limit":25,"timeout_ms":0}), Some(captured))).unwrap();
        });
        let result = rx.recv_timeout(Duration::from_secs(5));
        drop(writer);
        reader.join().unwrap();
        let result = result.unwrap().unwrap();
        assert_eq!(result["state_revision"], revision);
        assert_eq!(result["source"]["instance_id"], source);
        assert_eq!(
            result["allocation"]["allocation"]["request"]["allocation_id"],
            "allocation-200"
        );
        let current = direct(
            &f.0,
            &json!({"action":"inspect","view":"allocation","allocation_id":"allocation-200"}),
        )
        .unwrap();
        assert_eq!(current["state_revision"], revision + 1);
        assert_eq!(current["allocation"], result["allocation"]);
        assert_eq!(current["commitments"], result["commitments"]);
    }

    #[test]
    fn focused_current_history_and_exact_detail_preserve_state_and_coverage() {
        let f = Fixture::new();
        f.populate();
        let before = fs::read(f.0.join("state.json")).unwrap();
        let current = read(&f, "overview", None, json!({}), 1).unwrap();
        let gpu = direct(&f.0, &json!({"action":"inspect","view":"gpu_pressure"})).unwrap();
        assert_eq!(
            gpu["gpu_pressure"]["source"]["instance_id"],
            current["source"]["instance_id"]
        );
        assert_eq!(gpu["gpu_pressure"]["source"]["boot_id"], boot_id().unwrap());
        assert_eq!(gpu["gpu_pressure"]["gpu_constraints"], json!({}));
        assert!(gpu.get("allocations").is_none() && gpu.get("items").is_none());
        assert_eq!(current["items"].as_object().unwrap().len(), 1);
        assert_eq!(current["allocation_counts"]["released"], 200);
        assert_eq!(current["page"]["more"], false);
        assert!(!current.to_string().contains("full-source-detail"));
        assert!(current.get("state").is_none());
        assert!(current.get("policy").is_none());
        let mut p = json!({"view":"history"});
        let mut ids = std::collections::BTreeSet::new();
        loop {
            let page = read(&f, "inspect", None, p, 37).unwrap();
            assert_eq!(page["page"]["total_matching"], 200);
            for id in page["items"].as_object().unwrap().keys() {
                assert!(ids.insert(id.clone()));
            }
            if page["page"]["more"] == false {
                break;
            }
            p = page["page"]["next_parameters"].clone();
        }
        assert_eq!(ids.len(), 200);
        let exact = read(
            &f,
            "evidence",
            Some("allocation-200"),
            json!({"source":current["source"]}),
            1,
        )
        .unwrap();
        assert_eq!(
            exact["allocation"]["allocation"]["request"]["source_ref"],
            "full-source-detail"
        );
        assert_eq!(
            exact["allocation"]["resources"]["ram"]["observed"],
            Value::Null
        );
        assert_eq!(before, fs::read(f.0.join("state.json")).unwrap());
        assert!(
            handle(&f.0, json!({"action":"inspect"})).unwrap()["state"]["allocations"]
                .as_object()
                .unwrap()
                .len()
                == 201
        );
    }
    #[test]
    fn page_and_follow_reject_retargeting_and_controls_keep_original_guards() {
        let f = Fixture::new();
        f.populate();
        let current = read(&f, "overview", None, json!({}), 1).unwrap();
        assert!(
            read(
                &f,
                "inspect",
                None,
                json!({"view":"history","after_id":"allocation-000"}),
                1
            )
            .is_err()
        );
        let mut wrong = current["source"].clone();
        wrong["instance_id"] = json!("replacement");
        assert!(
            read(
                &f,
                "inspect",
                None,
                json!({"view":"history","after_id":"allocation-000","source":wrong}),
                1
            )
            .unwrap_err()
            .contains("source changed")
        );
        assert!(
            read(
                &f,
                "follow",
                Some("allocation-200"),
                json!({"cursor":current["cursor"]}),
                1
            )
            .unwrap_err()
            .contains("different view")
        );
        let unchanged = read(&f, "follow", None, json!({"cursor":current["cursor"]}), 1).unwrap();
        assert_eq!(unchanged["timed_out"], true);
        let mut replaced = current["cursor"].clone();
        replaced["source"] = wrong.clone();
        assert_eq!(
            read(&f, "follow", None, json!({"cursor":replaced}), 1).unwrap()["source_changed"],
            true
        );
        let hold = json!({"action":"hold","source":current["source"],"revision":999999,"hold":{"hold_id":"test","owner_ref":"fixture","reason":"hold","scope":{"host_id":"fixture-host"},"revision":0}});
        assert!(
            read(&f, "control", None, hold, 1)
                .unwrap_err()
                .contains("stale")
        );
        let controls = read(&f, "controls", None, json!({}), 1).unwrap();
        assert!(controls.get("items").is_none());
        assert!(
            controls["controls"][0]["input_schema"]["required"]
                .as_array()
                .unwrap()
                .contains(&json!("source"))
        );
        assert!(
            controls["controls"][0]["input_schema"]["required"]
                .as_array()
                .unwrap()
                .contains(&json!("revision"))
        );
    }
    #[test]
    fn held_records_are_pageable_and_removed_historical_resources_are_honest() {
        let f = Fixture::new();
        f.populate();
        {
            let mut store = Store::open(&f.0).unwrap();
            for id in ["a", "b", "c"] {
                store.state.hold(serde_json::from_value(json!({"hold_id":id,"owner_ref":"fixture","reason":"held","scope":{"host_id":"fixture-host"},"revision":0})).unwrap()).unwrap();
            }
            store
                .state
                .allocations
                .get_mut("allocation-000")
                .unwrap()
                .request
                .demand
                .insert("retired".into(), 42);
            store.save().unwrap();
        }
        let page = read(&f, "inspect", None, json!({"view":"holds"}), 2).unwrap();
        assert_eq!(page["page"]["more"], true);
        let next = read(
            &f,
            "inspect",
            None,
            page["page"]["next_parameters"].clone(),
            2,
        )
        .unwrap();
        assert_eq!(next["items"].as_object().unwrap().len(), 1);
        assert_eq!(next["page"]["more"], false);
        let old = read(&f, "inspect", Some("allocation-000"), json!({}), 1).unwrap();
        assert_eq!(
            old["allocation"]["resources"]["retired"]["status"],
            "resource_no_longer_enrolled"
        );
    }
    #[test]
    fn allocation_contributors_are_bounded_and_missing_attribution_is_not_zero() {
        let f = Fixture::new();
        f.populate();
        let sampled = now_ms().saturating_sub(10);
        {
            let mut store = Store::open(&f.0).unwrap();
            for id in ["allocation-197", "allocation-198", "allocation-199"] {
                store.state.allocations.get_mut(id).unwrap().phase = Phase::Active;
            }
            store.state.resource_observations.insert(
                "ram".into(),
                ResourceObservation {
                    boot_id: boot_id().unwrap(),
                    observed_at_ms: sampled,
                    source_ref: "retained sample".into(),
                    external_demand: Some(5),
                    unavailable: None,
                    accounted_revisions: BTreeMap::new(),
                    accounted_runtimes: BTreeMap::new(),
                    managed_demand: BTreeMap::new(),
                },
            );
            store.save().unwrap();
        }
        let first = read(&f, "inspect", Some("allocation-200"), json!({}), 2).unwrap();
        let evidence = &first["allocation"]["resources"]["ram"];
        assert_eq!(evidence["status"], "not_bound");
        assert_eq!(evidence["observed"], Value::Null);
        assert_eq!(evidence["requested"], 1);
        assert_eq!(evidence["committed"], 1);
        assert_eq!(evidence["observation_at_ms"], sampled);
        assert_eq!(first["commitments"]["page"]["total_matching"], 3);
        let items = first["commitments"]["items"].as_object().unwrap();
        assert_eq!(items.len(), 2);
        assert!(!items.contains_key("allocation-200"));
        assert_eq!(items["allocation-197"]["quantities"]["ram"], 1);
        assert!(items["allocation-197"].get("request").is_none());
        let last = read(
            &f,
            "inspect",
            Some("allocation-200"),
            first["commitments"]["page"]["next_parameters"].clone(),
            2,
        )
        .unwrap();
        assert_eq!(last["commitments"]["items"].as_object().unwrap().len(), 1);
        assert_eq!(last["commitments"]["page"]["more"], false);
    }
    #[test]
    fn help_uses_real_schema_without_live_state_or_unrelated_definitions() {
        let listing = help(&json!({"action":"help"})).unwrap();
        assert_eq!(listing["start"]["action"], "overview");
        let inspect = help(&json!({"action":"help","operation":"inspect"})).unwrap();
        assert!(inspect["input_schema"].get("$defs").is_none());
        let policy = help(&json!({"action":"help","operation":"reconfigure_policy"})).unwrap();
        assert_eq!(
            policy["input_schema"]["$defs"]
                .as_object().unwrap().keys().map(String::as_str).collect::<BTreeSet<_>>(),
            BTreeSet::from(["DomainControl", "ExpectedSource", "Policy", "ResourcePolicy"])
        );
    }

    #[test]
    fn every_native_action_has_matching_schema_examples_and_semantic_help() {
        let native: BTreeSet<_> = IndependentAction::NAMES.iter()
            .chain(StoredAction::NAMES).copied().collect();
        let schema: Value = serde_json::from_str(include_str!("../request.schema.json")).unwrap();
        let alternatives = schema["oneOf"].as_array().unwrap();
        let advertised: BTreeSet<_> = alternatives.iter()
            .map(|entry| entry["properties"]["action"]["const"].as_str().unwrap())
            .collect();
        assert_eq!(advertised.len(), alternatives.len(), "duplicate schema action");
        assert_eq!(advertised, native);
        let usage = usage();
        for surface in ["actions", "operation_details"] {
            let names: BTreeSet<_> = usage[surface].as_object().unwrap().keys().map(String::as_str).collect();
            assert_eq!(names, native, "incomplete {surface}");
        }
        for name in native {
            let described = help(&json!({"action":"help","operation":name})).unwrap();
            assert_eq!(described["input_schema"]["properties"]["action"]["const"], name);
            assert_eq!(described["semantics"], usage["operation_details"][name]);
        }
    }
}
