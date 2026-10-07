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

From the repository root:

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
instructions and the repository's `AGENTS.md`. The installed continuity commands
bind the actual interpreter used for installation (including a virtual environment),
using PowerShell syntax on Windows and POSIX shell syntax on Linux. File installation alone does not
prove native instruction loading or model behavior. No hook, automatic message
injection or Codex database modification is installed.

## Keep continuity for a real task

Use the exact task UUID supplied by your client. Do not choose the newest task
as a substitute. The examples below use a fictional UUID only to show syntax;
replace it with your task's actual identity.

```text
python scripts/tools/codex_thread_sketchpad.py check --thread-id 11111111-1111-4111-8111-111111111111
python scripts/tools/codex_thread_todo.py check --thread-id 11111111-1111-4111-8111-111111111111
```

Read existing records. If the body is missing, write the intended body and pin
it using `write --body-file <file> --pin`, or `write --stdin --pin`. If an existing
body is unpinned, `pin --thread-id <id>` preserves it. A to-do body should contain
`## Now`, `## Next` and `## Parked`; completed items belong in the durable result,
not a running completion diary. `usage --format json` lists the editing commands.

Both tools also accept `CODEX_THREAD_ID` (and compatibility environment names
`CODEX_THREAD_UUID` and `OPENAI_CODEX_THREAD_ID`). Exact IDs and environment
selection need no Codex session-index file. Title/latest selectors are optional
conveniences tied to a readable local `session_index.jsonl` format; they are not
necessary for ordinary operation and cannot establish the identity of your task.

“Pinned” refers to this toolkit's local registry of active continuity records.
It does not mean a Codex sidebar pin. The records are ordinary private Markdown,
metadata and registry files under the chosen continuity storage, not part of
application conversation state. One task owns its body; registry locking protects
independent task entries, not concurrent edits to the same Markdown body.

## Optional capabilities

Use the [tool catalog](../scripts/tool_catalog.manifest.json) for included tools
and [capability guidance](instructions/practice/using_capabilities.md) for their
boundaries. Credential delivery requires user-owned account configuration and
local protected custody. Concurrent-development support requires a Git repository
and deliberately selected source bindings. Protocol Runner remains a separately
installed integration; adopting instructions does not start orchestration or
create remote accounts.
