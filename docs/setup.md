# Setup on Windows and Linux

The same Python tools and instruction files run on both platforms. Use Python
3.11 or newer. `python` below means the selected Python interpreter; on Linux,
use `python3` if your installation does not provide `python`. Node.js and browser
runtime requirements apply only when you enable the credential/browser integration.
See [credential setup](credentials.md) for those requirements.

Keep this checkout at a stable location: installed guidance links back to its
complete framework and tools. There is no background service or automatic source
synchronization. Updating the checkout and updating installed instructions are
separate deliberate actions.

## Start without installing anything

Obtain the public source:

```sh
git clone https://github.com/Vector-Mosaic/agent-framework.git
cd agent-framework
```

From that repository root:

```text
python scripts/tools/tool_catalog_query.py search "continuity"
python scripts/tools/codex_instruction_delivery.py usage --format json
python scripts/tools/codex_instruction_delivery.py preview --format json
```

`preview` reads the authored guidance and selected destination, but writes no
files. It shows the exact resulting home `AGENTS.md`, including guidance already
there. Review the paths and instructions before installation. No account or
credential enrollment is needed for continuity or instruction delivery.

## Select your profile and private storage

| Setting | Selection order |
|---|---|
| Codex home | `--codex-root`, then `CODEX_HOME`, then `~/.codex` |
| Continuity storage | `--backup-root`, then `AGENT_FRAMEWORK_STATE_HOME`, then the platform default below |
| Framework owner/tool paths | `--repo-root`, then the `--source-root` checkout; source defaults to this script's checkout |

The default continuity storage is `%LOCALAPPDATA%/AgentFramework` on Windows and
`$XDG_STATE_HOME/agent-framework` on Linux, falling back to
`~/.local/state/agent-framework` when `XDG_STATE_HOME` is absent. Paths passed to
the installer must be absolute. Existing `CODEX_HOME` and credential configuration
are preserved; the installer does not rewrite shell settings or `config.toml`.

For consistent settings across invocations, set environment variables in your
own shell profile or pass the two root options explicitly every time. For example:

PowerShell:

```powershell
$env:CODEX_HOME = Join-Path $HOME '.codex'
$env:AGENT_FRAMEWORK_STATE_HOME = Join-Path $env:LOCALAPPDATA 'AgentFramework'
python scripts/tools/codex_instruction_delivery.py preview
```

Linux shell:

```sh
export CODEX_HOME="$HOME/.codex"
export AGENT_FRAMEWORK_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}/agent-framework"
python3 scripts/tools/codex_instruction_delivery.py preview
```

Changing these paths later does not move existing records. Move them deliberately
or keep explicit bindings to the old records; don't silently create a second
continuity store for the same tasks.

## Install, update and remove

```text
python scripts/tools/codex_instruction_delivery.py install
python scripts/tools/codex_instruction_delivery.py preview --operation update
python scripts/tools/codex_instruction_delivery.py update
python scripts/tools/codex_instruction_delivery.py preview --operation remove
python scripts/tools/codex_instruction_delivery.py remove
```

Installation appends one marked block to the selected Codex home's `AGENTS.md`
and writes `agent-framework-install.json` beside it. All existing bytes outside
the block stay intact. Keep your additions outside its markers. Updating replaces
only the unchanged managed block. Removing it preserves other instructions,
configuration, all task records and credential data; a home file created solely
for this block is removed if nothing else has been added to it.

An existing `AGENTS.override.md` can shadow `AGENTS.md`; install/update therefore
stop and explain the conflict. Decide how your own override should work first.
The tool neither replaces that file nor silently redirects the installation.
An edited managed block, unowned markers or conflicting install metadata also
stop the operation so your work is not overwritten. See the
[delivery procedure](ops/codex_instruction_delivery.md) for recovery details.

Start a fresh task in the intended client and confirm it loads the selected home
instructions and the common `AGENTS.md` entrypoint. Home guidance records the
actual interpreter used for installation (including a virtual environment),
using PowerShell invocation syntax on Windows and POSIX shell quoting on Linux.
Use that binding for the continuity commands in the common entrypoint. File installation alone does not
prove native instruction loading or model behavior. No hook, automatic message
injection or Codex database modification is installed.

