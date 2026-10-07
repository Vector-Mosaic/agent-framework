# Relational Expression Kernel Standard v1

- version: v1
- status: current standard
- scope: repo-wide practical method for representing interconnected information
- category_authority: `docs/instructions/README.md`
- enforcement_state: `baseline_guidance` and `touched_surface_rule`; no universal
  semantic hard gate
- default_read_posture: read purpose, applicability, practical method, and
  conventions for applying the method; use examples to understand and adapt
  it; read formal requirements only for an adopted profile or formal claim
- public_adaptation: preserves the practical method; examples are synthetic

## Purpose

The **Relational Expression Kernel** is a reusable method for organizing
interconnected information so agents can understand, navigate, combine,
maintain, and use it more efficiently and reliably. Its structure carries
meaningful relationships explicitly, reducing repeated reconstruction from
scattered or duplicated explanations. Seek broad use wherever this improves
the actual work, with consistent conventions and deliberate application.

The method gives shared subjects a stable home, gives relationships their own
explanations, keeps qualifications with the information they qualify, and
supports different ways of reading the same body of information. This should
help an agent reach the information it needs, understand why it matters, and
recognize what a change affects without rebuilding the explanation each time.
Preserving meaning and improving use are joint purposes.

Efficiency includes reading and interpretation effort, context needed for a
task, repeated analysis, and maintenance cost. Shorter text or fewer records
alone do not establish an improvement. Dense notation, excessive fragmentation,
or a web of pointers that requires opening everything can defeat the purpose.
Retain rich prose wherever it carries the explanation well.

These are intended benefits and design criteria, not a claim that comparative
performance has already been demonstrated. Applying the method does not require
a new benchmark. Implementations and evaluations are separately scoped work.

The established name and path remain the method's discovery route. Here
`relational` means meaningful connections, and `kernel` means the reusable
expression concepts below. It does not denote executable code or a database.

## Read And Apply

