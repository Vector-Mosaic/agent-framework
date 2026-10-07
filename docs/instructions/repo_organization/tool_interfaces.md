# Repository tool interfaces

This document owns shared repository-tool interface conventions. Implementation
and consumer qualification remain facts at their actual owners.

## Purpose and scope

Repository tools must teach agents enough to choose and invoke them correctly
without repeated failures or source archaeology. Preserve a predictable safe
front door, meaningful domain commands/selectors, complete invocation information
and clear effects/results. Learning one tool should help with comparable tools;
unlike jobs can still have different semantics.

This covers stable repository-built executable entrypoints used by agents,
humans, CI, runbooks or other automation, including system-local CLIs. Import-only
libraries and test helpers do not need a CLI. Existing third-party tools retain
their native contracts; a repo wrapper still owns the wrapper's behavior and
effects. The convention is not a requirement to wrap every tool or rewrite the
whole catalog before using it.

Use [capability guidance](../practice/using_capabilities.md) for discovering,
qualifying and operating tools in context. The catalog routes a job to a tool;
tool help describes callable behavior; a runbook explains a larger procedure.
This document owns their shared interface conventions, not particular tool
authority, provider grants or system operations.

## Discovering and learning a tool

If the exact owner is known, go directly to its documented safe first call.
Otherwise search the [canonical catalog](../../../scripts/tool_catalog.manifest.json)
through [tool_catalog_query.py](../../../scripts/tools/tool_catalog_query.py):

```text
python scripts/tools/tool_catalog_query.py search --root . "task continuity"
python scripts/tools/tool_catalog_query.py show --root . tool.ops.codex_thread_sketchpad
python scripts/tools/tool_catalog_query.py commands --root . tool.ops.codex_thread_sketchpad
python scripts/tools/tool_catalog_query.py risks --root . tool.ops.codex_thread_sketchpad
```

Search matches declared purpose, commands, aliases and tags, favoring exact
identity and distinct relevant terms over repeated words or incidental paths.
Its explanation and safe first call help assess a match. It is lexical search,
not a claim of semantic or negation understanding. No match is not proof that
a capability is absent; use owner docs, targeted file discovery or source when
needed. Unknown/legacy status does not automatically erase a useful narrow
capability.

For a repo-owned tool, the normal safe handshake is `usage`, with a structured
`usage --format json` equivalent. Safe no-argument usage is desirable when that
is the tool's actual contract; do not guess it for an unfamiliar or legacy
entrypoint. Read the catalog's declared `safe_no_arg`/first call. PowerShell
tools can retain their declared native spelling such as positional `usage` or
`-Mode usage` and `-Format json`. Do not invent flags from another tool's example.

The handshake itself performs no repository, workstation or remote mutation:
no login, installation, process launch, profile creation, credential refresh or
provider probe just to explain use. It must not depend on production credentials
or service availability. Help and parser errors must be safe and useful too.
Runtime/status commands may observe a provider according to their actual
contract; they are not the safe discovery handshake.

Discover once to adequate current understanding. Still-applicable knowledge is
reusable; help is not a mandatory ritual before every known command. Catalog
presence does not establish installation, access, authorization, fitness or
success. Establish only what the contemplated action materially depends on.

## Complete command information

Text and JSON usage expose equivalent essential information. A compact overview
routes into command-specific detail rather than dumping every command or hiding
important parameters in a flat global list. A caller needs to recover:

| Information | What it must make clear |
|---|---|
| Identity and job | Tool ID, purpose, executable entrypoint, safe first call and owned docs. |
| Invocation | Actual verb/syntax, required/optional selectors and parameters, types, defaults, valid choices and combinations. |
| Conditions | Relevant prerequisites, environment/target selection, configured access and applicable action authority. |
| Effects | Reads/writes, local/remote effects, consequential resource use, output locations, and what preview/dry-run actually covers. |
| Results | Available text/JSON representations, success/pending/partial/unknown meanings and material result fields. |
| Failures and recovery | Useful error meanings, retry safety and the next action for ambiguous effects. |
| Examples | Valid, representative commands whose targets and effects agree with the description. |

Do not expose secret values, private profile data or sensitive payloads in help,
examples or errors. Refer to safe keys and custody owners. Give examples that
teach the owned interface rather than brittle parser internals. Existing use
examples must change with removed commands and retired tool references.

Use meaningful domain verbs and selectors: `show --id ...`, `status --runtime-id
...` or a tool's exact workstream selector should identify the thing callers
actually mean. Prefer a common verb where it conveys the job; use a domain verb
when clearer. A generic `run` must not conceal a destructive operation. Existing
syntax stays compatible until its actual consumers are deliberately migrated.
Do not require every tool to adopt the same domain vocabulary or a universal
adapter classification.

For changed parsers, make common-option placement predictable and accurately
document accepted syntax. Order independence is useful when safely supported;
do not claim it from a catalog flag that the parser does not honor. Fix the
owned parser/help mismatch rather than teaching users to retry unexplained
combinations. Preview, planning and dry-run behavior must state real limits:
an estimate or local plan is not evidence that the provider will accept or
complete the operation.

## Shared representations and authoring

Retain the existing shared owners:

