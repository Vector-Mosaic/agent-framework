//! Change the callback for future pressure work while each already-issued
//! request keeps its original executable, arguments and qualification.
use crate::{Result, Store, boot_id, nonempty, pressure::DomainControl};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::Path;

pub const ROUTER: &str = "route-pressure-control";

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Selection {
    successor: DomainControl,
    originals: BTreeMap<String, Original>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Original {
    request_id: String,
    control: DomainControl,
}

impl Selection {
    fn select(&self, allocation: &str, request: &str) -> &DomainControl {
        self.originals
            .get(allocation)
            .filter(|original| original.request_id == request)
            .map_or(&self.successor, |original| &original.control)
    }
}

pub fn destination(selection: &str, request: &Value) -> Result<Vec<String>> {
    let selection: Selection = serde_json::from_str(selection).map_err(|e| e.to_string())?;
    let allocation = request["allocation_id"]
        .as_str()
        .ok_or("missing callback allocation")?;
    let request_key = match request["action"].as_str() {
        Some("inspect" | "request_release") => "request_id",
        Some("request_resume") => "pressure_request_id",
        _ => return Err("unknown pressure callback action".into()),
    };
    let request_id = request[request_key]
        .as_str()
        .ok_or("missing original pressure request")?;
    let argv = selection.select(allocation, request_id).argv.clone();
    if argv.is_empty() || !Path::new(&argv[0]).is_absolute() {
        return Err("invalid selected pressure executable".into());
    }
    Ok(argv)
}

impl Store {
    pub fn succeed_pressure_control(&mut self, input: &Value, router: &Path) -> Result<Value> {
        let system = input["system_id"]
            .as_str()
            .ok_or("domain system required")?;
        let change = input["change_ref"]
            .as_str()
            .ok_or("change reference required")?;
        nonempty(change, "pressure control succession reference")?;
        let previous: DomainControl =
            serde_json::from_value(input["previous_control"].clone()).map_err(|e| e.to_string())?;
        let successor: DomainControl =
            serde_json::from_value(input["control"].clone()).map_err(|e| e.to_string())?;
        let scheduling = self.state.scheduling.as_ref().map_or(0, |s| s.revision);
        let mut reservations = self
            .state
            .scheduling
            .as_ref()
            .and_then(|s| s.reservations.clone());
        if input["expected_source"]["instance_id"] != self.state.instance_id
            || input["expected_source"]["host_id"] != self.state.policy.host_id
            || input["expected_policy_revision"].as_u64() != Some(self.state.policy_revision)
            || input["expected_scheduling_revision"].as_u64() != Some(scheduling)
            || self.state.policy.domain_controls.get(system) != Some(&previous)
            || input.get("expected_reservations")
                != Some(&serde_json::to_value(&reservations).map_err(|e| e.to_string())?)
        {
            return Err("stale pressure control, physical policy or scheduling selection".into());
        }
        let old_selection: Option<Selection> = match previous.argv.as_slice() {
            [_, verb, selection] if verb == ROUTER => {
                Some(serde_json::from_str(selection).map_err(|e| e.to_string())?)
            }
            _ => None,
        };
        if successor == *old_selection.as_ref().map_or(&previous, |s| &s.successor)
            || successor.argv.get(1).is_some_and(|v| v == ROUTER)
        {
            return Err("select a changed qualified domain executable for new work".into());
        }
        let mut policy = self.state.policy.clone();
        policy
            .domain_controls
            .insert(system.into(), successor.clone());
        crate::pressure::validate_policy(&policy)?;
        let boot = boot_id()?;
        let mut originals = BTreeMap::new();
        for t in self
            .state
            .pressure
            .values()
            .filter(|t| t.needs_reconciliation(&self.state, &boot))
        {
            let a = self
                .state
                .allocations
                .get(&t.allocation_id)
                .ok_or("pending pressure request lacks its allocation")?;
            if a.request.system_id != system {
                continue;
            }
            let prior = old_selection
                .as_ref()
                .map_or(&previous, |s| s.select(&t.allocation_id, &t.request_id));
            if prior.timeout_ms != successor.timeout_ms
                || prior.stable_recovery_ms != successor.stable_recovery_ms
            {
                return Err(
                    "pending original callback requires its existing timeout and recovery interval"
                        .into(),
                );
            }
            originals.insert(
                t.allocation_id.clone(),
                Original {
                    request_id: t.request_id.clone(),
                    control: prior.clone(),
                },
            );
        }
        let count = originals.len();
        if count > 0 {
            let selection = serde_json::to_string(&Selection {
                successor: successor.clone(),
                originals,
            })
            .map_err(|e| e.to_string())?;
            // Linux execve limits each argument to MAX_ARG_STRLEN (32 pages).
            let page = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
            if page <= 0 || selection.len() >= 32 * page as usize {
                return Err(
                    "pending callback selection exceeds the native exec argument limit".into(),
                );
            }
            let mut routed = successor;
            routed.argv = vec![
                router
                    .to_str()
                    .ok_or("invalid immutable Capacity executable")?
                    .into(),
                ROUTER.into(),
                selection,
            ];
            policy.domain_controls.insert(system.into(), routed);
        }
        if let Some(r) = &reservations {
            r.validate(&self.state)?;
        }
        let revision = self
            .state
            .policy_revision
            .checked_add(1)
            .ok_or("physical policy revision overflow")?;
        self.state.policy = policy;
        self.state.policy_revision = revision;
        self.state.policy_change_ref = Some(change.into());
        self.state.tick();
        if let Some(r) = &mut reservations {
            // Keep exactly the existing native GPU guard, floor, resource, and
            // authority. Store's writer lock spans scheduling and state publication.
            r.policy_revision = revision;
            let instance = self.state.instance_id.clone();
            self.configure_reservations(
                &instance,
                revision,
                scheduling,
                reservations,
                change.into(),
            )?;
        }
        self.save()?;
        Ok(
            serde_json::json!({"system_id":system,"policy_revision":revision,
            "scheduling_revision":self.state.scheduling.as_ref().map_or(0,|s|s.revision),
            "preserved_pending_requests":count,"changed":true,"launched":false}),
        )
    }
}
