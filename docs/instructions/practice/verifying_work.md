# Verifying work

Use this guide to establish what can reasonably be relied on in implementation,
design, analysis and operations. Assurance serves the requested result: reveal
meaningful defects or missing conditions before relying on the work, preserve
important existing behavior and support useful truthful conclusions. Passing
whatever tests happen to exist is useful only through the properties they bear
on. The [framework](../framework/README.md) owns the full meanings of evidence,
judgment, authority and completion.

## Select evidence for the actual decision

Identify the material property or claim that needs support, its intended use and
the conditions that could change the next action. Choose evidence capable of
revealing a meaningful failure of that property. Source inspection, an existing
result, a focused test, a real consumer exercise, a simulation or deployed
observation can each be appropriate. Their jobs differ; no one automatically
substitutes for the others.

Start at the owning boundary where a faithful check can expose the issue. Broaden
for actual consumers, shared behavior, configuration, state, cross-boundary
effects or a requirement the narrow check cannot establish. A filename, tier,
coverage percentage or available aggregate does not establish relevance. Shared
fanout, concurrency, data preservation, recovery or security behavior can justify
broader checking when their real uncertainty warrants it. Unknown impact calls
for proportionate investigation, not automatically running everything.

One decisive check can suffice; many passes sharing the same blind spot or oracle
may add little. A test should fail for the material defect, not merely mirror the
implementation or satisfy a count. Mechanical validity can establish a parseable
manifest or resolving link, but cannot establish correct meaning, preserved
intent or a useful design. Intelligent source comparison remains necessary for
those conclusions.

