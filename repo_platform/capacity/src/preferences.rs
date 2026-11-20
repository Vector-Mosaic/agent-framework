//! An explicit system preference on a particular native GPU. This selection is
//! independent of resource amounts, reservations and operating-system priority.
use crate::*;
use std::os::unix::fs::MetadataExt;

const FILE: &str = "resource-preferences.json";
const MAX_BYTES: usize = 65_536;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GpuPreference {
    pub system_id: String,
    pub gpu_uuid: String,
}

impl GpuPreference {
    fn matches(&self, key: &str, policy: &Policy) -> bool {
        policy.cpu_aggregates.contains_key(&self.system_id)
            && matches!(policy.resources.get(key).and_then(|r| r.native.as_ref()),
                Some(native::Resource::GpuMemory { uuid }) if *uuid == self.gpu_uuid)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub instance_id: String,
    pub host_id: String,
    pub revision: u64,
    pub resources: BTreeMap<String, GpuPreference>,
    pub change_ref: String,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "schema")]
enum Stored {
    #[serde(rename = "apeiros.capacity.resource_preferences.v1")]
    V1(Selection),
}

impl Selection {
    pub(crate) fn validate_resources(&self, policy: &Policy) -> Result<()> {
        for (key, pref) in &self.resources {
            if !pref.matches(key, policy) {
                return Err(format!(
                    "resource preference requires the enrolled system and exact native GPU: {key}"
                ));
            }
        }
        Ok(())
    }
}

impl State {
    pub fn preference_revision(&self) -> u64 {
        self.resource_preferences.as_ref().map_or(0, |s| s.revision)
    }

    pub fn preferred_system_for_resource(&self, key: &str) -> Option<&str> {
        let pref = self.resource_preferences.as_ref()?.resources.get(key)?;
        // A legacy policy writer can rebind a device without consulting this
        // file. A retained preference never takes effect on a different GPU.
        pref.matches(key, &self.policy)
            .then_some(pref.system_id.as_str())
    }
}

impl Store {
    pub fn configure_resource_preferences(
        &mut self,
        instance: &str,
        policy_revision: u64,
        revision: u64,
        resources: BTreeMap<String, GpuPreference>,
        change_ref: String,
    ) -> Result<bool> {
        if instance != self.state.instance_id
            || policy_revision != self.state.policy_revision
            || revision != self.state.preference_revision()
        {
            return Err("stale preference owner, physical policy or preference revision".into());
        }
        nonempty(&change_ref, "resource preference change reference")?;
        if self
            .state
            .resource_preferences
            .as_ref()
            .map_or(resources.is_empty(), |s| {
                s.resources == resources && s.change_ref == change_ref
            })
        {
            return Ok(false);
        }
        let selection = Selection {
            instance_id: instance.to_owned(),
            host_id: self.state.policy.host_id.clone(),
            revision: revision
                .checked_add(1)
                .ok_or("preference revision overflow")?,
            resources,
            change_ref,
        };
        selection.validate_resources(&self.state.policy)?;
        let stored = Stored::V1(selection);
        if serde_json::to_vec_pretty(&stored)
            .map_err(|e| e.to_string())?
            .len()
            > MAX_BYTES
        {
            return Err("resource preference selection exceeds 64 KiB".into());
        }
        scheduling::write_policy(&self.root, FILE, &stored)?;
        let Stored::V1(selection) = stored;
        self.state.resource_preferences = Some(selection);
        Ok(true)
    }
}

pub(crate) fn read(root: &Path, state: &State) -> Result<Option<Selection>> {
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(root.join(FILE))
    {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.to_string()),
    };
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file()
        || metadata.mode() & 0o077 != 0
        || metadata.uid() != root.metadata().map_err(|e| e.to_string())?.uid()
        || metadata.len() > MAX_BYTES as u64
    {
        return Err(
            "resource preference selection must be a private owner file of at most 64 KiB".into(),
        );
    }
    let mut bytes = Vec::new();
    file.take(MAX_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > MAX_BYTES {
        return Err("resource preference selection exceeds 64 KiB".into());
    }
    let Stored::V1(selection) = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    if selection.host_id != state.policy.host_id
        || selection.instance_id != state.instance_id
        || selection.revision == 0
        || selection.change_ref.trim().is_empty()
    {
        return Err("invalid resource preference identity or revision".into());
    }
    Ok(Some(selection))
}
