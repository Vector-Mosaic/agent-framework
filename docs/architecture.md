# Architecture and public adaptation

## One coherent framework

The framework describes how an agent should preserve the user's intended outcome,
authority, relevant context, judgment and continuity through real work. It is not
a scheduler or a replacement reasoning engine. Its complete owners are the
[intent and narrative](instructions/framework/Intent_And_Narrative.md),
[conceptual model](instructions/framework/Conceptual_Model.md) and
[relationship model](instructions/framework/Relationship_Model.md).

The practical guides apply those meanings to actual work. Repository conventions
describe how source, documents and tool interfaces are organized. System-specific
contracts, operating facts and grants retain their own scope. A short instruction
carrier routes attention to these owners; it does not supersede their meaning.

This public edition adapts one working personal framework for an adopting human
owner. It preserves the full explanations while replacing private installation
bindings with local configuration. It does not create a second authority layer
between a supposed generic framework and a personal variation. Human decisions
that the framework reserves for its owner remain human decisions.

## Component boundaries

| Source | Responsibility |
|---|---|
| `AGENTS.md` | The immediately applicable repository instructions and routes to full owners. |
| `docs/instructions/framework/` | Purpose, definitions, qualifications and conceptual relationships. |
| `docs/instructions/practice/` | Applied judgment in collaboration, change, capability use, verification and maintenance. |
| `docs/instructions/agent_workflow/` | Continuity, accepted decisions, promotion, concurrent work and independent challenge. |
| `docs/instructions/repo_organization/` | Placement and roles of source, system documentation and interfaces. |
| `scripts/tools/` | Small user-facing command entrypoints with discoverable usage. |
| `repo_platform/tooling/` | Shared task-overlay, configuration and command-contract implementation. |
| `repo_platform/credentials/` | Protected local custody and explicit delivery adapters. |
| `repo_platform/concurrent_development/` | Optional Git workspace binding and conditional integration. |
| `docs/integrations/` | Independently selected browser, clipboard, terminal and Runner integration conditions. |

Only operational prerequisites needed for a selected feature apply. Reading the
framework does not require a keyring, browser, service or Protocol Runner. The
included tool catalog describes tools actually present in this checkout; it is
not a copy of the originating environment's capability or account inventory.

## Instruction delivery and continuity

The renderer expands explicit installation bindings in the framework's home
orientation kernel. Setup uses native `AGENTS.md` discovery and preserves text
outside its managed section. There is no background synchronization daemon,
conversation injection hook or patched agent runtime. See [setup](setup.md).

Each task has an exact identity and its own sketchpad and to-do. Their bodies and
registry live under the selected private state root. Here, **pinned** means pinned
in the toolkit's registry, not in the Codex sidebar. The instructions tell the
agent to check and read them. The tool does not make an otherwise unavailable
task identity magically discoverable and does not write Codex's conversation
database. The owning task writes its body; this is not a shared document editor.

## Credentials and execution

One user-owned account registry binds credential purposes and admitted HTTPS
origins to protected custody. Windows uses current-user DPAPI; Linux uses Secret
Service with an unlocked collection. A private worker obtains the selected value
and passes it to the browser module through an inherited descriptor. The module
checks the observed page, receiving document and field before delivery and checks
them again before filling. It never submits the form.

The credential module accepts an explicit browser tab and locator interface. A
client must expose those supported operations and permit the local helper to run.
Operating-system compatibility is separate from availability of a particular
browser client. Concrete tested combinations belong in [qualification](qualification.md).
Clipboard and trusted-terminal delivery are optional consumers of the same
custody, with their own destination and runtime conditions.

Protocol Runner is separately versioned. Its contract owns the assignment and
quality expectations; its work plan owns execution order and procedural state.
The framework owns neither Runner's processes nor a user's source-integration
authority merely because an integration document links them.

## Readiness and evidence

The conceptual corpus records the intended framework. Source shows the mechanisms
implemented here. Tests and observed use establish behavior only under their
actual conditions. None of these alone proves broad empirical benefit, universal
completeness or success on arbitrary agent work.

Private migration, evaluation and deployment records are not distributed as public
proof. This edition records its own checks in [qualification](qualification.md).
The public source starts with fresh Git history. Future updates are deliberate
exports and adaptations; there is no automatic mirroring of private source or
state. A maintainer reviews changed source, updates affected documentation and
checks the relevant behavior before publishing a new version.