Existing governing requirements remain applicable. Distinguish a required
substantive property whose means can vary from a required procedure or identity
that is itself part of the job. A credible alternative can establish the former;
technical superiority alone does not discharge the latter. See
[requirements, floors and excellence](../framework/Conceptual_Model.md#requirements-floors-and-excellence)
and [evidence and warranted reach](../framework/Conceptual_Model.md#evidence-and-warranted-reach).

## Reach sufficient grounds without unproductive caution

Make the strongest responsibly useful conclusion supported by evidence and sound
reasoning. Neither confidence alone nor defensive uncertainty helps. Additional
investigation, caveats, tests, explanation or delay should have a credible
contribution to the result relative to their full cost, including rework,
maintenance, missed opportunity and the user's attention.

Ask whether a remaining uncertainty could materially change what should be done
or reasonably relied upon, and whether addressing it is worth the cost. Merely
imagining a defect does not justify more work; a rare but consequential failure
can. A justified route can work across the live possibilities without resolving
each one. Explicit requirements are not waived by a judgment that extra work is
unproductive; use another eligible route or represent the exact unmet requirement.

Do not confuse first apparent adequacy with a sound framing. Where investigation
is materially relevant and its depth remains open, look for a productive next
inquiry that could reveal a missed dependency, mistaken question or stronger
approach. Stop when no reasonably identifiable increment has enough credible
value to displace the best alternative. This is the framework's
[productive investigative margin](../framework/Conceptual_Model.md#inquiry),
not a mandatory second pass, source quota or exhaustive adversarial search.

Adequate evidence, stopping investigation, permission to act, successful procedure
execution and completion are distinct. An honestly unresolved finding can complete
an investigation whose purpose permits it; it cannot complete a task that requires
the unresolved property to be established. See [calibration](../framework/Conceptual_Model.md#truth-warrant-uncertainty-calibration-and-intellectual-honesty)
and [success and completion](../framework/Conceptual_Model.md#success-and-completion).

## Check when the information matters

Run a check early when its answer materially guides implementation, establishes a
dependency needed before further work or prevents substantial wasted effort.
Otherwise finish a coherent body of work before checking it. Even a relevant test
can be premature when the behavior it expects is knowingly incomplete. Do not
repeatedly run the same failing aggregate between edits that cannot yet make its
target behavior work.

Reuse still-applicable evidence. A new turn, handoff, checkpoint, commit or phase
does not itself invalidate a result. Reconsider support when relevant source,
dependencies, environment, conditions, method, oracle or intended claim changes.
Check what that change actually affects. Final assurance can combine applicable
earlier results with checks of affected later changes; it does not require a
ceremonial full rerun.

Repetition can be valuable for nondeterminism, statistical variation, changing
environments or a distinct failure opportunity. Preserve those reasons rather
than imposing a maximum run count. The objective is useful assurance at the time
it informs work, not the fewest tests or a new evidence cache, scheduler or ledger.

For example, establish that a provider supports a required operation before
building its dependent workflow. After that succeeds, complete the workflow
before running its end-to-end check. A later documentation-only edit need not
repeat the provider experiment; a change to account scope or retry semantics can
invalidate relevant parts of its support.

<a id="test-observability-before-database-and-index-rebuilds"></a>

## Test progress and feasibility before substantial long-running operations

Do not launch a substantial long-running operation unless tested observation
through the actual operator-facing route shows meaningful progress within every
potentially long phase, available evidence supports a credible judgment of
remaining work and practical completion at the intended scale, and interruption
and recovery preserve completed work within a justified replay bound. Apply this
repository-wide requirement whenever foreseeable duration, resource commitment
or downtime is substantial, including aggregate repeated work. Database/index
rebuilds, reindexing, bulk conversions, history reconstruction, audits, imports
and large computations are examples, not the boundary. Staging, prototypes,
qualification and operations called ordinary saves are not exemptions.

### Estimate duration first and qualify jobs over one hour

Estimate a job's end-to-end duration before launch. Size it by expected runtime
at the intended input scale and execution conditions, not by how much code the
agent will write. Include consequential preparation, construction/conversion,
validation, finalization and cleanup; splitting one workload into small commands
does not hide its aggregate duration. Use relevant measurements and state the
estimate's material assumptions and uncertainty. Unknown duration is not evidence
that a job fits within one hour: establish a credible bound or satisfy the
over-one-hour requirements before launch. Apply those requirements when the
credible estimate range crosses one hour, not only when its optimistic end does.

Any job expected to take more than one hour must, before it starts, have all three
properties demonstrated through its actual execution and operator readback route:

- **Resumable:** restart from validated durable saved work with a justified bound
  on lost/repeated work. A backup of inputs, progress file, atomic rollback or
  ability to rerun from the beginning is not computation resumability.
- **Progress logged to a file:** write meaningful phase and within-phase work
  advancement, timestamps and completed/remaining work or useful bounds to a
  known retained file. Flush updates while running and make them accessible to
  the operator; stdout alone, an unreadable buffer or liveness-only messages do
  not suffice. Protect sensitive content and keep logging overhead bounded.
- **Clean stopping on a signal:** document and test the supported termination
  signal or platform equivalent, such as SIGTERM or SIGINT where applicable.
  It must stop work safely, preserve/publish consistent resumable state and useful
  diagnostics, release owned resources and exit within a demonstrated practical
  bound. A handler that waits for the entire opaque job to finish does not qualify.
  SIGKILL is not a catchable clean-stop mechanism. Test signal delivery and actual
  resume through the owning route with representative bounded cases before launch.

These are pre-launch capabilities, not promises to add them after work starts.
The one-hour threshold is not a timeout or authority to stop an existing job.
It applies in staging and production. Ordinary task authority continues to apply, including its real scope and any
distinct approval requirements; this rule adds no blanket requirement to obtain
approval for every substantial-operation sequence. Changing scope or effects beyond that authority still requires the
appropriate decision. Preserve the existing progress, practical-scale feasibility
and bounded-loss safeguards below, including for shorter high-consequence work.

### Preserve database recovery coverage

Do not automatically delete a newly built, rebuilt, or converted database if doing
so would eliminate its recovery coverage and require recreating the expensive
build or conversion. Without that coverage, deletion requires the user's explicit
authorization, including after failure or cancellation.

Recovery coverage does not require an identical or fully current copy: a usable
earlier snapshot or backup of the new database counts within the accepted
retention practice. Existing authority for routine backup rotation retains its scope; this rule
does not require keeping every version or grant new deletion authority.

The original pre-conversion database or source inputs alone do not count as
recovery coverage for the new build or format.

Apply this to automatic/finally-block cleanup and parent-directory removal as
well as direct deletion. A temporary path or failed validation does not establish
disposal authority. Coverage does not itself authorize deletion or certify the
retained database's correctness, production eligibility or computation
resumability. The [routine backup replacement owner](using_capabilities.md#routine-backup-replacement)
continues to govern ordinary authorized rotation.

### Establish progress and scale feasibility

Before committing the large workload, examine the cost-bearing input dimensions,
algorithm and repeated work, history depth/fanout, later validation/finalization,
and intended resource/concurrency conditions. Use relevant existing measurements
or representative bounded pilots to expose impractical or nonlinear scaling.
A tiny successful fixture or an assumed linear extrapolation is not evidence that
the full job will finish in a practical horizon. Controlled delays test progress
delivery, not runtime feasibility. If that feasibility is materially unknown,
resolve it with bounded investigation before the substantial launch.

Use a representative disposable rehearsal or focused tests that exercise the
actual implementation and the complete intended observation route: execution,
progress emission, collection/retention, status reader and operator-visible
result. Exercise every potentially long phase, including post-input processing,
history reconstruction, validation, finalization and commit where applicable.
A small input that finishes before exposing a long phase is insufficient; use
representative phase-specific cases or controlled delays/failures in the test
environment. Do not require another full-corpus rebuild just to establish
observability.

Before the substantial run, demonstrate that the operator can recover:

- The exact operation identity, active phase/subphase and elapsed time, including
  transitions after the initial input counters finish.
- Meaningful work advancement within every potentially long phase, with defined
  units and completed/remaining or total work. Where an exact total is genuinely
  dynamic, expose the discovered/completed work and remaining frontier or
  cost-bearing milestones, with evidence supporting useful remaining-work bounds.
  The operator must be able to distinguish early from late work and reassess
  practical completion, not merely see a growing counter. State estimate
  assumptions and uncertainty; do not invent percentages, weight unlike units
  equally, or present input consumption as overall completion.
- The age of the latest observation and latest meaningful progress, separately
  from process liveness or resource activity. Establish a useful reporting
  interval for the operation and test that updates arrive while work is running,
  rather than being buffered until return or hidden behind its transaction/lock.
- Actual success, failure and interruption outcomes, with retained diagnostic and
  partial-result evidence accessible through the intended readback route,
  including reconnecting after loss of the launching connection where applicable.

A running label, advancing CPU time, initial/final counters, emitted-but-unreadable
logs or a successful rebuild alone do not satisfy this requirement. For a long
indivisible call, provide internal progress or a tested safe diagnostic route
that can establish its advancement and credible remaining-work bound; a function
name, entry message or CPU sample does not resolve hours of opaque execution.
Unknown exact ETA is acceptable; inability to judge meaningful advancement and
practical completion is not a launchable large operation. Do not promise that a
heartbeat proves useful progress or that every stall can be classified
automatically.

### Preserve bounded work across interruption

Before launch, establish what survives loss of the observer connection, process
termination and the relevant host/storage failure conditions. Reconnecting to a
still-running process is not restarting a terminated one. Protecting the original
data, observing progress and preserving completed computation are separate jobs.
Report the last validated durable recovery point, the work still at risk since
it, and the expected cost of recovery/replay. Choose and justify checkpoint or
restart-unit bounds against the intended scale, failure exposure and operational
cost; do not adopt an arbitrary universal interval.

Substantial work must have a tested safe stop/recovery path that reuses validated
durable progress with bounded lost work. Use the system's existing operation and
state owners: checkpointed batches, independently reusable completed units or
durable staged construction may fit. Keep final publication/activation atomic;
do not weaken integrity, authorization, exact source binding or scientific
semantics merely to commit intermediate work. Where inputs continue changing,
bind recovery to its source snapshot and establish the later-change reconciliation
needed before activation; an old completed snapshot is not current live state.

Exercise representative interruptions during ordinary work and checkpoint
publication through the actual execution, collection and resume route. Establish
that incomplete/stale/incompatible checkpoints are rejected, valid ones resume
without repeating all prior work, and duplicate effects or mixed-state activation
are prevented. Cover relevant phase transitions and long finalization as well as
input ingestion. Use bounded disposable checks and applicable existing evidence,
not destructive production tests or another full-corpus run merely to prove
recovery. A progress file, input backup or atomic rollback alone does not establish
this property; a backup qualifies as a computation checkpoint only when the
tested resume path actually uses its saved work.

An inherently indivisible phase with substantial restart cost is not an exception
to the over-one-hour requirements. If it prevents actual resumability or clean
signal handling, redesign or defer the job before launch; accepting a restart
cost does not establish either capability. Unknown or unbounded restart cost is
not a bounded recovery path. Do not accumulate days of unrecoverable work first.

Repair an inadequate observation, feasibility or recovery foundation before
starting or restarting the substantial operation; do not launch on a promise to
add these afterward.
Reuse passing evidence only while implementation, phases, workload shape/scale,
capacity/concurrency, recovery format and delivery route remain applicable; changes affecting
them require the affected checks again. Retain the useful result and limits in
existing system Testing/Operations owners, not a new monitoring framework or
per-run bureaucracy.
Keep diagnostic overhead bounded and protect sensitive data, transaction
semantics and the primary result. If opacity, impractical scaling or unbounded
loss on interruption is discovered during a run, surface that limitation and its
operational consequences; repeated liveness readings and sunk runtime do not
resolve it. Reassess through the owning operation and
existing authority. This rule grants no new cancellation, restart or modification
authority over an already-running operation.

## Use the appropriate execution owner

Use native component commands or purpose-specific system operations. System
`Testing.md`, manifests and tool help explain useful selection, prerequisites and
limits. A compound operation is appropriate when its whole job is needed; the
existence of that operation does not make it every edit's default.

Discover actual software, data/store and remote qualification commands from
their owning system and component documentation. A list or preview is discovery,
not successful verification. Keep release, remote and custody qualification
requirements scoped to their actual consumers rather than recreating universal
tiers or a generic runner policy.

Respect the execution environment and user-configured resource limits in
[using capabilities](using_capabilities.md#respect-runtime-lifetime-and-host-limits).
An unavailable selected remote route does not authorize a prohibited local run.
Exact committed-source checking must establish the intended source and relevant
environment; an unrelated prior build is not evidence for new code. Qualify this
public toolkit on both Windows and Linux through the supported setup and actual
operating paths; platform-neutral syntax alone is not evidence of portability.

Preserve useful timeouts, process cleanup, output limits, actual exit status and
diagnostic evidence through the chosen execution owner. Bounded output must retain
the failure that matters rather than turn truncation into apparent success.
Timeout handling and cancellation cleanup are different behaviors. A launcher
must not promise one merely because the other works.

## Systems with a staging server

Recover the adopting system's selected execution arrangement before choosing a
build/test route. If the user has designated staging as the build and qualification
owner, use lightweight local checks within host limits and that server for builds
and further testing. Staging should mirror production's software environment
while running the newest production-intended code under evaluation. Do not
introduce GitHub Actions, a separate CI service or another build/test lane in place
of that selection without an explicit user decision changing the arrangement.
Missing staging capability is a staging issue to resolve, not permission to bypass
it. This selects execution location; it creates no mandatory local step or
full-suite gate.

This toolkit ships no staging-server address or account. Existing authorized
CI-and-staging arrangements retain their respective jobs; the mere presence of a
staging host does not revoke them. For a repository without a designated staging
build/qualification owner, use its documented supported environments and actual
resource limits. The guidance does not invent a remote-infrastructure dependency
for ordinary local work.

## Interpret failure and its reach

Investigate a selected failure enough to choose the next action. A product defect,
obsolete expected result, broken environment, invalid check method, route failure
and inconclusive observation imply different repairs and reliance. Do not dismiss
a failure as stale because it is inconvenient, or treat every failed command as a
defect in the product. A process can exit successfully while reporting a violation.

Apply the result to the exact claim and demonstrated dependents. A pre-existing
unrelated failure remains visible where material without invalidating the scoped
change or authorizing adjacent repair. An unexplained relevant failure remains
an actual gap. Wider invalidation requires a supported shared premise, common
cause, contamination, representative failure or inseparable dependency.

Missing evidence constrains the action that requires it. Use a suitable authorized
alternative or repair the broken route where covered; continue independent work
and preserve earned results. A failed preferred mechanism is not global inability.
Stopping a fruitless test loop does not declare the missing property established.
The complete owner is [testing and verification](../framework/Conceptual_Model.md#testing-and-verification).

When a mechanism can block a legitimate intended case, establish that the case
can actually pass as well as that relevant failures are detected, unless adequate
existing evidence already does so. Repair, simplify or replace a broken gate
rather than adding proofs of proofs. CI, a hook, an alert or an assessment result
has only the transition force its actual owner gives it; none creates permission
or a new requirement through implementation alone. There is no universal
fail-open, fail-closed, human-review or all-green rule.

## Preserve useful evidence and describe its limits

Carry enough subject identity, state, conditions, method, result and limitations
for the intended consumer to interpret and reuse the evidence. Existing task
continuity, test output and system result owners often suffice. Do not create a
universal receipt, per-test diary or permanent evidence archive.

Distinguish "not observed", "observed absent", "unknown" and "unavailable".
Absence is evidence only when the method and observation opportunity could have
detected the relevant thing. A health endpoint, log line or accepted request may
support a bounded finding without establishing the user's desired outcome.
Claims of persistence or reliability require a fitting observation horizon;
one success does not prove behavior over every environment or future condition.

Keep material counterevidence and qualifications attached to the conclusion and
honor them in subsequent action. A caveat does not cure operational reliance on
something unestablished. Later successful repair can support current recovery
without erasing earlier effects or making an earlier claim warranted at the time.
Observation and recovery retain their [framework owners](../framework/Conceptual_Model.md#observation-observability-and-monitoring).

## Keep protection and explanation that earn their continued cost

Create enduring checks for concrete future value: subtle behavior, meaningful
regression risk, repeated change, a difficult-to-observe failure or a real consumer
obligation. A transient question can instead use a one-time check. Reuse or
strengthen existing tests when suitable; preserve real behavior without needlessly
freezing implementation details. No test is owed merely because a file changed.

Improve, consolidate, replace or remove checks as their jobs change. Age or rare
failure alone does not prove uselessness. Retiring a validator or generic runner
does not delete useful product tests, security controls, functional input
validation or execution capabilities bundled with it. Handle actual consumers
through [complete retirement](changing_software.md#finish-retirement-rather-than-relocate-obsolete-behavior).

Keep stable selection, setup, non-obvious rationale and limits discoverable in
predictable help, informative tests and owning documentation. A test name need
not be accompanied by duplicate prose; important knowledge that agents repeatedly
reconstruct may deserve explanation. `Testing.md` retains its consistent role
under [system documentation](../repo_organization/system_documentation.md),
without mandatory per-test business cases, matrices or transcripts.

Meaningful self-review and independent challenge can expose errors that tests
miss. Select them for contribution, not a universal second-agent requirement,
approval count or fixed number of rounds. Use the [independent derivation method](../agent_workflow/purpose_coupled_independent_clean_room_standard_v1.md)
when controlling exposure to a particular incumbent has useful decision value.
Fresh authorship is not nonexposure, and ordinary review need not be clean-room.

## Evaluate capability improvements on the claim actually made

When comparing context, tooling, workflows or models, distinguish a coherent
design, implemented mechanism, observed result and demonstrated improvement.
Choose tasks and conditions representative of the claimed use and a credible
comparison. Measure relevant evidence and answer quality, corrections, failure,
recovery and total work where they bear on that claim; a flattering aggregate
can conceal the failure mode the improvement was supposed to address.

For a controlled benchmark or comparable run claim, preserve frozen task, scorer
and run definitions to the degree needed to interpret the comparison. Protect
hidden gold or holdouts where the claim depends on their nonexposure. Calibrate
scoring and inspect important cases; changing the scorer after seeing results
changes the comparison and must not inherit the old claim. Preserve material
provenance, dependence, uncertainty, limitations and explicit nonclaims.

These are conditions of the relevant evaluation, not universal requirements for
every test, design walkthrough or ordinary task. Paper examples can expose gaps
without proving empirical benefit; an internal benchmark does not establish
general superiority. A demonstrated improvement in one use can justify that use
while leaving wider transfer open. The owning system keeps its actual evaluation
definition, results and continuing obligations.
