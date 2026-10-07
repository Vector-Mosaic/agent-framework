# Repository structure

This document owns repository layout and placement conventions.

## Purpose and placement

Organize source by the system that owns its meaning and behavior, so an agent can
find the responsible code, interfaces, configuration, operating knowledge and
consumers without reconstructing the repository. Shared placement serves an
actual shared responsibility. A convenient folder or a repeated filename does
not establish ownership.

New source, design and durable ownership for this toolkit stay in this
repository unless the user explicitly requests a separate repository. Choose
between a system and a subsystem by their actual responsibilities; neither
choice implies a separate Git repository. Runtime installations, large research
data, credentials and provider-managed resources retain their distinct
authorized storage boundaries.

The root [repository README](../../../README.md) and [docs index](../../INDEX.md)
are the broad entrypoints. Adopting multi-system repositories can maintain a
`docs/systems/INDEX.md` for their own system inventory. Known owners
can be reached directly. These conventions apply with the framework's
[semantic ownership and representation](../framework/Conceptual_Model.md#semantic-governance-representation-lifecycle-and-migration),
the [system-documentation roles](system_documentation.md), and the
[changing-software guide](../practice/changing_software.md).

| Location | Responsibility |
|---|---|
| `systems/<system>/` | System-owned services, applications, libraries, contracts, configuration and operational assets. |
| `docs/` | Canonical long-form instructions, system knowledge, purpose and operational procedures. Code-folder READMEs point here. |
| `repo_platform/` | Shared importable implementation, reusable schemas/contracts and tooling libraries with actual shared consumers. |
| `infra/` | Shared environments, infrastructure definitions and deployment/orchestration assets. |
| `scripts/` | Stable repository automation entrypoints and their existing supported implementation. Reusable implementation belongs at its system or shared library owner. |
| `artifacts/` | Predictable temporary work and derived outputs, with ownership and retention governed by their actual use. |
| `.github/` | GitHub configuration and workflows. |
| `third_party/` | Deliberately vendored dependencies/assets and their required attribution and update information. |
| `examples/` | Synthetic configuration and runnable examples, with no real account records or credentials. |
| `tests/` | Focused checks for the public toolkit and supported platforms. |
| `templates/` | Installation templates and example instruction entrypoints where setup consumes them. |

This public toolkit uses `repo_platform/` for shared Python implementation,
`scripts/tools/` for stable commands, `docs/` for the complete instructions and
operating knowledge, and `tests/` and `examples/` for qualification and adoption.
Native packaging metadata remains at the root. The wider `systems/` and `infra/`
arrangement describes how the method scales to an adopting repository; it is not
a claim that those directories or deployed systems exist in this toolkit.

Update this layout owner deliberately before introducing a new top-level folder.
The table does not require creating empty directories or moving every historical
file. Existing configured runtime/state locations keep their owners. Retiring an
output root includes its recreation routes; a similarly named domain concept is
a different subject and keeps its system meaning.

## Systems and components

System slugs use `snake_case`. Service and application directory names use
`kebab-case`. Preserve established component identity when moving source; a path
change is not permission to redefine its purpose or public behavior.

The normal arrangement is:

```text
systems/<system>/
  system.manifest.json
  services/<service>/
    service.manifest.json
    ops/docker/Dockerfile
  apps/<app>/
    app.manifest.json
  packages/<package>/
    package.manifest.json
  contracts/
  config/
  ops/
docs/systems/<system>/
  INDEX.md
  ...
```

Only create the components and directories the system actually needs. A service
may be an API, worker, scheduler or another deployed process. An app is a
user-facing application such as web, desktop or mobile software. A package is an
imported library, not an independently deployed service. A system may have no
deployables. Subsystems express meaningful internal boundaries; use the
[subsystem documentation rule](system_documentation.md#subsystems) without
turning each code folder into another system.

Deployable-specific Dockerfiles belong at that deployable's
`ops/docker/Dockerfile`. System-local operational assets stay within the system;
shared infrastructure references them rather than maintaining copies. Long-form
operating procedures belong in the system's `Operations/` or `docs/ops/`.

## Shared platform and infrastructure

Keep implementation in its owning system until a real shared job warrants
promotion. Multiple consumers, an accepted cross-system contract, or common
importable tooling can justify `repo_platform/`; merely imagining future reuse
does not. Sharing needs clear semantic ownership and dependencies, not a
numerical adoption test or an abstract promise of permanence.

Use established shared-platform subdirectories where their roles fit. This
toolkit supplies `repo_platform/tooling/`, `schemas/` and credential implementation;
adopting repositories may also need `packages/` or `contracts/`. Deployables remain
in systems. Stable executable front
doors remain in `scripts/`; they can call shared importable tooling. An adopting
repository may keep a platform catalog where it serves actual shared-asset consumers; this toolkit needs no second inventory beyond its
[tool catalog](../../../scripts/tool_catalog.manifest.json). Catalog placement
does not make a system-specific implementation shared.

`infra/` contains such things as shared environment definitions, IaC,
multi-system orchestration, CI support, or shared observability infrastructure.
It does not mirror the full systems tree. One deployment's configuration stays
with that deployment, even when shared infrastructure refers to it. An infra
domain may carry a narrower system-specific section when orchestration needs it.

Discover existing platforms and authorized account/access routes through the
adopting project's actual catalogs and runbooks before adding another lane. Keep
those owned records current when infrastructure changes. The public toolkit's
[credential handling](../../credentials.md) supplies custody and handoff, not
provider accounts or permission to operate them. Catalog presence establishes
neither live access nor deployment success. Use relevant evidence when a decision
depends on current topology; there is no universal fresh coverage/drift audit per
edit.

Templates may describe configuration and required secret names. Actual secrets
stay in their owning custody mechanism. Infrastructure definitions must make
target and consequential effects legible; the
[capability-use guide](../practice/using_capabilities.md) and exact system/provider
procedure govern execution authority and protection.

## Component manifests and native metadata

When an adopting project needs multi-system component discovery, use the JSON
component family: `system.manifest.json`,
`service.manifest.json`, `app.manifest.json`, and `package.manifest.json`.
These manifests make identity, ownership, location and real entrypoints discoverable.
They do not require this toolkit to invent a system tree or duplicate native
Python packaging metadata.
Native metadata such as `package.json`, language workspace files and lockfiles
keeps its own job. Reference native commands instead of copying every dependency,
script and runtime setting into a repository manifest. This convention does not
force native configuration or interface descriptions into JSON.

| Record | Information to provide |
|---|---|
| All four component kinds | `version: 1`, the matching `kind`, stable `id`, and `name`. Existing consumers and owned schemas govern their concrete representation. |
| System | A meaningful `description`; `deployables` with actual service/app references; applicable package and shared-resource routes. Empty component collections are valid. |
| Service/app | The actual `type` and useful supported `commands`, along with relevant configuration/interface/runtime pointers. There is no universal required dev/start pair. |
| Package | Identity and useful language/import, command and dependency information not already adequately supplied by native metadata. |

Existing ID patterns are `sys.<system>`, `svc.<system>.<service>`,
`app.<system>.<app>`, and `pkg.<system>.<package>` (or `pkg.platform.<package>`).
Preserve referenced IDs across incidental path/name changes. System component
references use `{ "id", "name", "path" }`, with `type` when useful. Their paths
identify the component directory. Optional owner/docs, dependency, interface,
data and configuration pointers should contribute real discovery information;
unused fields and invented commands add drift.

Commands retain `commands.<name>.cmd` and optional `cwd`. New or deliberately
changed component command records use forward-slash paths relative to the
manifest directory; omitted `cwd` means that directory. For example, in a
service's manifest, `{ "cmd": "pnpm test", "cwd": "." }` runs the service's
native test entrypoint. It is a selectable command, not a requirement to run it
after every change. Other new component-local path references use the same
manifest-relative convention unless the owning field explicitly defines another
base. A documentation reference with a declared repo-relative base keeps it.

Before normalizing an old path or `cwd`, inspect the interpreter that actually
consumes it. Some existing commands have repo-relative content despite the old
prose. Migrate record and interpreter together; do not silently reinterpret all
old records. The tool catalog has a distinct established base: entrypoint paths
are relative to `scripts/`, while omitted command `cwd`/`REPO_ROOT` selects the
repository root. See [tool interfaces](tool_interfaces.md#catalog-and-local-authority).

For a new library-only system, this is a useful minimal system record:

```json
{
  "version": 1,
  "kind": "system",
  "id": "sys.example_analysis",
  "name": "example_analysis",
  "description": "Reusable analysis models owned by this system.",
  "deployables": { "services": [], "apps": [] },
  "packages": [
    { "id": "pkg.example_analysis.core", "name": "core", "path": "packages/core" }
  ]
}
```

This example explains identity and placement; it does not establish that the
illustrated package exists. Existing specialized manifests retain their own
consumed schemas. Optional extensions can carry real specialized meaning.
Readiness labels, interface assessment forms and retired enforcement fields do
not become new requirements. Remove obsolete metadata with its actual callers;
do not discard useful semantic content just because it shares an extension
container. An instruction change alone does not change a deployed reader.

Evolve consumed formats coherently. Additive optional fields can remain version
1 when consumers tolerate them; a breaking interpretation needs coordinated
reader/writer migration and an appropriate version boundary. Do not introduce a
new major or second manifest format merely to reorganize this documentation.
Existing mirrors survive only for an actual consumer, with unambiguous authoring
ownership; there is no general duty to create a YAML mirror.

## Working outputs and ignored files

Use a task-specific directory under `artifacts/` for temporary reports, probes,
logs, exports and packaging output when no existing operational owner specifies
another location. Put final useful source, docs and evidence at the owner that
will maintain or rely on it. Production behavior must not depend on an incidental
task artifact; a durable runtime input needs a declared owning location.

Persistent policy-blocked cleanup reminders belong in the maintained clone's
ignored `artifacts/pending_cleanup/` folder, outside cleanup targets and
disposable task/worktree roots. Use one `<task>-<purpose>.cleanup-pending.json`
per cleanup set, including host-qualified remote/outside-repository targets.
Protect sensitive metadata. The
[changing-software owner](../practice/changing_software.md#policy-blocked-temporary-cleanup)
owns required creation, discovery, rechecking and closure; a marker does not
authorize deletion or turn this folder into an automatic cleanup service.

Ignore dependency installs, caches, build output, local secret files and
disposable tool/browser profiles using the applicable root or component ignore
file. Before desktop/JS packaging, account for `node_modules/`, `dist/`,
`dist-artifacts/`, `win-unpacked/` and builder byproducts; portable bundles belong
in ignored artifact locations. Preserve trackable source and necessary published
artifacts. An ignore rule is not deletion authority.

Creators close out their disposable output, retaining useful results and
protecting active/shared inputs and unfinished work. The
[changing-software guide](../practice/changing_software.md) owns that lifecycle;
this document owns placement. Do not recreate retired output roots beneath
`artifacts/` or treat all system-generated/scientific data as disposable.
