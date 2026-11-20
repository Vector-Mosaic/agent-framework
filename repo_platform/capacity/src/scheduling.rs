//! Explicit scheduling corrections without rewriting immutable domain requests.
//! The same Store lock owns this small policy file. Legacy v1 state writers leave
//! it intact; activation of native priority still requires upgraded final gates.
use crate::*;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Route {
    pub owner_ref: String,
    pub original_system_id: String,
    pub original_priority: Priority,
    pub system_id: String,
    pub priority: Priority,
    pub execution_system_id: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Booking {
    pub original_request: Request,
    pub demand: Quantities,
    pub eligibility_ref: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Configuration {
    pub schema: String,
    pub instance_id: String,
    pub host_id: String,
    pub revision: u64,
    pub change_ref: String,
    pub routes: Vec<Route>,
    pub bookings: BTreeMap<String, Booking>,
    pub native_priorities: BTreeMap<String, Priority>,
    pub observe_running_cpu: bool,
    /// Selected through the same scheduling revision, stored separately so
    /// immutable older direct clients can still read their v1 policy.
    #[serde(default, skip_serializing_if = "is_false")]
    pub observe_running_memory: bool,
    /// Domain phase changes are version-joined outside the legacy policy body.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub demand_changes: BTreeMap<String, demand::Change>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reservations: Option<reservations::Configuration>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct DemandSelection {
    schema: String,
    instance_id: String,
    by_revision: BTreeMap<u64, BTreeMap<String, demand::Change>>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MemorySelection {
    schema: String,
    instance_id: String,
    /// Only the committed and next revisions: preserve current selection if
    /// publication stops between writing this extension and its v1 commit.
    by_revision: BTreeMap<u64, bool>,
}
fn is_false(value: &bool) -> bool {
    !*value
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ExpectedAllocation {
    pub revision: u64,
    pub request: Request,
    pub runtime: Option<RuntimeBinding>,
}
impl Configuration {
    fn route<'a>(&'a self, request: &Request) -> Option<&'a Route> {
        self.routes.iter().find(|r| {
            r.owner_ref == request.owner_ref
                && r.original_system_id == request.system_id
                && r.original_priority == request.priority
        })
    }
    pub fn validate(&self, state: &State) -> Result<()> {
        if self.schema != "apeiros.capacity.scheduling.v1"
            || self.instance_id != state.instance_id
            || self.host_id != state.policy.host_id
            || self.revision == 0
        {
            return Err("scheduling policy belongs to another owner or schema".into());
        }
        nonempty(&self.change_ref, "scheduling change reference")?;
        if let Some(reservations) = &self.reservations {
            reservations.validate(state)?;
        }
        let mut selectors = BTreeSet::new();
        for r in &self.routes {
            for s in [
                &r.owner_ref,
                &r.original_system_id,
                &r.system_id,
                &r.execution_system_id,
            ] {
                nonempty(s, "scheduling route")?;
            }
            if state
                .policy
                .cpu_aggregates
                .get(&r.system_id)
                .is_some_and(|g| g.priority != r.priority)
            {
                return Err("effective priority differs from enrolled logical system".into());
            }
            if !selectors.insert((
                r.owner_ref.clone(),
                r.original_system_id.clone(),
                format!("{:?}", r.original_priority),
            )) || !state
                .policy
                .cpu_aggregates
                .contains_key(&r.execution_system_id)
            {
                return Err("duplicate scheduling route or unknown execution aggregate".into());
            }
        }
        for (id, booking) in &self.bookings {
            let allocation = state
                .allocations
                .get(id)
                .ok_or("booking allocation missing")?;
            if allocation.request != booking.original_request
                || booking.demand.keys().ne(allocation.request.demand.keys())
                || booking.demand.values().any(|v| *v == 0)
            {
                return Err(
                    "booking changed original request or physical resource coverage".into(),
                );
            }
            nonempty(&booking.eligibility_ref, "booking eligibility evidence")?;
        }
        for key in self.native_priorities.keys() {
            if !state.policy.cpu_aggregates.contains_key(key) {
                return Err("unknown native priority aggregate".into());
            }
        }
        Ok(())
    }
    pub fn effective(&self, original: &Request) -> Request {
        let mut r = original.clone();
        if let Some(route) = self.route(original) {
            r.system_id = route.system_id.clone();
            r.priority = route.priority;
        }
        if let Some(booking) = self
            .bookings
            .get(&original.allocation_id)
            .filter(|b| b.original_request == *original)
        {
            r.demand = booking.demand.clone();
            r.eligibility_ref = booking.eligibility_ref.clone();
        }
        r
    }
    pub fn execution<'a>(&'a self, original: &'a Request) -> &'a str {
        self.route(original)
            .map_or(original.system_id.as_str(), |r| {
                r.execution_system_id.as_str()
            })
    }
}
impl State {
    pub fn effective_request(&self, original: &Request) -> Request {
        self.scheduling
            .as_ref()
            .map_or_else(|| original.clone(), |p| p.effective(original))
    }
    pub fn execution_system<'a>(&'a self, original: &'a Request) -> &'a str {
        self.scheduling
            .as_ref()
            .map_or(original.system_id.as_str(), |p| p.execution(original))
    }
    pub fn native_policy(&self) -> Policy {
        let mut p = self.policy.clone();
        if let Some(s) = &self.scheduling {
            for (id, priority) in &s.native_priorities {
                if let Some(g) = p.cpu_aggregates.get_mut(id) {
                    g.priority = *priority;
                }
            }
        }
        p
    }
    pub fn scope_matches(&self, scope: &Scope, original: &Request) -> bool {
        scope.matches(original) || scope.matches(&self.effective_request(original))
    }
    /// Selected running-use accounting does not reserve every lifetime forecast
    /// forever. New dispatch still fits the full request in State::assess.
    /// Missing/uncertain work retains its forecast, never an assumed zero.
    pub fn allocation_commitment(&self, a: &Allocation, key: &str, now: u64, boot: &str) -> u64 {
        if self.native_runtime_ended_at_boot(a, boot)
            && self.policy.resources.get(key).is_some_and(|p| {
                matches!(
                    p.native,
                    Some(
                        native::Resource::Cpu { .. }
                            | native::Resource::Memory
                            | native::Resource::GpuMemory { .. }
                    )
                )
            })
        {
            // An exact process incarnation cannot survive a host reboot. This
            // ends only its physical volatile-resource contribution, not the
            // allocation, workload work or owner's recovery responsibility.
            return 0;
        }
        let r = self.effective_request(&a.request);
        let requested = r.demand.get(key).copied().unwrap_or(0);
        let observed = self.resource_observations.get(key);
        let running_use = self.scheduling.as_ref().is_some_and(|s| {
            match self
                .policy
                .resources
                .get(key)
                .and_then(|p| p.native.as_ref())
            {
                Some(native::Resource::Cpu { .. }) => s.observe_running_cpu,
                Some(native::Resource::Memory) => {
                    s.observe_running_memory
                        && self.demand_change(&a.request.allocation_id).is_none()
                        && !a
                            .steps
                            .values()
                            .any(|step| step.outcome == StepOutcome::Uncertain)
                }
                _ => false,
            }
        });
        if running_use && a.phase == Phase::Active {
            if let Some(o) = observed.filter(|o| {
                o.boot_id == boot
                    && now >= o.observed_at_ms
                    && now - o.observed_at_ms <= self.policy.resources[key].observation_max_age_ms
                    && o.unavailable.is_none()
                    && o.external_demand.is_some()
                    && o.accounted_revisions.get(&r.allocation_id) == Some(&a.revision)
                    && o.accounted_runtimes.get(&r.allocation_id) == Some(&a.runtime)
                    && a.runtime.is_some()
            }) {
                if let Some(value) = o.managed_demand.get(&r.allocation_id) {
                    return *value;
                }
            }
        }
        self.committed_quantity(
            key,
            requested,
            observed
                .and_then(|o| o.managed_demand.get(&r.allocation_id))
                .copied()
                .unwrap_or(0),
        )
    }

    pub fn native_runtime_ended_at_boot(&self, a: &Allocation, boot: &str) -> bool {
        !boot.is_empty()
            && a.runtime.as_ref().is_some_and(|b| !b.process.boot_id.is_empty() && b.process.boot_id != boot)
            // A current-boot control/launch can already have an uncertain effect
            // before its successor runtime is bound. Keep that full commitment.
            && a.steps.values().all(|s| s.process.boot_id != boot)
    }
}
impl Store {
    pub fn configure_scheduling(
        &mut self,
        expected_instance: &str,
        expected_policy: u64,
        expected_revision: u64,
        mut configuration: Configuration,
        expected: &BTreeMap<String, ExpectedAllocation>,
    ) -> Result<()> {
        if self.state.instance_id != expected_instance
            || self.state.policy_revision != expected_policy
            || self.state.scheduling.as_ref().map_or(0, |p| p.revision) != expected_revision
        {
            return Err("stale scheduling owner or policy; reconcile original intent".into());
        }
        if configuration.revision
            != expected_revision
                .checked_add(1)
                .ok_or("scheduling revision overflow")?
        {
            return Err("configuration must select the next scheduling revision".into());
        }
        // Dedicated demand operations own the extension. A legacy policy writer
        // cannot erase pending phase intent merely by omitting unknown fields.
        configuration.demand_changes = self
            .state
            .scheduling
            .as_ref()
            .map(|s| s.demand_changes.clone())
            .unwrap_or_default();
        configuration.reservations = self
            .state
            .scheduling
            .as_ref()
            .and_then(|s| s.reservations.clone());
        configuration.validate(&self.state)?;
        // A native aggregate cannot silently elevate unrelated live/waiting work.
        for a in self
            .state
            .allocations
            .values()
            .filter(|a| a.phase != Phase::Released)
        {
            let execution = configuration.execution(&a.request);
            if configuration
                .native_priorities
                .get(execution)
                .is_some_and(|p| *p != configuration.effective(&a.request).priority)
            {
                return Err("native priority would affect differently classified work".into());
            }
            if self.state.execution_system(&a.request) != execution {
                if let Some(binding) = &a.runtime {
                    let group = self
                        .state
                        .policy
                        .cpu_aggregates
                        .get(execution)
                        .ok_or("execution aggregate missing")?;
                    if binding.cgroup != group.cgroup
                        && !binding.cgroup.starts_with(&format!("{}/", group.cgroup))
                    {
                        return Err("scheduling route would relocate an existing runtime".into());
                    }
                }
            }
        }

        let mut changed = BTreeSet::new();
        for (id, a) in &self.state.allocations {
            if a.phase == Phase::Released {
                continue;
            }
            if self.state.effective_request(&a.request) != configuration.effective(&a.request)
                || self.state.execution_system(&a.request) != configuration.execution(&a.request)
                || (self
                    .state
                    .scheduling
                    .as_ref()
                    .is_some_and(|s| s.observe_running_memory)
                    != configuration.observe_running_memory
                    && a.request.demand.keys().any(|key| {
                        self.state
                            .policy
                            .resources
                            .get(key)
                            .is_some_and(|p| matches!(p.native, Some(native::Resource::Memory)))
                    }))
                || self
                    .state
                    .native_policy()
                    .cpu_aggregates
                    .get(self.state.execution_system(&a.request))
                    .map(|g| g.priority)
                    != configuration
                        .native_priorities
                        .get(configuration.execution(&a.request))
                        .copied()
                        .or_else(|| {
                            self.state
                                .policy
                                .cpu_aggregates
                                .get(configuration.execution(&a.request))
                                .map(|g| g.priority)
                        })
            {
                let e = expected
                    .get(id)
                    .ok_or("changed allocation lacks original observation")?;
                if e.revision != a.revision || e.request != a.request || e.runtime != a.runtime {
                    return Err("stale or foreign allocation scheduling correction".into());
                }
                if let Some(binding) = &a.runtime {
                    native::validate_binding(binding)?;
                }
                changed.insert(id);
            }
        }
        if expected.keys().any(|id| !changed.contains(id)) {
            return Err("unexpected unaffected allocation in scheduling correction".into());
        }
        publish(&self.root, self.state.scheduling.as_ref(), &configuration)?;
        self.state.scheduling = Some(configuration);
        Ok(())
    }
}
/// Extensions are prepared first; scheduling.json is the single legacy-readable
/// commit point. Current and prepared revision retain an interrupted publication.
pub(crate) fn publish(
    root: &Path,
    previous: Option<&Configuration>,
    configuration: &Configuration,
) -> Result<()> {
    if previous.is_some_and(|p| p.revision >= configuration.revision) {
        return Err("working scheduling publication did not advance its revision".into());
    }
    write_policy(
        root,
        "scheduling-memory.json",
        &MemorySelection {
            schema: "apeiros.capacity.scheduling.memory.v1".into(),
            instance_id: configuration.instance_id.clone(),
            by_revision: previous
                .iter()
                .map(|s| (s.revision, s.observe_running_memory))
                .chain([(configuration.revision, configuration.observe_running_memory)])
                .collect(),
        },
    )?;
    write_policy(
        root,
        "scheduling-demands.json",
        &DemandSelection {
            schema: "apeiros.capacity.scheduling.demands.v1".into(),
            instance_id: configuration.instance_id.clone(),
            by_revision: previous
                .iter()
                .map(|s| (s.revision, s.demand_changes.clone()))
                .chain([(configuration.revision, configuration.demand_changes.clone())])
                .collect(),
        },
    )?;
    reservations::publish(root, previous, configuration)?;
    let mut compatible = configuration.clone();
    compatible.observe_running_memory = false;
    compatible.demand_changes.clear();
    compatible.reservations = None;
    write_policy(root, "scheduling.json", &compatible)
}

pub(crate) fn write_policy(root: &Path, name: &str, value: &impl Serialize) -> Result<()> {
    let path = root.join(name);
    let pending = root.join(format!("{name}.{}.{}.tmp", std::process::id(), now_ms()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&pending)
        .map_err(|e| e.to_string())?;
    file.write_all(&serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())?;
    fs::rename(&pending, &path).map_err(|e| e.to_string())?;
    File::open(root)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())?;
    Ok(())
}
pub fn read(root: &Path, state: &State) -> Result<Option<Configuration>> {
    let path = root.join("scheduling.json");
    match fs::symlink_metadata(&path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.to_string()),
        Ok(m) if !m.is_file() || m.permissions().mode() & 0o077 != 0 => {
            return Err("scheduling policy must be a private regular file".into());
        }
        Ok(_) => (),
    }
    let mut bytes = Vec::new();
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|e| e.to_string())?
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    let mut p: Configuration = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    // The extension cannot activate itself or another scheduling revision.
    p.observe_running_memory = false;
    let memory_path = root.join("scheduling-memory.json");
    match fs::symlink_metadata(&memory_path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
        Err(e) => return Err(e.to_string()),
        Ok(m) => {
            if !m.is_file() || m.permissions().mode() & 0o077 != 0 {
                return Err("memory selection must be a private regular file".into());
            }
            let mut bytes = Vec::new();
            OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(memory_path)
                .map_err(|e| e.to_string())?
                .take(8193)
                .read_to_end(&mut bytes)
                .map_err(|e| e.to_string())?;
            if bytes.len() > 8192 {
                return Err("memory selection exceeds8KiB".into());
            }
            let memory: MemorySelection =
                serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            if memory.schema != "apeiros.capacity.scheduling.memory.v1" {
                return Err("unknown memory selection schema".into());
            }
            if memory.by_revision.len() > 2 {
                return Err("memory selection must contain only current/next revisions".into());
            }
            if memory.instance_id == p.instance_id {
                p.observe_running_memory = memory
                    .by_revision
                    .get(&p.revision)
                    .copied()
                    .unwrap_or(false);
            }
        }
    }
    p.demand_changes.clear();
    let path = root.join("scheduling-demands.json");
    match fs::symlink_metadata(&path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
        Err(e) => return Err(e.to_string()),
        Ok(m) => {
            if !m.is_file() || m.permissions().mode() & 0o077 != 0 {
                return Err("working demand selection must be a private regular file".into());
            }
            let mut bytes = Vec::new();
            OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(path)
                .map_err(|e| e.to_string())?
                .read_to_end(&mut bytes)
                .map_err(|e| e.to_string())?;
            let selection: DemandSelection =
                serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            if selection.schema != "apeiros.capacity.scheduling.demands.v1"
                || selection.by_revision.len() > 2
                || selection.instance_id != p.instance_id
            {
                return Err(
                    "working demand selection has another owner or unsupported schema".into(),
                );
            }
            if let Some(changes) = selection.by_revision.get(&p.revision) {
                p.demand_changes = changes.clone();
            }
        }
    }
    p.reservations = reservations::read(root, state, &p)?;
    p.validate(state)?;
    Ok(Some(p))
}
