# Maintaining the instruction layer

Use this guide when changing framework explanations, practical guidance,
conventions, entrypoints or the mechanisms that expose them. The purpose is a
coherent, usable layer that preserves accepted meaning and helps agents do the
actual work. Document count, brevity, uniformity, enforcement coverage and
procedural conformance are not substitutes for that result.

The [framework](../framework/README.md) retains its complete narrative,
definitions and relationships. These guides apply it; conventions and systems
own their narrower meanings; operations own concrete procedures. A new file or
delivery route does not acquire authority merely by looking canonical.

## Identify the actual change and its authority

Distinguish changing meaning from correcting its representation or delivery.
Restoring an omitted accepted qualification can be a correction. Altering a
trigger, scope, obligation, exception, permission, owner, consequence or
completion meaning is a semantic revision even if described as cleanup,
clarification or simplification. Classification follows the actual effect.

Ground the change in the relevant complete owners and explicit direction.
Framework meaning remains criticizable and revisable, but an implementation task
does not authorize silently redesigning it. A better idea can be recommended
without becoming controlling. Likewise, moving a document, promoting a lock,
adding a hook or making a check mandatory does not itself authorize a higher
level of policy or a new prerequisite for the user's outcome.

Resolve ordinary wording, organization and implementation choices within the
granted task. Ask only for a materially new consequential decision or genuine
authority conflict that existing meaning and delegated discretion do not resolve.
Do not make every heading edit an approval ritual. The complete owners are
[correction and revision](../framework/Conceptual_Model.md#correction-and-revision),
[semantic roles](../framework/Conceptual_Model.md#semantic-roles-and-authority-planes)
and [operation-specific authority](../framework/Conceptual_Model.md#operation-specific-authority).

## Keep one recoverable owner for each complete meaning

Choose placement by the explanatory job rather than the source's former folder
or a desire for symmetry. Full general meaning lives in the framework. Practical
guides explain recurring work decisions. Repository conventions define useful
shared structures. System and north-star owners preserve actual domain purpose,
definitions, behavior, design and qualifications. Runbooks and tool help carry
the knowledge needed to perform their real operations.

An entrypoint states enough of the trigger and immediate invariant for an agent
to recognize the relevant obligation, then routes to the complete owner. A bare
link may be insufficient; copying the entire owner into every entrypoint creates
competing versions. Known exact owners remain directly accessible without a
mandatory sequence through all categories.

Preserve scope, force, authority, conditions, exceptions, relationships,
nonclaims and temporal status when rewriting or moving an explanation. A summary
that retains the nouns but drops a qualification is not full preservation. Keep
the narrative needed to understand why the concepts fit together, not just a
table of isolated labels. Updating every link establishes routing, not semantic
equivalence or completeness.

Remove superseded formulations rather than leave readers to reconstruct which
version wins. A short relocation pointer can route history when needed, but must
not become another maintained definition. Before deleting a source, establish
where each still-needed job and substantive meaning went, what was explicitly
retired and whether any consequential choice remains unresolved. Use actual
source comparison and bounded review, not a second authority matrix or a
mechanical semantic-preservation claim.

## Apply conventions consistently without making them universal

Use a shared convention where it makes fitting work predictable. Specialize
where requirements differ, making the consequential difference discoverable at
its owner. A system can contain several interaction types, and a cross-system
consumer can justify a common convention wider than one system. Repository,
system and interaction conventions are useful scopes, not a fixed profile
hierarchy.

The four [repository convention owners](../repo_organization/README.md) organize
structure, system documents, tool interfaces and application interfaces. They are
not an exhaustive taxonomy of every area that may benefit from consistency.
Configuration, data representation, background work, dependencies, diagnostics
and product interactions may already have adequate owners or need a justified
narrow convention. Absence of a separate document is not itself a gap, and an
unreviewed category is not silently settled.

When documentation is used, keep its purpose, ownership and application
conditions consistent. Proportional content does not authorize putting current
behavior in a vocabulary definition or moving system meaning into a convenient
instruction summary. Required files may remain effectively empty when content
is not needed; that permission neither erases existing knowledge nor excuses
misplacing it. [System documentation](../repo_organization/system_documentation.md)
owns exact roles and presence rules.

Use the [relational expression method](../engineering_standards/relational_expression_kernel_standard_v1.md)
where interconnected information would be easier to understand, find, combine or
maintain through explicit shared subjects and qualified relationships. Its
examples are non-exhaustive; different terminology or no exact example does not
establish inapplicability. Ordinary use needs no formal profile. Keep controlling
meaning with its owner and distinguish derived navigation from that meaning.

## Make the layer usable in actual contexts

Instruction delivery must provide the framework's small continuously available
orientation and a trustworthy route to full relevant meaning. Native startup,
delegation, compaction and resumption are real delivery boundaries. File presence
or a configured loader is not proof that an agent received the intended content
or can recover it when needed. The [recoverable-orientation owner](../framework/Conceptual_Model.md#recoverable-orientation-and-stable-foundations)
defines the requirement and its bounded exception conditions.

When changing delivery, inspect the actual host behavior, relevant limits and
existing support. Assess a specific hook or other mechanism for a concrete job
that native facilities do not adequately serve; do not install an automatic
loader merely because one can be built. Preserve native instruction discovery,
the existing continuity owners and exact mechanism boundaries. Reconcile any
affected restriction deliberately before enabling a change; silent bypass is
not implementation freedom.

Qualify the selected delivery using representative real contexts and the actual
content it will carry. Check consequential renewal as well as initial entry when
those are part of the claim. Mechanism qualification and understanding are
different: a successful injection proves neither semantic fidelity nor useful
agent behavior. A failure constrains the affected route or claim; it does not
authorize weakening the underlying requirement or inventing a global status.

Readiness for activation is not activation. Keep candidate instructions and
their live routing distinguishable during staged migration. Move active entry,
dependent references and the corresponding retirement together at the authorized
transition. Avoid having two simultaneously governing corpora while also
avoiding premature deletion of the owner's only usable instructions.

## Change discovery and consumers with their owners

Trace the real consumers of instructions and support: root routes, category
README files, system docs, tool help, generated projections, catalogs, scheduled
execution, installed host behavior and remaining callers. A source-only edit may
leave the old mandate active elsewhere. A historical citation may instead be
unaffected evidence, not another instruction to rewrite.

Update capability discovery when a tool, provider/account, access route or
operational owner changes. The [tool catalog](../repo_organization/tool_interfaces.md)
and the local [account registry](../../credentials.md) each retain their
own job; account discovery is not another permission or lifecycle system. Derive repeated parser/help/catalog details
where useful and consumer-safe, keeping semantic effects and authority at their
proper owners. Do not run every tool or authenticate every account merely to
change a discovery description.

Retire obsolete rules and their active mirrors completely within the authorized
scope. Separate useful operational functions from policy machinery sharing a
file. Removing a standard must not remove provider access, secret custody,
ordinary tests, functional Runner input validation or protected system behavior.
Nor should those useful jobs preserve an obsolete generic mandate. Follow
[dependency-aware change and retirement](changing_software.md#trace-actual-dependencies-and-effects).

Historical or unsupported mechanisms must not remain discoverable as current
authority. Git normally retains old instruction wording. Keep additional
historical material only for an actual continuing job, with status and boundaries
clear; an archive label cannot make a still-callable fallback inactive. Completed
removals do not revive when a replacement fails. Explicitly preserved access and
nonreactivation boundaries, remain controlling through any carrier move.

## Integrate decisions and learning coherently

The [lock and promotion lifecycle](../agent_workflow/concept_lock_lifecycle_and_promotion_standard_v1.md)
owns thread baselines and durable integration. Preserve exact selected text and
annotation qualifications. A lock does not authorize promotion or action by
itself; authorized promotion integrates a coherent accepted slice into its owner,
reconciles affected explanations and replaces the overlay duplicate with a
pointer. Update dependent to-do items and remove completed material in the same
maintenance pass. Do not build a parallel ledger or append a history of locks to
current authority.

Use real work to notice missing guidance, misleading examples, obsolete
assumptions and needless burden. Investigate the exact problem and improve its
owner when authorized. A repeated observation can motivate a proposal but does
not itself establish a new standing rule. Do not generalize a local exception,
successful workaround or inferred preference into universal policy.

Preserve useful lessons at the smallest fitting continuing owner, including
their conditions and limitations. Storing a lesson is not proof of learning;
later use must transfer it responsibly. Correct errors and retire obsolete
material while preserving necessary evidence and truthful history. There is no
perpetual whole-layer review loop, automatic repair program or obligation to
turn every incident into another document.

## Review the change through use and meaning

Compare the affected old and new explanations against the actual accepted
decision. Check relevant paths, anchors and example commands, and read for
semantic changes hidden by rewording. Work through materially different uses
where they could expose a missing condition, inherited ceremony or conflict
between owners. Keep examples illustrative and non-exhaustive; a guide should
help an agent generalize rather than reject anything that is not a word-for-word
match.

Apply [verification timing and reuse](verifying_work.md#check-when-the-information-matters):
test a delivery assumption early if the rest of the design depends on it, then
check finished coherent content. Reuse still-applicable findings. Do not build
validators for headings, phrase counts or duplicated metadata as a substitute
for judgment, and do not demand proof of empirical improvement merely to make
an authorized faithful documentation repair.

Report the actual horizon: authored, reviewed, qualified, integrated, activated or
observed in use, with the material remaining work. These are descriptions of the
particular result, not a new universal state machine. A complete source account
does not establish complete coverage, and a coherent design does not prove
realized benefit. Conversely, open future improvement does not keep an otherwise
fulfilled bounded change incomplete.

For example, replacing a mandatory testing standard requires removing its
every-commit rule from the root and scheduled callers while preserving useful
system checks. Changing a document's location requires faithful meaning and
working routes, not a new definition of the system it describes. These examples
illustrate the jobs; the actual dependency and authority determine the scope.
