# System documentation

This document owns consistent system-document roles and presence requirements.
Existing system meaning remains with its full system owners.

## Stable roles and complete meaning

System documents preserve purpose, accepted meaning, boundaries, behavior,
design, operating knowledge and evidence routes so agents can use them reliably.
Use each role consistently wherever it applies. A document may need little
content, but its job must not drift into whichever information is convenient
to place there. Deliberately change a flawed convention with its affected uses
reconciled; do not create local substitutions that make future review impossible.

Canonical long-form system knowledge lives under `docs/systems/<system>/`.
Code-folder READMEs are signposts to that knowledge and real entrypoints. In an adopting multi-system repository, use its `docs/systems/INDEX.md` to find
the system, then its `INDEX.md`,
`Vocabulary.md` when present, and the task-relevant applying/current/target
owners. A relevant direct route remains usable; this is not a duty to reread
every document for every edit.

Durable docs define accepted purpose, meaning and target design; code and
manifests establish implementation; tests/runtime and retained evidence support
claims about what works. Preserve those roles when they disagree and repair the
specific drift. A plan, a file's existence, a successful validator or a copied
summary cannot substitute for the claim's actual owner and evidence.

The framework's
[semantic ownership and representation](../framework/Conceptual_Model.md#semantic-governance-representation-lifecycle-and-migration)
governs meaning. [Changing software](../practice/changing_software.md) covers
dependency-aware changes; the retained
[lock and promotion owner](../agent_workflow/concept_lock_lifecycle_and_promotion_standard_v1.md)
governs moving accepted discussion into durable authority. Neither changing a
document's folder nor promoting it elevates its authority over its parent
purpose.

## Presence and indexing

The following baseline applies when creating a separately owned system in an
adopting repository. It does not require a system directory or placeholder system
documents for each small tool in this toolkit; its existing documentation owners
remain the [docs index](../../INDEX.md) and linked operating guides.

The required baseline system entrypoints are:

| File | Job |
|---|---|
| `INDEX.md` | System front door, ownership and navigation. |
| `Behavior.md` | Current externally observable behavior. |
| `Testing.md` | Relevant verification selection, interpretation and gaps. |
| `Contracts.md` | Current interface inventory and authoritative-definition routes. |
| `Security.md` | Current security/privacy/trust and protected-data posture. |
| `Data.md` | System-owned stores/artifact classes, schema and lifecycle routes. |
| `Operations/Migrations.md` | Actual migration execution and recovery. |
| `Operations/INDEX.md` | Required operational entrypoint and navigation. |
| `Operations/Observability.md` | Current runtime visibility and diagnostic use. |

Keep `Architecture.md` and `Status.md` as the system's target-design and
current-state entrypoints, expressing their requirements directly rather than
through an ai-ready, execution-ready or ops-ready profile. `Vocabulary.md` is
required for **every new system**, even with one or zero entries. Existing-system
Vocabulary assessment remains individually scoped; absence does not establish
that there are no important shared terms. Retained legacy systems do not acquire a Vocabulary backfill campaign solely
through adoption of this convention.

Any required document may remain effectively empty until useful content is
needed. A title or truthful short note is sufficient. Do not invent terms,
procedures, tests, risks or exception forms to fill it. This does not authorize
emptying useful existing material. For example, a new pure-library system can
have an `Operations/Migrations.md` saying that no migration procedure currently
exists; it need not invent databases or rollback commands. A Vocabulary title
alone is not evidence that someone has assessed the system as term-empty.

Every active system doc is discoverable from `INDEX.md` directly or through a
relevant indexed subrouter. Keep root navigation useful: name the destination's
job and consequential status/limitation rather than duplicating its body. Optional
documents must not replace or bypass the system's root meaning. When their
content changes system-wide behavior, contracts, data, security or state, update
the affected root owner in the same change. Subrouters can organize large bodies;
root docs still explain the whole system.

## Root document roles

### INDEX.md

Own the system's front door: purpose/boundary orientation, code roots and
manifests, read-first routes, semantic owner, subsystem and optional-doc routes,
and consequential gaps or legacy distinctions. Route to Vocabulary before
applying its terms. Keep ownership and relationships clear enough to decide
whether work belongs here, in a subsystem or at another owner. The index is not
an encyclopedia or a second copy of Behavior, Status or operational procedures.

### Vocabulary.md

Own accepted system-wide meanings whose coherence matters across applying
owners, or whose misunderstanding would materially damage the system boundary.
Ordinary language, local implementation names, configuration keys, endpoint
inventories and document-local definitions stay with their narrower owners
unless they independently meet that condition.

Give admitted terms their complete qualified meaning at the depth they need:
definition and purpose, relevant aliases, scope, neighboring distinctions,
material relationships, truth/nonclaim limits, applying-owner routes and
intentional deferrals. This is semantic authority, not a flattened glossary or
compressed replacement for design. No fixed entry template or term quota is
required. A short ordinary distinction and a complex connected definition can
legitimately need different space.

Applying owners retain procedures, examples, consequences, contracts, schemas,
storage, lifecycle, implementation and evidence. A narrower accepted use can
specialize the shared definition while stating its qualification; it cannot
silently redefine it. A link or shared name does not prove subtype, ownership,
storage, existence or implementation. Inclusion establishes accepted meaning,
not that any particular application is built, deployed or admitted. Application
owners identify current, target, planned and illustrative uses.

For genuinely shared cross-system meaning, use one appropriately scoped owner.
Consumer Vocabulary documents can route there and state their actual narrower
meaning. Resolve competing ownership rather than copying parallel definitions.
When changing a definition, rename, alias or retirement, preserve its authorized
semantic delta and reconcile affected applying uses. Retain an alias for actual
use rather than hypothetical compatibility.

For example, defining an accepted “source anchor” does not prove that the system
has persisted one. The Vocabulary owns what would qualify; the schema, behavior
and evidence owners establish its representation and actual occurrence. This
example illustrates the distinction, not a new repository term.

### Architecture.md

Own the accepted target design: purpose and scope, system boundary, ownership,
major components, dataflow, integration relationships, invariants and constraints.
Keep the relationship to implementation explicit, with a useful current delta
and its basis when the target is not fully implemented. Link current claims to
their current owners and evidence. A large architecture can route bounded design
detail to subsystem owners while retaining the system-wide explanation.

Architecture is not a task sequence, runbook, changelog or exhaustive code
inventory. Consequential rationale, rejected alternatives and tradeoffs belong
with the design they explain when they remain useful. Retiring mandatory ADR
creation does not discard that knowledge or actual evidence consumers.

### Status.md

Own a concise current-state view: what exists, current focus, target deltas,
material gaps/hazards and unresolved decisions, with relevant evidence routes.
Distinguish supported state from plans, speculation and historical success.
Expose subsystem state through useful routes without copying local task cursors.
Status is not a growing completion transcript, run log or another to-do system.

### Behavior.md

Own current externally observable behavior: user/operator/API journeys, inputs,
outputs, effects, consequential configuration, failure/recovery behavior,
behavioral invariants and meaningful limits. Explain what the system does today
and route deeper interface, operating or subsystem detail to its owner. Keep
future behavior in target design/current gaps rather than writing it as current
fact. This document is not the testing manual, API inventory or internal code map.

### Testing.md

Own how to select and interpret evidence for current behavior and relevant
claims. Provide useful focused commands/observations and when to choose them,
material prerequisites, cost/effects/cleanup, failure interpretation and known
gaps. Distinguish independently selectable checks from a genuinely cumulative
workflow. A broad suite needs an actual reason; a listed command is not an order
to run it on every change.

Testing instructions are not proof that a check passed. Route retained evidence
to its appropriate owner; do not make Testing a proof archive or manual QA diary.
Coverage explanations should help select relevant evidence, not enumerate every
test or populate empty tiers. Preserve known gaps honestly without inventing
tests. Use [verifying work](../practice/verifying_work.md) for timing, reuse,
sufficient grounds and interpreting failures.

### Contracts.md

Own the current inventory and navigation for supported boundaries: the
authoritative schema/declaration/artifact, authoring or regeneration ownership,
providers and actual consumer story, relevant compatibility/version/status and
verification routes or material gaps. Identify code-derived versus authored
definitions when that changes what maintainers edit. Preserve system-wide and
cross-subsystem visibility while linking deeper local detail.

Contracts is not a duplicate API definition or a dumping ground for future
interface ideas, tutorials or history. The general Contracts Standard's
retirement does not remove system interfaces or this file. Use
[application interfaces](application_interfaces.md) for predictable semantics,
HTTP/MCP support and consumer-aware evolution.

### Security.md

Own current system security and privacy meaning: protected interests/data,
classification assumptions, trust and ingress boundaries, effective identity and
permissions, credential/config flow by safe key/source reference, sensitive-data
handling, redaction, safe failure, incident routes and meaningful known gaps or
accepted exceptions. Scope any exception and identify its authority. Keep
cross-subsystem flows and shared protection visible at the root.

Do not include raw secret values, credentials, tokens, restricted personal or
customer data, unsafe examples or raw provider dumps. This is neither a
compliance claim by document presence nor a replacement for actual enforcement.
Optional audience-facing/compliance documents point to this truth; they cannot
change it through stronger wording.

### Data.md

Own the current inventory of system-owned persistent stores and artifact
classes, their purpose/ownership, schema authority, classification, retention,
lifecycle and migration/recovery/verification routes. Distinguish generated
artifacts, ephemeral runtime state, external dependencies and local-only data
where their treatment differs. Make actual cross-system/subsystem ownership
clear. No store need be invented for a system that has none.

Keep execution/recovery steps in Operations/Migrations and sensitive handling
in Security. Do not copy exports, connection-string values, sensitive examples
or live provider inventory into this document. Data inventory does not imply
that a backup exists or a restore has succeeded.

### Operations/Migrations.md

Own actual migration procedures and recovery, including source/runner paths,
target selection, relevant preconditions, commands, meaningful validation and
partial-failure/retry treatment. Clearly distinguish local reset from a safe
procedure for shared/persistent environments. Choose forward fix, rollback,
regeneration or another method for the actual data and consequences; there is
no universal expand-contract procedure.

A needed procedure must explain enough to execute it correctly; an unused
required file may stay empty. Keep schema inventory in Data and retained
migration history/scripts at their existing owners. File presence does not
require inventing rollback, escalation or multi-environment sections.

### Operations/INDEX.md

Own the required system operations entrypoint. Route to the applicable runbooks,
Observability and Migrations, with useful start-here guidance and consequential
target/risk distinctions. Preserve cross-subsystem dependencies and local
operations routes. This is navigation, not a duplicate deployment guide,
incident history or copy of every runbook. A system with no operational work can
say so briefly.

### Operations/Observability.md

Own how to see and diagnose the current runtime: applicable logs, health checks,
safe identifiers/correlation, diagnostic commands, useful signals and known
visibility gaps. Describe actual available metrics, tracing, dashboards or alerts
when they matter; no mandatory stack or absence report follows. Preserve enough
fidelity to investigate important failures before transient information is lost.

Distinguish a documented health command from current demonstrated health. Keep
unsafe payloads, raw auth, personal data and secrets out of examples. This is
neither an incident log nor a general observability-policy restatement. Root
guidance owns shared visibility; bounded subsystem guidance supplies local detail.

## Operational document roles

System operations live under `Operations/`; repository-wide procedures live
under `docs/ops/`. Preserve these different jobs without forcing every document
into an executable-runbook template:

| Role | Meaning and boundary |
|---|---|
| `runbook` | A repeatable procedure for an actual operation: applicability, exact target/access, prerequisites, commands, meaningful expected results, partial failure/retry/recovery where relevant. It is not permission to execute by itself. |
| `profile` | Current capability, environment or runtime configuration/inventory; distinguish declared configuration from observed live state. It is not an execution sequence or historical proof. |
| `checklist` | Relevant conditions or evidence for a concrete setup/release/operation decision. Checking a box cannot substitute for the underlying condition or evidence. |
| `receipt` | A retained point-in-time result with bounded subject, conditions and evidence. It does not become enduring current-health proof or a universal per-task obligation. |
| `operating_doctrine` | Accepted recurring operational rules and rationale; distinguish these from a one-time plan or observed result. Use this role consistently, without renaming existing files solely for policy organization. |
| `operator_guide` | Recurring human/agent judgment, navigation and workflow guidance, linking executable steps to their runbooks. It is not an alternative business/security owner. |

Keep comparable procedures predictable. Include commands, working directory,
effects and relevant settings/access routes where needed for correct execution;
never rely on hidden chat or secret values. Avoid fixed section order, mandatory
IDs/dates/safe-to-run flags, or invented rollback/escalation sections. If a
profile/guide acquires substantive executable instructions, give those steps
the clarity and owned home of a runbook. The
[capability-use guide](../practice/using_capabilities.md) explains selection and
operation; this document owns presence and role boundaries.

## Subsystems

A subsystem is a durable internal capability boundary of its parent system. It
can coordinate services, apps, packages, data, contracts and operating behavior.
Create subsystem documentation when that boundary needs its own explanation and
navigation: recurring work, confusing adjacent responsibilities or substantial
local operations can justify it. These are examples, not a counting threshold.
One feature, code folder, future idea or temporary task does not require a
subsystem.

Use `Subsystems/INDEX.md` as the system's subsystem router and
`Subsystems/<subsystem>/INDEX.md` as each entrypoint. The router explains boundary
focus and relationships and links each accepted subsystem. The local index owns
bounded purpose, responsibilities and exclusions, invariants, relevant surfaces,
dependencies, state and testing/operating routes. Add local Architecture,
Behavior, Contracts, Status, Testing or Operations docs only where a distinct
useful body warrants them.

Preserve distinctions between owned surfaces, intentionally shared surfaces,
dependencies owned elsewhere and historical/supporting material. Names and links
must not accidentally claim exclusive ownership. Useful prose and links can
carry this; repetitive mandatory tables or invented change procedures are not
required. Parent root docs continue explaining whole-system meaning and shared
behavior, data/security and cross-subsystem relationships. Resolve a conflict
as drift at the correct owners rather than selecting the convenient file.

Promoting an internal capability to a separate system changes ownership and
meaning; it is not merely a folder move. Reconcile the parent, system index,
manifests, interfaces and operations under the actual design/change authority.

## Optional supplements and audience-facing documents

Create optional documents for a real job, index them through the system, and
make their audience, scope and relationship to controlling truth clear. They
can explain a useful perspective without becoming competing authorities.

`compliance_overlay` maps actual external obligations to neutral system facts,
controls, operations and evidence. State the applicable obligation and limits
of the mapping. Mapping, planned controls or a completed document do not prove
certification, legal compliance, effective production operation or customer
acceptance. Security, Data, Behavior, Contracts and Operations retain their
underlying meanings; confidential evidence belongs only in its authorized store.

Commercial/application documents preserve distinct purposes:

| Role | Purpose and important limit |
|---|---|
| `customer_readiness` | Explain supported/conditional stakeholder-use posture grounded in system truth, without turning plans into current capability. |
| `deployment_overview` | Present a reviewable delivery model; route actual procedure and customer-specific state to their owners. |
| `approval_packet` | Assemble material for a particular audience and review, preserving sharing boundaries; it is not new source truth or evidence a packet was sent/approved. |
| `evidence_appendix` | Map claims to evidence with freshness and proof limits; the mapping itself does not establish the claim. |
| `discovery_questionnaire` | Reusable questions; keep completed customer answers at their authorized record owner. |
| `legal_procurement` | Organize material with its actual legal status; it is not authority for signed records or security/compliance truth. |
| `commercial_strategy_internal` | Internal reasoning with assumptions and uncertainty explicit; it does not redefine accepted product purpose, grant execution authority or become customer-facing truth. |
| `adoption_workflow` | Reusable phases, responsibilities and handoffs; actual customer progress and passed gates belong to their records/evidence. |
| `misc` | A temporary bounded exception when existing roles genuinely do not fit, with that reason and a useful reconsideration trigger; not a silent permanent category. |

Use these role names where they aid recognition, without imposing new metadata
forms or mandatory documents. Mixed documents must make their different claims
and boundaries clear. When a supplement changes accepted system meaning, update
the actual owner; stronger wording in a supplement cannot do that silently.

## Navigation, execution plans and retention

Use ordinary Markdown links and meaningful headings for navigation. A
substantive relational representation may live inside the document that owns
its meaning. Apply the [Relational Expression Kernel](../engineering_standards/relational_expression_kernel_standard_v1.md)
when explicit subjects and qualified relationships help understanding, finding,
combining or maintaining information. Applicability follows purpose and
relationships, not exact example matching. Ordinary use requires no formal
profile, and document navigation needs no separate relationship registry.

Keep target, current, planned and historical claims distinguishable in the
narrative. Metadata can help real consumers but does not confer authority.
Descriptive filenames and existing canonical root names provide predictable
routes; do not rename useful system files merely to reorganize instructions.

Do not create new active `Plans/**` as a home for accepted system concepts.
Durable meaning belongs at its owning system/subsystem document. For work
actually using Protocol Runner, use the uniquely named package
`Runner_Work_Plans/<work_plan_id>/work_plan.json` under the system docs. Its
schema, capabilities and coordinator/worker duties belong to the
[Protocol Runner playbook](../../integrations/protocol-runner.md),
not this document. Keep runtime transcripts/output out of the source package.
Ordinary discussion/planning does not require Runner or another permanent plan
artifact.

Preserve useful accepted rationale and evidence consumed by current work before
retiring a carrier. Mandatory ADR thresholds/templates and perpetual document
reviews are retired; this does not authorize blind deletion of existing ADRs,
system contracts, scientific history or other evidence. Remove superseded
duplicates when their useful meaning and consumers have been handled. Git can
retain incidental history, but cannot replace evidence still needed by an
active owner. Documentation consistency is maintained in the owning change,
without a new validator suite, ledger or recurring bureaucracy.
