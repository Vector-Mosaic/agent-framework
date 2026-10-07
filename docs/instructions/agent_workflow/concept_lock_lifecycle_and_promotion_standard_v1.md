# Concept Lock Lifecycle And Durable Promotion Standard v1

- version: v1
- status: current standard
- scope: repo-wide lifecycle for exploratory design, accepted working
  baselines, and promotion into durable narrative authority
- category_authority: `docs/instructions/agent_workflow/README.md`
- enforcement_state: mixed `agent_entrypoint_mirror`, `execution_contract`,
  and `touched_surface_rule`
- default_read_posture: read when the user says `lock`, `locked`, `lock this`,
  `locked as a baseline`, `accepted baseline`, `promote`, `make durable`,
  `canonize`, or `lock and promote`; also read for substantial sketchpad
  consolidation, durable concept integration, or concept-promotion fidelity
  review

---

## Purpose

This standard owns the repo-wide lifecycle for developing a concept in a
thread, accepting it as the current working baseline, and integrating it into
durable repository authority without losing semantic meaning, narrative
meaning, or narrative flow.

It solves a specific failure mode:

```text
sequential accepted discussion
  -> append-only sketchpad locks
  -> copied durable lock blocks
  -> duplicate or superseded authority
  -> chronology replaces explanation
  -> later readers cannot recover one current model
```

The replacement lifecycle is:

```text
exploratory discussion
  -> consolidated sketchpad lock
  -> verified durable narrative promotion
```

A sketchpad lock controls continued reasoning in its active workstream. It is
not repository authority. Promotion integrates the accepted current meaning
into the correct durable authority home and makes that meaning repository
authority.

The governing invariant is:

> Preserve every accepted semantic obligation and the coherent narrative that
> gives it meaning. Do not preserve obsolete textual containers, lock
> chronology, or superseded formulations inside active authority.

## Read With

This standard is the detailed workflow owner. Read it with:

- `AGENTS.md` for the compact mandatory agent behavior;
- `docs/instructions/agent_workflow/README.md` for category ownership,
  proportional grounding, and semantic-change authority;
- `docs/instructions/repo_organization/system_documentation.md` for durable document
  kinds and authority homes; and
- `docs/instructions/agent_workflow/concurrent_development.md` for isolated source work, saving, checked integration and safe refresh.

Those documents route to this standard for concept-lock lifecycle and
promotion method. They must not define a competing procedure.

## Mechanism And Authority Boundary

This standard does not create a second sketchpad, plan type, authority store,
or documentation hierarchy.

The existing mechanisms retain these jobs:

- The pinned thread sketchpad carries current thread-local intent and accepted
  working baselines.
- The thread to-do list carries current action order only.
- Durable docs carry accepted repository intent, target design, current truth,
  standards, policies, contracts, and operating rules under their owning
  document kinds.
- Git and thread backups preserve historical wording and change history;
  this does not require a product history service, supported old revisions,
  replay or backward compatibility. Semantic preservation carries accepted
  meaning forward; additional historical behavior needs a concrete task or
  domain requirement.
- Plans and runner work plans carry execution structure, not durable concept
  authority.
- Proportional source grounding and explicit semantic-change authority govern
  operations without creating a parallel workflow artifact.

Do not create another lock ledger, promotion document type, authority registry,
or parallel concept store. If the pinned sketchpad mechanism is unavailable,
keep the discussion exploratory and report the missing canonical mechanism; do
not invent a substitute local-lock mechanism.

### Parent-Outcome And Authority Conservation

Locking or promoting accepted meaning preserves its scope, force, and authority
level. Promotion increases durability; it does not broaden semantic scope or
raise hierarchical authority.

A subordinate constraint, caution, assurance mechanism, implementation choice,
example, or supporting property remains subordinate to the parent purpose and
accepted completion meaning it serves unless the user or controlling durable
authority explicitly changes that parent purpose or completion meaning. Before
consolidation or promotion, reconcile the accepted delta with its governing
parent outcome. Treat an interpretation as a semantic conflict when it would
make that accepted parent outcome impossible, materially redefine success, or
introduce a prerequisite absent from the accepted decision; do not resolve the
conflict through `coherence`, `integration`, `operational completion`, or
invented machinery.

Acceptance of a concern or desired property does not imply acceptance of every
mechanism that could address it. Each derived mechanism requires its own causal
justification. Coherence means faithfully reconciling accepted meaning; it does
not authorize completing an implied system around that meaning.

## Lifecycle States

### Exploratory Discussion

Exploratory discussion contains proposals, questions, alternatives,
hypotheticals, adversarial tests, and wording under consideration.

