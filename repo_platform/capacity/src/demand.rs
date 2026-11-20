//! Domain-owned working envelopes. Observations are a floor, not a prediction
//! of the next phase. Granted quantities use the existing legacy-readable
//! scheduling booking; pending growth keeps the actual runtime charged.
use crate::*;
use scheduling::{Booking, Configuration, ExpectedAllocation};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ChangeRequest {
    pub allocation_id: String,
    pub change_id: String,
    pub owner_ref: String,
    pub expected_allocation: ExpectedAllocation,
    pub expected_booking: Option<Booking>,
    pub expected_previous_change_id: Option<String>,
    pub demand: Quantities,
    pub eligibility_ref: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    Pending,
    Granted,
    Cancelled,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Change {
    pub request: ChangeRequest,
    pub queued_sequence: u64,
    pub decision: Decision,
    pub granted_sequence: Option<u64>,
    pub granted_system_id: Option<String>,
    /// Committed turn facts survive replacing the latest phase even if an
    /// earlier scheduling-before-state publication was interrupted.
    pub retained_turns: BTreeMap<String, u64>,
}

impl State {
    pub fn demand_change(&self, id: &str) -> Option<&Change> {
        self.scheduling.as_ref()?.demand_changes.get(id)
    }

    pub fn has_waiting_demand(&self, a: &Allocation) -> bool {
        a.phase == Phase::Waiting
            || (a.phase == Phase::Active
                && self
                    .demand_change(&a.request.allocation_id)
                    .is_some_and(|c| c.decision == Decision::Pending))
    }

    pub fn waiting_sequence(&self, a: &Allocation) -> u64 {
        if a.phase == Phase::Active {
            if let Some(c) = self.demand_change(&a.request.allocation_id) {
                return c.queued_sequence;
            }
        }
        a.queued_sequence
    }

    pub fn waiting_request(&self, a: &Allocation) -> Request {
        let mut r = self.effective_request(&a.request);
        if a.phase == Phase::Active {
            if let Some(c) = self
                .demand_change(&a.request.allocation_id)
                .filter(|c| c.decision == Decision::Pending)
            {
                r.demand = c.request.demand.clone();
                r.eligibility_ref = c.request.eligibility_ref.clone();
            }
        }
        r
    }

    fn demand_binding(&self, r: &ChangeRequest) -> Result<()> {
        let a = self
            .allocations
            .get(&r.allocation_id)
            .ok_or("demand allocation missing")?;
        if a.phase != Phase::Active
            || a.request.owner_ref != r.owner_ref
            || a.request != r.expected_allocation.request
            || a.revision != r.expected_allocation.revision
            || a.runtime != r.expected_allocation.runtime
            || a.runtime.is_none()
            || a.steps
                .values()
                .any(|s| s.outcome == StepOutcome::Uncertain)
        {
            return Err("working demand has stale, foreign, unbound or uncertain runtime; reconcile original phase".into());
        }
        let booking = self
            .scheduling
            .as_ref()
            .and_then(|s| s.bookings.get(&r.allocation_id));
        if booking != r.expected_booking.as_ref() {
            return Err("working demand booking changed; reconcile original phase".into());
        }
        Ok(())
    }

    pub fn waiting_assessment(&self, a: &Allocation, now: u64, boot: &str) -> Result<Assessment> {
        let mut assessment =
            self.assess_effective(&a.request, self.waiting_request(a), now, boot)?;
        if a.phase == Phase::Active {
            if let Some(c) = self.demand_change(&a.request.allocation_id) {
                let binding = self.demand_binding(&c.request).and_then(|()| {
                    if a.runtime.as_ref().is_some_and(|b| b.process.boot_id == boot) { Ok(()) }
                    else { Err("working demand belongs to an ended native boot; owner recovery required".into()) }
                });
                if let Err(reason) = binding {
                    assessment.eligible = false;
                    assessment.can_admit = false;
                    assessment.reasons.push(reason);
                }
            }
        }
        Ok(assessment)
    }

    fn next_demand_revision(&mut self) -> Result<()> {
        if self.scheduling.is_none() {
            self.scheduling = Some(Configuration {
                schema: "apeiros.capacity.scheduling.v1".into(),
                instance_id: self.instance_id.clone(),
                host_id: self.policy.host_id.clone(),
                revision: 0,
                change_ref: "domain working demand".into(),
                routes: vec![],
                bookings: BTreeMap::new(),
                native_priorities: BTreeMap::new(),
                observe_running_cpu: false,
                observe_running_memory: false,
                demand_changes: BTreeMap::new(),
                reservations: None,
            });
        }
        let s = self.scheduling.as_mut().unwrap();
        s.revision = s
            .revision
            .checked_add(1)
            .ok_or("scheduling revision overflow")?;
        Ok(())
    }

    /// Submit only at a domain boundary. A smaller envelope describes the settled
    /// phase; an expansion is not permission to allocate until granted and the
    /// existing final native-control/dispatch boundary has passed.
    pub fn request_demand_change(
        &mut self,
        r: ChangeRequest,
        now: u64,
        boot: &str,
    ) -> Result<Change> {
        nonempty(&r.change_id, "working demand change identity")?;
        nonempty(
            &r.eligibility_ref,
            "working demand qualification/phase reference",
        )?;
        if let Some(c) = self.demand_change(&r.allocation_id) {
            if c.request.change_id == r.change_id {
                return if c.request == r {
                    Ok(c.clone())
                } else {
                    Err("working demand identity reused for different intent".into())
                };
            }
            if c.decision == Decision::Pending {
                return Err("another phase is pending; reconcile or cancel it first".into());
            }
        }
        if self
            .demand_change(&r.allocation_id)
            .map(|c| &c.request.change_id)
            != r.expected_previous_change_id.as_ref()
        {
            return Err("previous working demand changed; reconcile original intent".into());
        }
        self.demand_binding(&r)?;
        if r.expected_allocation
            .runtime
            .as_ref()
            .is_none_or(|b| b.process.boot_id != boot)
        {
            return Err("working demand requires the current native boot; recover the original execution first".into());
        }
        if r.demand
            .keys()
            .ne(r.expected_allocation.request.demand.keys())
            || r.demand.values().any(|v| *v == 0)
        {
            return Err(
                "working demand must preserve physical resource coverage and positive quantities"
                    .into(),
            );
        }
        if r.demand
            .keys()
            .any(|key| !self.policy.resources.contains_key(key))
        {
            return Err("working demand references a resource absent from current policy".into());
        }
        let a = &self.allocations[&r.allocation_id];
        let current = self.effective_request(&a.request);
        let reduction = r
            .demand
            .iter()
            .all(|(key, value)| *value <= current.demand[key]);
        if reduction {
            // Unknown attribution cannot be used as a resource-release report.
            for key in r
                .demand
                .keys()
                .filter(|key| r.demand[*key] < current.demand[*key])
            {
                let fresh = self.resource_observations.get(key).is_some_and(|o| {
                    o.boot_id == boot
                        && o.observed_at_ms <= now
                        && now - o.observed_at_ms
                            <= self.policy.resources[key].observation_max_age_ms
                        && o.unavailable.is_none()
                        && o.external_demand.is_some()
                        && o.accounted_revisions.get(&r.allocation_id) == Some(&a.revision)
                        && o.accounted_runtimes.get(&r.allocation_id) == Some(&a.runtime)
                        && o.managed_demand.contains_key(&r.allocation_id)
                });
                if !fresh {
                    return Err(format!(
                        "working demand reduction lacks fresh exact attribution: {key}"
                    ));
                }
            }
        }
        let id = r.allocation_id.clone();
        let retained_turns = self
            .demand_change(&id)
            .map(|c| c.retained_turns.clone())
            .unwrap_or_default();
        let sequence = self.tick();
        self.next_demand_revision()?;
        self.scheduling.as_mut().unwrap().demand_changes.insert(
            id.clone(),
            Change {
                request: r,
                queued_sequence: sequence,
                decision: Decision::Pending,
                granted_sequence: None,
                granted_system_id: None,
                retained_turns,
            },
        );
        if reduction {
            // Returning a settled phase's unneeded booking is not a new turn;
            // independent holds may prevent growth but must not block reduction.
            self.grant_demand_change(&id, None)?;
        } else {
            self.schedule(now, boot)?;
        }
        Ok(self.demand_change(&id).unwrap().clone())
    }

    pub(crate) fn grant_demand_change(&mut self, id: &str, turn: Option<u64>) -> Result<()> {
        let r = self
            .demand_change(id)
            .ok_or("working demand missing")?
            .request
            .clone();
        self.demand_binding(&r)?;
        let system = self
            .effective_request(&r.expected_allocation.request)
            .system_id;
        self.next_demand_revision()?;
        let s = self.scheduling.as_mut().unwrap();
        s.change_ref = r.eligibility_ref.clone();
        s.bookings.insert(
            id.into(),
            Booking {
                original_request: r.expected_allocation.request,
                demand: r.demand,
                eligibility_ref: r.eligibility_ref,
            },
        );
        let c = s.demand_changes.get_mut(id).unwrap();
        c.decision = Decision::Granted;
        c.granted_sequence = turn;
        c.granted_system_id = turn.map(|_| system.clone());
        if let Some(sequence) = turn {
            let retained = c.retained_turns.entry(system).or_default();
            *retained = (*retained).max(sequence);
        }
        Ok(())
    }

    pub fn cancel_demand_change(&mut self, id: &str, change: &str, owner: &str) -> Result<()> {
        let c = self.demand_change(id).ok_or("working demand missing")?;
        if c.request.change_id != change || c.request.owner_ref != owner {
            return Err("stale or foreign working demand cancellation".into());
        }
        if c.decision == Decision::Cancelled {
            return Ok(());
        }
        if c.decision != Decision::Pending {
            return Err("granted working demand cannot be cancelled; reconcile the phase and report its actual successor envelope".into());
        }
        self.next_demand_revision()?;
        self.scheduling
            .as_mut()
            .unwrap()
            .demand_changes
            .get_mut(id)
            .unwrap()
            .decision = Decision::Cancelled;
        self.tick();
        Ok(())
    }

    /// The scheduling commit point can precede state.json during an interrupted
    /// save. Restore only the queue/turn facts recorded in that committed policy.
    pub(crate) fn recover_demand_sequences(&mut self) {
        if let Some(s) = &self.scheduling {
            for c in s.demand_changes.values() {
                self.sequence = self.sequence.max(c.queued_sequence);
                for (system, sequence) in &c.retained_turns {
                    self.sequence = self.sequence.max(*sequence);
                    let turn = self.last_turn.entry(system.clone()).or_default();
                    *turn = (*turn).max(*sequence);
                }
            }
        }
    }
}
