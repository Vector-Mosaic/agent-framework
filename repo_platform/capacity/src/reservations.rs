//! Explicit persistent GPU floors; idle protected capacity is not a fake job.
//! The physical policy contains a conservative legacy guard. Qualified readers
//! credit the part used by its real owner, so that capacity is never charged twice.
use crate::*;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ResourceReservation {
    pub resource_policy: ResourcePolicy,
    pub original_operational_margin: u64,
    pub system_floors: BTreeMap<String, u64>,
    pub selection_ref: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Configuration {
    pub policy_revision: u64,
    pub resources: BTreeMap<String, ResourceReservation>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Selection {
    schema: String,
    instance_id: String,
    by_revision: BTreeMap<u64, Option<Configuration>>,
}

impl Configuration {
    pub fn validate(&self, state: &State) -> Result<()> {
        if self.policy_revision != state.policy_revision {
            return Err("reservation physical policy revision changed".into());
        }
        for (id, reserve) in &self.resources {
            let policy = state
                .policy
                .resources
                .get(id)
                .ok_or("reservation resource missing")?;
            nonempty(&reserve.selection_ref, "reservation selection reference")?;
            if *policy != reserve.resource_policy
                || !matches!(policy.native, Some(native::Resource::GpuMemory { .. }))
                || reserve.system_floors.is_empty()
            {
                return Err("reservation requires the exact guarded native GPU policy".into());
            }
            let mut guard = reserve.original_operational_margin;
            for (system, quantity) in &reserve.system_floors {
                if !state.policy.cpu_aggregates.contains_key(system) || *quantity == 0 {
                    return Err(
                        "reservation requires a positive floor for an enrolled system".into(),
                    );
                }
                guard = guard.checked_add(*quantity).ok_or("reservation overflow")?;
            }
            if guard != policy.operational_margin {
                return Err(
                    "legacy GPU guard must equal original margin plus selected floors".into(),
                );
            }
        }
        Ok(())
    }
}

impl State {
    pub fn resource_reservation(&self, key: &str) -> Option<&ResourceReservation> {
        self.scheduling
            .as_ref()?
            .reservations
            .as_ref()
            .filter(|c| c.policy_revision == self.policy_revision)?
            .resources
            .get(key)
    }

    pub fn physical_operational_margin(&self, key: &str) -> u64 {
        self.resource_reservation(key)
            .map_or(self.policy.resources[key].operational_margin, |r| {
                r.original_operational_margin
            })
    }

    fn system_commitment(
        &self,
        system: &str,
        key: &str,
        excluding: Option<&str>,
        now: u64,
        boot: &str,
    ) -> u64 {
        self.allocations
            .values()
            .filter(|a| {
                matches!(
                    a.phase,
                    Phase::Admitted | Phase::Active | Phase::ReleasePending
                ) && excluding != Some(a.request.allocation_id.as_str())
                    && self.effective_request(&a.request).system_id == system
            })
            .map(|a| self.allocation_commitment(a, key, now, boot))
            .fold(0, u64::saturating_add)
    }

    /// A final assessment excludes its own current allocation and fits its full
    /// next demand. The owner's unused floor therefore excludes that row too.
    pub fn unused_system_reservation(
        &self,
        system: &str,
        key: &str,
        excluding: Option<&str>,
        now: u64,
        boot: &str,
    ) -> u64 {
        self.resource_reservation(key)
            .and_then(|r| r.system_floors.get(system))
            .map_or(0, |floor| {
                floor.saturating_sub(self.system_commitment(system, key, excluding, now, boot))
            })
    }

    pub fn admission_operational_margin(
        &self,
        request: &Request,
        key: &str,
        now: u64,
        boot: &str,
    ) -> u64 {
        let Some(reserve) = self.resource_reservation(key) else {
            return self.policy.resources[key].operational_margin;
        };
        reserve
            .system_floors
            .iter()
            .filter(|(system, _)| *system != &request.system_id)
            .fold(
                reserve.original_operational_margin,
                |margin, (system, floor)| {
                    margin.saturating_add(floor.saturating_sub(self.system_commitment(
                        system,
                        key,
                        Some(&request.allocation_id),
                        now,
                        boot,
                    )))
                },
            )
    }
}

impl Store {
    pub fn configure_reservations(
        &mut self,
        instance: &str,
        policy_revision: u64,
        revision: u64,
        reservations: Option<Configuration>,
        change_ref: String,
    ) -> Result<()> {
        if self.state.instance_id != instance
            || self.state.policy_revision != policy_revision
            || self.state.scheduling.as_ref().map_or(0, |s| s.revision) != revision
        {
            return Err("stale reservation owner, policy or scheduling revision".into());
        }
        nonempty(&change_ref, "reservation change reference")?;
        if let Some(r) = &reservations {
            r.validate(&self.state)?;
        }
        let mut next = self
            .state
            .scheduling
            .clone()
            .unwrap_or(scheduling::Configuration {
                schema: "apeiros.capacity.scheduling.v1".into(),
                instance_id: instance.into(),
                host_id: self.state.policy.host_id.clone(),
                revision: 0,
                change_ref: change_ref.clone(),
                routes: vec![],
                bookings: BTreeMap::new(),
                native_priorities: BTreeMap::new(),
                observe_running_cpu: false,
                observe_running_memory: false,
                demand_changes: BTreeMap::new(),
                reservations: None,
            });
        next.revision = revision
            .checked_add(1)
            .ok_or("scheduling revision overflow")?;
        next.change_ref = change_ref;
        next.reservations = reservations;
        next.validate(&self.state)?;
        scheduling::publish(&self.root, self.state.scheduling.as_ref(), &next)?;
        self.state.scheduling = Some(next);
        Ok(())
    }
}

pub(crate) fn publish(
    root: &Path,
    previous: Option<&scheduling::Configuration>,
    next: &scheduling::Configuration,
) -> Result<()> {
    scheduling::write_policy(
        root,
        "scheduling-reservations.json",
        &Selection {
            schema: "apeiros.capacity.scheduling.reservations.v1".into(),
            instance_id: next.instance_id.clone(),
            by_revision: previous
                .iter()
                .map(|s| (s.revision, s.reservations.clone()))
                .chain([(next.revision, next.reservations.clone())])
                .collect(),
        },
    )
}

pub(crate) fn read(
    root: &Path,
    state: &State,
    scheduling: &scheduling::Configuration,
) -> Result<Option<Configuration>> {
    let path = root.join("scheduling-reservations.json");
    match fs::symlink_metadata(&path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.to_string()),
        Ok(m) if !m.is_file() || m.permissions().mode() & 0o077 != 0 => {
            return Err("reservation selection must be a private regular file".into());
        }
        Ok(_) => (),
    }
    let mut bytes = Vec::new();
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|e| e.to_string())?
        .take(65537)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 65536 {
        return Err("reservation selection exceeds64KiB".into());
    }
    let selected: Selection = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    if selected.schema != "apeiros.capacity.scheduling.reservations.v1" || selected.by_revision.len() > 2
    {
        return Err("unsupported reservation selection".into());
    }
    // Absent/mismatched owner, commit point or physical-policy revision supplies
    // no credit. The legacy physical guard remains conservative and durable.
    if selected.instance_id != scheduling.instance_id {
        return Ok(None);
    }
    let chosen = selected
        .by_revision
        .get(&scheduling.revision)
        .cloned()
        .flatten()
        .filter(|c| c.policy_revision == state.policy_revision);
    if let Some(c) = &chosen {
        c.validate(state)?;
    }
    Ok(chosen)
}