It is not accepted meaning merely because it is detailed, repeated, useful, or
unopposed. Silence and conversational momentum do not promote it.

### Sketchpad Lock

A sketchpad lock is the accepted current working baseline for one concept in
the active workstream.

It:

- controls subsequent reasoning in that workstream;
- is consolidated with earlier accepted meaning for the same concept;
- remains revisable through a later explicit semantic delta;
- is local-only continuity state;
- does not override durable repository authority; and
- does not become durable merely because implementation or planning continues.

### Durable Promotion

Durable promotion integrates a consolidated sketchpad lock into the correct
repository authority home.

Promotion:

- reconciles the working baseline with existing durable meaning;
- preserves all still-valid semantic obligations;
- removes genuinely superseded active prose;
- rewrites the affected explanation as one current narrative;
- passes semantic, narrative, traceability, and zero-context checks
  proportionate to the change; and
- completes only after the durable edit is verified and checkpointed under the
  repo workflow.

### Explicit Unresolved Question

An unresolved question records a real conflict, missing decision, or
insufficient authority that prevents one current answer.

It is not a weak lock. It must remain visibly unresolved and must not be
silently merged into durable current meaning.

## Command Semantics

Use these command meanings repo-wide.

### Exploration Commands

`Sketch`, `proposal`, `what if`, `let's discuss`, and comparable language signal
exploration in context. Interpret `can we` and other questions by the requested
consequence: they can request action as well as discussion. An exploratory result
is not accepted merely because it was written; the user's subsequent acceptance
and authority determine whether to lock, promote or execute it.

### Lock Commands

`Lock`, `locked`, `lock this`, `locked as a baseline`, and `accepted baseline`
mean:

1. identify the affected concept and the exact accepted decision;
2. inventory its accepted semantic obligations and distinguish them from
   exploratory, rejected, or still-unresolved material;
3. reconcile the decision with that concept's existing current sketchpad
   baseline;
4. expose and resolve the semantic delta;
5. replace the prior baseline with one consolidated current baseline;
6. pass the Sketchpad Lock Fidelity Gate below; and
7. leave durable repository authority unchanged.

Both pinned overlays are required from thread entry under the category's
Pinned Thread Continuity rule, not only when a lock becomes substantial. If
either is absent, initialize only the missing overlay and preserve existing
content before recording the lock. Do not use chat memory alone as the baseline.

### Highlighted Selections And Baseline Language

When the user applies any recognized lock command (`lock`, `locked`, `lock this`,
`locked as a baseline`, or `accepted baseline`) to highlighted assistant
response text, the highlighted selection and any accompanying annotation or
comment together identify the exact accepted scope. The annotation controls
how the selected text is qualified or interpreted.

`Locked as a baseline` means:

```text
complete current accepted meaning
NOT permanently complete future meaning
NOT immutable wording
NOT weak or optional guidance
```

The baseline governs subsequent work unless and until the user accepts a later
semantic delta. It may later be clarified, refined, extended, corrected, or
replaced. Expected future expansion does not authorize an agent to omit detail
from the current lock, treat accepted meaning as provisional, or silently
reinterpret it later.

The highlighted selection and any accompanying annotation define the accepted
scope. The agent must:

- preserve its semantic meaning and narrative relationships;
- apply the accompanying user annotation as controlling clarification;
- integrate prior accepted baseline meaning needed to keep the selection
  coherent;
- avoid treating unrelated unselected content as accepted;
- preserve materially constraining examples and qualifications;
- expose any semantic conflict with the existing baseline; and
- ask when integration would otherwise require guessing what was accepted.

Unselected surrounding text may supply necessary context for understanding the
selection, but it does not become newly accepted merely because it is nearby.

### Promotion Commands

`Promote`, `make durable`, and `canonize` mean:

1. read the complete current sketchpad baseline;
2. ground on the owning and materially adjacent durable authority;
3. perform the promotion procedure in this standard; and
4. make the verified integrated result durable.

Promotion does not mean copying the sketchpad wording into a file.

### Combined Command

`Lock and promote` means perform both lifecycle transitions in one bounded
operation:

```text
accepted decision
  -> consolidated sketchpad baseline
  -> verified durable narrative authority
```

The sketchpad consolidation must still occur before durable editing so the
promotion source is explicit and reviewable.

### Planning And Implementation Commands

`Lock and plan` means lock the working baseline and then create or update the
appropriate execution structure. It does not imply durable promotion.

`Implement` does not silently promote a sketchpad lock. Implementation must not
depend on concept-level meaning unless that meaning is already durable or is
promoted in the same authorized change. Implementation discoveries that alter
accepted meaning require a new semantic delta and the appropriate lock or
promotion action.

