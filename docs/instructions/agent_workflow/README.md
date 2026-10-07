# Agent workflow

This entrypoint routes exact-task continuity, lock/promotion and Concurrent
Development. General purpose, judgment, grounding, authority, evidence,
coordination and completion remain with the complete [framework](../framework/README.md).
[Working with the user](../practice/working_with_user.md), [changing software](../practice/changing_software.md)
and [verifying work](../practice/verifying_work.md) explain their practical use.
Known owners can be reached directly; no category-reading sequence or extra
workflow record is required.

| Work | Full owner |
|---|---|
| Explore, lock, consolidate and promote accepted meaning | [Concept lock lifecycle and durable promotion](concept_lock_lifecycle_and_promotion_standard_v1.md). It owns exact command semantics, scope, checkpoint assessment, readiness, narrative integration and fidelity. |
| Coordinate independent writers, safe refresh and ready source integration | [Concurrent Development](concurrent_development.md), with executable details in [operations](../../ops/concurrent_development.md). |
| Apply useful independent derivation or challenge | [Clean-room method](purpose_coupled_independent_clean_room_standard_v1.md). Its exact purpose, exposure, reconciliation and closure boundaries remain scoped to the selected use. |
| Install, observe or repair native instruction delivery | [Codex instruction delivery](../../ops/codex_instruction_delivery.md). No new delivery mechanism is implied by a continuity need. |

## Pinned thread continuity

Every Codex task has one pinned sketchpad and one pinned to-do as soon as its
actual task identity exists, before other work. This includes small, conversational
and infrequently used tasks. Keep both pinned; minimal truthful content suffices,
and neither light use nor completion is a reason to unpin them.

Use the existing tools with the actual identity:

- `python scripts/tools/codex_thread_sketchpad.py check`
- `python scripts/tools/codex_thread_todo.py check`

Check both even when activation is unknown. Create only a missing body. Pin an
existing unpinned overlay without replacing it; preserve an already-pinned
baseline. A body with damaged/missing metadata is a continuity fault, not
permission to overwrite its content. Never select a guessed latest/title-matched
task. Resume reconciles the existing pair rather than initializing a replacement.
An unavailable exact identity, tool or storage owner is reported specifically;
do not invent a parallel memory or local-lock mechanism.

Read both before continuing. The sketchpad contains the current accepted
unpromoted baseline, relevant reasoning/context and unresolved meaning. The to-do
contains only Now / Next / Parked. Update when the user asks, when goal/reasoning
materially changes or when action ordering/parking changes. Rewrite the current
baseline in place: remove superseded material with no continuing explanatory
value, update dependent actions and then remove completed ones. Complete this
maintenance in the same update, not a future cleanup task. Do not append a
competing version or retain a completion transcript.

A checked action is completion state, not an archival entry. The to-do tool's
`complete` removes it from active state; legacy Done/Completed bodies are cleaned
through its existing read/write behavior. For a layered work map, update the full
body coherently using the supported write command instead of losing dependency
relationships through isolated item edits. Meaningful historical evidence stays
with Git, real status/evidence owners or existing task history, not active actions.

After authorized verified promotion, replace the promoted body in the sketchpad
with a concise durable-source pointer, retaining only current context and open
work. Neither overlay becomes a second repository authority. Use native AGENTS
discovery and the existing tools directly; do not add a continuity hook, automatic
file-body injection, parallel memory store or completion ledger. Home guidance
uses the installing user's explicit workspace and storage paths; use the tools'
`--codex-root` and `--backup-root` selectors.

## Locks, promotion and authority

`lock`, `locked`, `lock this`, `locked as a baseline` and `accepted baseline`
consolidate the affected meaning in one current sketchpad baseline. A highlighted
selection and its annotation define the accepted scope. General agreement does
not silently decide other unresolved intentions or mechanisms. A later explicit
semantic delta may revise the baseline; preserve its qualifications and actual
authority level.

At meaningful lock boundaries, assess whether a coherent settled slice needs
promotion to avoid materially misleading continued work. Name the exact slice,
owner and delta, unresolved exclusions, recommendation and reassessment condition.
A slice whose own plan or material companion boundaries remain unresolved is not
promotion-ready merely because some text is settled. A broader workstream can
remain open when the exact slice stands coherently alone. This checkpoint duty
flags the decision; it does not authorize promotion.

`promote`, `make durable` and `canonize` integrate complete accepted meaning in
its competent durable narrative. `lock and promote` performs both operations.
Preserve semantic obligations and explanatory relationships while removing
obsolete containers, chronology and redundant copies. Promotion increases
durability, not hierarchical authority: an operational mechanism cannot become a
new prerequisite or definition of its parent outcome without covering authority.
The [complete lifecycle owner](concept_lock_lifecycle_and_promotion_standard_v1.md)
remains controlling for readiness, fidelity and verified closeout; this router
does not replace it with a shorter procedure.

## Concurrent work and continuation

Multiple agents and optional Runner workers may work with one or more humans.
Follow the [Concurrent Development agreement](concurrent_development.md) for
independent mutable workspaces, ordinary saving, authorized integration of ready
changes, mechanical conflict resolution, safe refresh and lifecycle ownership.
The user-configured shared branch carries ready integrated progress, not
unfinished checkpoints. Managed native, external user-owned and Runner-owned
workspaces retain their distinct lifecycle owners. The helper is optional for
single-writer local work and requires no particular desktop client's worktree API.
Do not create another backup/retention system or general coordinator.

Carry the parent outcome, relevant authority, constraints, evidence, earned state,
unresolved decisions and possible effects into delegation. A worker's result
contributes to parent completion; the coordinator keeps integrated judgment.
A context replacement, commit or local contribution is not semantic reset or a
reason to stop. Recover fit state, apply material authority corrections before
dependent action, preserve unaffected progress and continue the requested work.
Report the achieved result, meaningful evidence and remaining work at its actual
scope rather than presenting a checkpoint as full completion.
