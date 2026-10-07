# Concurrent Development Operations

This optional tool combines deliberately selected changes from an independently
owned Git workspace with a shared branch while preserving unfinished source and
staging. The [operating agreement](../instructions/agent_workflow/concurrent_development.md)
owns the collaboration method; this page explains the implementation and limits.

[`scripts/tools/concurrent_development.py`](../../scripts/tools/concurrent_development.py)
wraps the [portable library](../../repo_platform/concurrent_development/core.py).
Python 3.11 or later and Git 2.38 or later are required. Commands have the same
arguments on Windows PowerShell and Linux shells. No Codex app API, background
service or private monorepo is required. Git author name/email and transport
authentication use the user's normal Git configuration; secrets do not belong in
command arguments or remote URLs.

## Owners and configuration

The tool never creates, deletes or backs up a workspace. With the default
`external_workspace` lifecycle, the user or their existing environment owns the
clone/worktree and its eventual removal. `codex_native` and `runner` record those
actual lifecycle owners when used; selecting a label does not invoke those
products or grant authority over another task.

A local clone or worktree must already exist and belong to the selected writer.
Independent source writers need independent mutable files. Read-only contributors
can share stable inputs. Do not adopt another writer's dirty checkout or remove
source merely because a binding has closed.

The default shared destination is **origin/main**. Set `--remote` and
`--shared-branch` explicitly for other configured Git remotes and branch names.
The destination is recorded at binding and cannot change during the operation.
Repeat nondefault options on every command, including status and close; mismatches
are rejected. The remote must have one matching fetch/push endpoint, so readback
observes the same repository that received publication. The tool does not create
a missing shared branch or choose one by guessing the remote's default.

Local state is outside source, selected in this order:

1. `--state-root` selects an exact explicit integration-state directory.
2. `$AGENT_FRAMEWORK_STATE_HOME/concurrent-development` uses the shared toolkit
   state home when configured (PowerShell: `$env:AGENT_FRAMEWORK_STATE_HOME`).
3. Windows: `%LOCALAPPDATA%/AgentFramework/concurrent-development`.
   Linux: `$XDG_STATE_HOME/agent-framework/concurrent-development`, defaulting to
   `~/.local/state/agent-framework/concurrent-development` when unset.

Retain the same selected directory on future commands. It is operation state,
not backup history.

State is partitioned by the configured repository endpoint. Bindings retain exact
workspace/common-directory paths, owner, incorporated base, remote/branch,
workspace branch, generation and candidate holds. OS file locks and atomic writes
coordinate cooperating processes. They cannot prevent unrelated editors or Git
writers from changing files, so operations also check exact source/index/ref
identities before installing a result.

## Bind an existing writer

Start with zero-effect help; it does not inspect a repository or use the network:

```text
python scripts/tools/concurrent_development.py usage --format json
```

Establish the actual owner identity, workspace and exact incorporated shared
commit. A fresh workspace can use its starting commit; do not infer the base of
already mixed source from a branch name or latest remote revision. Use detached
HEAD or an owned feature branch. Refresh protects the shared branch and refuses
a branch switch after binding. Finish/release a binding before deliberately
changing its ownership or destination.

Invoke the script from the toolkit, with `--root` pointing to the selected project
workspace. Replace placeholders with actual identities; do not run them unchanged:

```text
python scripts/tools/concurrent_development.py --root '<workspace>' bind --binding-id '<binding-id>' --owner-id '<actual-owner-id>' --base '<incorporated-shared-commit>' --owned-path 'docs/example.md' --format json
python scripts/tools/concurrent_development.py --root '<workspace>' status --binding-id '<binding-id>' --format json
```

For an alternate destination, add `--remote upstream --shared-branch integration`
to both commands and all subsequent commands. Global `--root`, `--state-root`,
`--remote`, `--shared-branch` and `--format` work before or after the verb. The
remaining examples omit `--root` only for readability: retain your explicit
workspace selection when the current directory is not that workspace.

