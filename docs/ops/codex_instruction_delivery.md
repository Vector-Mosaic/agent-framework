# Home instruction delivery

The framework [orientation kernel](../instructions/framework/README.md#orientation-kernel)
is the single authored source for generated home guidance. Repository `AGENTS.md`
owns this checkout's repository-specific conventions. Home guidance points to
complete owners and the tools; it does not copy the full conceptual model into
every task or create a second conceptual authority.

`python scripts/tools/codex_instruction_delivery.py usage --format json` describes
the complete CLI. The [setup guide](../setup.md) covers installation on Windows
and Linux, profile/state selection and continuity use.

## Source and destination

The renderer extracts exactly one `codex-home-kernel:start` / `end` pair from
`docs/instructions/framework/README.md`. It binds `REPO_ROOT`, `CODEX_HOME` and
`BACKUP_ROOT` to explicit absolute paths and `PYTHON_COMMAND` to the actual
interpreter running the installer. Emitted commands use PowerShell call syntax
on Windows and POSIX shell quoting on Linux, preserving a selected virtual
environment instead of assuming `python` exists on PATH. Missing or unknown placeholders and
malformed markers are errors. `--source-root` selects the source checkout;
`--repo-root` selects the maintained framework checkout whose owners/tools will
be referenced. These roots normally agree. Selecting a different destination
binding does not copy or validate the existence of a checkout on another host.
Run installation on the destination host using its own actual paths.

`preview` renders the whole resulting home file without writing anything.
`--operation install` is its default; `--operation update` and `--operation remove`
show those operations before they are applied. Preview output can contain your
existing personal instructions, so treat it as local configuration data.

## Managed ownership

`install` writes only the selected home `AGENTS.md`, the adjacent
`agent-framework-install.json`, and a temporary installation lock. It appends a
managed block while preserving all original bytes, including Unicode, BOM and
line endings outside the block. A repeated identical install is safe; a different
existing installation requires an explicit update. No Codex configuration, app
database, session, repository instructions or unrelated home file is rewritten.

`update` regenerates the block from the current authored source. The stored block
hash ensures edits inside the block are never silently discarded. Put personal
additions outside the markers; change public defaults at their actual authored
owner rather than maintaining a hidden edited generated copy.

`remove` deletes the unchanged block and its metadata. It restores surrounding
bytes, retaining additions made outside the block since installation. It does
not delete continuity records, credentials or user configuration. If installation
created `AGENTS.md` and no other material remains, removal deletes that file;
pre-existing empty files remain present.

The installer refuses symbolic-link destination files, malformed or duplicate
markers, unrecognized metadata, and conflicts with `AGENTS.override.md`. It
checks that destination bytes have not changed since preparation. A local lock
serializes toolkit installation operations. Atomic file replacement prevents
partially written home guidance during an ordinary write; metadata-write failure
attempts to restore the preceding guidance if it still owns the changed bytes.
This is not a transaction against unrelated editors or sudden power loss across
the two files. Do not edit the same home file during installation.

## Diagnosis and recovery

- If an override shadows the target, decide deliberately whether to retain that
  override or migrate its content into ordinary home guidance. The installer
  never makes that policy decision or alters the override automatically.
- If the managed block was edited, inspect the changes and move user additions
  outside its markers. Restore the authored generated block only after preserving
  the edits you want. An explicit source update can then replace it.
- If metadata or markers are missing after interruption, preserve the current
  home file and compare it with the previous preview/source before repairing the
  exact managed section and its metadata. The tool refuses automatic adoption of
  unowned markers; deleting metadata is not a supported reset.
- If `.agent-framework-install.lock` remains after a terminated process, establish
  that no installer is running for that home, then remove that exact stale lock.
  Do not use age alone to infer that an active operation has finished.

No unattended synchronization or global cleanup service is needed. Refresh
installed guidance intentionally after source changes and inspect the result.
The legacy option name `--backup-root` selects continuity storage, not a separate
automatic backup service. Physical disk-failure protection requires the user's
[explicit selection](../instructions/practice/using_capabilities.md#physical-disk-failure-backups-require-explicit-selection)
of its scope; installing or using continuity does not select that precaution.

## What successful installation establishes

A successful command establishes that the selected files contain the prepared
managed guidance. A fresh task must still demonstrate the intended client's
instruction discovery. Home and project instructions combine according to that
client's own supported discovery/precedence rules. An already-running task may
retain earlier context; no automatic context renewal or native app mutation is
claimed by this installer.

## Qualify entry and continuation

After an instruction change, host move or relevant client change, inspect the
actual profile, workspace and native discovery rules. Include personal home
guidance, the rendered managed block, repository root and applicable nested
instructions. Overrides, file permissions and working directory can change what
is selected. Measure the complete assembled content against the effective
client capacity rather than considering the kernel alone.

Some clients expose a byte limit such as Codex's `project_doc_max_bytes`; that is
a client-specific setting, not a token budget or a portable recommended value.
Inspect the installed client's actual behavior. Do not copy another machine's
limit, silently truncate essential meaning or shorten instructions merely to meet
an arbitrary quota. This installer does not edit client configuration.

Use a representative fresh task and available native readback to establish which
sources and complete bodies actually reached the agent. File presence, a loader
setting and a model's summary are different evidence from an exposed assembly.
For a continuation claim, check the relevant compaction, resume or delegation
boundary with the actual selected content. Initial entry alone does not establish
renewal, and another installation's successful observation does not qualify this
public installer, every profile or every runtime.

When a body is absent, shadowed or truncated, diagnose the exact source/profile
and repair within authority. Preserve the current task's work and recover the
relevant complete owners before dependent reliance. Manual recovery can support
continued work without proving automatic native restoration. A missing resident
kernel still needs restoration through the selected native route or the exact
planned, justified, isolated and temporary exception at the
[orientation owner](../instructions/framework/Conceptual_Model.md#recoverable-orientation-and-stable-foundations)
before dependent work. Manual recovery of full owners alone does not satisfy
automatic kernel delivery. Incorporate source
at a safe boundary; do not restart unrelated operations, replace private task
state or add an injection hook to manufacture a delivery claim.
