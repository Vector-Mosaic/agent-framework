# Concurrent Development

This is the operating agreement for concurrent source work. The implementation
and operational limits are described in [Concurrent Development Operations](../../ops/concurrent_development.md).

## Purpose and ownership

One or several humans can work with many agents, tasks and optional Protocol
Runner workers at once. Agents should make authorized progress without requiring
people to save their work, settle routine Git conflicts or remember cleanup.

Each independent source writer owns independent mutable files. Where the client
provides native managed worktrees, use that lifecycle. Where it does not, use an
explicit existing user-managed workspace and the integration helper's documented
external-workspace mode. Runner-owned workspaces retain their Runner owner.
Separate processes are not file isolation. Read-only contributors may share
stable inputs, and artifact-only work may use an existing disjoint output
arrangement. Single-writer work needs no additional checkout merely to satisfy
this document. Delegation preserves the parent outcome, accepted meaning and
coordinator's responsibility for the combined result.

Preserve every participant's source, staging and active execution. Do not reset,
stash, hide, overwrite or absorb another task's unfinished work. Establish the
actual task/run and workspace identity; titles and a guessed latest task cannot
supply that binding. Workspace creation and removal remain with their declared lifecycle owner;
registering an external workspace does not grant the tool deletion authority.

## Saving and shared progress

Save source through ordinary persistent files and appropriate local Git commits
as work proceeds. Unfinished work stays with its working owner and is separate
from shared integrated progress. Saving is automatic agent responsibility; it
does not establish readiness, publish the dirty tree or grant deployment.

The configured remote and shared branch contain authorized, ready, appropriately
checked integrated source. Select them explicitly under the [operations
runbook](../../ops/concurrent_development.md); do not assume a private branch name
or that the repository has a remote. Use automatic integration only when the
user's direction covers publication to that target. Reuse a covering grant without
routine human merge approval; absent that authority, prepare and preserve the
ready local contribution without pushing it. Select the actual ready contribution,
including deliberate hunk selection when one file contains both ready and
unfinished changes. A commit, prepared candidate, tool success or clean textual
merge does not decide semantic readiness.

Combine the selection with the freshly observed shared revision and preserve the
remaining unfinished work. Inspect the changed interaction and establish its
material properties with relevant evidence. Reuse evidence whose source,
conditions and claim remain applicable. Publish only the exact candidate against
its expected shared revision, with a verified descendant relationship. If shared
source moved, recombine and reconsider the affected evidence before publishing.
The conditional ref update protects concurrency; it never permits rewriting
shared history.

Resolve mechanical conflicts autonomously when the intended combined result is
clear within the task's authority. Involve the user when the combination requires
an unresolved product, design or meaning decision. Local conflict, unavailable
checking or uncertainty constrains its actual dependents, while compatible work
continues. A transport failure can leave publication uncertain: establish the
remote result before retrying, replacing or discarding that candidate.

Specialized publication retains its owner. Systems requiring coordinated refs,
artifacts or protected recovery procedures still use those owning transitions;
general source integration must not bypass them. Source publication, remote
qualification, release and provider activation remain separate actions with
their own authority and evidence.

## Safe refresh and closeout

Incorporate relevant shared changes at safe boundaries between coherent editing
or execution steps, after saving local work. Publication must not change another
task's files or replace the inputs of a running operation. Refresh combines the
chosen shared revision with current source and staging, preserves unfinished
work, and stops for reconciliation if the observed files or index changed during
the operation. A later correction to governing meaning still bears when known;
safe source refresh does not extend superseded authority.

An interrupted refresh retains only the exact active operation's inputs and
source/index/HEAD identities so its owner can finish or roll it back. Preserve
newer edits and another process's locks during reconciliation. The operation
releases this state when reconciled; it creates no recurring snapshot history
or unfinished-work retention service.

The task or coordinator closes its integration resources after their actual
consumers finish. A remote qualification job retains its exact candidate until
the job and needed results release it. Candidate refs serve integration and
qualification, not a second unfinished-work store.

Use native archive/cleanup and built-in restoration for native task workspaces
where supported. Do not infer immediate deletion or guaranteed recovery from an
archive control. Runner closes only its own writer workspaces after process exit
and verified source/output handoff. User-managed workspaces retain user-managed
cleanup; registering one with the helper does not authorize removing it. Preserve
material still needed by active work, and report an uncertain lifetime instead of
deleting by age or stale lease. Keep any user-selected automatic native cleanup
behavior without building another retention service around it.

There is no custom unfinished-work backup service, rotating recovery history,
retention schedule, expiration notice or backup prerequisite. Ordinary file/Git
saving and native restoration do not promise off-host disaster recovery.
Existing system data, domain evidence and credential-custody protections retain
their own purposes and requirements.

## Applying the agreement

The [operations runbook](../../ops/concurrent_development.md) owns concrete
creation, binding, selection, integration, refresh and cleanup commands. The
[Protocol Runner playbook](../../integrations/protocol-runner.md)
owns worker participation, backend identity and handoff. The
[framework's distributed-continuity account](../framework/Conceptual_Model.md#distributed-continuity-delegation-and-integration)
explains preservation of meaning, contributions, evidence and parent judgment;
it creates no additional universal form or workflow.

Migration from a shared dirty checkout transfers only positively attributed
changes to the correct owner from an exact base. Preserve original bytes until
the transfer is established. Do not bulk-handoff mixed work, infer ownership of
a jointly edited file or rebind a running Runner campaign. Existing tasks recover
current guidance before dependent work and incorporate shared source at safe
boundaries; activation is not permission to interrupt or rewrite their work.
