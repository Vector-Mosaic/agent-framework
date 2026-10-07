# Changing software

Use this guide when the requested outcome involves changing software, interfaces,
data, configuration, operational behavior or their instructions. It applies the
[framework](../framework/README.md) to implementation without prescribing one
engineering sequence for the whole repository. Inquiry or design-only work keeps
its requested boundary; this guide does not turn it into a coding task.

## Carry the authorized outcome through the change

Understand the behavior or consequence to achieve, what must remain true, the
actual authority to change it and the completion boundary. Recover accepted
meaning from the relevant system and domain owners, implementation from source
and manifests, and behavior from suitable tests or observation. These sources
answer different questions. If code and intended behavior disagree, expose the
drift rather than silently making either the other's authority.

Start a named system at its `INDEX.md`, then recover relevant vocabulary, accepted
architecture, current behavior, contracts and operations. Read enough for the next
consequence, expanding through real dependencies. Summaries and search results
help find owners; they do not replace the full meaning needed to change them.
Preserve purpose and domain definitions unless the requested scope grants the
corresponding semantic change. The [system documentation convention](../repo_organization/system_documentation.md)
defines the document roles.

Internal slices, commits, worker contributions and tools support the requested
unit. Complete inherent supporting work without making the user request each part,
but do not expand into a separate product goal or maintenance campaign merely
because it seems useful. See [governing purpose](../framework/Conceptual_Model.md#governing-purpose-and-requested-consequence)
and [continued fitness](../framework/Conceptual_Model.md#continued-fitness-and-renewal).

## Choose languages and technology for the system

For new systems and substantial architectural changes, choose languages and
technology stacks deliberately for the system's intended outcomes and expected
lifecycle. Repository history, nearby utilities, installed tooling, and human or
agent familiarity do not establish a preferred implementation language. The
presence of existing Python operational tools does not by itself require products
or new tools to be implemented in Python. Consider credible alternatives where
they could materially improve the result.

Weigh the actual workload, runtime efficiency, reliability, security, useful
libraries, deployment targets, interoperability, diagnosis, maintenance and
expected evolution, including implementation and operational costs. Account for
the actual capabilities of the agents and humans who will build and maintain it,
including useful compiler and type-checker feedback. Agent assistance can change
the weight of manual coding effort; it does not establish universal superiority
for a language or remove ecosystem and operational tradeoffs. Long-term fit
follows the intended project rather than speculative unlimited scale.

Choose at coherent system or component boundaries. Reuse existing technology for
concrete benefits; evaluate missing toolchain support as a real setup and
maintenance cost rather than an automatic veto. Multiple languages may serve
distinct needs when their integration and operating burden is justified; shared
interface conventions do not require one implementation language.

A local correction need not reopen an otherwise fitting language choice.
Reconsider it when the authorized design or change makes that consequential,
without turning this guidance into a rewrite mandate. Explain consequential
choices briefly in the existing system architecture or design owner. Use
investigation or a bounded experiment when a material uncertainty changes the
choice; no universal language ranking, comparison matrix, benchmark or additional
approval gate follows.

## Investigate the distinction that changes the remedy

For a failure, identify enough of the affected behavior, owner and explanation to
choose a sound response. Inspect evidence that could distinguish plausible
accounts instead of repeatedly changing things until a symptom disappears. A
failed check may concern the product, its expectation, the check environment or
an observation route. A restart that helps does not prove a root cause.

The necessary depth depends on the next action. Clear local correction,
authorized containment or a remedy valid across the live explanations can proceed
without pretending every cause is settled. If plausible explanations imply
incompatible fixes, different authority or materially different data/security
effects, resolve that dependency before choosing among them. Preserve useful
transient evidence before a repair destroys it. Keep compatible work moving;
there is no universal root-cause-certainty prerequisite.

When remediation is already within the task, perform the understood repair and
its relevant verification. When the task asks only for diagnosis, provide that
diagnosis and its warranted limits. [Reliability and diagnosis](../framework/Conceptual_Model.md#reliability-and-diagnosis)
owns the complete distinction between explanation, containment, cure and proof.

## Trace actual dependencies and effects

Before changing, removing, renaming, rerouting or preserving a surface, understand
its real upstream callers and downstream effects. This reaches more than function
calls: interfaces, imports, configuration, schemas, generated consumers, tests,
manifests, tool catalogs, instructions, scheduled execution, deployment and
operator procedures can depend on the same behavior. Use actual source, searches,
metadata and runtime evidence appropriate to the surface; do not infer absence
from an empty search that could not detect the dependency.

Update or deliberately disposition the active dependents affected by the
authorized change. A historical mention or unaffected reference does not require
a revision cascade. A real caller identifies migration work, not an automatic
right for obsolete code to survive. Resolve consequential uncertainty at the
affected boundary while independent work continues. Make a no-callers conclusion
clear enough to understand the relevant search and its limits when that conclusion
justifies removal; no universal tracing form or permanent dependency ledger is
required.

Before adding or extending overlapping mechanisms, establish the actual job and
current owners. Deliberately reuse, wrap, replace or remove existing behavior.
When several mechanisms remain, their different responsibilities must be clear.
A smaller patch is not better if it bypasses ownership or leaves two competing
implementations. Nor does every shared idea justify a general-purpose framework.
Use the [repository structure](../repo_organization/repository_structure.md)
for system, component, shared platform and infrastructure ownership.

## Preserve owned behavior across boundaries

External effects and cross-system behavior go through the competent owning
service, adapter, client or operational procedure. A new CLI, HTTP or MCP route
must preserve canonical business meaning, validation, permissions, confirmations,
persistence and retry behavior. A convenient provider SDK or direct database write
must not bypass those responsibilities. An adapter's name alone does not prove
that it owns them.

Keep canonical domain identity and material invariants coherent. Command,
storage, historical and protocol representations can legitimately differ through
owned mappings; do not force every representation into one schema or invent a
competing identity for the same thing. Preserve supported public behavior and
real consumer promises except where the authorized change deliberately updates
them. Unchanged syntax does not prove unchanged meaning.

Apply fitting shared conventions and specialize at the scope where requirements
differ. [Application interfaces](../repo_organization/application_interfaces.md)
owns reusable interaction conventions and relevant-product MCP support;
[tool interfaces](../repo_organization/tool_interfaces.md) owns predictable
repository-tool use. These are useful common relationships, not an exhaustive
taxonomy of every convention software may need. Configuration, identifiers,
units, background jobs or user interactions can need consistency across a
different coherent consumer group. Do not infer a repo-wide transport, database,
UI pattern, error envelope or compatibility version from those examples.

## Handle data, setup and operation as real parts of the change

Data evolution preserves the required meaning, integrity and dependent behavior
through the authorized transformation, including deliberate deletion. Use the
system's Data, Contracts and Operations/Migrations owners and existing migration
history. Consider partial execution, concurrent use, retry and recovery where
they affect the actual store. A successful fresh install does not show that
existing records survive an upgrade correctly; unchanged row counts may still
hide changed meaning. Disposable development data and irreplaceable live records
need different protection, not a universal migration recipe.

Configuration changes preserve the actual loading, defaults, override behavior,
custody and deployment interpretation until deliberately migrated. Templates
describe possible inputs, not effective live settings. Change reader and records
together when interpretation changes; never silently reinterpret old path or
precedence conventions. Keep sensitive effective values out of diagnostics.

Preserve useful native workspaces, pins, lockfiles, manifests and setup commands.
Change dependencies intentionally with their real consumers understood. Enough
reproducibility for building, diagnosing, recovering and relying on the software
is the goal; byte-identical outputs are required only when the real use requires
them. Supply-chain inventories and build provenance answer different questions.
Preserve actual consumer obligations and useful evidence without automatically
requiring both artifacts for every externally distributed build.

Provide diagnostic visibility while designing behavior that could otherwise fail
without recoverable evidence. Relevant operation identity, time, state transitions
and safe correlation can matter more than log volume. Use the system's
Operations/Observability owner for actual access and interpretation; no universal
telemetry stack follows. See [using capabilities](using_capabilities.md) for
runtime, provider, protection and release operations.

## Integrate and release at their actual boundaries

Independent source writers use independent mutable files. Preserve unrelated
user and agent changes. Follow [Concurrent Development](../agent_workflow/concurrent_development.md)
and its [operations](../../ops/concurrent_development.md) for ordinary source
saving, candidate preparation, relevant combined-result checks, automatic
integration and safe refresh. Resolve clear mechanical conflicts; bring an
unresolved product or design choice to the user. A clean merge does not establish
semantic compatibility.

Verification is interleaved where its result changes the next decision. Check a
questionable dependency before building on it; otherwise complete coherent work
before relevant checking. Reuse unaffected evidence through integration. The
[verification guide](verifying_work.md) owns selection, timing and result meaning.

Saving, committing and integrating code do not themselves deploy, publish, mutate
production or spend on a new service. Release work needs its own covered
authority and exact source, artifact and target. Observe what actually became
active and use recovery fitting the current data, configuration and consumers.
The provider's accepted upload is not proof that the new application is serving.

## Finish retirement rather than relocate obsolete behavior

For authorized replacement or deletion, remove obsolete execution and active
authority together after handling actual dependencies. Include displaced callers,
flags, aliases, schemas, configuration, tests of retired behavior, catalogs,
examples and instructions that would still present it as current. Preserve tests
of still-required behavior at the new owner. An unreachable importable module,
disabled branch, stale runbook or "just in case" fallback is not complete removal.

Use Git for ordinary implementation history. A separate non-executable reference
requires a concrete continuing recovery, migration, comparison or retention need
that history does not satisfy, with its purpose and non-authoritative status
clear. It must not remain callable or quietly become a second supported route.
Moving unwanted machinery into another directory does not fulfill deletion.

Establishing or enlarging active compatibility requires the user's explicit
approval, real dependency evidence, a responsible owner and reason, a bounded
behavior, relevant checks and a concrete removal or migration condition. Existing
approval covers ordinary work within its exact scope; it does not authorize
indefinite expansion. Retaining an obsolete surface in an active path likewise
needs explicit approved status and the real location dependency. Uncertainty or
fear of deletion is not a compatibility requirement. Remove the exception when
its condition is met through the owning authorized transition.

Retirement includes the owning implementation and its actual controllers,
observers, interfaces, installed consumers and verification dependents. Preserve
needed domain records, exact qualifications, auditability, backup recovery and
necessary retained-format readers. Those are not obsolete software; preserving
their interpretation does not justify keeping a displaced implementation. A live
deployment can require a bounded transition: report it rather than deleting
needed support or claiming completion.

Honor explicit access and nonreactivation restrictions on retired material.
General cleanup does not authorize opening protected source, history, archives
or stashes. Recover the actual retirement boundary and covering authority before
inspection or restoration; path-only metadata does not grant content access.

## Leave useful knowledge and a truthful result

Update accepted intent, public behavior, interfaces, configuration, operations
and relevant diagnostic or testing guidance at their real owners in the same
coherent change. Preserve document roles and full system meaning. Required
documents can remain effectively empty until needed; do not invent filler or
erase useful existing content. Consequential rationale belongs with the design
or operation it explains, without a mandatory ADR or replacement decision ledger.

Creators and coordinators clean temporary outputs once the work and actual
consumers no longer need them. Keep meaningful results at their appropriate
owners and preserve active, shared and unfinished inputs. Generated, ignored,
old or large does not mean disposable. Add fitting exclusions before producing
large build outputs; ignore rules do not untrack files, grant deletion or clean
them up. Retain enough faithful evidence to avoid needless reruns, without a
keep-all archive or a universal one-result size limit.

### Policy-blocked temporary cleanup

Whenever policy or automatic command approval blocks cleanup of disposable
temporary files or directories, write or update a persistent deletion marker
before closing out the work, even if its substantive result is complete. Use one
small JSON file per cleanup set at the maintained repository's
`artifacts/pending_cleanup/<task>-<purpose>.cleanup-pending.json`, outside the
targets and any disposable task/worktree. Update the existing set rather than
creating duplicate reminders. This also applies to projectless work and remote
or outside-repository targets; record each target's actual host.

Record exact absolute file/directory targets and their scopes, without globs or
parent expansion; task and lifecycle owner; why the targets are disposable;
ended consumers and remaining dependencies; the block reason and observation
time; and sufficient nonsecret identity to recognize a reused target, such as
type, size and creation/modified times. Protect sensitive metadata. Never include
secret contents, secret hashes or copied rejected commands.

Later cleanup starts by listing markers in the maintained host folders, then
rechecking actual target identity, disposability, current dependencies, authority
and policy. A marker is a reminder, not a deletion grant or executable cleanup
program. Keep failed or partial targets marked; close/remove the marker only
after verifying absence of every target in the set. If marker creation is also
blocked, report that failure and retain the exact cleanup scope in the existing
continuity owners; do not bypass the policy. No daemon, new CLI, schema, service
or automatic deletion is required.

Use the actual maintained clone path for the relevant host, rather than a task
checkout or guessed newest worktree. Set `$maintainedRepo` (PowerShell) or
`$maintained_repo` (POSIX) to that absolute path. Discovery lists filenames
without dumping potentially sensitive marker contents:

```powershell
Get-ChildItem -LiteralPath (Join-Path $maintainedRepo 'artifacts/pending_cleanup') -File -Filter '*.cleanup-pending.json' | Select-Object -ExpandProperty FullName
```

```sh
find "$maintained_repo/artifacts/pending_cleanup" -maxdepth 1 -type f -name '*.cleanup-pending.json' -print
```

Report the delivered behavior, meaningful verification, actual integration or
release state and any remaining dependency. A cleanup failure does not erase an
earned substantive result, but required cleanup cannot be hidden by an unqualified
completion claim. Finish the requested work rather than stopping at its first
successful tool call or a convenient internal slice.

For example, replacing a command includes migrating its real scheduled caller
and help route; it does not require editing a historical mention that no longer
directs execution. Renaming a stored field may require upgrade verification even
when a new-database test passes. These examples are illustrative; actual meaning
and effects determine the necessary work.
