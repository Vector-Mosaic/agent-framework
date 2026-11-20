//! Bounded native resource readback. Failure stays local to its resource.
use crate::*;
use std::ffi::CString;
use std::os::unix::fs::MetadataExt;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Resource {
    Memory,
    Cpu {
        eligible_cpus: Vec<u32>,
    },
    GpuMemory {
        uuid: String,
    },
    /// Exact mount binding; no directory walk or data-content inspection.
    Filesystem {
        mountpoint: String,
        source: String,
    },
    IoBandwidth {
        device: storage::DeviceBinding,
        capacity_evidence_ref: String,
    },
}
pub fn same_resource_identity(a: &Option<Resource>, b: &Option<Resource>) -> bool {
    match (a, b) {
        (
            Some(Resource::IoBandwidth { device: a, .. }),
            Some(Resource::IoBandwidth { device: b, .. }),
        ) => a == b,
        _ => a == b,
    }
}
pub fn validate_policy(policy: &Policy) -> Result<()> {
    let resources: Vec<_> = policy
        .resources
        .iter()
        .filter_map(|(id, p)| p.native.as_ref().map(|n| (id, p, n)))
        .collect();
    for (i, (id, p, n)) in resources.iter().enumerate() {
        if p.unit
            != match n {
                Resource::Cpu { .. } => "millicores",
                Resource::IoBandwidth { .. } => "bytes_per_second",
                _ => "bytes",
            }
        {
            return Err(format!("native resource has wrong unit: {id}"));
        }
        if let Resource::Cpu { eligible_cpus } = n {
            let unique: BTreeSet<_> = eligible_cpus.iter().collect();
            if unique.len() != eligible_cpus.len() || eligible_cpus.is_empty() {
                return Err("eligible CPU set must be nonempty and unique".into());
            }
        }
        if let Resource::IoBandwidth {
            capacity_evidence_ref,
            ..
        } = n
        {
            nonempty(capacity_evidence_ref, "actual I/O capacity evidence")?;
        }
        for (other, _, m) in resources.iter().skip(i + 1) {
            let overlap = match (n, m) {
                (Resource::Memory, Resource::Memory) => true,
                (Resource::Cpu { eligible_cpus: a }, Resource::Cpu { eligible_cpus: b }) => {
                    a.iter().any(|cpu| b.contains(cpu))
                }
                (Resource::GpuMemory { uuid: a }, Resource::GpuMemory { uuid: b }) => a == b,
                (
                    Resource::Filesystem { source: a, .. },
                    Resource::Filesystem { source: b, .. },
                ) => {
                    a == b
                        || fs::canonicalize(a)
                            .ok()
                            .zip(fs::canonicalize(b).ok())
                            .is_some_and(|(a, b)| a == b)
                }
                (
                    Resource::IoBandwidth { device: a, .. },
                    Resource::IoBandwidth { device: b, .. },
                ) => {
                    a == b
                        || storage::device(a)
                            .ok()
                            .zip(storage::device(b).ok())
                            .is_some_and(|(a, b)| a.physical_device == b.physical_device)
                }
                _ => false,
            };
            if overlap {
                return Err(format!(
                    "resource identities overlap physical capacity: {id}, {other}"
                ));
            }
        }
    }
    for (system, group) in &policy.cpu_aggregates {
        nonempty(system, "system aggregate")?;
        nonempty(&group.control_owner_ref, "sole CPU writer")?;
        if !group.cgroup.starts_with('/')
            || group.cgroup == "/"
            || group.cgroup.split('/').any(|s| s == ".." || s == ".")
        {
            return Err("invalid aggregate hierarchy".into());
        }
        if policy
            .cpu_aggregates
            .iter()
            .any(|(other, b)| other != system && nested(&group.cgroup, &b.cgroup))
        {
            return Err("system aggregate hierarchies overlap".into());
        }
    }
    Ok(())
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CpuSample {
    pub boot_id: String,
    pub monotonic_ms: u64,
    pub ticks: BTreeMap<u32, (u64, u64)>,
    pub managed_usage_usec: BTreeMap<String, u64>,
    pub managed_processes: BTreeMap<String, Process>,
}
fn cpu_sample(bindings: &[(&String, &Allocation, &RuntimeBinding)]) -> Result<CpuSample> {
    let mut time = std::mem::MaybeUninit::<libc::timespec>::uninit();
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, time.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let time = unsafe { time.assume_init() };
    let mut sample = CpuSample {
        boot_id: boot_id()?,
        monotonic_ms: (time.tv_sec as u64) * 1000 + (time.tv_nsec as u64) / 1_000_000,
        ticks: BTreeMap::new(),
        managed_usage_usec: BTreeMap::new(),
        managed_processes: BTreeMap::new(),
    };
    for line in fs::read_to_string("/proc/stat")
        .map_err(|e| e.to_string())?
        .lines()
    {
        let fields: Vec<_> = line.split_whitespace().collect();
        let Some(cpu) = fields
            .first()
            .and_then(|s| s.strip_prefix("cpu"))
            .and_then(|s| s.parse::<u32>().ok())
        else {
            continue;
        };
        let ticks: Vec<u64> = fields
            .iter()
            .skip(1)
            .take(8)
            .map(|s| s.parse::<u64>().map_err(|e| e.to_string()))
            .collect::<Result<_>>()?;
        if ticks.len() < 5 {
            return Err("incomplete CPU statistics".into());
        }
        sample
            .ticks
            .insert(cpu, (ticks.iter().sum(), ticks[3].saturating_add(ticks[4])));
    }
    for (id, _, b) in bindings {
        let data = fs::read_to_string(format!("/sys/fs/cgroup{}/cpu.stat", b.cgroup))
            .map_err(|e| e.to_string())?;
        if let Some(usage) = data
            .lines()
            .find_map(|s| s.strip_prefix("usage_usec "))
            .and_then(|s| s.parse::<u64>().ok())
        {
            sample.managed_usage_usec.insert((*id).clone(), usage);
            sample
                .managed_processes
                .insert((*id).clone(), b.process.clone());
        }
    }
    Ok(sample)
}
fn cpu_controls(path: &Path, priority: Priority, apply: bool) -> Result<serde_json::Value> {
    let idle = if priority == Priority::Normal {
        "1"
    } else {
        "0"
    };
    let mut writes = vec![];
    if apply {
        if fs::read_to_string(path.join("cpu.idle"))
            .map_err(|e| e.to_string())?
            .trim()
            != idle
        {
            fs::write(path.join("cpu.idle"), idle).map_err(|e| e.to_string())?;
            writes.push("cpu.idle");
        }
        // Native idle scheduling owns its effective weight. Kernel versions
        // may report different weights for idle groups.
        // Writing 100 while idle can clear/reset native idle mode or churn it.
        if priority == Priority::Priority
            && fs::read_to_string(path.join("cpu.weight"))
                .map_err(|e| e.to_string())?
                .trim()
                != "100"
        {
            fs::write(path.join("cpu.weight"), "100").map_err(|e| e.to_string())?;
            writes.push("cpu.weight");
        }
    }
    let weight = fs::read_to_string(path.join("cpu.weight")).map_err(|e| e.to_string())?;
    let current_idle = fs::read_to_string(path.join("cpu.idle")).map_err(|e| e.to_string())?;
    Ok(
        serde_json::json!({"cpu_weight":weight.trim(),"cpu_idle":current_idle.trim(),
        "expected_weight":if priority==Priority::Priority {Some("100")}else{None},
        "expected_idle":idle,"weight_role":if priority==Priority::Priority {"equal priority share"}else{"native idle readback; no independent weight requirement"},
        "matches":current_idle.trim()==idle && (priority==Priority::Normal || weight.trim()=="100"),"writes":writes}),
    )
}

pub fn cpu_policy(policy: &Policy, apply: bool) -> serde_json::Value {
    let mut outcomes = vec![];
    for (system, g) in &policy.cpu_aggregates {
        let result = (|| -> Result<serde_json::Value> {
            nonempty(&g.control_owner_ref, "sole CPU control owner")?;
            if !g.cgroup.starts_with('/')
                || g.cgroup == "/"
                || g.cgroup.split('/').any(|s| s == "." || s == "..")
            {
                return Err("exact aggregate cgroup required".into());
            }
            if policy
                .cpu_aggregates
                .iter()
                .any(|(other, x)| other != system && nested(&g.cgroup, &x.cgroup))
            {
                return Err("overlapping system aggregate cgroups".into());
            }
            let path = PathBuf::from(format!("/sys/fs/cgroup{}", g.cgroup));
            cpu_controls(&path, g.priority, apply)
        })();
        outcomes.push(match result{Ok(data)=>serde_json::json!({"system_id":system,"cgroup":g.cgroup,"owner_ref":g.control_owner_ref,"ok":true,"data":data}),Err(e)=>serde_json::json!({"system_id":system,"cgroup":g.cgroup,"ok":false,"error":e,"possible_partial_effect":apply,"recovery":"inspect this exact hierarchy; do not assume earlier writes were undone"})});
    }
    serde_json::json!({"applied":apply,"outcomes":outcomes,"scope":"one selected writer per enrolled system aggregate; equal priority weight, normal native idle; no per-child shares or permanent quotas"})
}
pub fn verify_cpu_dispatch(policy: &Policy, system: &str, owner: &Process) -> Result<()> {
    let group = policy
        .cpu_aggregates
        .get(system)
        .ok_or("CPU system aggregate unavailable")?;
    if process(owner.pid)? != *owner {
        return Err("CPU dispatch process changed".into());
    }
    let actual = process_cgroup(owner.pid)?;
    if actual != group.cgroup && !actual.starts_with(&format!("{}/", group.cgroup)) {
        return Err("dispatch must occur within enrolled system aggregate hierarchy".into());
    }
    verify_cpu_policy(policy, system)
}
pub fn verify_cpu_policy(policy: &Policy, system: &str) -> Result<()> {
    let group = policy
        .cpu_aggregates
        .get(system)
        .ok_or("CPU system aggregate unavailable")?;
    let base = PathBuf::from(format!("/sys/fs/cgroup{}", group.cgroup));
    if cpu_controls(&base, group.priority, false)?["matches"] != true {
        return Err("enrolled CPU aggregate policy not effective; inspect sole writer".into());
    }
    Ok(())
}
pub fn nested(a: &str, b: &str) -> bool {
    a == b || a.starts_with(&format!("{b}/")) || b.starts_with(&format!("{a}/"))
}
fn cgroup_cpus_within(cgroup: &str, eligible: &[u32]) -> bool {
    inherited_cpus_within(
        &PathBuf::from(format!("/sys/fs/cgroup{cgroup}")),
        Path::new("/sys/fs/cgroup"),
        eligible,
    )
}
fn inherited_cpus_within(mut path: &Path, root: &Path, eligible: &[u32]) -> bool {
    if !path.starts_with(root)
        || path
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
    {
        return false;
    }
    let text = loop {
        // A vanished cgroup must not borrow its former parent's scope.
        if !fs::metadata(path).is_ok_and(|m| m.is_dir()) {
            return false;
        }
        match fs::read_to_string(path.join("cpuset.cpus.effective")) {
            Ok(text) => break text,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound && path != root => {
                let Some(parent) = path.parent() else {
                    return false;
                };
                // Controller files exist only where the parent enables that
                // controller. Otherwise descendants inherit its effective
                // scope, while cpu.stat still accounts their own usage.
                let Ok(controls) = fs::read_to_string(parent.join("cgroup.subtree_control")) else {
                    return false;
                };
                if controls.split_whitespace().any(|c| c == "cpuset") {
                    return false;
                }
                path = parent;
            }
            Err(_) => return false,
        }
    };
    if text.trim().is_empty() {
        return false;
    }
    text.trim().split(',').all(|part| {
        if let Some((a, b)) = part.split_once('-') {
            let (Ok(a), Ok(b)) = (a.parse::<u32>(), b.parse::<u32>()) else {
                return false;
            };
            a <= b && b - a <= 4096 && (a..=b).all(|cpu| eligible.contains(&cpu))
        } else {
            part.parse::<u32>().is_ok_and(|cpu| eligible.contains(&cpu))
        }
    })
}
fn process_cgroup(pid: u32) -> Result<String> {
    let data = fs::read_to_string(format!("/proc/{pid}/cgroup")).map_err(|e| e.to_string())?;
    data.lines()
        .find_map(|s| s.strip_prefix("0::").map(str::to_owned))
        .ok_or("cgroup v2 unavailable".into())
}
fn bound_processes_before_gpu_query(
    bindings: &[(&String, &Allocation, &RuntimeBinding)],
) -> BTreeMap<u32, (Process, String)> {
    let mut captured = BTreeMap::new();
    let mut paths: Vec<_> = bindings
        .iter()
        .map(|(_, _, b)| PathBuf::from(format!("/sys/fs/cgroup{}", b.cgroup)))
        .collect();
    let mut seen = 0;
    while let Some(path) = paths.pop() {
        seen += 1;
        if seen > 4096 || captured.len() > 16384 {
            break;
        }
        if let Ok(contents) = fs::read_to_string(path.join("cgroup.procs")) {
            for line in contents.lines() {
                if let Ok(pid) = line.parse::<u32>() {
                    if let (Ok(incarnation), Ok(cgroup)) = (process(pid), process_cgroup(pid)) {
                        captured.insert(pid, (incarnation, cgroup));
                    }
                }
            }
        }
        if let Ok(entries) = fs::read_dir(path) {
            for entry in entries.flatten() {
                if entry.file_type().is_ok_and(|t| t.is_dir()) {
                    paths.push(entry.path());
                }
            }
        }
    }
    captured
}
pub fn validate_binding(b: &RuntimeBinding) -> Result<()> {
    nonempty(&b.supervisor_ref, "supervisor reference")?;
    if b.cgroup == "/"
        || !b.cgroup.starts_with('/')
        || b.cgroup.split('/').any(|p| p == ".." || p == ".")
    {
        return Err("exact non-root host cgroup required".into());
    }
    if process(b.process.pid)? != b.process {
        return Err("runtime process incarnation changed".into());
    }
    let actual = process_cgroup(b.process.pid)?;
    if actual != b.cgroup && !actual.starts_with(&format!("{}/", b.cgroup)) {
        return Err("process does not belong to bound host hierarchy".into());
    }
    Ok(())
}
pub fn validate_adoption_binding(binding: &RuntimeBinding) -> Result<()> {
    validate_binding(binding)?;
    let root = PathBuf::from(format!("/sys/fs/cgroup{}", binding.cgroup));
    let kind = fs::read_to_string(root.join("cgroup.type")).map_err(|e| e.to_string())?;
    if !matches!(kind.trim(), "domain" | "domain threaded") {
        return Err(
            "adoption requires the whole native accounting domain, not a threaded leaf".into(),
        );
    }
    fs::read_to_string(root.join("memory.current"))
        .map_err(|e| e.to_string())?
        .trim()
        .parse::<u64>()
        .map_err(|e| format!("native domain memory counter unavailable: {e}"))?;
    let cpu = fs::read_to_string(root.join("cpu.stat")).map_err(|e| e.to_string())?;
    if !cpu.lines().any(|line| {
        line.strip_prefix("usage_usec ")
            .is_some_and(|value| value.parse::<u64>().is_ok())
    }) {
        return Err("native domain CPU counter unavailable".into());
    }
    let events = fs::read_to_string(root.join("cgroup.events")).map_err(|e| e.to_string())?;
    if !events.lines().any(|line| line == "populated 1") {
        return Err("existing native accounting domain is not populated".into());
    }
    // Reframe counters with the same live process/hierarchy; an exit after this
    // check remains a charged obligation until exact owner reconciliation.
    validate_binding(binding)
}
pub fn verify_runtime_exited(binding: &RuntimeBinding) -> Result<()> {
    if boot_id()? != binding.process.boot_id {
        return Ok(());
    }
    if process(binding.process.pid).is_ok_and(|p| p == binding.process) {
        return Err(
            "bound execution still exists; pause acceptance does not establish exit/release".into(),
        );
    }
    let events = PathBuf::from(format!("/sys/fs/cgroup{}/cgroup.events", binding.cgroup));
    match fs::read_to_string(events) {
        Ok(text) => {
            if !text.lines().any(|l| l == "populated 0") {
                return Err("bound host hierarchy remains populated; descendants/container/VM still consume allocation".into());
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(format!("cannot establish runtime release: {e}")),
    }
    Ok(())
}
fn memory() -> Result<(u64, u64)> {
    let contents = fs::read_to_string("/proc/meminfo").map_err(|e| e.to_string())?;
    let read = |key: &str| -> Result<u64> {
        contents
            .lines()
            .find_map(|line| line.strip_prefix(key))
            .and_then(|s| s.split_whitespace().next())
            .ok_or("meminfo key unavailable")?
            .parse::<u64>()
            .map(|n| n.saturating_mul(1024))
            .map_err(|e| e.to_string())
    };
    let total = read("MemTotal:")?;
    Ok((total, total.saturating_sub(read("MemAvailable:")?)))
}

fn memory_value(path: &std::path::Path) -> Result<u64> {
    fs::read_to_string(path)
        .map_err(|e| format!("{}: {e}", path.display()))?
        .trim()
        .parse::<u64>()
        .map_err(|e| format!("{}: {e}", path.display()))
}

/// Read every protection boundary, including ancestors outside the enrolled
/// aggregate. A positive boundary conservatively retains all cache: effective
/// hierarchical protection is not reconstructed from an incomplete snapshot.
fn memory_protections(root: &std::path::Path, mount: &std::path::Path) -> Result<Vec<u64>> {
    if root == mount || !root.starts_with(mount) {
        return Err("managed memory requires a non-root cgroup below its mount".into());
    }
    let mut values = Vec::new();
    let mut at = root;
    while at != mount {
        for name in ["memory.min", "memory.low"] {
            let path = at.join(name);
            let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
            values.push(if text.trim() == "max" {
                u64::MAX
            } else {
                text.trim()
                    .parse::<u64>()
                    .map_err(|e| format!("{}: {e}", path.display()))?
            });
        }
        at = at
            .parent()
            .ok_or("memory protection ancestor unavailable")?;
    }
    Ok(values)
}

fn memory_working_bytes(current: u64, stat: &str, protected: bool) -> Result<u64> {
    let mut values = BTreeMap::new();
    for line in stat.lines() {
        let mut fields = line.split_whitespace();
        let key = fields.next().ok_or("empty memory.stat row")?;
        let value = fields
            .next()
            .ok_or("missing memory.stat value")?
            .parse::<u64>()
            .map_err(|e| format!("invalid memory.stat {key}: {e}"))?;
        if fields.next().is_some() || values.insert(key, value).is_some() {
            return Err(format!("ambiguous memory.stat row: {key}"));
        }
    }
    let get = |key: &str| {
        values
            .get(key)
            .copied()
            .ok_or_else(|| format!("memory.stat missing {key}"))
    };
    let file = get("file")?;
    let shmem = get("shmem")?;
    let dirty = get("file_dirty")?;
    let writeback = get("file_writeback")?;
    let inactive = get("inactive_file")?;
    // Type counters and LRU counters overlap; never sum them. Retain all
    // anonymous/shared/dirty/writeback/active/unevictable memory and slab.
    // Dirty and writeback may overlap, so subtracting both only reduces credit.
    // Dirty/writeback pages could ALL be on the inactive list even when other
    // clean pages are active. Subtract them from the inactive intersection;
    // min(inactive, clean_file) alone could incorrectly credit dirty pages.
    let clean_inactive = inactive
        .min(file.saturating_sub(shmem))
        .saturating_sub(dirty)
        .saturating_sub(writeback);
    let credit = if protected {
        0
    } else {
        clean_inactive.min(current)
    };
    Ok(current - credit)
}

fn managed_memory(root: &std::path::Path, mount: &std::path::Path) -> Result<u64> {
    let protections = memory_protections(root, mount)?;
    let before = memory_value(&root.join("memory.current"))?;
    let stat_before = fs::read_to_string(root.join("memory.stat")).map_err(|e| e.to_string())?;
    let stat_after = fs::read_to_string(root.join("memory.stat")).map_err(|e| e.to_string())?;
    let after = memory_value(&root.join("memory.current"))?;
    if memory_protections(root, mount)? != protections {
        return Err("memory protection changed during native capture".into());
    }
    memory_working_between(
        before,
        &stat_before,
        after,
        &stat_after,
        protections.iter().any(|v| *v != 0),
    )
}

fn memory_working_between(
    before: u64,
    stat_before: &str,
    after: u64,
    stat_after: &str,
    protected: bool,
) -> Result<u64> {
    // These are bounded samples, not an atomic kernel snapshot. Keep the larger
    // observed charge and smaller observed cache credit, including composition
    // changes (cache replaced by anonymous pages at unchanged total). Host
    // baseline, observation age and margins remain necessary independent guards.
    let current = before.max(after);
    Ok(memory_working_bytes(current, stat_before, protected)?
        .max(memory_working_bytes(current, stat_after, protected)?))
}
fn query_gpu(query: &str) -> Result<String> {
    let mut child = Command::new("nvidia-smi")
        .arg(query)
        .arg("--format=csv,noheader,nounits")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    let start = Instant::now();
    loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            if !status.success() {
                return Err("native GPU query failed".into());
            }
            let mut result = String::new();
            child
                .stdout
                .take()
                .ok_or("missing GPU output")?
                .take(64 * 1024)
                .read_to_string(&mut result)
                .map_err(|e| e.to_string())?;
            return Ok(result);
        }
        if start.elapsed() > Duration::from_secs(3) {
            let _ = child.kill();
            let _ = child.wait();
            return Err("native GPU query timed out".into());
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

const GPU_PROCESSES: &str = "--query-compute-apps=gpu_uuid,pid,used_gpu_memory";
const GPU_DEVICE: &str = "--query-gpu=uuid,memory.total,memory.used";

struct GpuCapture {
    processes_before: String,
    devices: String,
    processes_after: String,
}

impl GpuCapture {
    fn read(mut query: impl FnMut(&str) -> Result<String>) -> Result<Self> {
        Ok(Self {
            processes_before: query(GPU_PROCESSES)?,
            devices: query(GPU_DEVICE)?,
            processes_after: query(GPU_PROCESSES)?,
        })
    }

    fn account(
        &self,
        uuid: &str,
        mut owner: impl FnMut(u32) -> Option<String>,
    ) -> Result<(u64, u64, Quantities)> {
        let mut device = self
            .devices
            .lines()
            .map(|line| line.split(',').map(str::trim))
            .find(|fields| fields.clone().next() == Some(uuid))
            .ok_or("bound GPU UUID unavailable")?
            .skip(1);
        let total = gpu_bytes(device.next())?;
        let used = gpu_bytes(device.next())?;
        if used > total {
            return Err("GPU used memory exceeds total".into());
        }
        let before = gpu_process_bytes(&self.processes_before, uuid)?;
        let after = gpu_process_bytes(&self.processes_after, uuid)?;
        let mut managed = Quantities::new();
        let mut external = used;
        for pid in before.keys().chain(after.keys()).collect::<BTreeSet<_>>() {
            let Some(id) = owner(*pid) else { continue };
            let first = before.get(pid).copied().unwrap_or(0);
            let last = after.get(pid).copied().unwrap_or(0);
            if first != last {
                return Err(format!(
                    "managed GPU memory changed during device capture for PID {pid}: {first} to {last} bytes"
                ));
            }
            external = external
                .checked_sub(last)
                .ok_or("managed GPU memory exceeds observed device use")?;
            *managed.entry(id).or_default() += last;
        }
        Ok((total, external, managed))
    }
}

fn gpu_bytes(field: Option<&str>) -> Result<u64> {
    field
        .ok_or("missing GPU memory")?
        .parse::<u64>()
        .map_err(|e| e.to_string())?
        .checked_mul(1024 * 1024)
        .ok_or_else(|| "GPU byte count overflow".into())
}

fn gpu_process_bytes(rows: &str, uuid: &str) -> Result<BTreeMap<u32, u64>> {
    let mut processes = BTreeMap::new();
    for row in rows.lines() {
        let mut fields = row.split(',').map(str::trim);
        if fields.next() != Some(uuid) {
            continue;
        }
        let pid = fields
            .next()
            .ok_or("missing GPU pid")?
            .parse::<u32>()
            .map_err(|e| e.to_string())?;
        if processes.insert(pid, gpu_bytes(fields.next())?).is_some() {
            return Err(format!("ambiguous GPU memory for PID {pid}"));
        }
    }
    Ok(processes)
}
fn filesystem(mountpoint: &str, source: &str) -> Result<(u64, u64)> {
    if !Path::new(mountpoint).is_absolute() {
        return Err("mountpoint must be absolute".into());
    }
    let mounts = fs::read_to_string("/proc/self/mountinfo").map_err(|e| e.to_string())?;
    let found = mounts.lines().any(|line| {
        let Some((before, after)) = line.split_once(" - ") else {
            return false;
        };
        let device_matches = after.split_whitespace().nth(1) == Some(source)
            || fs::metadata(source).ok().is_some_and(|m| {
                use std::os::unix::fs::FileTypeExt;
                m.file_type().is_block_device()
                    && before.split_whitespace().nth(2)
                        == Some(
                            format!("{}:{}", libc::major(m.rdev()), libc::minor(m.rdev())).as_str(),
                        )
            });
        before.split_whitespace().nth(4) == Some(mountpoint) && device_matches
    });
    if !found {
        return Err(
            "required exact mount/source unavailable; root-directory fallback forbidden".into(),
        );
    }
    let path = CString::new(mountpoint).map_err(|e| e.to_string())?;
    let mut data = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    if unsafe { libc::statvfs(path.as_ptr(), data.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let data = unsafe { data.assume_init() };
    Ok((
        data.f_blocks.saturating_mul(data.f_frsize),
        data.f_blocks
            .saturating_sub(data.f_bavail)
            .saturating_mul(data.f_frsize),
    ))
}
pub fn observe(state: &State) -> Observation {
    let capture_started_at_ms = now_ms();
    let boot = boot_id().unwrap_or_default();
    let mut external = Quantities::new();
    let mut unavailable = BTreeMap::new();
    let mut accounted = BTreeMap::new();
    let mut managed_demand: BTreeMap<String, Quantities> = BTreeMap::new();
    let bindings: Vec<_> = state
        .allocations
        .iter()
        .filter_map(|(id, a)| {
            a.runtime
                .as_ref()
                .filter(|b| validate_binding(b).is_ok())
                .map(|b| (id, a, b))
        })
        .collect();
    let cpu = cpu_sample(&bindings);
    let io = storage::sample(&state.policy, &bindings);
    let needs_gpu = state
        .policy
        .resources
        .values()
        .any(|p| matches!(p.native, Some(Resource::GpuMemory { .. })));
    let gpu_before = if needs_gpu {
        bound_processes_before_gpu_query(&bindings)
    } else {
        BTreeMap::new()
    };
    let gpu = needs_gpu.then(|| GpuCapture::read(query_gpu));
    for (key, p) in &state.policy.resources {
        let Some(native) = &p.native else {
            continue;
        };
        let value = (|| -> Result<(u64, u64)> {
            match native {
                Resource::Cpu { eligible_cpus } => {
                    if eligible_cpus.is_empty() {
                        return Err("explicit eligible CPU identities required".into());
                    }
                    let current = cpu.as_ref().map_err(Clone::clone)?;
                    let previous = state
                        .native_counters
                        .as_ref()
                        .filter(|p| {
                            p.boot_id == current.boot_id && p.monotonic_ms < current.monotonic_ms
                        })
                        .ok_or("second same-boot CPU sample required")?;
                    let elapsed = current.monotonic_ms - previous.monotonic_ms;
                    if elapsed < 100 {
                        return Err("CPU observation interval shorter than 100ms".into());
                    }
                    let mut total_ticks = 0u64;
                    let mut idle_ticks = 0u64;
                    for cpu in eligible_cpus {
                        let (a, b) = current.ticks.get(cpu).ok_or("eligible CPU unavailable")?;
                        let (c, d) = previous
                            .ticks
                            .get(cpu)
                            .ok_or("eligible CPU absent from prior sample")?;
                        total_ticks = total_ticks
                            .saturating_add(a.checked_sub(*c).ok_or("CPU counter reset")?);
                        idle_ticks = idle_ticks
                            .saturating_add(b.checked_sub(*d).ok_or("CPU idle counter reset")?);
                    }
                    if total_ticks == 0 {
                        return Err("CPU sample has no interval".into());
                    }
                    let total = (eligible_cpus.len() as u64) * 1000;
                    let used =
                        total.saturating_mul(total_ticks.saturating_sub(idle_ticks)) / total_ticks;
                    let mut managed = 0u64;
                    for (id, a, b) in &bindings {
                        if !a.request.demand.contains_key(key) {
                            continue;
                        }
                        if previous.managed_processes.get(*id) != Some(&b.process)
                            || !cgroup_cpus_within(&b.cgroup, eligible_cpus)
                        {
                            continue;
                        }
                        if let (Some(current), Some(old)) = (
                            current.managed_usage_usec.get(*id),
                            previous.managed_usage_usec.get(*id),
                        ) {
                            if let Some(delta) = current.checked_sub(*old) {
                                let use_milli = delta / elapsed;
                                managed = managed.saturating_add(use_milli);
                                accounted.insert((*id).clone(), a.revision);
                                managed_demand
                                    .entry((*id).clone())
                                    .or_default()
                                    .insert(key.clone(), use_milli);
                            }
                        }
                    }
                    Ok((total, used.saturating_sub(managed)))
                }
                Resource::Memory => {
                    let (total, used) = memory()?;
                    let mut managed = 0u64;
                    for (id, a, b) in &bindings {
                        if !a.request.demand.contains_key(key) {
                            continue;
                        }
                        let count = managed_memory(
                            &PathBuf::from(format!("/sys/fs/cgroup{}", b.cgroup)),
                            std::path::Path::new("/sys/fs/cgroup"),
                        )?;
                        managed = managed.saturating_add(count);
                        accounted.insert((*id).clone(), a.revision);
                        managed_demand
                            .entry((*id).clone())
                            .or_default()
                            .insert(key.clone(), count);
                    }
                    // Subtract the SAME working-set basis we publish for each
                    // managed allocation, not memory.current including cache.
                    // Active observed-memory accounting replaces lifetime
                    // forecasts with these SAME working sets. Adding external
                    // demand still charges at least host unavailable; pending
                    // or unattributed work keeps its full forecast separately.
                    Ok((total, used.saturating_sub(managed)))
                }
                Resource::GpuMemory { uuid } => {
                    let capture = gpu
                        .as_ref()
                        .ok_or("GPU capture missing")?
                        .as_ref()
                        .map_err(Clone::clone)?;
                    let (total, external, gpu_demand) = capture.account(uuid, |pid| {
                        let (before, before_cgroup) = gpu_before.get(&pid)?;
                        let cgroup = process_cgroup(pid).ok()?;
                        if &cgroup != before_cgroup || process(pid).ok().as_ref() != Some(before) {
                            return None;
                        }
                        bindings.iter().find_map(|(id, a, b)| {
                            (a.request.demand.contains_key(key)
                                && (cgroup == b.cgroup
                                    || cgroup.starts_with(&format!("{}/", b.cgroup))))
                            .then(|| (*id).clone())
                        })
                    })?;
                    for (id, usage) in gpu_demand {
                        accounted.insert(id.clone(), state.allocations[&id].revision);
                        managed_demand
                            .entry(id)
                            .or_default()
                            .insert(key.clone(), usage);
                    }
                    Ok((total, external))
                }
                Resource::Filesystem { mountpoint, source } => filesystem(mountpoint, source),
                Resource::IoBandwidth { .. } => {
                    // Throughput capacity is a selected measured service level,
                    // not a physical byte size discoverable from the device.
                    let used = storage::rate(
                        state,
                        &io,
                        key,
                        &bindings,
                        &mut accounted,
                        &mut managed_demand,
                    )?;
                    Ok((p.capacity, used))
                }
            }
        })();
        match value {
            Ok((total, used)) if total >= p.capacity => {
                external.insert(key.clone(), used);
            }
            Ok((total, _)) => {
                unavailable.insert(
                    key.clone(),
                    format!(
                        "observed capacity {total} is below configured {}",
                        p.capacity
                    ),
                );
            }
            Err(e) => {
                unavailable.insert(key.clone(), e);
            }
        }
    }
    // Native ownership can change while provider/counter reads run. Never
    // publish subtraction attributed to an exited/replaced/moved hierarchy.
    for (id, _, binding) in &bindings {
        if accounted.contains_key(*id) && validate_binding(binding).is_err() {
            if let Some(demands) = managed_demand.remove(*id) {
                for (key, _) in demands {
                    external.remove(&key);
                    unavailable.insert(
                        key,
                        "runtime incarnation or hierarchy changed during native capture".into(),
                    );
                }
            }
            accounted.remove(*id);
        }
    }
    Observation {
        host_id: state.policy.host_id.clone(),
        boot_id: boot,
        observed_at_ms: capture_started_at_ms,
        source_ref:
            "native Linux MemAvailable/cgroup-v2 clean-inactive-cache working set/NVIDIA/mountinfo statvfs; no content inspection".into(),
        external_demand: external,
        accounted_allocations: accounted,
        managed_demand,
        cpu_sample: cpu.ok(),
        io_sample: Some(io),
        unavailable,
    }
}

#[cfg(test)]
mod gpu_capture_tests {
    use super::*;

    const MIB: u64 = 1024 * 1024;

    // Drive the real query order with a device having an externally occupied
    // 1,930 MiB throughout, including the instant a managed process loads.
    fn capture_with_load(amounts: [(u64, u64); 3]) -> GpuCapture {
        let mut timeline = amounts.into_iter();
        let value = GpuCapture::read(|query| {
            let (managed, external) = timeline.next().unwrap();
            match query {
                GPU_DEVICE => Ok(format!("GPU-1, 24576, {}", managed + external)),
                GPU_PROCESSES => Ok(format!("GPU-1, 72, {managed}")),
                _ => panic!("unexpected query"),
            }
        })
        .unwrap();
        assert!(timeline.next().is_none());
        value
    }

    #[test]
    fn changing_managed_load_cannot_disappear_external_memory_or_trigger_release() {
        let before = (8_000, 700);
        let after = (12_096, 700);
        for window in [
            [before, before, after],
            [before, after, after],
            [after, before, before],
        ] {
            let capture = capture_with_load(window);
            let error = capture.account("GPU-1", |_| Some("renderer".into())).unwrap_err();
            assert!(error.contains("changed during device capture"), "{error}");
        }
        let stable = capture_with_load([after; 3]);
        let (total, external, managed) = stable.account("GPU-1", |_| Some("renderer".into())).unwrap();
        assert_eq!(
            (total, external, managed["renderer"]),
            (24_576 * MIB, 700 * MIB, 12_096 * MIB)
        );
        // A real change in external demand is immediately visible in a
        // coherent sample. No retained high-water mark or constant buffer.
        let released = capture_with_load([(12_096, 400); 3]);
        assert_eq!(
            released.account("GPU-1", |_| Some("renderer".into())).unwrap().1,
            400 * MIB
        );
    }

    #[test]
    fn only_attributed_gpu_processes_govern_capture_coherence() {
        let mut capture = GpuCapture {
            processes_before: "GPU-1, 42, 400
GPU-1, 43, 500
GPU-1, 71, 70
GPU-2, 90, 2"
                .into(),
            devices: "GPU-1, 2000, 1100
GPU-2, 3000, 50"
                .into(),
            processes_after: "GPU-1, 42, 400
GPU-1, 43, 500
GPU-1, 79, 170
GPU-2, 90, 5"
                .into(),
        };
        let owner = |pid| matches!(pid, 42 | 43).then(|| "indexer".to_owned());
        let (_, external, managed) = capture.account("GPU-1", owner).unwrap();
        assert_eq!((external, managed["indexer"]), (200 * MIB, 900 * MIB));
        capture.processes_after = "GPU-1, 43, 500".into();
        assert!(capture.account("GPU-1", owner).is_err());
        std::mem::swap(&mut capture.processes_before, &mut capture.processes_after);
        assert!(capture.account("GPU-1", owner).is_err());
        capture
            .processes_before
            .clone_from(&capture.processes_after);
        capture.devices = "GPU-1, 2000, 800".into();
        assert!(capture
            .account("GPU-1", owner)
            .unwrap_err()
            .contains("exceeds observed device use"));
    }
}

#[cfg(test)]
mod cpu_scope_tests {
    use super::*;

    struct Tree(PathBuf);
    impl Tree {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "capacity-cpu-scope-{}-{}",
                std::process::id(),
                fs::read_to_string("/proc/sys/kernel/random/uuid")
                    .unwrap()
                    .trim()
            ));
            fs::create_dir_all(root.join("aggregate/worker/child")).unwrap();
            fs::write(root.join("cpuset.cpus.effective"), "0-7\n").unwrap();
            fs::write(root.join("cgroup.subtree_control"), "cpuset cpu memory\n").unwrap();
            fs::write(root.join("aggregate/cpuset.cpus.effective"), "0-3\n").unwrap();
            fs::write(
                root.join("aggregate/cgroup.subtree_control"),
                "memory pids\n",
            )
            .unwrap();
            fs::write(
                root.join("aggregate/worker/cgroup.subtree_control"),
                "memory\n",
            )
            .unwrap();
            Self(root)
        }
    }
    impl Drop for Tree {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn cpu_scope_inherits_the_nearest_effective_controller() {
        let t = Tree::new();
        let worker = t.0.join("aggregate/worker");
        assert!(inherited_cpus_within(&worker, &t.0, &[0, 1, 2, 3]));
        assert!(inherited_cpus_within(
            &worker.join("child"),
            &t.0,
            &[0, 1, 2, 3]
        ));
        assert!(!inherited_cpus_within(&worker, &t.0, &[0, 1]));
        fs::write(worker.join("cpuset.cpus.effective"), "1-2\n").unwrap();
        assert!(inherited_cpus_within(&worker, &t.0, &[1, 2]));
        assert!(!inherited_cpus_within(&worker, &t.0, &[0, 1]));
    }

    #[test]
    fn cpu_scope_refuses_unknown_or_unexpected_missing_interfaces() {
        let t = Tree::new();
        let worker = t.0.join("aggregate/worker");
        let eligible = [0, 1, 2, 3];
        for invalid in ["", "0-", "3-1", "0,4", "0,", "garbage"] {
            fs::write(worker.join("cpuset.cpus.effective"), invalid).unwrap();
            assert!(!inherited_cpus_within(&worker, &t.0, &eligible));
        }
        fs::remove_file(worker.join("cpuset.cpus.effective")).unwrap();
        fs::create_dir(worker.join("cpuset.cpus.effective")).unwrap();
        assert!(!inherited_cpus_within(&worker, &t.0, &eligible));
        fs::remove_dir(worker.join("cpuset.cpus.effective")).unwrap();
        fs::write(
            t.0.join("aggregate/cgroup.subtree_control"),
            "cpuset memory",
        )
        .unwrap();
        assert!(!inherited_cpus_within(&worker, &t.0, &eligible));
        fs::remove_file(t.0.join("aggregate/cgroup.subtree_control")).unwrap();
        assert!(!inherited_cpus_within(&worker, &t.0, &eligible));
        assert!(!inherited_cpus_within(
            &worker.join("missing"),
            &t.0,
            &eligible
        ));
        assert!(!inherited_cpus_within(&t.0, &worker, &eligible));
    }

    #[test]
    fn cpu_scope_native_interval_reaches_running_use_accounting() {
        let process = process(std::process::id()).unwrap();
        let cgroup = process_cgroup(process.pid).unwrap();
        let cpus: Vec<_> = cpu_sample(&[]).unwrap().ticks.into_keys().collect();
        let policy: Policy = serde_json::from_value(serde_json::json!({
            "host_id":"native-cpu-fixture", "machine_id":machine_id().unwrap(),
            "resources":{"cpu":{"unit":"millicores","capacity":cpus.len() as u64 * 1000,
                "operational_margin":0,"priority_growth_margin":0,"observation_max_age_ms":60000,
                "native":{"kind":"cpu","eligible_cpus":cpus}}}
        }))
        .unwrap();
        let mut state = State::new(policy).unwrap();
        let request: Request = serde_json::from_value(serde_json::json!({
            "allocation_id":"self","host_id":"native-cpu-fixture","system_id":"fixture",
            "owner_ref":"fixture","operation_id":"native-interval","attempt_id":"one",
            "source_ref":"fixture","configuration_ref":"fixture","priority":"priority",
            "demand":{"cpu":1000},"eligibility_ref":"fixture","release_route":"fixture"
        }))
        .unwrap();
        state.allocations.insert(
            "self".into(),
            Allocation {
                request,
                phase: Phase::Active,
                revision: 4,
                queued_sequence: 1,
                admitted_sequence: Some(2),
                steps: BTreeMap::new(),
                release_evidence: None,
                adoption: None,
                runtime: Some(RuntimeBinding {
                    process,
                    cgroup,
                    supervisor_ref: "fixture".into(),
                }),
            },
        );
        state.scheduling = Some(scheduling::Configuration {
            schema: "apeiros.capacity.scheduling.v1".into(),
            instance_id: state.instance_id.clone(),
            host_id: state.policy.host_id.clone(),
            revision: 1,
            change_ref: "fixture".into(),
            routes: vec![],
            bookings: BTreeMap::new(),
            native_priorities: BTreeMap::new(),
            observe_running_cpu: true,
            observe_running_memory: false,
            demand_changes: BTreeMap::new(),
            reservations: None,
        });
        let boot = boot_id().unwrap();
        state.observe(observe(&state), now_ms(), &boot).unwrap();
        std::thread::sleep(Duration::from_millis(150));
        let sample = observe(&state);
        assert!(
            sample.unavailable.get("cpu").is_none(),
            "{:?}",
            sample.unavailable
        );
        assert_eq!(sample.accounted_allocations.get("self"), Some(&4));
        let observed = sample.managed_demand["self"]["cpu"];
        state.observe(sample, now_ms(), &boot).unwrap();
        assert_eq!(
            state.allocation_commitment(&state.allocations["self"], "cpu", now_ms(), &boot),
            observed
        );
    }
}

#[cfg(test)]
mod memory_tests {
    use super::*;
    use std::io::Write;

    fn counters(file: u64, inactive: u64, shmem: u64, dirty: u64, writeback: u64) -> String {
        format!(
            "file {file}\ninactive_file {inactive}\nshmem {shmem}\nfile_dirty {dirty}\nfile_writeback {writeback}\n"
        )
    }

    #[test]
    fn clean_inactive_cache_does_not_become_a_permanent_memory_commitment() {
        let gib = 1 << 30;
        assert_eq!(
            memory_working_bytes(21 * gib, &counters(20 * gib, 20 * gib, 0, 0, 0), false).unwrap(),
            gib
        );
        assert_eq!(
            memory_working_bytes(21 * gib, &counters(20 * gib, 20 * gib, 0, 0, 0), true).unwrap(),
            21 * gib
        );
    }

    #[test]
    fn dirty_shared_active_unevictable_and_slab_memory_keep_their_charge() {
        assert_eq!(
            memory_working_bytes(100, &counters(80, 60, 20, 20, 10), false).unwrap(),
            70
        );
        // Extra counters are explanatory, not additional credits. Inactive file
        // cannot claim active file, anonymous/shared memory or reclaimable slab.
        let stat = format!(
            "{}active_file 50\nanon 20\nunevictable 10\nslab_reclaimable 20\n",
            counters(60, 10, 0, 0, 0)
        );
        assert_eq!(memory_working_bytes(110, &stat, false).unwrap(), 100);
        assert_eq!(
            memory_working_bytes(100, &counters(50, 90, 20, 20, 30), false).unwrap(),
            100
        );
        // All inactive pages may be dirty while all clean pages are active.
        assert_eq!(
            memory_working_bytes(100, &counters(100, 50, 0, 50, 0), false).unwrap(),
            100
        );
        assert_eq!(
            memory_working_bytes(100, &counters(100, 50, 0, 0, 50), false).unwrap(),
            100
        );
        assert_eq!(
            memory_working_bytes(100, &counters(200, 200, 0, 0, 0), false).unwrap(),
            0
        );
    }

    #[test]
    fn missing_or_malformed_memory_counters_never_supply_optimistic_credit() {
        let valid = counters(80, 60, 0, 0, 0);
        for bad in [
            String::new(),
            valid.replace("file 80\n", ""),
            valid.replace("shmem 0", "shmem invalid"),
            format!("{valid}file 80\n"),
            format!("{valid}anon 18446744073709551616\n"),
            format!("{valid}anon 0 trailing\n"),
        ] {
            assert!(memory_working_bytes(100, &bad, false).is_err(), "{bad}");
            assert!(memory_working_bytes(100, &bad, true).is_err(), "{bad}");
        }
    }

    #[test]
    fn observed_cache_replacement_at_constant_total_keeps_anonymous_charge() {
        assert_eq!(
            memory_working_between(
                100,
                &counters(80, 80, 0, 0, 0),
                100,
                &counters(0, 0, 0, 0, 0),
                false
            )
            .unwrap(),
            100
        );
        assert_eq!(
            memory_working_between(
                100,
                &counters(0, 0, 0, 0, 0),
                100,
                &counters(80, 80, 0, 0, 0),
                false
            )
            .unwrap(),
            100
        );
        assert_eq!(
            memory_working_between(
                100,
                &counters(80, 80, 0, 0, 0),
                120,
                &counters(80, 80, 0, 0, 0),
                false
            )
            .unwrap(),
            40
        );
        assert!(memory_working_between(100, &counters(80, 80, 0, 0, 0), 100, "", false).is_err());
    }

    #[test]
    fn native_reader_preserves_ancestor_and_local_protections_and_errors() {
        let mount =
            std::env::temp_dir().join(format!("vm-memory-{}-{}", std::process::id(), now_ms()));
        let parent = mount.join("aggregate");
        let child = parent.join("worker");
        fs::create_dir_all(&child).unwrap();
        for dir in [&parent, &child] {
            for name in ["memory.min", "memory.low"] {
                fs::write(dir.join(name), "0\n").unwrap();
            }
        }
        fs::write(child.join("memory.current"), "100\n").unwrap();
        fs::write(child.join("memory.stat"), counters(80, 60, 0, 0, 0)).unwrap();
        assert_eq!(managed_memory(&child, &mount).unwrap(), 40);
        for dir in [&parent, &child] {
            for name in ["memory.min", "memory.low"] {
                for value in ["1", "max"] {
                    fs::write(dir.join(name), value).unwrap();
                    assert_eq!(managed_memory(&child, &mount).unwrap(), 100);
                }
                fs::write(dir.join(name), "broken").unwrap();
                assert!(managed_memory(&child, &mount).is_err());
                fs::write(dir.join(name), "0").unwrap();
            }
        }
        fs::remove_file(parent.join("memory.low")).unwrap();
        assert!(managed_memory(&child, &mount).is_err());
        fs::write(parent.join("memory.low"), "0").unwrap();
        fs::write(child.join("memory.current"), "max").unwrap();
        assert!(managed_memory(&child, &mount).is_err());
        assert!(memory_protections(&mount, &mount).is_err());
        fs::remove_dir_all(mount).unwrap();
    }

    /// Optional native fixture. The invocation owner selects an
    /// isolated accounting domain and APEIROS_CAPACITY_EXPECT_PROTECTED=0 or 1. This
    /// test reads cgroup settings; it never writes a limit/protection or reclaims
    /// anyone's cache. Its sole payload is a temporary 64-MiB file plus 32-MiB
    /// anonymous allocation, both removed when the fixture ends.
    #[test]
    #[ignore = "requires an explicitly selected isolated cgroup and filesystem"]
    fn native_memory_cache_and_anonymous_readback() {
        let expected = std::env::var("APEIROS_CAPACITY_EXPECT_PROTECTED").unwrap();
        assert!(matches!(expected.as_str(), "0" | "1"));
        let native_group = process_cgroup(std::process::id()).unwrap();
        let root = PathBuf::from(format!("/sys/fs/cgroup{native_group}"));
        let mount = std::path::Path::new("/sys/fs/cgroup");
        let protected = memory_protections(&root, mount)
            .unwrap()
            .iter()
            .any(|v| *v > 0);
        assert_eq!(protected, expected == "1");
        let mut anon = vec![1u8; 32 << 20];
        for page in anon.chunks_mut(4096) {
            page[0] = 17;
        }
        let path = std::env::current_dir()
            .unwrap()
            .join(format!(".memory-fixture-{}.tmp", std::process::id()));
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .unwrap();
        file.write_all(&anon).unwrap();
        file.write_all(&anon).unwrap();
        file.sync_all().unwrap();
        let current = memory_value(&root.join("memory.current")).unwrap();
        let stat = fs::read_to_string(root.join("memory.stat")).unwrap();
        let working = managed_memory(&root, mount).unwrap();
        let exact = memory_working_bytes(current, &stat, protected).unwrap();
        // Anonymous allocation remains charged in both cases. The retained
        // margin tolerates bounded native per-cpu counter sampling differences.
        assert!(working >= 32 << 20);
        if protected {
            assert_eq!(exact, current);
        } else {
            assert!(
                current.saturating_sub(exact) >= 48 << 20,
                "no representative clean inactive cache: {stat}"
            );
        }
        println!(
            "{}",
            serde_json::json!({"cgroup":native_group,"current":current,"working":working,"counter_working":exact,"protected":protected,"anonymous_bytes":anon.len(),"file_bytes":64 << 20})
        );
        assert_eq!(anon[0], 17);
        drop(file);
        fs::remove_file(path).unwrap();
    }
}

#[cfg(test)]
mod cpu_idle_tests {
    use super::*;
    #[test]
    fn already_idle_native_weights_are_accepted_without_any_write() {
        let root =
            std::env::temp_dir().join(format!("vmcpu-idle-{}-{}", std::process::id(), now_ms()));
        fs::create_dir(&root).unwrap();
        for value in ["0", "1"] {
            fs::write(root.join("cpu.idle"), "1").unwrap();
            fs::write(root.join("cpu.weight"), value).unwrap();
            for name in ["cpu.idle", "cpu.weight"] {
                fs::set_permissions(root.join(name), fs::Permissions::from_mode(0o444)).unwrap();
            }
            let outcome = cpu_controls(&root, Priority::Normal, true).unwrap();
            assert_eq!(outcome["matches"], true);
            assert_eq!(outcome["writes"], serde_json::json!([]));
            assert_eq!(outcome["expected_weight"], serde_json::Value::Null);
            for name in ["cpu.idle", "cpu.weight"] {
                fs::set_permissions(root.join(name), fs::Permissions::from_mode(0o600)).unwrap();
            }
        }
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn priority_restores_nonidle_and_equal_weight_and_is_then_idempotent() {
        let root = std::env::temp_dir().join(format!(
            "vmcpu-priority-{}-{}",
            std::process::id(),
            now_ms()
        ));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("cpu.idle"), "1").unwrap();
        fs::write(root.join("cpu.weight"), "1").unwrap();
        let first = cpu_controls(&root, Priority::Priority, true).unwrap();
        assert_eq!(first["matches"], true);
        assert_eq!(
            first["writes"],
            serde_json::json!(["cpu.idle", "cpu.weight"])
        );
        for name in ["cpu.idle", "cpu.weight"] {
            fs::set_permissions(root.join(name), fs::Permissions::from_mode(0o444)).unwrap();
        }
        let next = cpu_controls(&root, Priority::Priority, true).unwrap();
        assert_eq!(next["writes"], serde_json::json!([]));
        assert_eq!(next["matches"], true);
        fs::remove_dir_all(root).unwrap();
    }
}