Within your deliberately adopted scope, the full framework and practical guides
accompany work across hosts, delegation and projectless contexts. Local host and
project rules keep their actual conditions. After moving hosts or changing the
client, inspect the effective instruction sources, combined size and capacity,
then check relevant continuation or compaction behavior before relying on it.
The [delivery and renewal procedure](ops/codex_instruction_delivery.md#qualify-entry-and-continuation)
explains the evidence and recovery boundaries. No fixed host path or client
capacity value is installed by this guidance.

## Keep continuity for a real task

Use the exact task UUID supplied by your client. Do not choose the newest task
as a substitute. The examples below use a fictional UUID only to show syntax;
replace it with your task's actual identity.

```text
python scripts/tools/codex_thread_sketchpad.py check --thread-id 11111111-1111-4111-8111-111111111111
python scripts/tools/codex_thread_todo.py check --thread-id 11111111-1111-4111-8111-111111111111
```

On first acquisition, read existing records; a complete already-delivered body
or check excerpt satisfies that read. Reuse retained current understanding and
recover only missing or changed content. If both body and metadata are absent,
`activate --expect-absent` creates and pins an empty sketchpad or an empty
Now / Next / Parked to-do. An explicitly intended body may instead be supplied
with `write --expect-absent --body-file <file> --pin` or
`write --expect-absent --stdin --pin`. For existing records, use the observed
`mutation_token` as `--expected <token>` on every mutation. To pin an existing
body, `pin --thread-id <id> --expected <token>` preserves its contents. A to-do body should contain
`## Now`, `## Next` and `## Parked`, containing only unfinished work and
discussions. Remove completed items and obsolete detail after updating remaining
dependents. Generated Notes/Metadata and completion bookkeeping do not belong.
Keep the sketchpad empty unless the user asks to retain an idea or it is actually
needed for continued development or reasoning. After authorized verified promotion,
clear the promoted material completely, including pointers and completion remnants.
`usage --format json` lists the editing commands. To-do append is retired; use a
coherent write or the action commands. Useful unfinished legacy material must be
reconciled before normalization, never silently discarded.
For example, after observing one exact task with `check --format json`:

```text
python scripts/tools/codex_thread_sketchpad.py write --thread-id <actual-id> --expected <observed-token> --body-file <file>
python scripts/tools/codex_thread_sketchpad.py outline --thread-id <actual-id> --format json
python scripts/tools/codex_thread_sketchpad.py read --thread-id <actual-id> --section-ref <observed-reference> --max-chars 2000 --format json
```

Retain the actual source revision and use `next_cursor` for subsequent pages.
Changed sources or stale edit bases are refused. Reconcile them before preparing
a new action; never automatically refetch a token and replay an old edit. A
partial publication needs exact readback and, when metadata and pin state are
known, `repair --expected <observed-token>`. See the full
[conditional-update and upgrade contract](instructions/agent_workflow/README.md#conditional-updates-and-recovery).

Both tools also accept `CODEX_THREAD_ID` (and compatibility environment names
`CODEX_THREAD_UUID` and `OPENAI_CODEX_THREAD_ID`). Exact IDs and environment
selection need no Codex session-index file. Title/latest selectors are optional
conveniences tied to a readable local `session_index.jsonl` format; they are not
necessary for ordinary operation and cannot establish the identity of your task.

“Pinned” refers to this toolkit's local registry of active continuity records.
It does not mean a Codex sidebar pin. The records are ordinary private Markdown,
metadata and registry files under the chosen continuity storage, not part of
application conversation state. Document locking and revision checks protect
participating tools from applying stale prepared edits; registry locking also
protects independent task entries. Reads carry identity and coverage with the
body and may create lock files. Default text readback is concise; select needed
JSON fields rather than printing an entire envelope beside the same body.
Older copied tools and arbitrary editors do not acquire these locks; finish their
use before upgrading. This remains task-owned continuity, not a shared editor.

## Optional capabilities

Use the [tool catalog](../scripts/tool_catalog.manifest.json) for included tools
and [capability guidance](instructions/practice/using_capabilities.md) for their
boundaries. Credential delivery requires user-owned account configuration and
local protected custody. Concurrent-development support requires a Git repository
and deliberately selected source bindings. Protocol Runner remains a separately
installed integration; adopting instructions does not start orchestration or
create remote accounts.