Binding IDs use bounded letters/digits/dot/underscore/hyphen. The owner identifies
the actual task, person or worker attempt. Repeat `--owned-path` for known new
files whose unfinished contents must survive incorporation; selectors are literal
relative file paths with forward slashes, not directories, wildcards or pathspecs.
A path binding records source ownership, not authority for unrelated effects.

Rebinding an identical owner returns existing state. A conflicting workspace,
owner or unreadable record requires reconciliation; changing the ID to bypass a
conflict is not a repair. `status` is local read-only and returns the generation
used by prepare/refresh; it does not establish current remote state.

## Save and select ready source

Persist edits normally and make local Git commits where they serve the work.
There is no `save`, snapshot, rotation or backup command. An unfinished local
commit is not a reason to push it to the shared branch.

Prepare an explicitly ready whole-file contribution:

```text
python scripts/tools/concurrent_development.py prepare-integration --binding-id '<binding-id>' --path 'docs/ops/example.md' --path 'scripts/tools/example.py' --message 'Describe the ready change' --expected-generation <generation-from-status> --format json
```

`--path` selects that file's complete change relative to the incorporated base,
including a selected deletion or new file. It does not mean "only staged hunks."
For mixed readiness inside one file, deliberately construct a Git tree rooted
at the binding base with only the ready edits, using ordinary Git and a private
index. Supply that exact tree instead of `--path`:

```text
python scripts/tools/concurrent_development.py prepare-integration --binding-id '<binding-id>' --ready-tree '<ready-tree-oid>' --message 'Describe the ready subset' --expected-generation <generation-from-status> --format json
```

Do not use the current full working tree as a shortcut for that decision. Include
only source intended for publication; credentials, private data, arbitrary
untracked files and generated/runtime debris do not become eligible through
explicit selection. Selection alone cannot detect an embedded secret.

Preparation captures tracked source and explicitly owned new files through a
private Git index. It preserves the real index and working files. It fetches
the current shared commit and constructs two combinations: the ready selection
with shared source, then the remaining complete local source with that candidate.
It refuses a concurrent source/index change. The returned
`data.candidate` contains immutable `candidate_id`, `commit`, `tree`, `parent`,
selection and generation identities; `data.residual_tree` is the retained
unfinished result. `assurance_claimed: false` is deliberate.

The local ref `refs/agent-framework/candidates/<binding-id>` identifies the candidate.
`refs/agent-framework/integration-state/<binding-id>` keeps that operation's selected,
complete and residual trees reachable while they are needed for correct
incorporation. These are current operation inputs, removed at candidate closeout,
not rotating checkpoint history. Further preparation increments the
generation and supersedes an earlier selection only when that candidate has no
active consumer or unresolved publication. If selected source changes, the old
candidate is not silently treated as still ready. Reprepare the intended subset,
or explicitly retract it:

```text
python scripts/tools/concurrent_development.py retract-candidate --binding-id '<binding-id>' --candidate-id '<candidate-id>' --format json
```

Retraction is prospective. It does not undo published source or cancel an
already issued remote job.

## Inspect and establish the combined result

Read the exact candidate and its difference from the returned parent. These Git
commands inspect committed candidate objects without checking them out over the
writer's files:

```text
git diff --stat '<candidate-parent>' '<candidate-commit>'
git diff '<candidate-parent>' '<candidate-commit>' -- 'docs/ops/example.md'
git show '<candidate-commit>:docs/ops/example.md'
```

Choose evidence for the material changed properties and interactions. Existing
results can remain valid where their source, conditions and claim still fit.
A clean merge does not establish semantic compatibility; a local check on
different dirty bytes does not automatically qualify this exact candidate.
Running all checks after every edit or commit is not required.

Respect the selected environment and project resource limits. When an authorized
remote check needs pre-integration source, expose the immutable candidate:

```text
python scripts/tools/concurrent_development.py expose-candidate --binding-id '<binding-id>' --candidate-id '<candidate-id>' --format json
```