## Existing Descriptive `Locked` Vocabulary

Some existing durable standards use metadata such as:

```text
status: locked standard
```

That phrase describes an already-durable document's accepted status. It is not
the interactive `lock` command and does not create a second lifecycle.

This standard does not require a repo-wide metadata vocabulary migration.
New workflow prose should prefer `current`, `durable`, or `promoted` when that
avoids ambiguity. Touched documents may migrate descriptive wording when the
change is meaning-preserving and locally coherent.

## Sketchpad Consolidation Contract

### One Current Baseline Per Concept

The active sketchpad must contain one consolidated current baseline for each
concept it governs.

Do not accumulate:

```text
Original Lock
Superseding Lock
Clarification Lock
Correction Lock
Updated Lock
```

as simultaneously active bodies.

Thread backups preserve prior versions. The active sketchpad preserves the
current reasoning state.

### Baseline Content

A consolidated baseline must preserve all detail needed for faithful continued
reasoning, including as applicable:

- purpose and exact subject;
- accepted meaning and governing propositions;
- conditions, scope, and authority;
- required distinctions and neighboring boundaries;
- examples that materially constrain interpretation;
- explicit non-goals;
- unresolved questions;
- durable destination when known; and
- current promotion status.

The standard does not require one universal field schema. The baseline's shape
must serve the concept without losing semantic force or the narrative
relationships among its accepted propositions.

### Sequential-Lock Reconciliation

Before a later lock changes an existing baseline, compare the newly accepted
decision with the current baseline closely enough to understand what remains,
what changes, and what is still unresolved. Distinguish clarification or
refinement from extension, correction, or replacement when that distinction
materially affects authority or downstream meaning. Do not create a mandatory
field list, classification form, or separate comparison artifact.

A clarification or refinement must not silently weaken or broaden the concept.
Extension, correction, and replacement require explicit user or
durable-authority basis. A material unresolved conflict stops consolidation at
the affected boundary.

After the user accepts the delta, rewrite the affected sketchpad baseline in
place. Do not preserve former active wording merely to show chronology.

### Scope Discipline

One lock may affect several concepts only when the accepted decision genuinely
changes all of them. Update each affected baseline explicitly.

Do not let one concept's wording become an unreviewed template for another.
Do not normalize item-native or system-native meaning merely to make
consolidation easier.

## Sketchpad Lock Fidelity

The consolidated sketchpad baseline must faithfully preserve the accepted
meaning. A concise summary or copied selection is sufficient only when it does
not lose material force, constraints, boundaries, or unresolved questions.

### Meaning Preservation

For an initial lock, preserve the governing meaning, materially constraining
explanation or qualifications, and genuine unresolved questions while leaving
unaccepted exploration out of the baseline. For a later lock, compare the
prior baseline with the newly accepted decision and update the affected meaning
without erasing prior accepted meaning unless the new decision explicitly
corrects, removes, or replaces it.

If an accepted decision genuinely spans another concept, update that concept's
baseline as part of the same lock. New assertions must come from the accepted
decision, the prior baseline, or governing durable authority. Rewriting for
coherence is not authority to invent a generalization, exception, mechanism,
owner, or conclusion. Perform this review proportionately within the lock
operation; do not create a separate obligation inventory, trace, or proof
artifact.

### Narrative And Zero-Context Review

Reread the consolidated baseline without relying on conversation memory. A
competent reader should be able to explain:

- what concept was accepted and why it exists;
- how its propositions relate to one another;
- its governing force, scope, conditions, boundaries, and qualifications;
- what remains unresolved;
- what subsequent work must preserve.

The baseline may be less polished than durable documentation, but it must be a
coherent current explanation rather than a pile of selected excerpts or
chronological lock notes.

### Lock Acceptance Gate

The sketchpad lock fails when any of the following remains:

- an accepted obligation is missing, weakened, or detached from necessary
  context;
- unaccepted discussion is presented as accepted meaning;
- a new assertion lacks accepted authority;
- prior and new formulations remain active without reconciliation;
- an unresolved conflict was silently answered;
- semantic relationships must be reconstructed from chat memory;
- chronology or copied excerpts substitute for one current explanation; or
- the affected baseline cannot be understood in a zero-context reread.

A legitimate unresolved question does not fail the lock when it is preserved
explicitly and does not prevent an honest current baseline.

## Promotion Checkpoint Assessment Duty

At each meaningful lock boundary, the coordinating agent must assess whether
leaving the accepted meaning only in the sketchpad would materially mislead
continued grounding or downstream work. When that risk exists, the agent must
explicitly flag a durable-promotion checkpoint and state whether promotion is
ready now or must remain deferred.

