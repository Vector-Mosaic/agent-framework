//! Pressure is a host decision joined to a real domain controller, never a save
//! implementation. Domain calls run outside the capacity lock and native release
//! still uses the existing exact allocation/release owner.
use crate::*;
use serde_json::{Value, json};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DomainControl {
    pub argv: Vec<String>,
    pub qualification_ref: String,
    pub timeout_ms: u64,
    pub stable_recovery_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Transition {
    pub allocation_id: String,
    pub request_id: String,
    pub reason: String,
    pub constrained_resources: BTreeSet<String>,
    pub stage: String,
    pub expected_control_revision: Option<u64>,
    pub resume_request_id: Option<String>,
    pub resume_control_revision: Option<u64>,
    pub last_observation: Option<DomainObservation>,
    pub last_error: Option<String>,
    pub recovery_since_ms: Option<u64>,
    pub recovery_boot_id: Option<String>,
    pub last_check_ms: u64,
}
impl Transition {
    pub fn terminal(&self) -> bool {
        matches!(self.stage.as_str(), "resumed" | "completed" | "rejected")
    }

    pub(crate) fn needs_reconciliation(&self, state: &State, boot: &str) -> bool {
        if self.terminal() {
            return false;
        }
        // An inspection without an admitted release cannot free resources from
        // an already released, positively ended native lifetime. Keep its
        // evidence without fencing the resource or calling its former owner.
        let ended_inspection = self.stage == "inspecting"
            && self.expected_control_revision.is_none()
            && self.resume_request_id.is_none()
            && self.resume_control_revision.is_none()
            && self
                .last_observation
                .as_ref()
                .is_none_or(|o| o.pressure_request.is_none())
            && state.allocations.get(&self.allocation_id).is_some_and(|a| {
                a.phase == Phase::Released && state.native_runtime_ended_at_boot(a, boot)
            });
        !ended_inspection
    }
}

/// The owner returns only operation/control evidence. Its continuation and
/// workload records stay at their existing owner_ref.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DomainObservation {
    pub allocation_id: String,
    pub owner_ref: String,
    pub operation_id: String,
    pub attempt_id: String,
    pub control_revision: u64,
    pub release_eligible: bool,
    pub resume_eligible: bool,
    /// A current owner observation: release discards only reconstructible idle
    /// residency, rather than interrupting useful active computation.
    #[serde(default)]
    pub reclaimable_idle: bool,
    pub qualification_refs: Vec<String>,
    pub limitation: Option<String>,
    pub pressure_request: Option<PressureRequest>,
    #[serde(default)]
    pub original_attempt_state: Option<String>,
    #[serde(default)]
    pub independent_successor: Option<IndependentSuccessor>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IndependentSuccessor {
    pub origin: String,
    pub request_id: Option<String>,
    pub attempt_id: String,
    pub allocation_id: Option<String>,
    pub owner_ref: String,
    pub state: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PressureRequest {
    pub request_id: String,
    pub origin: String,
    pub state: String,
    pub effect_started: bool,
    #[serde(default)]
    pub retry_eligible: bool,
    pub processes_stopped: bool,
    pub resources_released: bool,
    // Keep acknowledgement presence, not a copied workload checkpoint body.
    pub save_acknowledged: bool,
    /// A qualified stateless boundary is not a workload checkpoint. Its
    /// evidence remains at the exact owner and is freshly inspected per action.
    #[serde(default)]
    pub continuation_preserved: bool,
    #[serde(default)]
    pub continuation_ref: Option<String>,
    pub resume: Option<ResumeRequest>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResumeRequest {
    pub request_id: String,
    pub pressure_request_id: String,
    pub state: String,
    #[serde(default)]
    pub retry_eligible: bool,
    pub successor: Option<Successor>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Successor {
    pub attempt_id: String,
    pub allocation_id: String,
    pub owner_ref: String,
    pub state: String,
}
fn parse_observation(bytes: &[u8]) -> Result<DomainObservation> {
    let mut reply: Value = serde_json::from_slice(bytes).map_err(|_| "invalid domain response")?;
    if reply["status"] != "ok" {
        return Err("domain control returned error; reconcile original request".into());
    }
    let data = reply.get_mut("data").ok_or("domain response lacks data")?;
    if let Some(workflow) = data
        .get_mut("pressure_request")
        .and_then(Value::as_object_mut)
    {
        let acknowledged = workflow
            .remove("fresh_checkpoint")
            .is_some_and(|v| !v.is_null());
        workflow.insert("save_acknowledged".into(), json!(acknowledged));
    }
    serde_json::from_value(data.take())
        .map_err(|_| "domain response lacks exact control fields".into())
}

pub fn validate_policy(policy: &Policy) -> Result<()> {
    for (system, control) in &policy.domain_controls {
        nonempty(system, "domain control system")?;
        nonempty(
            &control.qualification_ref,
            "qualified domain control reference",
        )?;
        if control.argv.is_empty()
            || !Path::new(&control.argv[0]).is_absolute()
            || control
                .argv
                .iter()
                .any(|s| s.is_empty() || s.contains('\0'))
            || !(1..=10_000).contains(&control.timeout_ms)
            || control.stable_recovery_ms == 0
        {
            return Err("domain control requires absolute fixed argv, bounded acceptance and explicit recovery interval".into());
        }
    }
    Ok(())
}

fn fresh<'a>(
    state: &'a State,
    resource: &str,
    now: u64,
    boot: &str,
) -> Option<&'a ResourceObservation> {
    let policy = state.policy.resources.get(resource)?;
    state.resource_observations.get(resource).filter(|o| {
        o.boot_id == boot
            && now >= o.observed_at_ms
            && now - o.observed_at_ms <= policy.observation_max_age_ms
            && o.unavailable.is_none()
            && o.external_demand.is_some()
            && o.accounted_runtimes.iter().all(|(id, binding)| {
                state
                    .allocations
                    .get(id)
                    .is_some_and(|a| &a.runtime == binding)
            })
    })
}

/// Describe a useful host release boundary. Unknown evidence cannot justify a
/// blind release; it continues to block admission through the existing owner.
pub fn constrained(state: &State, now: u64, boot: &str) -> BTreeMap<String, String> {
    constrained_by(state, now, boot)
        .into_iter()
        .map(|(key, constraint)| (key, constraint.reason))
        .collect()
}

struct Constraint {
    reason: String,
    driver: Driver,
    // Physical pressure can coincide with an independently short preferred waiter.
    preferred_waiter: Option<String>,
    reservation_shortfall: Option<ReservationShortfall>,
}

enum Driver {
    Physical,
    Waiting(String),
    Reservation,
}

impl Constraint {
    fn waiter(&self) -> Option<&String> {
        match &self.driver {
            Driver::Waiting(waiter) => Some(waiter),
            _ => None,
        }
    }
}

#[derive(Serialize)]
struct ReservationShortfall {
    bytes: u64,
    unused_by_system: BTreeMap<String, u64>,
}

fn constrained_by(state: &State, now: u64, boot: &str) -> BTreeMap<String, Constraint> {
    let mut reasons = BTreeMap::new();
    let mut shortfalls = BTreeMap::new();
    for (id, policy) in &state.policy.resources {
        let Some(observation) = fresh(state, id, now, boot) else {
            continue;
        };
        let mut unused_by_system = state
            .resource_reservation(id)
            .map(|r| r.system_floors.clone())
            .unwrap_or_default();
        let commitments: u64 = state
            .allocations
            .values()
            .filter(|a| {
                matches!(
                    a.phase,
                    Phase::Admitted | Phase::Active | Phase::ReleasePending
                )
            })
            .map(|a| {
                let charge = state.allocation_commitment(a, id, now, boot);
                if charge != 0 && !unused_by_system.is_empty() {
                    let system = state.effective_request(&a.request).system_id;
                    if let Some(unused) = unused_by_system.get_mut(&system) {
                        *unused = unused.saturating_sub(charge);
                    }
                }
                charge
            })
            .fold(0, u64::saturating_add);
        let charged = commitments.saturating_add(observation.external_demand.unwrap());
        let usable = policy
            .capacity
            .saturating_sub(state.physical_operational_margin(id));
        if charged > usable {
            reasons.insert(
                id.clone(),
                Constraint {
                    reason: "observed commitments approach the operational floor".into(),
                    driver: Driver::Physical,
                    preferred_waiter: None,
                    reservation_shortfall: None,
                },
            );
        }
        unused_by_system.retain(|_, unused| *unused > 0);
        let protected: u64 = unused_by_system.values().sum();
        let bytes = protected.saturating_sub(usable.saturating_sub(charged));
        if bytes > 0 {
            shortfalls.insert(
                id.clone(),
                ReservationShortfall {
                    bytes,
                    unused_by_system,
                },
            );
        }
    }
    let mut waiting: Vec<_> = state
        .allocations
        .values()
        .filter(|a| {
            state.has_waiting_demand(a)
                && state.effective_request(&a.request).priority == Priority::Priority
        })
        .collect();
    waiting.sort_by_key(|a| {
        (
            state.waiting_sequence(a).max(
                state
                    .last_turn
                    .get(&state.effective_request(&a.request).system_id)
                    .copied()
                    .unwrap_or(0),
            ),
            state.waiting_sequence(a),
        )
    });
    for waiter in waiting {
        let Ok(assessment) = state.waiting_assessment(waiter, now, boot) else {
            continue;
        };
        if waiter
            .request
            .demand
            .keys()
            .any(|r| fresh(state, r, now, boot).is_none())
        {
            continue;
        }
        if !assessment.eligible || !assessment.hold_ids.is_empty() || assessment.can_admit {
            continue;
        }
        for (resource, demand) in &state.waiting_request(waiter).demand {
            if fresh(state, resource, now, boot).is_some()
                && assessment
                    .available
                    .get(resource)
                    .is_some_and(|available| available < demand)
            {
                reasons
                    .entry(resource.clone())
                    .or_insert_with(|| Constraint {
                        reason: format!(
                            "oldest eligible waiting demand: {}",
                            waiter.request.allocation_id
                        ),
                        driver: Driver::Waiting(waiter.request.allocation_id.clone()),
                        preferred_waiter: None,
                        reservation_shortfall: None,
                    });
            }
        }
        // The oldest eligible contender defines the useful conflicting boundary;
        // later requests do not trigger extra drains ahead of its turn.
        if !reasons.is_empty() {
            break;
        }
    }
    // The preferred caller owns precedence on this GPU alone. All other
    // binding resources and physical pressure retain the ordinary decision.
    for (resource, id) in state.preferred_gpu_waiters(now, boot) {
        if fresh(state, &resource, now, boot).is_none() {
            continue;
        }
        let waiter = &state.allocations[&id];
        let request = state.waiting_request(waiter);
        if state
            .waiting_assessment(waiter, now, boot)
            .is_ok_and(|assessment| {
                assessment
                    .available
                    .get(&resource)
                    .is_some_and(|available| available < &request.demand[&resource])
            })
        {
            if let Some(physical) = reasons
                .get_mut(&resource)
                .filter(|c| matches!(c.driver, Driver::Physical))
            {
                physical.preferred_waiter = Some(id);
            } else {
                reasons.insert(
                    resource,
                    Constraint {
                        reason: format!("preferred eligible waiting demand: {id}"),
                        driver: Driver::Waiting(id.clone()),
                        preferred_waiter: Some(id),
                        reservation_shortfall: None,
                    },
                );
            }
        }
    }
    for (resource, shortfall) in shortfalls {
        let explanation = format!(
            "standing GPU reservation short by {} bytes",
            shortfall.bytes
        );
        let constraint = reasons
            .entry(resource)
            .and_modify(|c| {
                c.reason.push_str("; ");
                c.reason.push_str(&explanation);
            })
            .or_insert_with(|| Constraint {
                reason: explanation,
                driver: Driver::Reservation,
                preferred_waiter: None,
                reservation_shortfall: None,
            });
        constraint.reservation_shortfall = Some(shortfall);
    }
    reasons
}

const INSPECTION_CONTEXT_ENV: &str = "APEIROS_CAPACITY_PRESSURE_CONTEXT_V1";

fn inspection_context(
    state: &State,
    constraints: &BTreeMap<String, Constraint>,
    captured_at: u64,
    boot: &str,
) -> Value {
    let waiter_identity = |id: &String| {
        let system = state
            .effective_request(&state.allocations[id].request)
            .system_id;
        json!({"allocation_id":id,"system_id":system})
    };
    let gpu_constraints: BTreeMap<_, _> = constraints.iter().filter_map(|(key, constraint)| {
        let policy = &state.policy.resources[key];
        let Some(native::Resource::GpuMemory { uuid }) = &policy.native else { return None };
        // constrained_by already required a fresh sample in this exact state.
        let sample = &state.resource_observations[key];
        let mut hint = json!({
            "gpu_uuid":uuid,
            "observed_at_ms":sample.observed_at_ms,
            "valid_through_ms":sample.observed_at_ms.saturating_add(policy.observation_max_age_ms),
            "cause":match constraint.driver {
                Driver::Physical => "physical_pressure", Driver::Waiting(_) => "waiting_demand",
                Driver::Reservation => "reservation_shortfall"
            },
            "waiter":constraint.waiter().map(waiter_identity),
            "preferred_system":state.preferred_system_for_resource(key),
            "preferred_waiter":constraint.preferred_waiter.as_ref().map(waiter_identity),
        });
        if let Some(shortfall) = &constraint.reservation_shortfall {
            hint["reservation_shortfall"] = json!(shortfall);
        }
        Some((key, hint))
    }).collect();
    json!({
        "schema":"apeiros.capacity.pressure_inspection.v1",
        "source":{"host_id":state.policy.host_id,"instance_id":state.instance_id,"boot_id":boot},
        "captured_at_ms":captured_at, "state_revision":state.revision,
        "policy_revision":state.policy_revision, "preference_revision":state.preference_revision(),
        "gpu_constraints":gpu_constraints,
    })
}

/// A passive snapshot of the native pressure decision. Domain authorization
/// and actual effects still require the independent current owner joins.
pub fn gpu_pressure_context(state: &State, now: u64, boot: &str) -> Value {
    inspection_context(state, &constrained_by(state, now, boot), now, boot)
}

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Yield {
    Ordinary,
    PreferredPeer,
    IdleOnly,
}

// Releasing an owner's charged bytes below its own floor does not make
// additional capacity available to a different reserved system.
fn lends_to_reservation(
    state: &State,
    allocation: &Allocation,
    resource: &str,
    now: u64,
    boot: &str,
) -> u64 {
    let system = state.effective_request(&allocation.request).system_id;
    let protected = state.unused_system_reservation(
        &system,
        resource,
        Some(&allocation.request.allocation_id),
        now,
        boot,
    );
    state
        .allocation_commitment(allocation, resource, now, boot)
        .saturating_sub(protected)
}

fn yield_order(
    state: &State,
    allocation: &Allocation,
    resources: &BTreeSet<String>,
    constraints: &BTreeMap<String, Constraint>,
    now: u64,
    boot: &str,
) -> Option<Yield> {
    let system = state.effective_request(&allocation.request).system_id;
    resources
        .iter()
        .filter_map(|key| {
            let constraint = constraints.get(key)?;
            let waiter = constraint.waiter();
            if waiter == Some(&allocation.request.allocation_id) {
                return None;
            }
            if matches!(constraint.driver, Driver::Reservation) {
                return (lends_to_reservation(state, allocation, key, now, boot) > 0)
                    .then_some(Yield::Ordinary);
            }
            if state.preferred_system_for_resource(key) != Some(system.as_str()) {
                return Some(Yield::Ordinary);
            }
            let Some(waiter) = waiter else {
                return Some(Yield::PreferredPeer); // Physical pressure can still require release.
            };
            if state
                .effective_request(&state.allocations[waiter].request)
                .system_id
                == system
            {
                return Some(Yield::PreferredPeer);
            }
            // Returning bytes within the owner's standing floor would leave the
            // lower-ranked waiter just as far from fit, even if weights are idle.
            (lends_to_reservation(state, allocation, key, now, boot) > 0).then_some(Yield::IdleOnly)
        })
        .min()
}

fn ranked_candidates(
    state: &State,
    constraints: &BTreeMap<String, Constraint>,
    now: u64,
    boot: &str,
) -> Vec<(String, BTreeSet<String>, String, Yield)> {
    let pending: BTreeSet<_> = state
        .pressure
        .values()
        .filter(|t| {
            t.needs_reconciliation(state, boot) && t.stage != "blocked" && t.stage != "released"
        })
        .flat_map(|t| t.constrained_resources.iter().cloned())
        .collect();
    let mut candidates: Vec<_> = state
        .allocations
        .values()
        .filter_map(|a| {
            if a.phase != Phase::Active
                || state.native_runtime_ended_at_boot(a, boot)
                || !state
                    .policy
                    .domain_controls
                    .contains_key(&a.request.system_id)
                || state.pressure.contains_key(&a.request.allocation_id)
            {
                return None;
            }
            let resources: BTreeSet<_> = a
                .request
                .demand
                .keys()
                .filter(|key| {
                    constraints.get(*key).is_some_and(|constraint| {
                        // Unloading a phase solely to satisfy its own total growth
                        // need cannot create the missing capacity. Avoid an idle
                        // unload/reload loop; physical pressure can still select it.
                        constraint.waiter() != Some(&a.request.allocation_id)
                    }) && !pending.contains(*key)
                })
                .cloned()
                .collect();
            if resources.is_empty() {
                return None;
            }
            let reason = resources
                .iter()
                .map(|r| format!("{r}: {}", constraints[r].reason))
                .collect::<Vec<_>>()
                .join("; ");
            let order = yield_order(state, a, &resources, constraints, now, boot)?;
            Some((a, resources, reason, order))
        })
        .collect();
    let lone_reservation = constraints
        .iter()
        .next()
        .filter(|(_, c)| constraints.len() == 1 && matches!(c.driver, Driver::Reservation));
    candidates.sort_by_cached_key(|(a, _, _, _)| {
        let fit = lone_reservation.map(|(resource, c)| {
            let need = c.reservation_shortfall.as_ref().unwrap().bytes;
            let release = lends_to_reservation(state, a, resource, now, boot);
            // Select one useful boundary. Prefer the smallest release that
            // covers the deficit, or the largest contribution if none can.
            (release < need, release.abs_diff(need))
        });
        (
            if state.effective_request(&a.request).priority == Priority::Normal {
                0
            } else {
                1
            },
            fit,
            a.admitted_sequence.unwrap_or(u64::MAX),
        )
    });
    candidates
        .into_iter()
        .map(|(a, resources, reason, order)| {
            (a.request.allocation_id.clone(), resources, reason, order)
        })
        .collect()
}

pub fn candidates(state: &State, now: u64, boot: &str) -> Vec<(String, BTreeSet<String>, String)> {
    ranked_candidates(state, &constrained_by(state, now, boot), now, boot)
        .into_iter()
        .map(|(id, resources, reason, _)| (id, resources, reason))
        .collect()
}

fn resume_fit(state: &State, allocation: &Allocation, now: u64, boot: &str) -> bool {
    if allocation.phase != Phase::Released
        || !state
            .assess(&allocation.request, now, boot)
            .is_ok_and(|a| a.can_admit)
    {
        return false;
    }
    !state.allocations.values().any(|other| {
        state.has_waiting_demand(other)
            && other
                .request
                .demand
                .keys()
                .any(|r| allocation.request.demand.contains_key(r))
            && state
                .waiting_assessment(other, now, boot)
                .is_ok_and(|a| a.eligible && a.hold_ids.is_empty())
    })
}

fn call(
    control: &DomainControl,
    request: &Value,
    inspection_context: Option<&str>,
) -> Result<DomainObservation> {
    // Nonblocking pipes bound the whole owner frontdoor, including lost output
    // and a descendant retaining a pipe. No detached reader/writer threads.
    let mut command = Command::new(&control.argv[0]);
    command.env_remove(INSPECTION_CONTEXT_ENV);
    if let Some(context) = inspection_context {
        command.env(INSPECTION_CONTEXT_ENV, context);
    }
    let mut child = command
        .args(&control.argv[1..])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "domain control could not start; reconcile original request")?;
    let mut input = child.stdin.take();
    let mut output = child.stdout.take().ok_or("domain output unavailable")?;
    for fd in [
        input
            .as_ref()
            .ok_or("domain stdin unavailable")?
            .as_raw_fd(),
        output.as_raw_fd(),
    ] {
        let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
        if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
            let _ = child.kill();
            let _ = child.wait();
            return Err("domain pipe control unavailable".into());
        }
    }
    let bytes = serde_json::to_vec(request).map_err(|e| e.to_string())?;
    let mut written = 0;
    let mut result = vec![];
    let mut buffer = [0u8; 8192];
    let started = Instant::now();
    let outcome = (|| -> Result<DomainObservation> {
        loop {
            if let Some(pipe) = input.as_mut() {
                match pipe.write(&bytes[written..]) {
                    Ok(n) => written += n,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                    Err(_) => return Err("domain request write uncertain".into()),
                }
                if written == bytes.len() {
                    input = None;
                }
            }
            loop {
                match output.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(n) => {
                        result.extend_from_slice(&buffer[..n]);
                        if result.len() > 1024 * 1024 {
                            return Err("domain output exceeds interface bound".into());
                        }
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(_) => return Err("domain response read uncertain".into()),
                }
            }
            if let Some(status) = child.try_wait().map_err(|_| "domain status uncertain")? {
                if !status.success() {
                    return Err("domain control failed; reconcile original request".into());
                }
                // Reap before parsing; a descendant's inherited stdout cannot
                // make an otherwise complete frontdoor wait without a bound.
                loop {
                    match output.read(&mut buffer) {
                        Ok(0) => break,
                        Ok(n) => {
                            result.extend_from_slice(&buffer[..n]);
                            if result.len() > 1024 * 1024 {
                                return Err("domain output exceeds bound".into());
                            }
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                        Err(_) => return Err("domain output unavailable".into()),
                    }
                }
                return parse_observation(&result);
            }
            if started.elapsed() >= Duration::from_millis(control.timeout_ms) {
                return Err(
                    "domain acceptance timed out; original effect remains uncertain".into(),
                );
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    })();
    if outcome.is_err() {
        let _ = child.kill();
        let _ = child.wait();
    }
    outcome
}
fn request(state: &State, transition: &Transition, action: &str) -> Result<Value> {
    let allocation = &state.allocations[&transition.allocation_id];
    // Inspect the original workflow even if the resume frontdoor never saw its
    // prepared new ID. Its nested resume record reconciles an accepted effect.
    let resume = action == "request_resume";
    let mut value = json!({"action":action,"allocation_id":transition.allocation_id,
        "owner_ref":allocation.request.owner_ref,
        "request_id":if resume {transition.resume_request_id.as_ref().ok_or("resume request identity absent")?} else {&transition.request_id}});
    if action != "inspect" {
        value["expected_attempt"] = allocation
            .request
            .attempt_id
            .parse::<u64>()
            .map(|v| json!(v))
            .unwrap_or_else(|_| json!(allocation.request.attempt_id));
        value["expected_control_revision"] = json!(if resume {
            transition.resume_control_revision
        } else {
            transition.expected_control_revision
        });
        if resume {
            value["pressure_request_id"] = json!(transition.request_id);
        }
    }
    Ok(value)
}
fn validate_observation(
    allocation: &Allocation,
    transition: &Transition,
    observed: &DomainObservation,
) -> Result<()> {
    if observed.allocation_id != transition.allocation_id
        || observed.attempt_id != allocation.request.attempt_id
        || observed.owner_ref != allocation.request.owner_ref
        || observed.operation_id != allocation.request.operation_id
    {
        return Err("domain observation refers to another allocation/operation/attempt".into());
    }
    if let Some(workflow) = &observed.pressure_request {
        if workflow.request_id != transition.request_id
            || workflow.origin != "capacity_pressure"
            || !matches!(
                workflow.state.as_str(),
                "accepted" | "saving" | "released" | "rejected" | "failed" | "resumed"
            )
        {
            return Err("domain pressure request identity/disposition differs".into());
        }
        if workflow.state == "released"
            && !(workflow.processes_stopped
                && workflow.resources_released
                && (workflow.save_acknowledged
                    || (workflow.continuation_preserved
                        && workflow
                            .continuation_ref
                            .as_ref()
                            .is_some_and(|r| !r.trim().is_empty())
                        && !observed.qualification_refs.is_empty()
                        && observed
                            .qualification_refs
                            .iter()
                            .all(|r| !r.trim().is_empty()))
                    || observed.original_attempt_state.as_deref() == Some("completed")))
        {
            return Err(
                "domain release lacks preserved continuation, execution exit or resource evidence"
                    .into(),
            );
        }
        if let Some(resume) = &workflow.resume {
            if Some(&resume.request_id) != transition.resume_request_id.as_ref()
                || resume.pressure_request_id != transition.request_id
                || !matches!(resume.state.as_str(), "accepted" | "rejected" | "failed")
            {
                return Err("domain resume refers to another pressure workflow".into());
            }
        }
    }
    if (observed.release_eligible || observed.resume_eligible)
        && (observed.qualification_refs.is_empty()
            || observed
                .qualification_refs
                .iter()
                .any(|s| s.trim().is_empty()))
    {
        return Err("eligible domain control lacks actual qualification references".into());
    }
    Ok(())
}

/// One independent resident cycle; external calls never hold Store's decision
/// lock. A separate native singleton lock prevents concurrent pressure callers.
pub fn tick(root: &Path) -> Result<Value> {
    let lock = OpenOptions::new()
        .create(true)
        .write(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(root.join("pressure.lock"))
        .map_err(|e| e.to_string())?;
    if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Ok(json!({"busy":true,"effects":"another native pressure cycle owns this state"}));
    }
    // Observe prospective owners outside the decision lock before selecting a
    // drain. Priority alone cannot distinguish an idle cache from active
    // computation. No speculative inspect constitutes a release request.
    let captured = Store::snapshot(root)?;
    let boot = boot_id()?;
    let captured_at = now_ms();
    let constraints = constrained_by(&captured, captured_at, &boot);
    let prospective = ranked_candidates(&captured, &constraints, captured_at, &boot);
    let context = (!prospective.is_empty())
        .then(|| inspection_context(&captured, &constraints, captured_at, &boot).to_string());
    let mut fallback: Option<(Yield, Transition)> = None;
    let mut selected = None;
    let mut observed_blockers = Vec::new();
    for (id, resources, reason, order) in prospective {
        let uuid = fs::read_to_string("/proc/sys/kernel/random/uuid").map_err(|e| e.to_string())?;
        let mut transition = Transition {
            allocation_id: id.clone(),
            request_id: uuid.trim().replace('-', ""),
            reason,
            constrained_resources: resources,
            stage: "inspecting".into(),
            expected_control_revision: None,
            resume_request_id: None,
            resume_control_revision: None,
            last_observation: None,
            last_error: None,
            recovery_since_ms: None,
            recovery_boot_id: None,
            last_check_ms: 0,
        };
        let allocation = &captured.allocations[&id];
        let control = &captured.policy.domain_controls[&allocation.request.system_id];
        let observed = call(
            control,
            &request(&captured, &transition, "inspect")?,
            context.as_deref(),
        )
        .and_then(|o| {
            validate_observation(allocation, &transition, &o)?;
            Ok(o)
        });
        if let Ok(observed) = observed {
            if !observed.release_eligible {
                observed_blockers.push(json!({"allocation_id":id,"owner_ref":observed.owner_ref,
                    "control_revision":observed.control_revision,"release_eligible":false,
                    "limitation":observed.limitation,"effect_requested":false}));
                continue;
            }
            // Keep the existing revision field: an intervening owner change
            // cannot turn this selection into newly refreshed release intent.
            transition.expected_control_revision = Some(observed.control_revision);
            let idle = observed.reclaimable_idle;
            transition.last_observation = Some(observed);
            if idle {
                selected = Some(transition);
                break;
            }
            if order == Yield::IdleOnly {
                observed_blockers.push(json!({"allocation_id":id, "limitation":"Productive preferred work retains this resource ahead of the waiting system.", "effect_requested":false}));
            } else if fallback
                .as_ref()
                .is_none_or(|(retained, _)| order < *retained)
            {
                fallback = Some((order, transition));
            }
        } else if let Err(error) = observed {
            observed_blockers.push(
                json!({"allocation_id":id,"owner_ref":allocation.request.owner_ref,
                "error":error,"effect_requested":false}),
            );
        }
    }
    let mut store = Store::open(root)?;
    if let Some(mut selected) = selected.or_else(|| fallback.map(|(_, t)| t)) {
        // Other admissions, holds and observers may advance while owners reply.
        // Rejoin the original candidate under the current decision lock.
        let id = selected.allocation_id.clone();
        if store.state.instance_id == captured.instance_id
            && store.state.policy.domain_controls == captured.policy.domain_controls
            && store
                .state
                .allocations
                .get(&id)
                .is_some_and(|a| a.request == captured.allocations[&id].request)
        {
            let now = now_ms();
            if let Some((_, resources, reason, _)) = ranked_candidates(
                &store.state,
                &constrained_by(&store.state, now, &boot),
                now,
                &boot,
            )
            .into_iter()
            .find(|(candidate, _, _, order)| {
                candidate == &id
                    && (*order != Yield::IdleOnly
                        || selected
                            .last_observation
                            .as_ref()
                            .is_some_and(|o| o.reclaimable_idle))
            }) {
                selected.constrained_resources = resources;
                selected.reason = reason;
                store.state.pressure.insert(id, selected);
                store.state.tick();
                store.save()?;
            }
        }
    }
    let mut pending: Vec<_> = store
        .state
        .pressure
        .values()
        .filter(|t| t.needs_reconciliation(&store.state, &boot))
        .collect();
    pending.sort_by_key(|t| t.last_check_ms);
    let ids: Vec<_> = pending
        .into_iter()
        .take(4)
        .map(|t| t.allocation_id.clone())
        .collect();
    drop(store);
    for id in &ids {
        reconcile(root, id)?;
    }
    Ok(
        json!({"observed_transitions":ids,"observed_blockers":observed_blockers,"domain_owned_release":true}),
    )
}

fn reconcile(root: &Path, id: &str) -> Result<()> {
    let captured = Store::snapshot(root)?;
    let transition = captured
        .pressure
        .get(id)
        .ok_or("pressure transition disappeared")?;
    let allocation = &captured.allocations[id];
    let control = captured
        .policy
        .domain_controls
        .get(&allocation.request.system_id)
        .ok_or("pending domain control disappeared")?;
    let observed = call(control, &request(&captured, transition, "inspect")?, None).and_then(|o| {
        validate_observation(allocation, transition, &o)?;
        Ok(o)
    });
    let mut store = Store::open(root)?;
    if store.state.instance_id != captured.instance_id
        || store
            .state
            .pressure
            .get(id)
            .is_none_or(|t| t.request_id != transition.request_id)
    {
        return Err("pressure source/request changed during observation".into());
    }
    let observed = match observed {
        Ok(o) => o,
        Err(e) => {
            let pending = store.state.pressure.get_mut(id).unwrap();
            pending.last_error = Some(e);
            pending.last_check_ms = now_ms();
            store.state.tick();
            store.save()?;
            return Ok(());
        }
    };
    let now = now_ms();
    let boot = boot_id()?;
    let fit = resume_fit(&store.state, &store.state.allocations[id], now, &boot);
    let still_constrained = yield_order(
        &store.state,
        &store.state.allocations[id],
        &transition.constrained_resources,
        &constrained_by(&store.state, now, &boot),
        now,
        &boot,
    )
    .is_some_and(|order| order != Yield::IdleOnly || observed.reclaimable_idle);
    let allocation_released = store.state.allocations[id].phase == Phase::Released;
    let pending = store.state.pressure.get_mut(id).unwrap();
    pending.last_error = None;
    pending.last_observation = Some(observed.clone());
    pending.last_check_ms = now;
    if allocation_released
        && (observed.original_attempt_state.as_deref() == Some("completed")
            || observed.independent_successor.as_ref().is_some_and(|next| {
                next.attempt_id != allocation.request.attempt_id
                    && !next.owner_ref.trim().is_empty()
                    && !next.origin.trim().is_empty()
            }))
    {
        // Close only our obsolete automation. A declared final result or manual
        // successor is not a pressure checkpoint or pressure-owned resume.
        pending.stage = "completed".into();
        store.state.tick();
        store.save()?;
        return Ok(());
    }
    let mut action = None;
    let disposition = observed
        .pressure_request
        .as_ref()
        .map(|p| p.state.as_str())
        .unwrap_or("absent");
    match disposition {
        "resumed" | "rejected" => pending.stage = disposition.into(),
        "failed" => pending.stage = "blocked".into(),
        "accepted" | "saving" => {
            pending.stage = "release_requested".into();
            if observed
                .pressure_request
                .as_ref()
                .is_some_and(|p| p.retry_eligible)
                && pending.expected_control_revision.is_some()
            {
                // The owner has reconciled native absence and the original
                // control meaning. Retry only its same durable handoff request.
                action = Some("request_release");
            }
        }
        "absent" => {
            if observed.release_eligible && still_constrained && pending.resume_request_id.is_none()
            {
                if let Some(expected) = pending.expected_control_revision {
                    if expected == observed.control_revision {
                        action = Some("request_release");
                    } else {
                        pending.stage = "blocked".into();
                    }
                } else {
                    pending.expected_control_revision = Some(observed.control_revision);
                    action = Some("request_release");
                }
            } else {
                pending.stage = "blocked".into();
            }
        }
        "released" => {
            pending.stage = "released".into();
            let existing_resume = observed
                .pressure_request
                .as_ref()
                .and_then(|p| p.resume.as_ref());
            if let Some(resume) = existing_resume {
                pending.stage = match resume.state.as_str() {
                    "accepted" => "resume_requested",
                    _ => "blocked",
                }
                .into();
                if resume.state == "accepted"
                    && resume.retry_eligible
                    && resume.successor.is_none()
                    && fit
                    && pending.resume_control_revision.is_some()
                {
                    action = Some("request_resume");
                }
            } else if allocation_released && fit && observed.resume_eligible {
                if pending.recovery_boot_id.as_deref() != Some(&boot)
                    || pending.recovery_since_ms.is_none_or(|t| t > now)
                {
                    pending.recovery_boot_id = Some(boot.clone());
                    pending.recovery_since_ms = Some(now);
                }
                if now.saturating_sub(pending.recovery_since_ms.unwrap_or(now))
                    >= control.stable_recovery_ms
                {
                    if let Some(expected) = pending.resume_control_revision {
                        if expected == observed.control_revision {
                            action = Some("request_resume");
                        } else {
                            pending.stage = "blocked".into();
                        }
                    } else {
                        pending.resume_control_revision = Some(observed.control_revision);
                        pending.resume_request_id = Some(
                            fs::read_to_string("/proc/sys/kernel/random/uuid")
                                .map_err(|e| e.to_string())?
                                .trim()
                                .replace('-', ""),
                        );
                        action = Some("request_resume");
                    }
                }
            } else {
                pending.recovery_since_ms = None;
                pending.recovery_boot_id = None;
            }
        }
        _ => {}
    }
    if let Some(action) = action {
        pending.stage = if action == "request_release" {
            "release_requested"
        } else {
            "resume_requested"
        }
        .into();
    }
    let actual_request = action
        .map(|a| request(&store.state, &store.state.pressure[id], a))
        .transpose()?;
    let effect_transition = store.state.pressure[id].clone();
    store.state.tick();
    store.save()?;
    drop(store);
    if let Some(actual_request) = actual_request {
        // Persisted exact selectors precede the effect. The next inspection
        // reconciles its original request even after timeout or coordinator exit.
        let result = call(control, &actual_request, None).and_then(|o| {
            validate_observation(allocation, &effect_transition, &o)?;
            Ok(o)
        });
        let mut store = Store::open(root)?;
        if store.state.instance_id != captured.instance_id
            || store.state.pressure[id].request_id != transition.request_id
        {
            return Err(
                "pressure source changed after possible domain effect; reconcile original owner"
                    .into(),
            );
        }
        let pending = store.state.pressure.get_mut(id).unwrap();
        match result {
            Ok(observation) => {
                pending.last_observation = Some(observation);
                pending.last_error = None;
            }
            Err(error) => pending.last_error = Some(error),
        }
        store.state.tick();
        store.save()?;
    }
    Ok(())
}
