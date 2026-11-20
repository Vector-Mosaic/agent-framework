# Linux capacity coordination

Agents sharing a Linux host can ask whether their combined workloads fit before
starting them. Each workload declares its demand and checks again at launch.
The coordinator records which allocations are waiting, what they need, and which
holds or other commitments prevent them from starting. The host operator chooses
the resources, workloads and priorities to enroll.

## Install and discover

From the repository root, build with Cargo and put the installed executable on
your `PATH`. The crate uses the Rust 2024 edition and was tested with Rust 1.97.1.

```sh
cargo install --locked --path repo_platform/capacity
apeiros-capacity usage
```

To read the same command information before building, run
`python scripts/tools/capacity.py usage --format json`. The Python entrypoint can
also use an installed binary on `PATH` or the absolute path in
`APEIROS_CAPACITY_EXECUTABLE`.

## Try an allocation

This Bash example uses `jq`, Linux procfs, and a new private directory. It reads
actual host RAM counters, books 1 MiB for a one-shot demonstration, and launches
`/bin/true`. The example keeps the policy and allocation records in the
temporary directory. A failed run leaves those records available for inspection.

```bash
(
  set -euo pipefail
  demo_root=$(mktemp -d /tmp/apeiros-capacity-demo.XXXXXXXX)
  export APEIROS_CAPACITY_STATE_ROOT="$demo_root"
  echo "Demo state: $demo_root"
  cap() { apeiros-capacity request; }

  ram_bytes=$(awk '/^MemTotal:/ {printf "%.0f", $2 * 1024}' /proc/meminfo)
  jq -n --arg machine "$(cat /etc/machine-id)" --argjson ram "$ram_bytes" '{
    action:"initialize", policy:{host_id:"demo", machine_id:$machine, resources:{
      ram:{unit:"bytes", capacity:$ram, operational_margin:0,
           priority_growth_margin:0, observation_max_age_ms:60000, native:{kind:"memory"}}
    }}
  }' | cap
  echo '{"action":"observe_native"}' | cap

  job='{"allocation_id":"demo-1","host_id":"demo","system_id":"example",
    "owner_ref":"demo-shell","operation_id":"example-job","attempt_id":"attempt-1",
    "source_ref":"/bin/true","configuration_ref":"demo-policy","priority":"normal",
    "demand":{"ram":1048576},"eligibility_ref":"local one-shot example",
    "release_route":"demo-shell observes foreground exit"}'
  job_action() { jq -n --arg a "$1" --argjson job "$job" '{action:$a, request:$job}' | cap; }

  # A hold scoped to this workload prevents admission.
  hold_rev=$(echo '{"action":"hold","hold":{"hold_id":"demo-hold","owner_ref":"demo-shell",
    "reason":"demonstrate hold","scope":{"host_id":"demo","system_ids":["example"]},
    "revision":0}}' | cap | jq -er '.data.hold.revision')
  job_action assess | jq '.data.assessment | {can_admit, reasons, hold_ids}'
  jq -n --argjson r "$hold_rev" '{action:"release_hold", hold_id:"demo-hold",
    owner_ref:"demo-shell", revision:$r}' | cap

  # Only proceed if the allocation fits. Dispatch checks its revision and holds again.
  echo '{"action":"observe_native"}' | cap
  alloc_rev=$(job_action admit_if_fit | jq -er 'select(.data.assessment.can_admit)
    | .data.allocation.revision')
  apeiros-capacity dispatch --allocation-id demo-1 --revision "$alloc_rev" \
    --step-id run-once -- /bin/true

  alloc_rev=$(echo '{"action":"inspect","view":"allocation","allocation_id":"demo-1"}' \
    | cap | jq -er '.data.allocation.revision')
  alloc_rev=$(jq -n --argjson r "$alloc_rev" '{action:"reconcile_step", allocation_id:"demo-1",
    owner_ref:"demo-shell", revision:$r, step_id:"run-once", outcome:"applied",
    evidence_ref:"foreground /bin/true returned success"}' | cap | jq -er '.data.allocation.revision')
  jq -n --argjson r "$alloc_rev" --arg boot "$(cat /proc/sys/kernel/random/boot_id)" '{
    action:"release", allocation_id:"demo-1", revision:$r, evidence:{
      owner_ref:"demo-shell", attempt_id:"attempt-1", boot_id:$boot, continuation_ref:null,
      execution_exited_ref:"foreground child exited zero",
      resources_released_ref:"one-shot child exited with no descendants",
      no_successor_ref:"demo-shell started one process and scheduled no successor"}
  }' | cap | jq -e '.data.released'
  rm -r -- "$demo_root"
)
```

For real work, choose a durable absolute state directory shared by participating
tools under the same trusted account. Initialize it once with the host's actual
machine identity, resource measurements, observation lifetimes and selected
margins. Choose the workload's minimum useful demand and any peaks that coincide.
Native CPU observation needs two samples from the same boot. Requests for native
CPU or I/O bandwidth also need an enrolled cgroup v2 aggregate.
Native GPU memory accounting uses NVIDIA tooling and an explicitly selected GPU
UUID. Missing or stale measurements can make a request wait.

Use `assess` for a read-only fit check, `admit_if_fit` for an immediate decision,
or `admit` to retain an eligible request in the queue. A supervisor that starts
a workload in its own cgroup can pass `--supervisor-ref` at dispatch to bind the
actual process and hierarchy. The workload owner verifies exit, checkpoint and
resource release as appropriate, then releases the original allocation. After
an interrupted launch or lost reply, inspect the original allocation and step
before deciding whether another action is needed.

## Connect agents to a resident

Set `APEIROS_CAPACITY_STATE_ROOT` to the initialized absolute state directory.
Run `apeiros-capacity serve` under that account's supervisor. The resident
samples enrolled native resources and reconciles any explicitly configured
native controls and workload pressure callbacks. Authorized clients can submit
the same JSON requests over its private Unix socket.

```sh
curl --unix-socket "$APEIROS_CAPACITY_STATE_ROOT/capacity.sock" \
  --json '{"action":"overview"}' http://localhost/v1/capacity
```

For an MCP client, set the executable to `apeiros-capacity`, the argument to
`mcp`, and pass the same absolute state root in its environment. The stdio
client forwards live operations to the resident. Start with `{"action":"overview"}`
for current allocations and waits, or `{"action":"help","operation":"admit"}`
for the input schema and effects. The [request schema](../repo_platform/capacity/request.schema.json)
and [HTTP contract](../repo_platform/capacity/openapi.json) cover the remaining operations.