Readiness is judged for the exact proposed promotion unit. Settled wording is
not enough. A unit is not promotion-ready when:

- its accepted sketchpad plan still treats that unit as unfinished;
- material questions about its purpose, meaning, scope, boundaries, ownership,
  dependencies, or other decisions remain unresolved inside it;
- the accepted plan requires pending parts to be resolved together before one
  coherent durable narrative can be stated;
- promotion would imply answers to excluded or unresolved companion work; or
- the unit cannot independently pass the Sketchpad Lock Fidelity Gate.

Readiness is unit-relative rather than whole-workstream-relative. Related work
may remain open when the exact slice is complete, coherent, independently
understandable, and cleanly bounded from unresolved material. Independently
settled concepts deserve particular checkpoint attention when durable
promotion would clarify the authority used by the remaining discussion or
work.

For this checkpoint assessment, recommend promotion now only when:

- the exact settled slice and its durable owner are known;
- current durable authority materially contradicts or omits the slice;
- upcoming grounding or downstream work will rely on it; and
- the slice can be promoted as a bounded semantic delta without deciding
  unresolved material.

When sketchpad-only treatment creates material drift risk but any readiness
condition fails, the checkpoint notice recommends deferral rather than
promotion and names the condition that must be reassessed.

The checkpoint notice identifies:

- the exact settled slice and why it does or does not stand coherently alone;
- the material drift risk from continued sketchpad-only treatment;
- the durable owner and bounded semantic delta;
- the unresolved or unfinished material explicitly excluded;
- whether promotion is recommended now or deferred; and
- the trigger for reassessment when deferred.

This assessment duty creates no automatic promotion authority and no new
lifecycle state, document type, or authority store. A checkpoint notice may
precede a promotion request, but durable editing still requires the explicit
authority defined by the command semantics above or authority already granted
by the active task.

## Promotion Readiness

Promotion may begin only when:

- the user explicitly requests promotion, durable integration, canonization, or
  `lock and promote`;
- the consolidated sketchpad baseline is recoverable;
- the sketchpad baseline faithfully preserves the accepted meaning;
- the likely durable owner is identified;
- relevant current evidence and materially adjacent authorities have been
  read proportionately;
- the durable change has explicit user or controlling durable authority and is
  understood against the existing meaning; and
- no unresolved conflict prevents one honest current narrative.

Before adding, replacing, moving, or deleting authority, follow the Dependency
Trace Gate. Identify upstream readers and references, downstream effects, and
any adjacent owner that could become contradictory.

If the durable home or owner is unclear, stop and resolve ownership. Do not
create a new document merely because no existing section is convenient.

## Durable Narrative Integration Contract

### Integrate, Do Not Append

Promotion rewrites the affected durable explanation in context.

The resulting document should normally explain:

1. purpose and authority;
2. position in the larger model;
3. current definitions and propositions;
4. internal behavior or structure;
5. interaction with adjacent concepts;
6. truth, scope, and non-goal boundaries; and
7. genuine unresolved questions.

Do not append the sketchpad text as a timestamped or labeled lock block.

### Present Current Meaning Directly

Active durable authority should normally use present-tense current
explanation. It should not retain process narration such as:

```text
This lock supersedes...
During Phase 4...
Promoted from the sketchpad...
As of <date>...
Previous baseline...
The latest clarification says...
```

Delete or integrate obsolete wording after its valid meaning has a verified
current home.

Chronology may remain where chronology is the document's subject, such as an
ADR, status history, migration record, plan, incident record, or historical
archive. That exception does not authorize chronology inside current
conceptual or operational explanation when the history has no current
semantic role.

### Preserve Narrative Meaning

Narrative preservation requires more than retaining isolated propositions.
The durable explanation must make clear:

- why the concept exists;
- what problem it solves;
- where it belongs;
- how it relates to neighboring concepts;
- what depends on it;
- what it does and does not establish; and
- where accepted authority currently ends.

A list of correct statements can still fail narrative preservation when the
reader must reconstruct their relationships.

### Preserve One Primary Owner

Each semantic rule receives one primary durable owner. Adjacent documents may
link to or narrowly apply it, but they must not reproduce a second full
definition.

Promotion must repair or remove contradictory duplicate authority in the
authorized touched scope. It must not create another explanation merely to
avoid editing the existing owner.

## Semantic Preservation Contract

Semantic preservation means preserving the actual accepted meaning and force,
including:

