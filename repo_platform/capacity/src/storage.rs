//! Stable mounted-volume binding and actual native device/cgroup I/O counters.
//! Metadata only: this module never opens volume contents or walks data paths.
use crate::*;
use std::os::unix::fs::{FileTypeExt, MetadataExt};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DeviceBinding {
    pub mountpoint: String,
    pub filesystem_uuid: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Device {
    pub filesystem_device: String,
    pub physical_device: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Limit {
    /// Explicit null removes this owner's limit; no implicit permanent ceiling.
    #[serde(deserialize_with = "explicit_limit")]
    pub read_bytes_per_second: Option<u64>,
    #[serde(deserialize_with = "explicit_limit")]
    pub write_bytes_per_second: Option<u64>,
    pub change_ref: String,
    pub ending_condition: String,
}
fn explicit_limit<'de, D: serde::Deserializer<'de>>(
    input: D,
) -> std::result::Result<Option<u64>, D::Error> {
    Option::<u64>::deserialize(input)
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Sample {
    pub boot_id: String,
    pub monotonic_ms: u64,
    pub devices: BTreeMap<String, Device>,
    pub total_bytes: BTreeMap<String, u64>,
    pub managed_bytes: BTreeMap<String, Quantities>,
    pub managed_processes: BTreeMap<String, Process>,
    pub unavailable: BTreeMap<String, String>,
}

pub fn device(binding: &DeviceBinding) -> Result<Device> {
    if !Path::new(&binding.mountpoint).is_absolute()
        || binding.filesystem_uuid.is_empty()
        || !binding
            .filesystem_uuid
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-')
    {
        return Err("exact mounted filesystem UUID/path required for I/O".into());
    }
    let path = PathBuf::from("/dev/disk/by-uuid").join(&binding.filesystem_uuid);
    let metadata = fs::metadata(&path).map_err(|_| "selected filesystem device unavailable")?;
    if !metadata.file_type().is_block_device() {
        return Err("selected volume is not a native block device".into());
    }
    let filesystem_device = format!(
        "{}:{}",
        libc::major(metadata.rdev()),
        libc::minor(metadata.rdev())
    );
    let mounted = fs::read_to_string("/proc/self/mountinfo")
        .map_err(|e| e.to_string())?
        .lines()
        .any(|line| {
            let fields: Vec<_> = line.split_whitespace().collect();
            fields.get(2).copied() == Some(filesystem_device.as_str())
                && fields.get(4).copied() == Some(binding.mountpoint.as_str())
        });
    if !mounted {
        return Err(
            "required UUID-bound mount unavailable; bare directory fallback forbidden".into(),
        );
    }
    let sys = fs::canonicalize(PathBuf::from("/sys/dev/block").join(&filesystem_device))
        .map_err(|e| e.to_string())?;
    let physical = if sys.join("partition").exists() {
        sys.parent()
            .ok_or("partition has no device parent")?
            .to_path_buf()
    } else {
        sys
    };
    if physical.join("slaves").is_dir()
        && fs::read_dir(physical.join("slaves"))
            .map_err(|e| e.to_string())?
            .next()
            .is_some()
    {
        return Err("stacked backend needs its actual joined accounting/control owner".into());
    }
    let physical_device = fs::read_to_string(physical.join("dev"))
        .map_err(|e| e.to_string())?
        .trim()
        .to_string();
    Ok(Device {
        filesystem_device,
        physical_device,
    })
}
pub fn counter(device: &Device) -> Result<u64> {
    let values = fs::read_to_string(
        PathBuf::from("/sys/dev/block")
            .join(&device.physical_device)
            .join("stat"),
    )
    .map_err(|e| e.to_string())?;
    let numbers: Vec<u64> = values
        .split_whitespace()
        .map(|v| v.parse::<u64>().map_err(|e| e.to_string()))
        .collect::<Result<_>>()?;
    if numbers.len() < 11 {
        return Err("device I/O counters incomplete".into());
    }
    numbers[2]
        .checked_add(numbers[6])
        .and_then(|v| v.checked_mul(512))
        .ok_or("device counter overflow".into())
}
pub fn cgroup_counter(group: &str, device: &Device) -> Result<u64> {
    let input =
        fs::read_to_string(format!("/sys/fs/cgroup{group}/io.stat")).map_err(|e| e.to_string())?;
    let line = input
        .lines()
        .find(|line| line.split_whitespace().next() == Some(device.physical_device.as_str()));
    let Some(line) = line else { return Ok(0) }; // Existing kernel counter file, no I/O yet on this selected device.
    let mut read = None;
    let mut write = None;
    for entry in line.split_whitespace().skip(1) {
        if let Some(v) = entry.strip_prefix("rbytes=") {
            read = Some(v.parse::<u64>().map_err(|e| e.to_string())?);
        }
        if let Some(v) = entry.strip_prefix("wbytes=") {
            write = Some(v.parse::<u64>().map_err(|e| e.to_string())?);
        }
    }
    read.zip(write)
        .and_then(|(r, w)| r.checked_add(w))
        .ok_or("selected cgroup I/O counter incomplete/overflow".into())
}

pub fn sample(policy: &Policy, bindings: &[(&String, &Allocation, &RuntimeBinding)]) -> Sample {
    let mut clock = std::mem::MaybeUninit::<libc::timespec>::uninit();
    let clock_ok = unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, clock.as_mut_ptr()) } == 0;
    let monotonic_ms = if clock_ok {
        let clock = unsafe { clock.assume_init() };
        clock.tv_sec as u64 * 1000 + clock.tv_nsec as u64 / 1_000_000
    } else {
        0
    };
    let mut sample = Sample {
        boot_id: boot_id().unwrap_or_default(),
        monotonic_ms,
        devices: BTreeMap::new(),
        total_bytes: BTreeMap::new(),
        managed_bytes: BTreeMap::new(),
        managed_processes: BTreeMap::new(),
        unavailable: BTreeMap::new(),
    };
    for (resource, policy) in &policy.resources {
        let Some(native::Resource::IoBandwidth {
            device: binding, ..
        }) = &policy.native
        else {
            continue;
        };
        let result = (|| -> Result<()> {
            if !clock_ok {
                return Err("native monotonic I/O clock unavailable".into());
            }
            let device = device(binding)?;
            if sample
                .devices
                .values()
                .any(|other| other.physical_device == device.physical_device)
            {
                return Err("I/O identities alias the same physical backend".into());
            }
            let total = counter(&device)?;
            for (id, allocation, runtime) in bindings {
                if !allocation.request.demand.contains_key(resource) {
                    continue;
                }
                let bytes = cgroup_counter(&runtime.cgroup, &device)?;
                sample
                    .managed_bytes
                    .entry((*id).clone())
                    .or_default()
                    .insert(resource.clone(), bytes);
                sample
                    .managed_processes
                    .insert((*id).clone(), runtime.process.clone());
            }
            if self::device(binding)? != device {
                return Err("mounted I/O device changed during capture".into());
            }
            sample.devices.insert(resource.clone(), device);
            sample.total_bytes.insert(resource.clone(), total);
            Ok(())
        })();
        if let Err(error) = result {
            sample.unavailable.insert(resource.clone(), error);
        }
    }
    sample
}

pub fn rate(
    state: &State,
    current: &Sample,
    resource: &str,
    bindings: &[(&String, &Allocation, &RuntimeBinding)],
    accounted: &mut BTreeMap<String, u64>,
    managed_demand: &mut BTreeMap<String, Quantities>,
) -> Result<u64> {
    if let Some(error) = current.unavailable.get(resource) {
        return Err(error.clone());
    }
    let previous = state
        .native_io_counters
        .as_ref()
        .ok_or("I/O requires two native counter samples")?;
    if previous.boot_id != current.boot_id
        || current.monotonic_ms <= previous.monotonic_ms
        || current.devices.get(resource) != previous.devices.get(resource)
    {
        return Err("I/O interval boot/device changed; reacquire two samples".into());
    }
    let elapsed = current.monotonic_ms - previous.monotonic_ms;
    if elapsed < 100 {
        return Err("I/O interval shorter than100ms".into());
    }
    let current_bytes = *current
        .total_bytes
        .get(resource)
        .ok_or("current I/O counter absent")?;
    let previous_bytes = *previous
        .total_bytes
        .get(resource)
        .ok_or("prior I/O counter absent")?;
    let total = current_bytes
        .checked_sub(previous_bytes)
        .and_then(|delta| delta.checked_mul(1000))
        .ok_or("I/O counter reset/overflow")?
        / elapsed;
    let mut managed = 0u64;
    for (id, allocation, runtime) in bindings {
        if current.managed_processes.get(*id) != Some(&runtime.process)
            || previous.managed_processes.get(*id) != Some(&runtime.process)
        {
            continue;
        }
        let Some(current) = current.managed_bytes.get(*id).and_then(|q| q.get(resource)) else {
            continue;
        };
        let Some(previous) = previous
            .managed_bytes
            .get(*id)
            .and_then(|q| q.get(resource))
        else {
            continue;
        };
        let Some(rate) = current
            .checked_sub(*previous)
            .and_then(|delta| delta.checked_mul(1000))
            .map(|delta| delta / elapsed)
        else {
            continue;
        };
        managed = managed.saturating_add(rate);
        accounted.insert((*id).clone(), allocation.revision);
        managed_demand
            .entry((*id).clone())
            .or_default()
            .insert(resource.into(), rate);
    }
    Ok(total.saturating_sub(managed))
}

pub fn validate_policy(policy: &Policy) -> Result<()> {
    for (system, resources) in &policy.io_limits {
        if !policy.cpu_aggregates.contains_key(system) {
            return Err("I/O controls require the actual enrolled system aggregate".into());
        }
        for (resource, limit) in resources {
            if !policy
                .resources
                .get(resource)
                .is_some_and(|r| matches!(r.native, Some(native::Resource::IoBandwidth { .. })))
            {
                return Err("I/O control requires the selected native backend resource".into());
            }
            if limit.read_bytes_per_second == Some(0) || limit.write_bytes_per_second == Some(0) {
                return Err("I/O limits must be positive or explicit null to clear".into());
            }
            nonempty(&limit.change_ref, "I/O adjustment source")?;
            nonempty(&limit.ending_condition, "I/O adjustment ending condition")?;
        }
    }
    Ok(())
}
fn read_limits(group: &str, device: &Device) -> Result<(Option<u64>, Option<u64>)> {
    let text =
        fs::read_to_string(format!("/sys/fs/cgroup{group}/io.max")).map_err(|e| e.to_string())?;
    let Some(line) = text
        .lines()
        .find(|line| line.split_whitespace().next() == Some(device.physical_device.as_str()))
    else {
        return Ok((None, None));
    };
    let fields: BTreeMap<_, _> = line
        .split_whitespace()
        .skip(1)
        .filter_map(|s| s.split_once('='))
        .collect();
    if fields.get("riops").is_some_and(|v| *v != "max")
        || fields.get("wiops").is_some_and(|v| *v != "max")
    {
        return Err("unexpected IOPS writer constrains enrolled native byte control".into());
    }
    let parse = |key| -> Result<Option<u64>> {
        match fields.get(key) {
            Some(&"max") => Ok(None),
            Some(value) => value.parse::<u64>().map(Some).map_err(|e| e.to_string()),
            None => Err("native I/O limit row is incomplete".into()),
        }
    };
    Ok((parse("rbps")?, parse("wbps")?))
}
pub fn controls(policy: &Policy, apply: bool) -> serde_json::Value {
    let mut outcomes = vec![];
    for (system, resources) in &policy.io_limits {
        for (resource, limit) in resources {
            let outcome = (|| -> Result<serde_json::Value> {
                let group = policy
                    .cpu_aggregates
                    .get(system)
                    .ok_or("I/O aggregate absent")?;
                let Some(native::Resource::IoBandwidth {
                    device: binding, ..
                }) = policy
                    .resources
                    .get(resource)
                    .and_then(|p| p.native.as_ref())
                else {
                    return Err("native I/O resource absent".into());
                };
                let device = device(binding)?;
                let before = read_limits(&group.cgroup, &device)?;
                let selected = (limit.read_bytes_per_second, limit.write_bytes_per_second);
                if apply && before != selected {
                    let value =
                        |v: Option<u64>| v.map(|n| n.to_string()).unwrap_or_else(|| "max".into());
                    fs::write(
                        format!("/sys/fs/cgroup{}/io.max", group.cgroup),
                        format!(
                            "{} rbps={} wbps={}",
                            device.physical_device,
                            value(selected.0),
                            value(selected.1)
                        ),
                    )
                    .map_err(|e| e.to_string())?;
                }
                if self::device(binding)? != device {
                    return Err("native I/O binding changed during control".into());
                }
                let after = read_limits(&group.cgroup, &device)?;
                Ok(
                    serde_json::json!({"device":device,"cgroup":group.cgroup,"read_bytes_per_second":after.0,"write_bytes_per_second":after.1,"matches_selected":after==selected,"changed":before!=after,"change_ref":limit.change_ref,"ending_condition":limit.ending_condition,"evidence_scope":"native io.max readback; useful throughput/save traffic remain at the qualified owner"}),
                )
            })();
            outcomes.push(match outcome{Ok(data)=>serde_json::json!({"system_id":system,"resource_id":resource,"ok":true,"data":data}),Err(error)=>serde_json::json!({"system_id":system,"resource_id":resource,"ok":false,"error":error})});
        }
    }
    serde_json::json!({"applied":apply,"outcomes":outcomes,"scope":"selected native io.max byte limits; actual backend effects, useful progress and save traffic require qualification"})
}
pub fn verify_controls(policy: &Policy, system: &str) -> Result<()> {
    let Some(resources) = policy.io_limits.get(system) else {
        return Ok(());
    };
    let group = policy
        .cpu_aggregates
        .get(system)
        .ok_or("I/O aggregate absent")?;
    for (resource, limit) in resources {
        let Some(native::Resource::IoBandwidth {
            device: binding, ..
        }) = policy
            .resources
            .get(resource)
            .and_then(|p| p.native.as_ref())
        else {
            return Err("native I/O resource absent".into());
        };
        if read_limits(&group.cgroup, &device(binding)?)?
            != (limit.read_bytes_per_second, limit.write_bytes_per_second)
        {
            return Err("native I/O controls differ from selected scope".into());
        }
    }
    Ok(())
}
pub fn validate_reconfiguration(old: &Policy, new: &Policy) -> Result<()> {
    for (system, resources) in &old.io_limits {
        for (resource, limit) in resources {
            let next = new.io_limits.get(system).and_then(|r| r.get(resource));
            let identity_changed = !native::same_resource_identity(
                &old.resources.get(resource).and_then(|p| p.native.clone()),
                &new.resources.get(resource).and_then(|p| p.native.clone()),
            ) || old.cpu_aggregates.get(system)
                != new.cpu_aggregates.get(system);
            if next.is_none() || identity_changed {
                if limit.read_bytes_per_second.is_some() || limit.write_bytes_per_second.is_some() {
                    return Err("clear selected native I/O limits and verify before retiring/rebinding their owner".into());
                }
                verify_controls(old, system)?;
            }
        }
    }
    Ok(())
}