- [Applicability](#applicability-and-deliberate-use) explains when to use the
  method, independently of examples.
- [Practical method](#practical-method) covers constructing, reading, combining,
  and changing a representation.
- [Reusable conventions](#reusable-expression-conventions) supplies the shared
  distinctions that make different applications understandable.
- [Worked applications](#worked-applications) demonstrates actual use and
  explains what transfers.
- [Optional formal profiles](#optional-formal-profiles) is for jobs that adopt a
  formal interpretation contract or make a conformance claim.

[AGENTS.md](../../../AGENTS.md) carries the active trigger. The
[instruction router](../README.md) routes this owner. The
[docs schema](../repo_organization/system_documentation.md#navigation-execution-plans-and-retention)
preserves document roles and ordinary navigation while permitting substantive
relational content in its owning document. The existing
[dependency-trace](../practice/changing_software.md)
and [lock/promotion](../agent_workflow/concept_lock_lifecycle_and_promotion_standard_v1.md)
owners govern affected dependencies and accepted changes; this method creates
no parallel change ledger.

## Applicability And Deliberate Use

Determine applicability from the information's relationships and the work the
representation would support. Examples are illustrative and non-exhaustive.
Different terminology, subject matter, or structure, and the absence of a
matching example, do not establish that the method is inapplicable. Adapt the
method while preserving the application's actual meaning.

Use the method when explicit subjects, relationships, and qualifications would
materially improve understanding, navigation, combination, or maintenance.
That can include shared information used for different reasons, dependencies
with conditional consequences, evidence bearing on exact claims, alternatives,
multi-party relationships, or information that needs reading from several
directions. These are recognizable opportunities, not a checklist, exhaustive
catalog, or requirement to match a named pattern.

Agents should construct or improve a useful representation as part of
authorized work when those benefits apply. Do not limit application to
preventing an obvious meaning error, wait for ordinary prose to become
incapable of expressing the information, or require the user to invoke the
method by name. Follow the conventions here and any applicable owning-domain
conventions rather than inventing unrelated ones for each task.

Ordinary application, historically called **kernel-guided expression**, needs
no formal profile, applicability record, conformance exercise, or special
permission. It can be a useful table within an existing document, a connected
set of readable records, a diagram with explained relationships, a transient
working representation, or a richer structure where the job needs one.
Applying the method alone authorizes neither changing domain meaning nor
creating a new persistent tool, registry, or parallel information owner.

Use an existing adequate representation directly. Keep simple information
simple; apply the portions of the method that help. An application does not
need every construct. Persistence follows the information's continuing job and
existing owner, not the mere presence of relationships. Useful representation
work is not contingent on completing a separate formalization project.

## Practical Method

The following describes how to do the work. It is not a required report,
artifact set, sequence of approvals, or new document schema.

### Start From The Work And Its Meaning

Identify what an agent needs to understand, decide, find, combine, or change.
Locate the information and its controlling sources, accepted meanings, and
material uncertainties. These establish what the representation must carry
and which connections will be useful. A representation for finding change
effects may emphasize dependencies; one for comparing explanations may
emphasize claims, evidence, and conditions.

The method can express information being authored and maintained directly, or
organize information whose sources remain elsewhere. A representation may
also contain both, with clear ownership of the different meanings. A proposed
design remains proposed, an observation remains an observation, and an
inference remains an inference. Structure does not promote their authority.

Preserve established vocabulary and document purposes. A system's Vocabulary,
Architecture, Behavior, Contracts, or Operations document retains its job when
its content uses this method. Do not move all system meaning into a separate
graph or copy it into a second editable source.

### Construct An Intelligible Representation

Find the subjects that need distinguishable identity, shared use, independent
qualification, navigation, or maintenance. Keep each exact shared meaning in
one controlling logical home within its scope. Similar wording alone is not
grounds to merge subjects. A coherent explanation may stay together as prose;
splitting every sentence into a record adds work without necessarily helping.

Express substantive relationships with their participants and roles, the
meaning of the connection, and the explanation particular to that use.
`A depends on B` can be insufficient: an agent may also need what A needs
from B, why it needs it, and under what conditions that dependency matters.
Put that explanation with the relationship, so referencing B elsewhere does
not import all of A's needs.

Attach qualifications at the exact subject, relationship, or participant use
they affect. Keep shared conditions separately addressable when several uses
need the same condition. Keep unresolved meaning visible at its real scope
without manufacturing an answer or making unrelated information uncertain.

Choose a readable form that fits the owning surface and consumers. Use the
shared conventions below consistently within the application. Adapt physical
format and domain vocabulary deliberately; preserve their meaning when moving
between forms. The conventions describe what readers need to recover, not
mandatory field names, IDs, storage, or syntax.

### Read And Navigate For A Task

Start from the subject, relationship, question, or change relevant to the task.
Follow connections that bear on it in the needed direction. Include the
relevant subject meanings, relationship explanations, conditions, and
significant limits together in a task-focused reading. Expand when a missing
dependency or qualification could change the answer; there is no fixed hop
count or requirement to load the complete body.

An index or reverse lookup can identify connections using a shared subject.
The connections then explain how each use matters. Navigation finds relevant
meaning; an arrow followed backward does not reverse the relationship's
semantics or establish a new fact.

A useful reading includes enough explanation to be intelligible without
opening every pointer. Derived presentations may repeat names and relevant
content for reading convenience, with their source and limits clear. One
controlling home does not require every presentation to be an opaque ID list.
Repeated presentation must not become independently editable competing truth.

Make scope and material omissions clear when presenting a selection. Omitted
information is not thereby nonexistent. A useful bounded answer need not
claim to reproduce the whole representation.

### Combine Information Without Flattening Its Meaning

Reuse a shared subject when its meaning and identity are actually the same.
Keep distinct subjects distinct where scope, version, referent, or meaning
differs. Match definitions and controlling sources rather than labels alone;
keep identity unresolved when it has not been established.

Retain each relationship's own reason, role, conditions, and consequences.
Two uses of one capability may have different requirements. Two observations
about a claim may support different parts of it. Combining their presentation
does not transfer a qualification, evidence strength, permission, or support
promise from one use to another.

Interpret relationships according to their owning domain. Connecting
information from different domains does not import their rules wholesale.
If combining it yields a new inference or decision, express that meaning and
its basis explicitly under the appropriate owner rather than hiding it in a
navigation view or treating shared shape as agreement.

### Maintain Meaning And Follow Actual Change Effects

Change the owning subject or relationship, then use its connections to locate
potentially affected information. Judge the actual effect from what changed
and each relationship's explanation and conditions. Reachability identifies
candidates for reconsideration, not automatic invalidation of everything
connected.

Update affected references, dependent explanations, and retained derived views
as needed. Preserve unaffected conclusions and evidence. When using an older
derived view, account for source changes that could affect the current task;
do not present an old source state as current.

Distinguish reorganizing the same meaning from revising it. Moving prose,
changing a carrier, or rebuilding navigation should preserve identity, scope,
roles, qualifications, and authority. A changed participant, broadened rule,
merged subject, or new consequence may change meaning and requires the
authority appropriate to that work. IDs and unchanged wording do not prove
semantic continuity.

Select verification when it informs the actual next decision or establishes
a material property affected by the change. This method creates no automatic
test, benchmark, receipt, or full-graph revalidation obligation.

## Reusable Expression Conventions

Use these concepts to make applications consistently understandable. Within
the kernel's expression grammar, the independently identifiable constructs are
**elements** and **typed connections**. Bindings, intrinsic meanings, and local
qualifications describe an exact owner; views present information. These
distinctions do not require a particular physical record layout.

| Concept | Meaning and practical use |
|---|---|
| Element | A distinguishable subject or logical position that can be shared, referenced, qualified, or maintained independently of one exact connection. |
| Typed connection | One substantive relationship among participants in specified roles. It carries the exact-use explanation and any domain-defined consequences. A connection may itself be referenced or participate in another connection. |
| Participant binding | Which exact subject or connection occupies which role in a connection. A qualification may apply to one binding without applying to the other participants. |
| Intrinsic meaning | Information belonging to that element or connection wherever it is referenced. A reason specific to one use of a shared element belongs to that use's connection. |
| Local qualification | A condition, limitation, explanation, or unresolved aspect belonging to one exact element, connection, or binding. Represent it independently as an element or typed connection according to its meaning when it acquires independently shared or related meaning. |
| Explicit reference | An unambiguous designation of the intended subject or connection within its scope. It supports navigation; it does not by itself assert another relationship or transfer authority. |
| Derived view | A selection or reorganization for a task: reverse navigation, a comparison, an outline, a diagram, a summary, or another presentation whose controlling meaning remains with its sources. |

Use readable names with enough scope to distinguish their referents. Use
stable local references where independent links or updates need them; reuse
existing domain identities when applicable. No universal ID format or global
registry is required. A heading anchor, record key, or other explicit reference
can serve; proximity, similar names, indentation, and file location alone do
not define a semantic connection.

Keep relationship direction and participant roles clear. A multi-party
relationship should remain one occurrence when the role combination is what
makes it that relationship; several independent binary facts should remain
separate. Referencing or qualifying a connection does not turn it into an
element.

Define domain-specific relation terms and operators where they are used or
link their established owner. Terms such as `supports`, `requires`, `alternative`,
and `condition` do not have universal consequences. State whether all or some
participants are needed, what a condition controls, or what an observation
actually supports when that affects interpretation. Cycles, roots, ordering,
closure, and completeness likewise belong to the application.

Keep each qualification local unless an explicit domain rule gives it wider
effect. Explain significant limitations alongside the information they limit.
A relationship can carry an explanation in prose; a bare edge label is not a
substitute for the reason an agent needs.

Separate source content, interpretation, and reading aids where their roles
differ. An ordinary representation can be its domain's accepted maintained
expression without a formal conformance claim. In the formal terminology
below, **canonical relational structure** denotes the explicitly
profile-governed case. Neither spelling nor format makes information true.

When several applications share useful conventions, reuse the conventions
and their explanation through the existing owner. Specialize for actual
domain differences. A formal profile is available when an explicit shared
interpretation or conformance contract serves a further job; ordinary reuse
of the method does not require one.

## Worked Applications

These applications develop the method through construction, use, and change.
All names, requirements, observations, and quantities below are hypothetical.
They are not repository system facts, accepted product designs, universal
schemas, or required documents.

The tables are readable representations for these examples. An application
may use a different carrier, vocabulary, or structure. Transfer the reasoning
about information and its use; do not require a matching topic or copy the
examples' domain rules.

### Shared Capability In A Product Design

An agent is designing interactive and scheduled CSV exports. It needs to
explain shared behavior, preserve each consumer's requirements, and identify
what future changes affect. The proposal uses one configurable CSV encoder.
Here `uses` means a design dependency on the stated capability, not a generic
operator with consequences supplied by graph shape.

Start with these subjects:

| Local reference | Meaning maintained here |
|---|---|
| `interactive-export` | Produces a downloadable CSV from the records selected in an interactive session. |
| `scheduled-report` | Produces a nightly CSV using a saved report configuration. |
| `csv-encoder` | Converts supplied rows into escaped CSV according to supplied formatting options. Does not select records or deliver files. Its default date format is ISO date; an explicit date-format option overrides that default. |

Represent each use separately:

| Local reference | Participants and relationship | Meaning belonging to this use |
|---|---|---|
| `interactive-encoding` | `interactive-export` uses `csv-encoder` | Column order comes from the current selection. Delimiter is comma. Date formatting uses the encoder default. |
| `scheduled-encoding` | `scheduled-report` uses `csv-encoder` | Column order comes from saved report configuration. Delimiter is semicolon. Date format is explicitly `DD/MM/YYYY` because this report's consumer requires it. |

The construction puts encoder behavior in one shared subject. The report
consumer's date-format requirement belongs to its exact use. It does not
become a requirement of all CSV output or an intrinsic restriction on the
encoder. The relationship bodies preserve reasons that a diagram with two
unexplained `uses` arrows would omit. These remain proposed design commitments;
they do not establish implemented behavior.

To answer **which date-format changes affect scheduled reports**, read
`scheduled-encoding` together with the encoder's option/default behavior.
A task-focused view can contain:

> Scheduled reports explicitly request DD/MM/YYYY for their consumer
> (scheduled-encoding). The encoder honors an explicit format before its
> default (csv-encoder). Changing only the default does not change the
> specified report format; changing override behavior may.

This reading carries the relevant relationship, shared meaning, and reason
together. It is derived from the two owning entries, not a separately maintained
copy of the design. The unrelated delivery and interactive-selection details
need not be reconstructed to answer that question.

Now suppose the encoder's default changes. Reverse navigation from
`csv-encoder` finds both uses. Reading the relationships shows that
`interactive-encoding` relies on the default and needs reconsideration;
`scheduled-encoding` explicitly overrides it. If escaping behavior changes
instead, both uses may be affected. The connections find candidates; their
meaning determines the actual effect and appropriate checking.

A different scheduled consumer might require another format. Express its own
use rather than changing the shared encoder definition to imply every consumer
needs that format. Conversely, do not merge two implementations merely because
both are called an encoder if their meanings or promised behavior differ.

The transferable method is shared subjects with separately explained uses,
task-focused reading, and meaning-based change impact. It applies to designs,
plans, policies, resources, and other interconnected information. CSV options,
nightly scheduling, and this example's dependency interpretation are local.

### Evidence And Requirements In A Decision

An agent is comparing two document parsers for a product. In this hypothetical
case, the product needs paragraph and table extraction from born-digital PDFs,
and its accepted deployment requirement keeps document contents on the local
machine. Existing source material includes a sample observation and a service
access description. The agent is organizing that material, not conducting a
new test.

Use these subjects, retaining their actual source pointers in a real
application:

| Local reference | Meaning or source scope |
|---|---|
| `extraction-need` | This product needs paragraph and table extraction from born-digital PDFs. |
| `local-data-rule` | Document contents must remain on the local machine for this deployment. |
| `birch` | Candidate local parsing library. |
| `cedar` | Candidate hosted parsing service. |
| `sample-observation` | Existing comparison on twenty born-digital PDFs; paragraph extraction was assessed and table extraction was not. |
| `cedar-access-doc` | Source description states that the evaluated Cedar route requires uploading document contents. |

Allocate candidate-specific results and interpretation to relationships:

| Local reference | Participants and meaning | Qualification or reason |
|---|---|---|
| `birch-observed` | `sample-observation` assesses `birch`: correct paragraph extraction in nineteen of twenty samples. | Limited to the stated sample and paragraph assessment. |
| `cedar-observed` | `sample-observation` assesses `cedar`: correct paragraph extraction in twenty of twenty samples. | Limited to the stated sample and paragraph assessment. |
| `cedar-local-conflict` | `cedar` conflicts with `local-data-rule` for the evaluated route. | The route requires uploading the protected document contents. This is a deployment-suitability interpretation, not a statement about general extraction quality. |
| `upload-basis` | `cedar-access-doc` supplies the upload premise used by `cedar-local-conflict`. | Applies to the route described by that source; it does not establish how every possible Cedar route works. |

The observation owns the sample and assessment scope once. Each assessment
owns its candidate-specific result. The conflict joins a candidate with a
particular deployment requirement. The source relationship points to that
exact conflict, demonstrating that a relationship can itself be a participant.
Its explanation identifies the premise supported by the source rather than
claiming that a document automatically proves every conclusion about Cedar.

For **why Cedar conflicts with this deployment despite the higher observed
paragraph result**, start at `cedar-local-conflict`, follow `upload-basis`,
and read the deployment requirement and access description. A concise view is:

> This route uploads document contents (cedar-access-doc, via upload-basis).
> This deployment requires those contents to remain local (local-data-rule).
> That is the basis of cedar-local-conflict. The separate paragraph comparison
> does not remove that conflict or establish general inferiority.

For **what is known about table extraction**, start with `extraction-need`
and `sample-observation`: table extraction matters to the product, but this
observation assessed paragraphs only. The candidate results preserve their
sample meaning and support no table-extraction conclusion. The representation
makes the gap discoverable without pretending the candidates lack the feature.

If the deployment later permits uploads, reconsider `cedar-local-conflict`.
The source statement that this route uploads content and the recorded
paragraph observations do not change. If the product adds scanned PDFs,
retain what the born-digital sample established while recognizing that its
scope does not answer the new question. Choose additional investigation when
it helps the next decision; adding a relationship creates no testing ritual.

The transferable method separates sources, requirements, and interpretations;
shares evidence without broadening it; permits explanation about an exact
relationship; and localizes reconsideration. The same method can support
research, diagnosis, planning, or policy interpretation. The twenty-document
sample, parser criteria, and local-data requirement are not general rules.

### Generalize Beyond These Examples

Recognize the information problem: what is shared, what belongs to one use,
what qualifies which meaning, and what the next reader needs to do. Then
adapt the construction, reading, and maintenance conventions to that problem.
A temporal history, an explanation with alternatives, or a relationship with
several participants may need a different shape from either table above.

Keep the general method explicit independently of its examples. Examples
explain application choices and their reasons; they do not delimit eligible
domains, require a fixed structure, or supply unspoken domain semantics.
Agents need not search an example catalog before applying the method.

## Optional Formal Profiles

Read this section when a job has adopted a relational-expression profile,
when designing or revising such a profile, or when making a formal conformance
claim. Complexity, persistence, reuse, or use of a worked example alone does
not require a profile or a profile-applicability review. Ordinary practical
use above is complete in its own right.

A **relational-expression profile** defines the bounded interpretation contract
for a job that benefits from explicit shared types, roles, consequences,
interoperability, or conformance. It can be a compact part of existing owning
documentation; no separate file, catalog, registry, or empty section is
required. Existing adopted profiles retain their domain meanings and
obligations until deliberately revised by their owner.

### Authority And Scope

The kernel supplies the shared expression grammar. The applying domain owns
facts, definitions, accepted intent, decisions, and consequences. A profile
defines how that domain's meaning is represented and interpreted; it cannot
create or overrule domain truth, evidence, consent, authorization, ontology
admission, or implementation support.

A **canonical relational structure** is the accepted controlling logical body
of meaning for its declared scope, with one controlling profile for each exact
governed region. Its topology and payload may jointly be the domain's
authoritative expression. A profile defines interpretation rather than
duplicating the structure's content. Canonicality is neither global truth nor
physical uniqueness: one logical structure can span files or have derived
presentations, without several independently editable authorities.

Within this grammar, elements and typed connections have independent
occurrence identity. Intrinsic meaning, bindings, and local qualifications
are owned through their exact occurrence. The domain/profile defines
substantive identity, types, roles, and permitted consequences. The kernel
does not prescribe a universal ontology or completeness model.

### Applying-Profile Contract

Address the following where material to the formal job. These are questions
to answer coherently, not mandatory headings, fields, or forms; unused
constructs need no boilerplate.

| Responsibility | What the profile establishes |
|---|---|
| Boundary and authority | The job, domain owner, inclusions and exclusions, applicability scope, canonical-authority scope, structure-instance boundary, and identity/duplicate scope. Keep distinct scopes distinguishable even when they coincide. |
| Vocabulary and allocation | Applicable element and connection types, participant roles, intrinsic and binding meaning, qualifications, and composition. Every retained meaning has one controlling home. External vocabulary may be referenced when its owner, scope, interpretation, and allowed use are clear. |
| Identity and participation | Same, equivalent-but-distinct, revised, duplicate, and distinct occurrences; cross-structure identity; legal participant kinds and roles, arity/cardinality, self or repeated roles, connection-as-participant rules, direction/symmetry, ordering, and external references as applicable. |
| Consequences and qualifications | What each construct means and entails; qualification owner, scope, conflict rules, and any propagation. No unstated consequence or propagation is the default. Define actual conjunction, alternatives, conditions, precedence, or other operators rather than inheriting meaning from shape or familiar names. |
| Integrity and semantic closure | Permitted constructs and external delegation; applicable topology, reference, participation, convergence, and duplicate constraints. Undeclared meaning requires a scoped revision or remains outside the conforming body. |
| Sufficiency and integration | What the structure must contain for its claim, allowed partiality and omissions, unresolved-reference treatment, required closure, external boundaries, and any explicit cross-profile handoffs. Structural completeness is not exhaustive domain knowledge or source coverage. |
| Conformance and limits | Which exact profile/structure/view is assessed, which obligations apply, and what the resulting conclusion does and does not establish. |

A materially different semantic job or incompatible authority boundary needs
its own bounded profile rather than an exception-laden universal profile.
Changing only a carrier, query, view, consumer, or file does not create a new
semantic job.

### Composition, Identity, And References

Reuse accepted profiles where their semantics fit. Structures may contain
separately governed regions or chain jobs through explicit handoffs. Identify
the profile/revision and the handoff's participant role, permitted referent,
direction, qualification, and consequence. Every exact governed meaning has
one controlling interpretation. Shared spelling creates no implicit
inheritance, profile import, truth transfer, or compatibility.

The referencing profile owns its local reason and consequence; the referenced
domain retains target identity, meaning, acceptance, and revision. A new
substantive cross-profile relationship needs an explicit domain owner and,
when formal conformance is claimed, its governing profile. Merely navigating
across structures does not create that relationship.

Distinguish identity from equivalence, shared reference from copied
manifestation, and similar distinct occurrences from competing homes of the
same meaning. Do not narrow scopes to hide competing authorities. Matching
labels, bytes, IDs, participants, paths, or formatting alone do not establish
identity or authorize merging, splitting, or choosing a surviving meaning.

A reference must designate the intended referent unambiguously within its
scope. Distinguish designation from establishing that the target exists and
is eligible for the consequence claimed. An unresolved reference cannot
silently bind to the nearest similar name or count as resolved participation.
A profile may permit bounded unresolved designations with explicit limits.

### Conformance And Fidelity

Keep four judgments distinct:

- A profile conforms to the kernel's expression and authority rules.
- A structure conforms to its profile's interpretation and sufficiency rules.
- A physical representation faithfully carries the accepted meaning.
- A derived view faithfully selects or reorganizes its source meaning.

None alone proves domain truth, utility, evidence sufficiency, implementation,
runtime behavior, domain acceptance, or broad coverage. A conforming structure
is not automatically accepted as the canonical one.

For the relevant formal claim, establish permitted meaning allocation, scoped
identity, roles and participants, references, direction, qualifications,
composition, and the domain's actual integrity/sufficiency requirements.
Grammar closure, reference closure, structure closure, and domain/source
coverage are different questions. There is no universal DAG, root, cycle,
terminal, orphan, global identity, or exhaustive-coverage requirement.

Faithful physical representation preserves all accepted distinctions within
its responsibility or references their owners. Its interpretive features must
be grounded in accepted meaning, and readers must be able to recover roles,
scope, identity, direction, qualifications, consequences, and external
boundaries without hidden conventions. Schema validity and byte equality are
not substitutes for semantic fidelity.

A derived view keeps sources, scope, transformations, material omissions, and
currentness limits recoverable. It may repeat information for useful reading,
filter, summarize, or reorganize. It may not conceal a qualification,
manufacture equivalence, turn reverse navigation into inverse truth, imply
partial content is complete, or hide a new inference in an aggregate. A view
of an older source state can be faithful to that state without being current.

Use evidence appropriate to the exact claim. Relevant semantic review, source
comparison, reference checks, or domain-specific tools may suffice; this
standard requires no universal validator, receipt, score, benchmark, or
independent review ritual. A material unmet obligation limits its own
conformance claim; unrelated successes do not compensate for it.

### Changes And Existing Applications

Route a change to the meaning it changes: domain meaning to its domain owner,
profile interpretation to the profile owner, representation conventions to
this method, and carrier or view behavior to its actual implementation owner.
Existing authority for the task governs execution; this is not an additional
permission process. User-authorized improvements to this method do not require
proving a universal contradiction or collecting repeated cross-profile
failures first.

Make material changes clear enough to preserve purpose, identity, scope,
authority, and affected consumers. Use the existing dependency and
lock/promotion workflow. No separate semantic-delta form, new ADR mandate, or
kernel-specific change ledger follows.

Carrier changes and meaning-preserving normalization retain interpretation.
Choosing a new participant, merging meanings, moving a qualification to a
broader owner, or changing consequences is semantic change even if performed
by a formatter or repair tool. Tools can diagnose and perform authorized
transformations; their outputs do not acquire authority to decide meaning.

Follow actual affected dependencies, preserve unaffected conclusions, and
update or retire superseded interpretations and views. A migration retains
one controlling meaning and a clear maintenance route; any necessary
temporary copies must have explicit ownership and cutover. Restoring old bytes
does not by itself restore an old accepted meaning or authorize a semantic
rollback.

Existing Interface Readiness and Family Genealogy profiles retain their
specific meanings, restrictions, and support claims. This general method does
not change their declarations or establish their conformance. MPE's
node-identity backchain design supplied a motivating case; its proof roles,
operators, source anchors, closure, identity, navigation, and encoding remain
MPE-owned. Neither ordinary use nor this revision converts existing designs
into conforming formal structures.

This standard supplies a practical method and optional formal interpretation
rules. It does not instantiate a runtime, graph store, library, universal file
format, profile registry, validator, or mandatory conversion of existing
information. Build such support only as separately authorized work serving
the actual application.