Omitting `--expected-remote` requires the remote candidate ref to be absent. To
replace this binding's known prior exposure, supply its exact old commit:

```text
python scripts/tools/concurrent_development.py expose-candidate --binding-id '<binding-id>' --candidate-id '<candidate-id>' --expected-remote '<prior-exposed-commit>' --format json
```

The fixed remote route is
`refs/heads/agent-framework/candidates/<binding-id>`. It does not move the shared branch, authorize
deployment or imply remote garbage collection. A source ref cannot move beneath
an active qualification consumer. Use that consumer's actual execution route
for source admission, cancellation and result collection. The receiving service
must explicitly support fetching the candidate ref; this tool does not configure
or qualify a remote test service.

Remote owners retain/release their actual job's candidate hold. If operating
that interface directly, use the exact issued job identity:

```text
python scripts/tools/concurrent_development.py retain-candidate --binding-id '<binding-id>' --candidate-id '<candidate-id>' --job-id '<actual-job-id>' --format json
python scripts/tools/concurrent_development.py release-candidate --binding-id '<binding-id>' --candidate-id '<candidate-id>' --job-id '<same-finished-job-id>' --format json
```

Release only after the consumer has finished using the source. A stale lease or
an unavailable status call does not establish that fact. Do not add manual holds
when the remote owner already maintains the same relationship.

## Publish and incorporate at a safe boundary

Once the authorized candidate is ready and appropriately checked, publish it
against the exact parent returned by preparation:

```text
python scripts/tools/concurrent_development.py publish --binding-id '<binding-id>' --candidate-id '<candidate-id>' --expected-shared '<candidate-parent>' --format json
```

The command reads shared state, verifies candidate identity and current source
selection, and uses an exact Git lease. The candidate's verified single-parent
relationship to the expected shared commit establishes fast-forward eligibility. The lease is not permission to force-rewrite
shared history. Publishing never writes another task or the main checkout.

`published` and `already_included` mean readback established exact candidate
equality or descendant inclusion in shared history. They return
`refresh_required: true`: the writer's source/index have not yet been changed.
At a safe boundary, inspect current status, then incorporate a returned observed
shared commit that includes the published contribution:

```text
python scripts/tools/concurrent_development.py status --binding-id '<binding-id>' --format json
python scripts/tools/concurrent_development.py refresh --binding-id '<binding-id>' --target '<observed-shared-commit>' --expected-generation <current-generation> --format json
```

The same refresh command incorporates relevant shared source without publication
when no ready candidate is outstanding: publish it or deliberately retract it
first, and reconcile actual consumers or uncertain outcomes. Use an exact known shared
commit; refresh verifies it remains included in current the configured shared branch.
Stop task-owned file consumers before changing their inputs. Refresh preserves
unfinished current source, local commit ancestry and unrelated staging; it does
not perform a blanket stash, reset or rebase. New edits since preparation are
recombined rather than overwritten by the saved residual tree.

Refresh supports the originally bound detached HEAD or owned feature branch.
The shared branch is protected. A branch switch after binding,
sparse checkout, in-progress merge/rebase, unresolved real index, gitlink or
changed symlink needs its specific Git owner/procedure; do not disable guards
or mislabel the workspace to force it through.

## Conflicts, stale identities and uncertain effects