- `must` versus `may`;
- `AND` versus `OR`;
- universal versus conditional rules;
- exact subject and scope;
- ownership and authority;
- what something proves;
- what it explicitly does not prove;
- false-positive and neighboring-identity boundaries;
- legitimate uncertainty;
- exceptions and qualifications;
- distinctions from adjacent concepts;
- explicit non-goals; and
- consequences needed by later conceptual or implementation work.

Textual preservation is not semantic preservation. Wording, headings, tables,
and paragraph order may change when the accepted meaning and narrative remain
intact.

Examples are retained when they materially constrain interpretation, expose a
boundary, or prevent a known false reading. Illustrative examples may be
rewritten, consolidated, or removed when the governing prose independently
preserves their meaning.

## Semantic Preservation Review

For a substantial promotion, compare the current durable owner, materially
adjacent authority, consolidated sketchpad baseline, and accepted unresolved
questions closely enough to confirm that accepted meaning lands in the right
durable owner and that every new assertion has accepted authority. Equivalent
integrated wording is valid when it preserves the same force, scope, and
consequences without hidden reconstruction.

Perform this comparison as part of ordinary editing and review. Do not require
a separate semantic-obligation inventory, disposition table, forward/reverse
trace, or proof artifact.

### Zero-Context Review

A substantial promotion must be reread from the perspective of a competent
reader with no conversation history.

The reader should be able to explain:

- the concept and its purpose;
- its relationship to adjacent concepts;
- its governing rules and boundaries;
- its unresolved questions; and
- the current authority home

without consulting the sketchpad, reconstructing chronology, or deciding which
passage is newest.

### Acceptance Gate

Promotion fails when any of the following remains:

- an accepted obligation is missing or weakened;
- old and new governing models remain active together;
- narrative flow requires hidden conversation context;
- a new assertion lacks authority;
- an unresolved conflict was silently answered;
- duplicate authority remains or was created;
- a timestamp, lock label, or process note substitutes for current
  explanation;
- the durable home is wrong or ambiguous; or
- required docs, manifest, dependency-trace, or verification checks fail.

For narrow promotions, review may be compact. For large, cross-document,
ontology, architecture, policy, or contract promotions, review both directions
against the accepted meaning and use an independent read-only review when that
materially reduces omission risk.

## Promotion Closeout

After durable promotion passes:

1. Update the sketchpad baseline to a concise durable pointer or remove its
   full promoted body.
2. Preserve only still-active discussion, unresolved questions, and current
   work state.
3. Update the thread to-do list to the next real action.
4. Run the relevant docs, manifest, and repository verification.
5. Preserve the owned change in Git; push only when the active task or standing
   policy authorizes publication to the exact configured target.
6. Report the promoted authority home, semantic disposition, verification, and
   checkpoint.

Git and thread backups preserve prior wording. Active durable docs and the
active sketchpad do not need to duplicate that history.

If promotion fails, do not partially declare success. Keep the working
baseline in the sketchpad, report the exact failed gate, and leave or restore
durable authority to one coherent current state.

## Plans, ADRs, And Implementation

Plans and runner work plans may cite a sketchpad lock or promoted durable
concept, but they do not become the concept owner.

An ADR may preserve why a durable decision was made. The current target or
behavioral meaning still belongs in the appropriate architecture, contract,
behavior, standard, policy, or subsystem authority.

Implementation may expose a needed correction. It does not silently redefine
the promoted concept. Apply a new semantic delta, obtain the required
acceptance, update the sketchpad baseline when the workstream remains active,
and promote the corrected meaning into durable authority.

Plan closeout that depends on durable concept extraction is complete only
after promotion passes this standard.

## Enforcement And Backfill

Current enforcement is:

- `AGENTS.md`: `agent_entrypoint_mirror`;
- direct and delegated conceptual work: `execution_contract`;
- touched durable concept surfaces: `touched_surface_rule`; and
- deterministic semantic-fidelity enforcement: `non_current_target`.

No validator can prove full semantic or narrative fidelity. Mechanical checks
may detect stale links, duplicate headings, forbidden structured relationship
metadata, or obvious chronology patterns, but human or agent review remains
required for meaning.

Existing active documents do not become compliant merely because this standard
exists. When an authorized task touches a document with append-only lock
chronology, conflicting current models, or duplicate authority, apply this
standard within the integrated touched scope. Broader backfill requires an
explicitly scoped audit or remediation workstream rather than opportunistic
mass rewriting.

## Compact Completion Test

The lifecycle is complete only when:

```text
one consolidated working baseline
AND verified sketchpad lock fidelity
AND one correct durable owner
AND every accepted semantic obligation preserved or explicitly dispositioned
AND one coherent present-tense narrative
AND no superseded active formulation
AND no invented durable meaning
AND verified promotion closeout
```
