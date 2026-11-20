//! Host capacity coordination, not a domain job or workload-state registry.
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

pub type Quantities = BTreeMap<String, u64>;
pub type Result<T> = std::result::Result<T, String>;
pub mod demand;
pub mod native;
pub mod preferences;
pub mod pressure;
pub mod pressure_succession;
pub mod reservations;
pub mod scheduling;
pub mod storage;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub host_id: String,
    pub machine_id: String,
    /// Units and physical identities belong to the enrolled host/domain owner.
    pub resources: BTreeMap<String, ResourcePolicy>,
    #[serde(default)]
    pub cpu_aggregates: BTreeMap<String, CpuAggregate>,
    /// Only actual qualified domain controllers are enrolled for automatic effects.
    #[serde(default)]
    pub domain_controls: BTreeMap<String, pressure::DomainControl>,
    #[serde(default)]
    pub io_limits: BTreeMap<String, BTreeMap<String, storage::Limit>>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CpuAggregate {
    pub cgroup: String,
    pub priority: Priority,
    pub control_owner_ref: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ResourcePolicy {
    pub unit: String,
    pub capacity: u64,
    pub operational_margin: u64,
    pub priority_growth_margin: u64,
    pub observation_max_age_ms: u64,
    #[serde(default)]
    pub native: Option<native::Resource>,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Priority {
    Priority,
    Normal,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub allocation_id: String,
    pub host_id: String,
    pub system_id: String,
    pub owner_ref: String,
    pub operation_id: String,
    pub attempt_id: String,
    pub source_ref: String,
    pub configuration_ref: String,
    pub priority: Priority,
    /// A jointly useful allocation, including material save/finalization peaks.
    pub demand: Quantities,
    /// Native/domain evidence needed to establish fit is referenced, not copied.
    pub eligibility_ref: String,
    pub release_route: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Waiting,
    Admitted,
    Active,
    ReleasePending,
    Released,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Process {
    pub boot_id: String,
    pub pid: u32,
    pub start_ticks: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StepOutcome {
    Dispatching,
    Applied,
    Failed,
    Uncertain,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Step {
    pub process: Process,
    pub target_ref: Option<String>,
    pub outcome: StepOutcome,
    pub evidence_ref: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Allocation {
    pub request: Request,
    pub phase: Phase,
    pub revision: u64,
    pub queued_sequence: u64,
    pub admitted_sequence: Option<u64>,
    pub steps: BTreeMap<String, Step>,
    pub release_evidence: Option<ReleaseEvidence>,
    pub runtime: Option<RuntimeBinding>,
    /// Existing native work may be recorded without inventing a launch/admission.
    #[serde(default)]
    pub adoption: Option<Adoption>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Adoption {
    pub instance_id: String,
    pub policy_revision: u64,
    pub binding: RuntimeBinding,
    pub evidence_ref: String,
    pub recorded_at_ms: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeBinding {
    pub process: Process,
    /// Real host cgroup v2 tree, not the Docker client or guest-internal tree.
    pub cgroup: String,
    pub supervisor_ref: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Scope {
    pub host_id: String,
    #[serde(default)]
    pub system_ids: BTreeSet<String>,
    #[serde(default)]
    pub operation_ids: BTreeSet<String>,
    #[serde(default)]
    pub resource_ids: BTreeSet<String>,
}
impl Scope {
    pub fn matches(&self, r: &Request) -> bool {
        self.host_id == r.host_id
            && (self.system_ids.is_empty() || self.system_ids.contains(&r.system_id))
            && (self.operation_ids.is_empty() || self.operation_ids.contains(&r.operation_id))
            && (self.resource_ids.is_empty()
                || r.demand.keys().any(|k| self.resource_ids.contains(k)))
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Hold {
    pub hold_id: String,
    pub owner_ref: String,
    pub reason: String,
    pub scope: Scope,
    pub revision: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Observation {
    pub host_id: String,
    pub boot_id: String,
    pub observed_at_ms: u64,
    pub source_ref: String,
    /// Unmanaged demand only. Missing resources are unavailable, never zero.
    pub external_demand: Quantities,
    /// Exact active allocations whose native demand was excluded above.
    pub accounted_allocations: BTreeMap<String, u64>,
    #[serde(default)]
    pub managed_demand: BTreeMap<String, Quantities>,
    #[serde(default)]
    pub cpu_sample: Option<native::CpuSample>,
    #[serde(default)]
    pub io_sample: Option<storage::Sample>,
    #[serde(default)]
    pub unavailable: BTreeMap<String, String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ReleaseEvidence {
    pub owner_ref: String,
    pub attempt_id: String,
    pub boot_id: String,
    pub continuation_ref: Option<String>,
    pub execution_exited_ref: String,
    pub resources_released_ref: String,
    pub no_successor_ref: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct State {
    /// Separate same-lock policy; legacy v1 writers cannot erase it.
    #[serde(skip)]
    pub scheduling: Option<scheduling::Configuration>,
    #[serde(skip)]
    pub resource_preferences: Option<preferences::Selection>,
    pub schema: String,
    pub instance_id: String,
    pub policy: Policy,
    /// Changes only when policy changes, not on periodic observations.
    #[serde(default)]
    pub policy_revision: u64,
    #[serde(default)]
    pub policy_change_ref: Option<String>,
    pub revision: u64,
    pub sequence: u64,
    pub allocations: BTreeMap<String, Allocation>,
    pub holds: BTreeMap<String, Hold>,
    pub observation: Option<Observation>,
    /// System turn history cannot be multiplied by child requests.
    pub last_turn: BTreeMap<String, u64>,
    pub maintenance: BTreeMap<String, Maintenance>,
    pub retained_resource_observations: BTreeMap<String, ResourceEvidence>,
    pub resource_observations: BTreeMap<String, ResourceObservation>,
    pub native_counters: Option<native::CpuSample>,
    #[serde(default)]
    pub native_io_counters: Option<storage::Sample>,
    #[serde(default)]
    pub pressure: BTreeMap<String, pressure::Transition>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResourceObservation {
    pub boot_id: String,
    pub observed_at_ms: u64,
    pub source_ref: String,
    pub external_demand: Option<u64>,
    pub unavailable: Option<String>,
    pub accounted_revisions: BTreeMap<String, u64>,
    pub accounted_runtimes: BTreeMap<String, Option<RuntimeBinding>>,
    pub managed_demand: BTreeMap<String, u64>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResourceEvidence {
    pub boot_id: String,
    pub observed_at_ms: u64,
    pub source_ref: String,
    pub external_demand: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Maintenance {
    pub operation_id: String,
    pub owner_ref: String,
    pub change_ref: String,
    pub hold_id: String,
    pub scope: Scope,
    pub revision: u64,
    pub stage: String,
    /// Native/domain records, not copied logs or a competing updater database.
    pub evidence_refs: BTreeMap<String, String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Assessment {
    pub eligible: bool,
    pub can_admit: bool,
    pub reasons: Vec<String>,
    pub available: Quantities,
    pub commitments: Quantities,
    pub hold_ids: Vec<String>,
    pub resource_observation_times: BTreeMap<String, u64>,
}

/// A read-only explanation from the same pass that orders real admissions.
/// `would_admit` describes this captured state, never a reservation or launch.
#[derive(Clone, Debug, Serialize)]
pub struct QueueDecision {
    pub assessment: Assessment,
    pub would_admit: bool,
    pub blocked_by_allocations: Vec<String>,
    pub reasons: Vec<String>,
}

fn nonempty(s: &str, name: &str) -> Result<()> {
    if s.trim().is_empty() || s.len() > 4096 || s.contains('\0') {
        Err(format!("invalid {name}"))
    } else {
        Ok(())
    }
}
fn add(to: &mut Quantities, from: &Quantities) -> Result<()> {
    for (key, value) in from {
        let old = to.entry(key.clone()).or_default();
        *old = old.checked_add(*value).ok_or("quantity overflow")?;
    }
    Ok(())
}
impl State {
    pub fn committed_quantity(&self, resource: &str, requested: u64, observed: u64) -> u64 {
        if self
            .policy
            .resources
            .get(resource)
            .is_some_and(|p| matches!(p.native, Some(native::Resource::Cpu { .. })))
        {
            // Qualified native CPU aggregates may expand into spare cycles.
            // Their requested useful minimum remains the schedulable commitment;
            // observed elastic expansion is visible, not an irreducible lease.
            requested
        } else {
            requested.max(observed)
        }
    }
    pub fn new(policy: Policy) -> Result<Self> {
        nonempty(&policy.host_id, "host_id")?;
        if policy.machine_id != machine_id()? {
            return Err("policy physical machine identity mismatch".into());
        }
        if policy.resources.is_empty() {
            return Err("at least one observed resource is required".into());
        }
        native::validate_policy(&policy)?;
        pressure::validate_policy(&policy)?;
        storage::validate_policy(&policy)?;
        for (id, r) in &policy.resources {
            nonempty(id, "resource identity")?;
            nonempty(&r.unit, "resource unit")?;
            if r.capacity == 0
                || r.observation_max_age_ms == 0
                || r.operational_margin
                    .checked_add(r.priority_growth_margin)
                    .is_none_or(|v| v >= r.capacity)
            {
                return Err(format!("invalid resource margins/capacity: {id}"));
            }
        }
        Ok(Self {
            scheduling: None,
            resource_preferences: None,
            schema: "apeiros.capacity.v1".into(),
            instance_id: fs::read_to_string("/proc/sys/kernel/random/uuid")
                .map_err(|e| e.to_string())?
                .trim()
                .into(),
            policy,
            policy_revision: 0,
            policy_change_ref: None,
            revision: 0,
            sequence: 0,
            allocations: BTreeMap::new(),
            holds: BTreeMap::new(),
            observation: None,
            last_turn: BTreeMap::new(),
            maintenance: BTreeMap::new(),
            retained_resource_observations: BTreeMap::new(),
            resource_observations: BTreeMap::new(),
            native_counters: None,
            native_io_counters: None,
            pressure: BTreeMap::new(),
        })
    }
    fn tick(&mut self) -> u64 {
        self.revision += 1;
        self.sequence += 1;
        self.sequence
    }
    pub fn reconfigure_policy(
        &mut self,
        expected: u64,
        policy: Policy,
        change_ref: String,
    ) -> Result<()> {
        if expected != self.policy_revision {
            return Err("stale policy revision; do not replay against newer policy".into());
        }
        nonempty(&change_ref, "policy change reference")?;
        // Reuse all initial policy validation without publishing a second state.
        Self::new(policy.clone())?;
        if policy.host_id != self.policy.host_id || policy.machine_id != self.policy.machine_id {
            return Err("policy reconfiguration cannot change physical host".into());
        }
        storage::validate_reconfiguration(&self.policy, &policy)?;
        if let Some(selection) = &self.resource_preferences {
            selection.validate_resources(&policy)?;
        }
        let boot = boot_id()?;
        for transition in self
            .pressure
            .values()
            .filter(|t| t.needs_reconciliation(self, &boot))
        {
            let system = &self
                .allocations
                .get(&transition.allocation_id)
                .ok_or("pending pressure transition has no allocation owner")?
                .request
                .system_id;
            if self.policy.domain_controls.get(system) != policy.domain_controls.get(system) {
                return Err(format!(
                    "domain control still owns pending pressure transition: {system}"
                ));
            }
        }
        for allocation in self
            .allocations
            .values()
            .filter(|a| a.phase != Phase::Released)
        {
            for key in allocation.request.demand.keys() {
                let old = self.policy.resources.get(key);
                let new = policy.resources.get(key);
                let unchanged_identity = match (old, new) {
                    (Some(old), Some(new)) => {
                        old.unit == new.unit
                            && native::same_resource_identity(&old.native, &new.native)
                    }
                    (None, Some(_)) => allocation.phase == Phase::Waiting,
                    _ => false,
                };
                if !unchanged_identity {
                    return Err(format!(
                        "resource identity is still referenced by pending/live allocation: {key}"
                    ));
                }
            }
            let system = self.execution_system(&allocation.request);
            let old = self.policy.cpu_aggregates.get(system);
            let new = policy.cpu_aggregates.get(system);
            if old != new
                && !(old.is_none()
                    && allocation.phase == Phase::Waiting
                    && new.is_some_and(|g| g.priority == allocation.request.priority))
            {
                return Err(format!(
                    "aggregate identity/priority still has pending/live allocations: {system}"
                ));
            }
        }
        if let Some(scheduling) = &self.scheduling {
            let mut proposed = self.clone();
            proposed.policy = policy.clone();
            scheduling.validate(&proposed)?;
        }
        // Capacity changes affect native demand derivation. Invalidate only the
        // affected evidence; no commitment, hold, process or stopped intent ends.
        let changed: BTreeSet<_> = self
            .policy
            .resources
            .keys()
            .chain(policy.resources.keys())
            .filter(|id| self.policy.resources.get(*id) != policy.resources.get(*id))
            .cloned()
            .collect();
        for id in &changed {
            self.resource_observations.remove(id);
            self.retained_resource_observations.remove(id);
        }
        if changed.iter().any(|id| {
            [self.policy.resources.get(id), policy.resources.get(id)]
                .into_iter()
                .flatten()
                .any(|p| matches!(p.native, Some(native::Resource::Cpu { .. })))
        }) {
            self.native_counters = None;
        }
        if changed.iter().any(|id| {
            [self.policy.resources.get(id), policy.resources.get(id)]
                .into_iter()
                .flatten()
                .any(|p| matches!(p.native, Some(native::Resource::IoBandwidth { .. })))
        }) {
            self.native_io_counters = None;
        }
        self.policy = policy;
        self.policy_revision += 1;
        self.policy_change_ref = Some(change_ref);
        self.tick();
        Ok(())
    }
    fn validate_request(&self, r: &Request) -> Result<()> {
        for (s, n) in [
            (&r.allocation_id, "allocation_id"),
            (&r.system_id, "system_id"),
            (&r.owner_ref, "owner_ref"),
            (&r.operation_id, "operation_id"),
            (&r.attempt_id, "attempt_id"),
            (&r.source_ref, "source_ref"),
            (&r.configuration_ref, "configuration_ref"),
            (&r.eligibility_ref, "eligibility_ref"),
            (&r.release_route, "release_route"),
        ] {
            nonempty(s, n)?;
        }
        if r.host_id != self.policy.host_id {
            return Err("host identity mismatch".into());
        }
        let effective = self.effective_request(r);
        let execution = self.execution_system(r);
        if self
            .policy
            .cpu_aggregates
            .get(&effective.system_id)
            .is_some_and(|g| g.priority != effective.priority)
        {
            return Err("request priority differs from enrolled system aggregate".into());
        }
        if r.demand.keys().any(|key| {
            self.policy.resources.get(key).is_some_and(|p| {
                matches!(
                    p.native,
                    Some(native::Resource::Cpu { .. } | native::Resource::IoBandwidth { .. })
                )
            })
        }) && !self.policy.cpu_aggregates.contains_key(execution)
        {
            return Err(
                "native CPU/I/O demand requires an enrolled system aggregate and sole control owner".into(),
            );
        }
        if self
            .scheduling
            .as_ref()
            .and_then(|p| p.native_priorities.get(execution))
            .is_some_and(|p| *p != effective.priority)
        {
            return Err("request conflicts with effective native aggregate priority; select its real execution aggregate".into());
        }
        if r.demand.is_empty() || r.demand.values().any(|v| *v == 0) {
            return Err("demand must identify positive joint requirements".into());
        }
        Ok(())
    }
    pub fn assess(&self, r: &Request, now: u64, boot: &str) -> Result<Assessment> {
        self.assess_effective(r, self.effective_request(r), now, boot)
    }
    pub(crate) fn assess_effective(
        &self,
        original: &Request,
        effective: Request,
        now: u64,
        boot: &str,
    ) -> Result<Assessment> {
        self.validate_request(original)?;
        let r = &effective;
        let mut a = Assessment {
            eligible: true,
            can_admit: true,
            reasons: vec![],
            available: Quantities::new(),
            commitments: Quantities::new(),
            hold_ids: vec![],
            resource_observation_times: self
                .resource_observations
                .iter()
                .map(|(k, v)| (k.clone(), v.observed_at_ms))
                .collect(),
        };
        for allocation in self.allocations.values().filter(|a| {
            a.request.allocation_id != r.allocation_id
                && matches!(
                    a.phase,
                    Phase::Admitted | Phase::Active | Phase::ReleasePending
                )
        }) {
            let requested = self.effective_request(&allocation.request);
            let keys: BTreeSet<_> = requested
                .demand
                .keys()
                .chain(
                    self.resource_observations
                        .iter()
                        .filter(|(_, o)| {
                            o.managed_demand
                                .contains_key(&allocation.request.allocation_id)
                        })
                        .map(|(key, _)| key),
                )
                .collect();
            let committed = keys
                .into_iter()
                .map(|key| {
                    (
                        key.clone(),
                        self.allocation_commitment(allocation, key, now, boot),
                    )
                })
                .collect();
            add(&mut a.commitments, &committed)?;
        }
        for h in self
            .holds
            .values()
            .filter(|h| self.scope_matches(&h.scope, original))
        {
            a.hold_ids.push(h.hold_id.clone());
        }
        if !a.hold_ids.is_empty() {
            a.can_admit = false;
            a.reasons
                .push("scoped hold prevents admission and dispatch".into());
        }
        for (key, demand) in &r.demand {
            let demand = self.committed_quantity(
                key,
                *demand,
                self.resource_observations
                    .get(key)
                    .and_then(|o| o.managed_demand.get(&r.allocation_id))
                    .copied()
                    .unwrap_or(0),
            );
            let Some(p) = self.policy.resources.get(key) else {
                a.eligible = false;
                a.can_admit = false;
                a.reasons.push(format!("resource not enrolled: {key}"));
                continue;
            };
            let margin = self
                .admission_operational_margin(r, key, now, boot)
                .saturating_add(if r.priority == Priority::Normal {
                    p.priority_growth_margin
                } else {
                    0
                });
            if demand > p.capacity.saturating_sub(margin) {
                a.eligible = false;
                a.can_admit = false;
                a.reasons
                    .push(format!("joint minimum exceeds eligible capacity: {key}"));
            }
            let obs = self.resource_observations.get(key).filter(|o| {
                o.boot_id == boot
                    && now >= o.observed_at_ms
                    && now - o.observed_at_ms <= p.observation_max_age_ms
                    && o.unavailable.is_none()
                    && o.accounted_runtimes.iter().all(|(id, binding)| {
                        self.allocations
                            .get(id)
                            .is_some_and(|a| a.phase == Phase::Released || a.runtime == *binding)
                    })
            });
            let Some(external) = obs.and_then(|o| o.external_demand) else {
                a.can_admit = false;
                a.reasons
                    .push(format!("fresh host-bound observation unavailable: {key}"));
                continue;
            };
            // A live allocation not identified by this observation may already be
            // included in external demand: conservative double counting is explicit.
            let committed = a.commitments.get(key).copied().unwrap_or(0);
            let available = p
                .capacity
                .saturating_sub(margin)
                .saturating_sub(external)
                .saturating_sub(committed);
            a.available.insert(key.clone(), available);
            if demand > available {
                a.can_admit = false;
                a.reasons.push(format!(
                    "insufficient currently available {key}: need {demand}, available {available}"
                ));
            }
        }
        Ok(a)
    }
    pub fn observe(&mut self, o: Observation, now: u64, boot: &str) -> Result<()> {
        if o.host_id != self.policy.host_id || o.boot_id != boot || o.observed_at_ms > now {
            return Err("observation host/boot/time mismatch".into());
        }
        nonempty(&o.source_ref, "observation source")?;
        for (id, rev) in &o.accounted_allocations {
            let a = self
                .allocations
                .get(id)
                .ok_or("observation excluded unknown allocation")?;
            if a.revision != *rev || !matches!(a.phase, Phase::Active | Phase::ReleasePending) {
                return Err("observation excluded stale/inactive allocation".into());
            }
        }
        if o.managed_demand
            .keys()
            .any(|id| !o.accounted_allocations.contains_key(id))
        {
            return Err("managed demand lacks exact allocation revision".into());
        }
        if o.external_demand
            .keys()
            .chain(o.unavailable.keys())
            .any(|k| !self.policy.resources.contains_key(k))
        {
            return Err("observation references unenrolled resource".into());
        }
        for (key, value) in &o.external_demand {
            if !o.unavailable.contains_key(key) {
                self.retained_resource_observations.insert(
                    key.clone(),
                    ResourceEvidence {
                        boot_id: o.boot_id.clone(),
                        observed_at_ms: o.observed_at_ms,
                        source_ref: o.source_ref.clone(),
                        external_demand: *value,
                    },
                );
            }
        }
        let covered: BTreeSet<_> = o
            .external_demand
            .keys()
            .chain(o.unavailable.keys())
            .cloned()
            .collect();
        for key in covered {
            if !self.policy.resources.contains_key(&key) {
                return Err("observation references unenrolled resource".into());
            }
            let managed: BTreeMap<_, _> = o
                .managed_demand
                .iter()
                .filter_map(|(id, quantities)| {
                    quantities.get(&key).map(|value| (id.clone(), *value))
                })
                .collect();
            let revisions: BTreeMap<_, _> = o
                .accounted_allocations
                .iter()
                .filter(|(id, _)| managed.contains_key(*id))
                .map(|(id, rev)| (id.clone(), *rev))
                .collect();
            let runtimes = revisions
                .keys()
                .map(|id| (id.clone(), self.allocations[id].runtime.clone()))
                .collect();
            self.resource_observations.insert(
                key.clone(),
                ResourceObservation {
                    boot_id: o.boot_id.clone(),
                    observed_at_ms: o.observed_at_ms,
                    source_ref: o.source_ref.clone(),
                    external_demand: o.external_demand.get(&key).copied(),
                    unavailable: o.unavailable.get(&key).cloned(),
                    accounted_revisions: revisions,
                    accounted_runtimes: runtimes,
                    managed_demand: managed,
                },
            );
        }
        if o.cpu_sample.is_some() {
            self.native_counters = o.cpu_sample.clone();
        }
        if o.io_sample.is_some() {
            self.native_io_counters = o.io_sample.clone();
        }
        self.observation = Some(o);
        self.tick();
        Ok(())
    }
    pub fn admit(&mut self, r: Request, now: u64, boot: &str) -> Result<Assessment> {
        self.admit_demand(r, now, boot, false)
    }
    fn admit_demand(
        &mut self,
        r: Request,
        now: u64,
        boot: &str,
        opportunistic: bool,
    ) -> Result<Assessment> {
        self.validate_request(&r)?;
        if let Some(old) = self.allocations.get(&r.allocation_id) {
            if old.request != r {
                return Err("allocation identity reused with different request".into());
            }
            let mut result = self.assess(&r, now, boot)?;
            result.can_admit = matches!(old.phase, Phase::Admitted | Phase::Active);
            if !result.can_admit && result.reasons.is_empty() {
                result
                    .reasons
                    .push(format!("original allocation remains {:?}", old.phase));
            }
            return Ok(result);
        }
        let a = self.assess(&r, now, boot)?;
        if !a.eligible {
            return Ok(a);
        }
        let sequence = self.tick();
        self.allocations.insert(
            r.allocation_id.clone(),
            Allocation {
                request: r.clone(),
                phase: Phase::Waiting,
                revision: 1,
                queued_sequence: sequence,
                admitted_sequence: None,
                steps: BTreeMap::new(),
                release_evidence: None,
                runtime: None,
                adoption: None,
            },
        );
        self.schedule_decisions(now, boot, opportunistic.then_some(r.allocation_id.as_str()))?;
        let mut result = self.assess(&r, now, boot)?;
        result.can_admit = matches!(self.allocations[&r.allocation_id].phase, Phase::Admitted);
        if !result.can_admit && result.reasons.is_empty() {
            result
                .reasons
                .push("waiting for older eligible system demand at next release boundary".into());
        }
        Ok(result)
    }
    /// Opportunistic residency must not become a waiter that drains useful
    /// running work. Reuse the exact scheduler on a private copy under the
    /// existing admission lock, committing only an actual fitting admission.
    pub fn admit_if_fit(&mut self, r: Request, now: u64, boot: &str) -> Result<Assessment> {
        self.validate_request(&r)?;
        if self.allocations.contains_key(&r.allocation_id) {
            return self.admit(r, now, boot);
        }
        let mut proposed = self.clone();
        let trial = proposed.admit_demand(r.clone(), now, boot, true)?;
        if trial.can_admit {
            *self = proposed;
            return Ok(trial);
        }
        // Report current commitments, not the private scheduler simulation's
        // hypothetical admissions, while retaining its actual deferral reason.
        let mut result = self.assess(&r, now, boot)?;
        result.can_admit = false;
        result.reasons = trial.reasons;
        Ok(result)
    }
    /// Record an already-authorized native obligation. This is not admission:
    /// pressure/holds cannot make existing demand disappear, and no work starts.
    pub fn adopt_runtime(
        &mut self,
        expected_instance: &str,
        expected_policy_revision: u64,
        request: Request,
        binding: RuntimeBinding,
        evidence_ref: &str,
    ) -> Result<bool> {
        nonempty(expected_instance, "expected capacity instance")?;
        nonempty(evidence_ref, "existing native owner evidence")?;
        if let Some(old) = self.allocations.get(&request.allocation_id) {
            let original = old.adoption.as_ref().ok_or(
                "allocation already belongs to admission; reconcile its original operation",
            )?;
            if old.request != request
                || original.instance_id != expected_instance
                || original.policy_revision != expected_policy_revision
                || original.binding != binding
                || original.evidence_ref != evidence_ref
            {
                return Err("adoption identity reused with different original input".into());
            }
            // A lost reply can be reconciled even after policy, liveness or the
            // original allocation's disposition changed. Never reactivate it.
            return Ok(false);
        }
        if self.instance_id != expected_instance
            || self.policy_revision != expected_policy_revision
            || self.policy.machine_id != machine_id()?
        {
            return Err("stale or foreign adoption owner/policy; inspect original intent".into());
        }
        self.validate_request(&request)?;
        if request
            .demand
            .keys()
            .any(|key| !self.policy.resources.contains_key(key))
        {
            return Err("adoption references an unenrolled resource".into());
        }
        for required in ["CPU", "memory"] {
            let covered = request.demand.keys().any(|key| {
                matches!(
                    (&self.policy.resources[key].native, required),
                    (Some(native::Resource::Cpu { .. }), "CPU")
                        | (Some(native::Resource::Memory), "memory")
                )
            });
            if !covered {
                return Err(format!(
                    "native adoption requires explicit {required} commitment"
                ));
            }
        }
        let aggregate = self
            .policy
            .cpu_aggregates
            .get(self.execution_system(&request))
            .ok_or("adoption requires an enrolled native aggregate")?;
        if !binding
            .cgroup
            .starts_with(&format!("{}/", aggregate.cgroup))
        {
            return Err("adopt the owned native domain below, not the shared aggregate".into());
        }
        self.reject_runtime_overlap(&request.allocation_id, &binding)?;
        native::verify_cpu_policy(&self.native_policy(), self.execution_system(&request))?;
        storage::verify_controls(&self.policy, self.execution_system(&request))?;
        native::validate_adoption_binding(&binding)?;
        let adoption = Adoption {
            instance_id: expected_instance.into(),
            policy_revision: expected_policy_revision,
            binding: binding.clone(),
            evidence_ref: evidence_ref.into(),
            recorded_at_ms: now_ms(),
        };
        self.allocations.insert(
            request.allocation_id.clone(),
            Allocation {
                request,
                phase: Phase::Active,
                revision: 1,
                // This runtime did not queue or receive a fabricated grant.
                queued_sequence: 0,
                admitted_sequence: None,
                steps: BTreeMap::new(),
                release_evidence: None,
                runtime: Some(binding),
                adoption: Some(adoption),
            },
        );
        self.tick();
        Ok(true)
    }
    /// Admission is event driven; no workload work is launched here.
    pub fn schedule(&mut self, now: u64, boot: &str) -> Result<Vec<String>> {
        self.schedule_decisions(now, boot, None)
            .map(|(admitted, _)| admitted)
    }

    /// Simulate this exact scheduler pass on a private copy. No persisted state,
    /// sequence, turn, reservation or domain effect changes during inspection.
    pub fn waiting_decisions(
        &self,
        now: u64,
        boot: &str,
    ) -> Result<BTreeMap<String, QueueDecision>> {
        if !self
            .allocations
            .values()
            .any(|a| self.has_waiting_demand(a))
        {
            return Ok(BTreeMap::new());
        }
        self.clone()
            .schedule_decisions(now, boot, None)
            .map(|(_, decisions)| decisions)
    }

    /// The oldest eligible preferred waiter on each selected GPU. Protection is
    /// limited to useful queued work with fresh resource evidence and enough
    /// non-GPU capacity. Preserve earlier contenders' ordinary share of those
    /// other resources even when the requests also compete for the GPU.
    pub fn preferred_gpu_waiters(&self, now: u64, boot: &str) -> BTreeMap<String, String> {
        self.preferred_gpu_waiters_except(now, boot, None)
    }
    fn preferred_gpu_waiters_except(
        &self,
        now: u64,
        boot: &str,
        opportunistic: Option<&str>,
    ) -> BTreeMap<String, String> {
        let mut selected = BTreeMap::new();
        let Some(policy) = &self.resource_preferences else {
            return selected;
        };
        if policy.resources.is_empty() {
            return selected;
        }
        let mut waiting: Vec<_> = self
            .allocations
            .values()
            .filter(|a| self.has_waiting_demand(a))
            .collect();
        waiting.sort_by_key(|a| {
            let r = self.effective_request(&a.request);
            (
                r.priority != Priority::Priority,
                self.waiting_sequence(a)
                    .max(self.last_turn.get(&r.system_id).copied().unwrap_or(0)),
                self.waiting_sequence(a),
            )
        });
        for (position, a) in waiting.iter().enumerate() {
            let r = self.waiting_request(a);
            if Some(r.allocation_id.as_str()) == opportunistic {
                continue;
            }
            let is_preferred_gpu =
                |k: &str| self.preferred_system_for_resource(k) == Some(r.system_id.as_str());
            for key in r.demand.keys().filter(|k| is_preferred_gpu(k)) {
                if selected.contains_key(key) {
                    continue;
                }
                let Ok(assessment) = self.waiting_assessment(a, now, boot) else {
                    continue;
                };
                if !assessment.eligible
                    || !assessment.hold_ids.is_empty()
                    || r.demand.iter().any(|(other, n)| {
                        assessment
                            .available
                            .get(other)
                            .is_none_or(|v| !is_preferred_gpu(other) && v < n)
                    })
                {
                    continue;
                }
                let conflicts_elsewhere = waiting[..position].iter().any(|older| {
                    let o = self.waiting_request(older);
                    if (o.system_id == r.system_id && o.priority == r.priority)
                        || !r
                            .demand
                            .keys()
                            .any(|k| !is_preferred_gpu(k) && o.demand.contains_key(k))
                    {
                        return false;
                    }
                    self.waiting_assessment(older, now, boot)
                        .is_ok_and(|prior| prior.eligible && prior.hold_ids.is_empty())
                        && r.demand.keys().any(|k| {
                            !is_preferred_gpu(k)
                                && o.demand.contains_key(k)
                                && self.consumes_protected_capacity(
                                    &r,
                                    older,
                                    k,
                                    &assessment,
                                    now,
                                    boot,
                                )
                        })
                });
                if !conflicts_elsewhere {
                    selected.insert(key.clone(), r.allocation_id.clone());
                }
            }
        }
        selected
    }
    fn consumes_protected_capacity(
        &self,
        candidate: &Request,
        earlier: &Allocation,
        key: &str,
        assessment: &Assessment,
        now: u64,
        boot: &str,
    ) -> bool {
        let previous = self.waiting_request(earlier);
        let observed = |id: &str| {
            self.resource_observations
                .get(key)
                .and_then(|o| o.managed_demand.get(id))
                .copied()
                .unwrap_or(0)
        };
        let total =
            self.committed_quantity(key, previous.demand[key], observed(&previous.allocation_id));
        let charged = if earlier.phase == Phase::Active {
            self.allocation_commitment(earlier, key, now, boot)
        } else {
            0
        };
        let protected =
            total
                .saturating_sub(charged)
                .saturating_sub(self.unused_system_reservation(
                    &previous.system_id,
                    key,
                    None,
                    now,
                    boot,
                ));
        let candidate_floor = self.unused_system_reservation(
            &candidate.system_id,
            key,
            Some(&candidate.allocation_id),
            now,
            boot,
        );
        let needed = self.committed_quantity(
            key,
            candidate.demand[key],
            observed(&candidate.allocation_id),
        );
        assessment
            .available
            .get(key)
            .map(|v| v.saturating_sub(candidate_floor))
            .and_then(|v| v.checked_sub(needed.saturating_sub(candidate_floor)))
            .is_none_or(|surplus| surplus < protected)
    }

    fn schedule_decisions(
        &mut self,
        now: u64,
        boot: &str,
        opportunistic: Option<&str>,
    ) -> Result<(Vec<String>, BTreeMap<String, QueueDecision>)> {
        let mut admitted = vec![];
        let mut decisions = BTreeMap::new();
        loop {
            let mut groups: BTreeMap<String, std::collections::VecDeque<String>> = BTreeMap::new();
            let mut waiting: Vec<_> = self
                .allocations
                .values()
                .filter(|a| self.has_waiting_demand(a))
                .collect();
            waiting.sort_by_key(|a| {
                (
                    if self.effective_request(&a.request).priority == Priority::Priority {
                        0
                    } else {
                        1
                    },
                    self.waiting_sequence(a),
                )
            });
            for a in waiting {
                groups
                    .entry(self.effective_request(&a.request).system_id)
                    .or_default()
                    .push_back(a.request.allocation_id.clone());
            }
            let preferred = self.preferred_gpu_waiters_except(now, boot, opportunistic);
            let mut older_blocked_resources = BTreeMap::<String, String>::new();
            let mut advanced = false;
            while !groups.is_empty() {
                let system = groups
                    .iter()
                    .min_by_key(|(system, queue)| {
                        let a = &self.allocations[queue.front().unwrap()];
                        (
                            if self.effective_request(&a.request).priority == Priority::Priority {
                                0
                            } else {
                                1
                            },
                            self.waiting_sequence(a)
                                .max(self.last_turn.get(*system).copied().unwrap_or(0)),
                            self.waiting_sequence(a),
                        )
                    })
                    .unwrap()
                    .0
                    .clone();
                let queue = groups.get_mut(&system).unwrap();
                let id = queue.pop_front().unwrap();
                if queue.is_empty() {
                    groups.remove(&system);
                }
                let request = self.waiting_request(&self.allocations[&id]);
                let assessment = self.waiting_assessment(&self.allocations[&id], now, boot)?;
                let mut decision = QueueDecision {
                    reasons: assessment.reasons.clone(),
                    assessment: assessment.clone(),
                    would_admit: false,
                    blocked_by_allocations: vec![],
                };
                if !assessment.eligible || !assessment.hold_ids.is_empty() {
                    decisions.insert(id, decision);
                    continue;
                }
                let mut blockers = BTreeSet::new();
                for resource in request.demand.keys() {
                    let first_on_gpu = preferred.get(resource);
                    if first_on_gpu == Some(&id) {
                        // The preference replaces only this GPU's turn.
                        continue;
                    }
                    for other_id in [older_blocked_resources.get(resource), first_on_gpu]
                        .into_iter()
                        .flatten()
                    {
                        let other = &self.allocations[other_id];
                        let owner = self.waiting_request(other);
                        if owner.system_id == request.system_id
                            && owner.priority == request.priority
                        {
                            // Siblings may normally share a turn. Fit-only warming cannot
                            // spend bytes needed by a real waiter of the same system.
                            if opportunistic == Some(id.as_str()) && first_on_gpu == Some(other_id)
                            {
                                let current = if other.phase == Phase::Active {
                                    self.allocation_commitment(other, resource, now, boot)
                                } else {
                                    0
                                };
                                let needed = request.demand[resource]
                                    .saturating_add(owner.demand[resource].saturating_sub(current));
                                if assessment
                                    .available
                                    .get(resource)
                                    .is_none_or(|v| *v < needed)
                                {
                                    blockers.insert(other_id.clone());
                                }
                            }
                            continue;
                        }
                        if self.consumes_protected_capacity(
                            &request,
                            other,
                            resource,
                            &assessment,
                            now,
                            boot,
                        ) {
                            blockers.insert(other_id.clone());
                        }
                    }
                }
                decision.blocked_by_allocations = blockers.into_iter().collect();
                if !decision.blocked_by_allocations.is_empty() {
                    decision.reasons.push("Admission would consume capacity protected for a competing waiter under its resource preference or aggregate turn.".into());
                    decisions.insert(id, decision);
                    continue;
                }
                if !assessment.can_admit {
                    for resource in request.demand.keys() {
                        older_blocked_resources
                            .entry(resource.clone())
                            .or_insert_with(|| id.clone());
                    }
                    decisions.insert(id, decision);
                    continue;
                }
                decision.would_admit = true;
                decision.reasons.push("Fits at this position in the captured scheduler pass; inspection reserves and launches nothing.".into());
                decisions.insert(id.clone(), decision);
                let sequence = self.tick();
                if self.allocations[&id].phase == Phase::Active {
                    self.grant_demand_change(&id, Some(sequence))?;
                } else {
                    let allocation = self.allocations.get_mut(&id).unwrap();
                    allocation.phase = Phase::Admitted;
                    allocation.admitted_sequence = Some(sequence);
                    allocation.revision += 1;
                }
                self.last_turn.insert(request.system_id, sequence);
                admitted.push(id);
                advanced = true;
                // Reconsider even previously blocked aggregates after a useful
                // turn. Keeping old barriers here would let this aggregate's
                // children retain precedence after its last_turn has advanced.
                break;
            }
            if !advanced {
                return Ok((admitted, decisions));
            }
        }
    }
    pub fn hold(&mut self, mut hold: Hold) -> Result<Vec<String>> {
        if hold.scope.host_id != self.policy.host_id {
            return Err("hold host mismatch".into());
        }
        for (s, n) in [
            (&hold.hold_id, "hold_id"),
            (&hold.owner_ref, "owner_ref"),
            (&hold.reason, "reason"),
        ] {
            nonempty(s, n)?;
        }
        if let Some(old) = self.holds.get(&hold.hold_id) {
            hold.revision = old.revision;
            if *old != hold {
                return Err(
                    "hold identity already exists with different scope/owner/reason".into(),
                );
            }
        } else {
            hold.revision = self.tick();
            self.holds.insert(hold.hold_id.clone(), hold.clone());
        }
        Ok(self
            .allocations
            .values()
            .filter(|a| {
                self.scope_matches(&hold.scope, &a.request)
                    && matches!(a.phase, Phase::Active | Phase::ReleasePending)
            })
            .map(|a| a.request.allocation_id.clone())
            .collect())
    }
    pub fn release_hold(&mut self, id: &str, owner: &str, revision: u64) -> Result<()> {
        if self
            .maintenance
            .values()
            .any(|m| m.hold_id == id && m.stage != "verified")
        {
            return Err("maintenance hold remains until its operation is verified; reconcile maintenance first".into());
        }
        let h = self
            .holds
            .get(id)
            .ok_or("hold not found; inspect current state")?;
        if h.owner_ref != owner || h.revision != revision {
            return Err("stale or foreign hold release".into());
        }
        self.holds.remove(id);
        self.tick();
        Ok(())
    }
    pub fn begin_step(
        &mut self,
        id: &str,
        revision: u64,
        step_id: &str,
        process: Process,
    ) -> Result<()> {
        self.begin_step_inner(id, revision, step_id, process, None)
    }
    pub fn begin_control_step(
        &mut self,
        id: &str,
        revision: u64,
        step_id: &str,
        process: Process,
        target_ref: String,
    ) -> Result<()> {
        nonempty(&target_ref, "prepared native target reference")?;
        self.begin_step_inner(id, revision, step_id, process, Some(target_ref))
    }
    fn begin_step_inner(
        &mut self,
        id: &str,
        revision: u64,
        step_id: &str,
        process: Process,
        target_ref: Option<String>,
    ) -> Result<()> {
        nonempty(step_id, "step_id")?;
        let a = self.allocations.get(id).ok_or("allocation not found")?;
        if a.revision != revision {
            return Err("stale allocation revision; do not replay old intent".into());
        }
        if !matches!(a.phase, Phase::Admitted | Phase::Active) {
            return Err("allocation not dispatchable".into());
        }
        if a.steps.contains_key(step_id) {
            return Err("dispatch step already exists; reconcile original effect".into());
        }
        if self
            .holds
            .values()
            .any(|h| self.scope_matches(&h.scope, &a.request))
        {
            return Err("dispatch held".into());
        }
        let assessment = self.assess(&a.request, now_ms(), &process.boot_id)?;
        if !assessment.can_admit {
            return Err(format!(
                "dispatch prerequisites changed: {}",
                assessment.reasons.join("; ")
            ));
        }
        if a.request.demand.keys().any(|key| {
            self.policy.resources.get(key).is_some_and(|p| {
                matches!(
                    p.native,
                    Some(native::Resource::Cpu { .. } | native::Resource::IoBandwidth { .. })
                )
            })
        }) {
            if target_ref.is_some() {
                native::verify_cpu_policy(
                    &self.native_policy(),
                    self.execution_system(&a.request),
                )?;
            } else {
                native::verify_cpu_dispatch(
                    &self.native_policy(),
                    self.execution_system(&a.request),
                    &process,
                )?;
            }
        }
        storage::verify_controls(&self.policy, self.execution_system(&a.request))?;
        let a = self.allocations.get_mut(id).unwrap();
        a.steps.insert(
            step_id.into(),
            Step {
                process,
                target_ref,
                outcome: StepOutcome::Dispatching,
                evidence_ref: None,
            },
        );
        a.phase = Phase::Active;
        a.revision += 1;
        self.tick();
        Ok(())
    }
    pub fn finish_step(
        &mut self,
        id: &str,
        step_id: &str,
        outcome: StepOutcome,
        evidence: &str,
    ) -> Result<()> {
        nonempty(evidence, "step evidence")?;
        let a = self.allocations.get_mut(id).ok_or("allocation not found")?;
        let step = a.steps.get_mut(step_id).ok_or("step not found")?;
        step.outcome = outcome;
        step.evidence_ref = Some(evidence.into());
        a.revision += 1;
        self.tick();
        Ok(())
    }
    pub fn release(&mut self, id: &str, revision: u64, evidence: ReleaseEvidence) -> Result<()> {
        let a = self.allocations.get(id).ok_or("allocation not found")?;
        if a.revision != revision
            || a.request.owner_ref != evidence.owner_ref
            || a.request.attempt_id != evidence.attempt_id
        {
            return Err("stale or foreign release evidence".into());
        }
        if a.phase == Phase::Released {
            return Err("already released; inspect original outcome".into());
        }
        if let Some(adoption) = &a.adoption {
            if evidence.boot_id != adoption.binding.process.boot_id {
                return Err("adopted release evidence identifies another execution boot".into());
            }
            native::verify_runtime_exited(&adoption.binding)?;
        }
        for (s, n) in [
            (&evidence.boot_id, "boot"),
            (&evidence.execution_exited_ref, "execution exit evidence"),
            (
                &evidence.resources_released_ref,
                "resource release evidence",
            ),
            (&evidence.no_successor_ref, "successor exclusion evidence"),
        ] {
            nonempty(s, n)?;
        }
        let a = self.allocations.get_mut(id).unwrap();
        a.phase = Phase::Released;
        a.release_evidence = Some(evidence);
        a.revision += 1;
        self.tick();
        Ok(())
    }
    pub fn bind_runtime(
        &mut self,
        id: &str,
        revision: u64,
        owner: &str,
        binding: RuntimeBinding,
    ) -> Result<()> {
        let a = self.allocations.get(id).ok_or("allocation not found")?;
        if a.revision != revision
            || a.request.owner_ref != owner
            || !matches!(a.phase, Phase::Active | Phase::ReleasePending)
        {
            return Err("stale/foreign/inactive runtime binding".into());
        }
        native::validate_binding(&binding)?;
        if a.adoption
            .as_ref()
            .is_some_and(|original| original.binding != binding)
        {
            return Err("adopted runtime is immutable; reconcile its original incarnation".into());
        }
        if a.steps.values().any(|s| s.target_ref.is_some())
            && !a
                .steps
                .values()
                .any(|s| s.target_ref.as_deref() == Some(binding.supervisor_ref.as_str()))
        {
            return Err(
                "runtime target is not one of this allocation's prepared dispatch targets".into(),
            );
        }
        if let Some(group) = self
            .policy
            .cpu_aggregates
            .get(self.execution_system(&a.request))
        {
            if binding.cgroup != group.cgroup
                && !binding.cgroup.starts_with(&format!("{}/", group.cgroup))
            {
                return Err("runtime is outside its enrolled system aggregate".into());
            }
        }
        self.reject_runtime_overlap(id, &binding)?;
        let a = self.allocations.get_mut(id).unwrap();
        a.runtime = Some(binding);
        a.revision += 1;
        self.tick();
        Ok(())
    }
    fn reject_runtime_overlap(&self, id: &str, binding: &RuntimeBinding) -> Result<()> {
        for other in self
            .allocations
            .values()
            .filter(|a| a.request.allocation_id != id && !matches!(a.phase, Phase::Released))
        {
            if let Some(b) = &other.runtime {
                if native::nested(&b.cgroup, &binding.cgroup) {
                    return Err(
                        "overlapping host cgroup: descendant or VM would be counted twice".into(),
                    );
                }
            }
        }
        Ok(())
    }
    pub fn prepare_maintenance(&mut self, mut m: Maintenance) -> Result<()> {
        for (s, n) in [
            (&m.operation_id, "maintenance operation"),
            (&m.owner_ref, "maintenance owner"),
            (&m.change_ref, "intended change"),
        ] {
            nonempty(s, n)?;
        }
        if self.maintenance.contains_key(&m.operation_id) || self.holds.contains_key(&m.hold_id) {
            return Err("maintenance identity already exists; reconcile original operation".into());
        }
        self.hold(Hold {
            hold_id: m.hold_id.clone(),
            owner_ref: m.owner_ref.clone(),
            reason: format!("maintenance {}: {}", m.operation_id, m.change_ref),
            scope: m.scope.clone(),
            revision: 0,
        })?;
        m.stage = "held".into();
        m.revision = 1;
        m.evidence_refs.clear();
        self.maintenance.insert(m.operation_id.clone(), m);
        self.tick();
        Ok(())
    }
    pub fn record_maintenance(
        &mut self,
        id: &str,
        revision: u64,
        owner: &str,
        stage: &str,
        evidence: BTreeMap<String, String>,
    ) -> Result<()> {
        let m = self
            .maintenance
            .get(id)
            .ok_or("maintenance operation not found")?;
        if m.revision != revision || m.owner_ref != owner {
            return Err("stale or foreign maintenance operation".into());
        }
        let allowed = match stage {
            "drained" => matches!(m.stage.as_str(), "held" | "uncertain"),
            "installed" => matches!(m.stage.as_str(), "drained" | "uncertain"),
            "activated" => matches!(m.stage.as_str(), "installed" | "uncertain"),
            "verified" => matches!(m.stage.as_str(), "activated" | "uncertain"),
            "uncertain" => m.stage != "released",
            _ => false,
        };
        if !allowed {
            return Err(
                "invalid maintenance transition; reconcile actual installer/activation outcome"
                    .into(),
            );
        }
        if evidence.is_empty()
            || evidence
                .iter()
                .any(|(k, v)| k.trim().is_empty() || v.trim().is_empty())
        {
            return Err("actual native/domain outcome references required".into());
        }
        if stage == "drained"
            && self.allocations.values().any(|a| {
                self.scope_matches(&m.scope, &a.request)
                    && matches!(a.phase, Phase::Active | Phase::ReleasePending)
            })
        {
            return Err("affected active/uncertain allocations still charged; domain save/exit/release required".into());
        }
        let m = self.maintenance.get_mut(id).unwrap();
        m.stage = stage.into();
        m.evidence_refs.extend(evidence);
        m.revision += 1;
        self.tick();
        Ok(())
    }
    pub fn finish_maintenance(&mut self, id: &str, revision: u64, owner: &str) -> Result<()> {
        let m = self
            .maintenance
            .get(id)
            .ok_or("maintenance operation not found")?;
        if m.revision != revision || m.owner_ref != owner || m.stage != "verified" {
            return Err("maintenance not verified or stale/foreign completion".into());
        }
        let h = self
            .holds
            .get(&m.hold_id)
            .ok_or("maintenance hold missing; reconcile coverage")?
            .clone();
        self.release_hold(&h.hold_id, owner, h.revision)?;
        let m = self.maintenance.get_mut(id).unwrap();
        m.stage = "released".into();
        m.revision += 1;
        self.tick();
        Ok(())
    }
}

pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64
}
pub fn boot_id() -> Result<String> {
    fs::read_to_string("/proc/sys/kernel/random/boot_id")
        .map(|s| s.trim().into())
        .map_err(|e| e.to_string())
}
pub fn machine_id() -> Result<String> {
    fs::read_to_string("/etc/machine-id")
        .map(|s| s.trim().into())
        .map_err(|e| e.to_string())
}
pub fn process(pid: u32) -> Result<Process> {
    let text = fs::read_to_string(format!("/proc/{pid}/stat")).map_err(|e| e.to_string())?;
    let fields: Vec<_> = text
        .rsplit_once(") ")
        .ok_or("malformed proc stat")?
        .1
        .split_whitespace()
        .collect();
    Ok(Process {
        boot_id: boot_id()?,
        pid,
        start_ticks: fields
            .get(19)
            .ok_or("missing process start")?
            .parse()
            .map_err(|_| "invalid process start")?,
    })
}

/// One OS decision lock, shared by the resident service and native dispatch.
/// It is close-on-exec so exec of the final owner program orders before a hold.
pub struct Store {
    root: PathBuf,
    _lock: File,
    pub state: State,
}
impl Store {
    pub fn open(root: &Path) -> Result<Self> {
        let lock = Self::lock(root, libc::LOCK_EX)?;
        let state = Self::read_locked(root)?;
        Ok(Self {
            root: root.into(),
            _lock: lock,
            state,
        })
    }

    /// Capture a complete passive view under the original shared decision lock.
    /// No lock survives the return: expensive projections cannot block native
    /// decisions, and this detached state cannot be saved or used for dispatch.
    pub fn snapshot(root: &Path) -> Result<State> {
        let _lock = Self::lock(root, libc::LOCK_SH)?;
        Self::read_locked(root)
    }

    fn lock(root: &Path, operation: libc::c_int) -> Result<File> {
        if !root.is_absolute() {
            return Err("state root must be absolute".into());
        }
        let metadata = fs::symlink_metadata(root).map_err(|e| e.to_string())?;
        if !metadata.is_dir() || metadata.permissions().mode() & 0o077 != 0 {
            return Err("state directory must be private mode 0700".into());
        }
        let mut options = OpenOptions::new();
        options
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC);
        if operation == libc::LOCK_EX {
            options.write(true).create(true).mode(0o600);
        }
        let lock = options
            .open(root.join("capacity.lock"))
            .map_err(|e| e.to_string())?;
        if unsafe { libc::flock(lock.as_raw_fd(), operation) } != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        Ok(lock)
    }

    // The caller keeps either the shared observation lock or the exclusive
    // mutation lock through both state and scheduling capture/validation.
    fn read_locked(root: &Path) -> Result<State> {
        let mut file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(root.join("state.json"))
            .map_err(|e| e.to_string())?;
        let mut text = String::new();
        // This is the complete local owner state under capacity.lock, not a
        // bounded external request. Retained allocations can grow beyond a
        // request-sized prefix; parse the whole file or refuse its actual error.
        file.read_to_string(&mut text).map_err(|e| e.to_string())?;
        let mut state: State =
            serde_json::from_str(&text).map_err(|e| format!("state unavailable: {e}"))?;
        if state.schema != "apeiros.capacity.v1" {
            return Err("unsupported capacity state schema".into());
        }
        if state.policy.machine_id != machine_id()? {
            return Err("stored capacity owner belongs to another physical machine".into());
        }
        state.scheduling = scheduling::read(root, &state)?;
        state.resource_preferences = preferences::read(root, &state)?;
        state.recover_demand_sequences();
        Ok(state)
    }
    pub fn initialize(root: &Path, policy: Policy) -> Result<()> {
        let state = State::new(policy)?;
        if !root.is_absolute() {
            return Err("state root must be absolute".into());
        }
        if root.exists() {
            let metadata = fs::symlink_metadata(root).map_err(|e| e.to_string())?;
            if !metadata.is_dir() || metadata.permissions().mode() & 0o077 != 0 {
                return Err("existing state root must be a private directory".into());
            }
        } else {
            fs::create_dir_all(root).map_err(|e| e.to_string())?;
            fs::set_permissions(root, fs::Permissions::from_mode(0o700))
                .map_err(|e| e.to_string())?;
        }
        let lock = OpenOptions::new()
            .write(true)
            .read(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(root.join("capacity.lock"))
            .map_err(|e| e.to_string())?;
        if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX) } != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        if fs::symlink_metadata(root.join("state.json")).is_ok() {
            return Err("initialization requires absent state; inspect the existing owner".into());
        }
        Self {
            root: root.into(),
            _lock: lock,
            state,
        }
        .save()
    }
    pub fn save(&self) -> Result<()> {
        if let Some(configuration) = &self.state.scheduling {
            let previous = scheduling::read(&self.root, &self.state)?;
            if previous.as_ref() != Some(configuration) {
                scheduling::publish(&self.root, previous.as_ref(), configuration)?;
            }
        }
        let path = self
            .root
            .join(format!("state.{}.{}.tmp", std::process::id(), now_ms()));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
            .map_err(|e| e.to_string())?;
        file.write_all(&serde_json::to_vec_pretty(&self.state).unwrap())
            .map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        fs::rename(&path, self.root.join("state.json")).map_err(|e| e.to_string())?;
        File::open(&self.root)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())
    }
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use std::sync::mpsc;
    use std::time::Duration;

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "capacity-snapshot-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            let policy = serde_json::from_value(serde_json::json!({"host_id":"snapshot-host",
                "machine_id":machine_id().unwrap(),"resources":{"ram":{"unit":"bytes","capacity":100,
                "operational_margin":0,"priority_growth_margin":0,"observation_max_age_ms":60000}}})).unwrap();
            Store::initialize(&root, policy).unwrap();
            Self(root)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn passive_snapshots_share_lock_and_release_it_before_projection() {
        let f = Fixture::new();
        let shared = Store::lock(&f.0, libc::LOCK_SH).unwrap();
        let (tx, rx) = mpsc::channel();
        let threads: Vec<_> = (0..2)
            .map(|_| {
                let root = f.0.clone();
                let tx = tx.clone();
                std::thread::spawn(move || tx.send(Store::snapshot(&root)).unwrap())
            })
            .collect();
        drop(tx);
        let states: Vec<_> = (0..2)
            .map(|_| rx.recv_timeout(Duration::from_secs(5)).unwrap().unwrap())
            .collect();
        let exclusive = OpenOptions::new()
            .read(true)
            .write(true)
            .open(f.0.join("capacity.lock"))
            .unwrap();
        assert_eq!(
            unsafe { libc::flock(exclusive.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) },
            -1
        );
        assert_eq!(
            std::io::Error::last_os_error().kind(),
            std::io::ErrorKind::WouldBlock
        );
        drop(shared);
        // The detached states stay alive, but their snapshot locks do not.
        assert_eq!(
            unsafe { libc::flock(exclusive.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) },
            0
        );
        assert_eq!(states[0].instance_id, states[1].instance_id);
        for thread in threads {
            thread.join().unwrap();
        }
    }

    #[test]
    fn passive_snapshot_waits_for_writer_and_captures_its_complete_state() {
        let f = Fixture::new();
        let mut writer = Store::open(&f.0).unwrap();
        let (tx, rx) = mpsc::channel();
        let root = f.0.clone();
        let thread = std::thread::spawn(move || tx.send(Store::snapshot(&root)).unwrap());
        assert!(matches!(
            rx.recv_timeout(Duration::from_millis(100)),
            Err(mpsc::RecvTimeoutError::Timeout)
        ));
        writer.state.revision += 1;
        let revision = writer.state.revision;
        writer.save().unwrap();
        drop(writer);
        let captured = rx.recv_timeout(Duration::from_secs(5)).unwrap().unwrap();
        assert_eq!(captured.revision, revision);
        thread.join().unwrap();
    }

    #[test]
    fn passive_snapshot_preserves_owner_and_scheduling_refusals_without_creating_lock() {
        let f = Fixture::new();
        let path = f.0.join("state.json");
        let original = fs::read(&path).unwrap();
        let mut state: serde_json::Value = serde_json::from_slice(&original).unwrap();
        state["policy"]["machine_id"] = serde_json::json!("another-machine");
        fs::write(&path, serde_json::to_vec(&state).unwrap()).unwrap();
        assert!(
            Store::snapshot(&f.0)
                .unwrap_err()
                .contains("another physical machine")
        );
        fs::write(&path, original).unwrap();
        fs::write(f.0.join("scheduling.json"), b"{}").unwrap();
        assert!(Store::snapshot(&f.0).is_err());
        fs::remove_file(f.0.join("scheduling.json")).unwrap();
        fs::set_permissions(&f.0, fs::Permissions::from_mode(0o755)).unwrap();
        assert!(Store::snapshot(&f.0).unwrap_err().contains("private mode"));
        fs::set_permissions(&f.0, fs::Permissions::from_mode(0o700)).unwrap();
        fs::remove_file(f.0.join("capacity.lock")).unwrap();
        assert!(Store::snapshot(&f.0).is_err());
        assert!(!f.0.join("capacity.lock").exists());
    }
}