| Observation | Meaning and next action |
|---|---|
| `generation_changed`, `ready_selection_changed` or `workspace_changed` | The assumed source or binding no longer matches. Inspect current files/staging and status. Reprepare or refresh only the intended current contribution; reassess evidence actually affected. |
| `merge_conflict` | The returned role and exact base/ours/theirs/tree identify the unresolved combination. Source/index remain untouched before installation. Resolve the understood mechanical combination through owned Git state; ask the user only for an unresolved semantic decision. |
| `resolution_inputs_required` | Supply the exact identities returned with the conflict, including an immutable ready-tree selection. Carry and residual resolutions also name their complete source tree. |
| `stale_resolution` | Shared source, the incorporated base, or the complete local source no longer matches the resolution's expected input. Preserve the newer work and resolve the current combination. Do not relabel an older resolution with newer expected identities. |
| `stale` publication | Shared history advanced without including this candidate. Reprepare the current selection against the new shared revision and reconsider affected evidence. Identical selection/current-source retries carry prior resolved candidate/residual state forward rather than replaying the original patch blindly. |
| `not_published` | Readback did not establish inclusion and the expected shared ref remains. Diagnose transport and authority; retry the same still-current candidate only after understanding the state. |
| `uncertain` | Transport/readback could not establish the result. Keep the exact candidate and expected identities. Repeat the same exposure/publication operation for its readback/reconciliation; do not retract, replace or close away an unknown remote effect. |
| `candidate_in_use` | An actual consumer still holds the candidate. Finish, reconcile or cancel through that consumer's owner; release its hold only after that relationship ends. |
| `binding_busy`, `index_busy` or `workspace_operation_active` | Another cooperating operation owns state, or Git has an unfinished operation. Identify and finish the actual owner; do not delete another process's lock. |
| `untracked_collision` | Incorporation would overwrite an unselected existing file. Preserve it and resolve its actual ownership before retrying. |
| `refresh_interrupted` | A bound source-update transaction has not completed. Read status and use the exact [refresh reconciliation](#reconcile-an-interrupted-refresh) route; preserve its inputs, newer edits and foreign locks. |

Preparation accepts explicit `--resolved-candidate-tree` and
`--resolved-residual-tree` for the corresponding reported conflicts. Construct
those Git trees from the reported combination and inspect them before retrying.
Every explicit preparation resolution requires `--ready-tree`, `--expected-base`
and `--expected-shared`, using the `ready_tree`, `expected_base` and
`expected_shared` fields returned with that conflict. The base is the binding's
incorporated shared commit; the expected shared commit is the observed
the configured shared branch used by that attempt. They can differ. The exact ready tree
avoids recapturing a changed whole-file selection while reusing an old resolution.
Retain the failed attempt's returned `expected_generation` too. If generation
changed, recombine and resolve the new attempt rather than relabeling an old
resolution with the newly read generation. For example:

```text
python scripts/tools/concurrent_development.py prepare-integration --binding-id '<binding-id>' --ready-tree '<reported-ready-tree>' --expected-base '<reported-expected-base>' --expected-shared '<reported-expected-shared>' --message 'Describe the ready subset' --expected-generation <reported-generation> --resolved-candidate-tree '<resolved-candidate-tree>' --format json
```

Carry and residual resolutions additionally require `--expected-source-tree`
with the conflict's `expected_source_tree` value. This identifies the complete
local source captured before carry or combination, including unfinished edits.
All expected identities are checked before any supplied resolution is accepted.
A changed shared head or source/base causes `stale_resolution` and leaves the
candidate binding and real source/index untouched. Obtain the new conflict and
revise the resolution for its actual inputs; do not just update the expected SHA.
Ordinary preparation without a supplied resolution does not require these guards.
A residual resolution preserves
unfinished work; it is not a second ready-source selection. These options cannot
replace a clean merge with an unrelated tree. Ordinary Git tree/index operations
are sufficient; do not shell-create an unowned worktree to resolve a conflict.

When preparation repeats the same ready selection after shared history advances,
it first carries newer unfinished edits onto the previously resolved residual.
A conflict with role `new_residual_edits` takes `--resolved-carry-tree`: the
complete source tree resolving that exact reported base/ours/theirs combination.
It preserves the earlier resolution and newer unfinished edits before combining
with the latest shared source. It is neither a new ready selection nor the final
residual tree supplied through `--resolved-residual-tree` for a later `residual`
conflict. For an unchanged explicit ready-tree selection, retry with its original
input and reported generation:

```text
python scripts/tools/concurrent_development.py prepare-integration --binding-id '<binding-id>' --ready-tree '<reported-ready-tree>' --expected-base '<reported-expected-base>' --expected-shared '<reported-expected-shared>' --expected-source-tree '<reported-expected-source-tree>' --message 'Describe the ready subset' --expected-generation <reported-generation> --resolved-carry-tree '<resolved-complete-carry-tree>' --format json
```

If the failed attempt used whole-file `--path` selection, retry its explicit
resolution with the exact `ready_tree` returned by that attempt instead of
recapturing those paths. Supply candidate or final residual resolutions only for their
own reported combinations. A carry resolution is accepted only when retaining
the previous ready selection; it cannot replace a clean carry merge with a
different tree.

Refresh separately accepts `--resolved-source-tree`, `--resolved-index-tree`
and `--resolved-head-tree`. Supply only the resolved trees needed for the
reported working-source, staged-intent or local-commit conflict. Retry the same
exact target with the failed attempt's reported generation. Every explicit refresh resolution
also supplies the reported `--expected-base`. Source resolution requires
`--expected-source-tree`; HEAD resolution requires `--expected-head`; index
resolution requires `--expected-head` and `--expected-index-tree`. The conflict
returns all four identities. Supply the checks relevant to the resolutions being
used; unrelated source or staging need not be frozen for a different operation.
If a relevant input changed, `stale_resolution` preserves the newer state and
requires resolving the new combination. For example, after resolving only the
source conflict:

```text
python scripts/tools/concurrent_development.py refresh --binding-id '<binding-id>' --target '<same-shared-commit>' --expected-generation <reported-generation> --expected-base '<reported-expected-base>' --expected-source-tree '<reported-expected-source-tree>' --resolved-source-tree '<resolved-complete-source-tree>' --format json
```

The source tree includes unfinished work, the index tree preserves deliberate
staging, and the HEAD tree preserves local commits combined with shared history.
Retraction advances the ready-selection generation once, so pending resolutions
cannot reuse the earlier candidate's carry behavior after readiness was withdrawn.
Repeated retraction is idempotent; read the current generation before another
preparation or refresh. Existing consumer holds and closeout still use the exact
retained candidate identity.
They are not interchangeable. All combinations must resolve before installation,
and a last source/index/HEAD identity check still rejects intervening edits.

JSON uses the common `tool_result.v1` envelope. Inspect `data.status` and error
details, not just the outer status or process exit. Exit 0 can still require
refresh; exit 1 denotes a stale, not-published/not-exposed or uncertain operation;
exit 2 denotes argument, I/O or a precise core-state error. A changed binding is
not a reason to retry the identical stale generation indefinitely.

## Reconcile an interrupted refresh

Before installing source, refresh records one bounded transaction under the
owning workspace's Git metadata at
`concurrent-development/refresh-<binding-id>`. It contains only the affected
original/replacement files, index inputs, exact HEAD/base/target identities and
operation manifest. The local ref `refs/agent-framework/refresh-state/<binding-id>` keeps
the active operation's Git inputs reachable. Obtain the actual Git metadata root
with `git rev-parse --path-format=absolute --git-dir`; a worktree's `.git` may be
a file, so do not assume a directory beneath its source root.

A killed process or interrupted installation can leave a mixture of the known
before/after state. Other binding mutations stop while this transaction remains.
Read its exact identity through the existing status command:

```text
python scripts/tools/concurrent_development.py status --binding-id '<binding-id>' --format json
```

`data.binding.refresh_operation` identifies a bound unfinished refresh. Establish
a safe boundary with no writer or active file consumer, inspect the reported
state, and choose one of the following outcomes; do not run both as a routine:

```text
python scripts/tools/concurrent_development.py reconcile-refresh --binding-id '<binding-id>' --outcome finish --format json
```

`finish` installs the prepared combined source/index/HEAD and advances the binding
to the selected target. If that incorporation contains the published candidate,
it becomes eligible for ordinary candidate closeout after its consumers finish.

```text
python scripts/tools/concurrent_development.py reconcile-refresh --binding-id '<binding-id>' --outcome rollback --format json
```

`rollback` restores the exact pre-operation source/index/HEAD and incorporated
base. It does not undo shared publication; a published contribution still needs
safe incorporation before candidate closeout.

Both routes compare source bytes and modes, retained transaction inputs, real
index, HEAD and symbolic branch identity. They acquire or recognize only the
operation's own index lock and recheck state after acquisition. Newer edits,
staging, commits or a foreign lock block automatic reconciliation and remain
preserved. Inspect and reconcile their actual ownership; do not delete a lock,
reset new source or modify the journal to force a pass. A source conflict found
before installation still uses the resolution-tree options above, not this
command.

On success the command reports `refresh_finished` or `refresh_rolled_back`,
updates the binding, and removes the active refresh ref and operation directory.
An interruption during final cleanup can be reconciled by the same command; a
completed/no-longer-bound operation returns `no_pending_refresh`. Incomplete
preparation without a valid operation manifest requires explicit inspection of
the reported directory; it is not evidence that source installation occurred.

This journal exists only to finish or reverse one in-progress source mutation.
It has no periodic saving, retained generations, expiration policy, notification
service or off-host copy. It does not replace ordinary file/Git saving or native
workspace lifecycle. Do not retain it after its operation has ended or repurpose
its ref as the rejected unfinished-work backup mechanism.

## Close candidate resources and release the binding

After publication and incorporation, finish actual qualification consumers and
close the exact operation:

```text
python scripts/tools/concurrent_development.py close --binding-id '<binding-id>' --candidate-id '<candidate-id>' --format json
```

For a deliberately abandoned unpublished selection, reconcile any uncertain
effects, retract it, release finished consumers and then close. Needed source
remains in the working owner or its ordinary coordinator handoff. Close deletes
only the owned candidate and integration-state refs with expected-old checks
and clears the active candidate; it returns `workspace_removed: false`. An
unfinished refresh must be reconciled first. Close leaves the workspace and
its binding available for continuing work. It is not workspace archive.

When the workspace's source participation itself is finished, release its empty
binding before the workspace lifecycle removes the workspace:

```text
python scripts/tools/concurrent_development.py release-binding --binding-id '<binding-id>' --owner-id '<same-actual-owner-id>' --format json
```

This removes transient integration metadata after candidate closeout and source
handoff. It removes no workspace or Git history and need not run between
successive contributions from the same continuing task. An orchestration
integration should assign this step to its actual source-handoff owner.

## Relocate bindings after a main-root rename

`relocate-common-dir` owns only the local integration binding path transition
for an authorized same-volume rename of the main checkout. Filesystem movement,
Git worktree repair, native task metadata and other installed path consumers keep
their own owners. This command never moves source, changes Git refs, publishes,
recreates a missing workspace, or releases a candidate/consumer hold.

Coordinate a quiet boundary for the affected repository before moving it. All
cooperating writers must use this owner version; older copies do not recognize
the relocation marker. Finish an active refresh or pending ref update through
its actual owner, and resolve Git locks/operations without deleting foreign
locks. If discovered after moving, physically return and repair Git before
resuming that original operation. Candidate holds may remain; coordinate their
consumers' real path dependencies rather than treating a hold as permission to
stop or discard a job.

Preview from the existing main root, using exact absolute `.git` paths:

```text
python scripts/tools/concurrent_development.py --root '<old-main-root>' relocate-common-dir --from-common-dir '<old-main-root>/.git' --to-common-dir '<new-main-root>/.git' --dry-run --format json
```

The preview writes nothing and uses no locks or network. `data.ready` and
`data.blockers` describe current local readiness; preview success alone does not
freeze writers or authorize movement. Retain `data.expected_state`. Its digest
binds the full selected binding records and identity set, their retained local
candidate/integration/refresh refs, both paths, repository identity and the
physical Git directory's device/file identity. A copy, another clone, or changed
integration state cannot inherit that expectation. Only exact matching
`git_common_dir` records participate; other clones sharing the same remote
partition remain untouched. Existing destination bindings are not adopted by a
fresh preview.

After the authorized physical rename and Git repair, invoke the operation from
the new main root with that original expectation:

```text
python scripts/tools/concurrent_development.py --root '<new-main-root>' relocate-common-dir --from-common-dir '<old-main-root>/.git' --to-common-dir '<new-main-root>/.git' --expected-state '<pre-move-expected-state>' --format json
```

The old location must be absent; junctions, overlapping roots and copied
repositories are excluded. Existing mapped workspaces must resolve to their
exact expected top level and repaired common directory. Absent historical
workspace records are included and reported as absent without reconstruction.
Only `git_common_dir` and workspace paths beneath the moved main root change.
Binding/owner IDs, generations, base, owned paths, candidate descriptors and
states, uncertain-effect flags and consumer holds retain their values.

The operation takes the registry and affected binding locks, rechecks the
expectation, and creates one bounded active marker at
`<repository-state-directory>/relocation/active.json` before atomic replacements.
If interrupted, affected cooperating mutations stop with
`relocation_interrupted`; unrelated clones can continue. A read-only retry with
the same paths, `--expected-state` and `--dry-run` reports which records are
before/after. Retry the original application to finish. Newer or inconsistent
records/refs are preserved and reject reconciliation; do not hand-edit the
marker or binding JSON to force acceptance.

To reverse an interrupted transition, physically return the same main directory
to the original location and repair Git first, then use the original path
direction and expectation with `--outcome rollback`. Only a mixture of the exact
known before/after states is accepted. The marker is removed after successful
finish or rollback, including retry after an interruption during final cleanup.
After a completed transition, a later reverse move starts with a fresh preview
whose source/destination paths are reversed. This bounded marker contains no
source snapshot, backup generations or recurring lifecycle service.

Focused disposable-Git checks live in
`repo_platform/concurrent_development/tests/test_relocation.py`.
They exercise this metadata owner independently of application builds or remote
deployment. Successful fixture checks do not establish that live consumers have
reached their cutover boundary.

## External publication and orchestration rules

This generic tool does not enforce a project's release process, sensitive-file
policy, multi-reference transaction or deployment approval. Use the project's
actual specialized workflow for such transitions; do not treat a generic Git
push as equivalent. Preparing a candidate does not authorize publication.

Protocol Runner is an optional separate integration. A source writer should have
its own attempt-owned workspace and explicit contribution contract. The worker
saves its source and returns the contribution to its coordinator; only the
participant assigned publication responsibility integrates it. Candidate holds
identify actual consumers and remain until their source/output needs finish.
Do not infer file isolation from separate processes or terminate another backend
to make this one work. See the [integration guide](../integrations/protocol-runner.md).

After source handoff and binding release, the external/native/Runner workspace
owner handles cleanup. The integration tool reports `workspace_removed: false`;
that is intentional. Do not use task age as deletion authority. Ordinary Git
saving, candidate refs and refresh recovery are not off-host backup guarantees.

## Verification and platform limits

The focused tests under `repo_platform/concurrent_development/tests` use only
temporary local Git repositories, remotes and state. They exercise source and
staging preservation, partial selections, simultaneous publication, interruption,
explicit conflict resolution, candidate holds and path relocation. Run them with:

```text
python -m unittest discover -s repo_platform/concurrent_development/tests -v
```

Network-filesystem lock semantics and nonstandard Git filesystems need their own
qualification before shared state is placed there. Sparse checkouts, submodule
integration and symlink/junction parent traversal are rejected rather than
silently handled as ordinary files. Windows symbolic links still depend on the
host's normal Git/filesystem permissions. Large repositories retain their actual
runtime/resource limits; no small fixture establishes their performance.