- [Python handshake helper](../../../repo_platform/tooling/tool_handshake.py).
- [Usage schema](../../../repo_platform/schemas/tool_handshake/tool_usage.v1.schema.json)
  and [result schema](../../../repo_platform/schemas/tool_handshake/tool_result.v1.schema.json).

`tool_usage.v1` retains `schema_version`, `tool_id`, `status`, `purpose`,
`entrypoint`, `safe_first_calls`, `verbs`, `selectors`, `output_formats`,
`side_effects`, `examples`, `docs` and `errors`. The optional additive
`command_details` array supplies individual command records without invalidating
older consumers. A record names its command and can supply syntax, parameters,
constraints, prerequisites, effects, authority, errors and examples. Parameters
carry their names, required state and summary with useful aliases, types,
defaults, choices and cardinality. The schema and actual helper declarations own
the exact machine shape; this prose explains how to use it.

Python's `argparse_command_details(parser, semantics=...)` derives safely declared
syntax/inputs/types/defaults/choices and parser combinations. It does not execute
parser actions or discover effects by running commands. Authors supply semantics
that parser declarations cannot know: effects, prerequisites, authority,
constraints, errors and examples. Tools written in another language can declare
the same schema-defined information through their own supported implementation;
this package does not require PowerShell or ship a second-language wrapper merely
to imitate Python. Shared text rendering must preserve the essential content
rather than omit information available in JSON.

Use parsers or shared declarations as the source for mechanical facts where
reliable. Keep one owned source for authored semantic facts. Do not build a
second parser, maintain divergent help tables, or scrape arbitrary tool output
to guess authority. Both renderers can derive their views from the same payload.
Consumers may select available command detail without a new universal command
selector; use only selectors the tool actually exposes.

For example, a restore command may have a required target, mutually exclusive
source selectors and a default mode. The parser can describe those shapes. The
author must explain whether that mode writes data, what source is accepted,
whether an interrupted restore may have partially applied, and how to inspect
the result before retry. Merely listing `--target` and `--source` is not enough
to make the operation predictable. This is illustrative, not a new restore tool.

`tool_result.v1` supplies the existing general envelope: `schema_version`,
`tool_id`, `status`, `verb`, `summary`, `data`, `warnings` and `errors`. Preserve a
system-specific result contract where it already serves real consumers; no
blanket response conversion is required. Exit code, structured result and text
summary must agree about the claim they actually make. A command accepting a
job reports acceptance, not downstream completion. An error should identify the
smallest useful safe next step, including an unknown-effect state when known
failure cannot be established.

## Catalog and local authority

`scripts/tool_catalog.manifest.json` is the canonical tool discovery index.
Its `version: 1`, `kind: tool_catalog` and `tools` collection retain their
consumed shapes. Records preserve stable ID, name, purpose/description,
category, entrypoints, safe invocation and useful owner/docs information.
Categories/tags aid discovery; they do not create authority or require a
classification campaign for every Python file.

Catalog `entrypoints[].path` is relative to `scripts/`. Catalog commands retain
`cmd` and optional `cwd`, with omitted `cwd` or `REPO_ROOT` selecting the repo
root. That differs from component-manifest command cwd; see
[repository structure](repository_structure.md#component-manifests-and-native-metadata).
Preserve those real reader contracts when adding projections.

`agent_interface` describes the preferred entrypoint, safe bootstrap/usage,
known no-argument behavior, grammar, verbs/selectors, formats and relevant
preview support. Existing effect metadata informs choice; it does not authorize
effects. Retired migration/audit labels, validator thresholds and adapter forms
are not obligations to recreate. Preserve consumer-needed fields until their
callers are handled.

Tool-local declarations own callable details. The catalog can retain projections
needed by current consumers, derived from declared safe metadata where practical.
Runbooks own larger operating procedures and credential/access boundaries. A
selected projection refresh preserves unrelated entries and never invokes all
catalog tools merely to inventory them. Update touched discovery and docs with
the owning change; no new refresh daemon or handshake validator is required.

## Effects, output and evolution

Command names and descriptions must make consequential effects obvious. Read,
diagnose, plan and preview commands should not unexpectedly mutate state; any
declared report-output write or other necessary effect must be explicit. Use
real operation-specific authority and fit protection for mutation. Tool access,
a danger label or a dry-run result grants no new permission, and standing
authority need not be repeatedly requested.

Bound console output without hiding failures or altering exit codes. Route large
needed logs to declared outputs and expose useful failure context. Use owned
temporary/output locations and [artifact placement](repository_structure.md#working-outputs-and-ignored-files),
protect secrets and retain enough information to diagnose the real effect.
Output truncation must not turn an unknown result into success.

Improve a touched tool and shared renderers where that solves a real usability
problem. Preserve current entrypoint, argument/result semantics and system
security boundaries unless the authorized change handles actual consumers.
Compatibility work is governed by [changing software](../practice/changing_software.md).
Do not add wrappers merely to make a surface appear compliant, silently rename
selectors, or invent a new tool framework. Confirm representative actual users
such as catalog queries, platform discovery and owning system clients continue
to receive the information/contracts they use. No all-tool rewrite or universal
execution sweep follows from this convention.
