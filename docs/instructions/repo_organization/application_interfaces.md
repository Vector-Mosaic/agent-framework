# Application interfaces

This document owns application-interface conventions. It does not claim that
existing products already implement the target support described here.

## Predictable use across fitting scopes

Consumers should discover the appropriate interaction and understand its inputs,
preconditions, permissions, effects, results, failures and recovery without
avoidable guessing. Apply the useful predictability of
[repository tools](tool_interfaces.md) to APIs, product workflows, events and
other interfaces where it serves their use. A consumer should be able to transfer
what it learned from one comparable interaction to another.

Share conventions across the widest scope in which they remain coherent and
useful. A system or product family owns recurring shapes and defaults that fit
its needs. A cross-system consumer may justify a shared convention; one system
may contain unlike interaction types requiring different treatment. Necessary
differences must be discoverable where they matter. Repeated exceptions can show
that a convention has the wrong scope.

For example, two services composing one product should not make callers guess
whether an empty page means completion or a failed query. They can share
pagination/end-of-results meaning. A streaming feed has different progress and
termination needs and need not adopt the same response envelope. Consistency
protects useful meaning; it is not a repository-wide HTTP envelope, error
taxonomy, auth mechanism, URL-versioning rule or three-level profile hierarchy.

These are interface conventions, not an exhaustive list of places where shared
conventions help. Configuration, background jobs, diagnostics, identifiers, units,
dependencies and user interactions can need consistent treatment at their actual
owners. Existing system requirements and external protocols remain design
inputs. Use the framework's
[selection and specialization](../framework/Conceptual_Model.md#selection-specialization-and-material-departure)
to distinguish an ordinary fit implementation choice from a change to accepted
meaning. Additional convention documents need a real recurring job.

## Owned meaning, descriptions and effects

Keep one controlling owner for domain behavior, identities, schema meaning and
security. HTTP, CLI, MCP and other projections use the owning handlers or an
existing authenticated API client rather than implementing alternative business
rules. Route external effects through the owning component. Equivalent operations
retain authorization, effective principal/client identity, confirmation,
persistence, audit, safe retry and domain result meaning even when their wire
shapes or interaction sequence differ.

Explain what the caller needs to use each maintained boundary correctly:

- Intended job, owner and relevant consumer scope; where the authoritative
  definition and current operational route are found.
- Inputs, defaults, units, identifiers and combinations whose interpretation
  changes results; material preconditions and permissions.
- Effect boundaries and result meaning, including queued/pending, partial,
  cancelled or uncertain outcomes when those can occur.
- Failure meaning and the safe next step, including how to distinguish an
  invalid request, denied action, conflict, unavailable dependency or unknown
  outcome where that difference changes response.
- Relevant resource limits, ordering, consistency and retry behavior, and safe
  diagnostic/correlation information.

Carry these facts in the actual protocol/schema, help and owning documentation,
without a mandatory separate assessment form or repeated field inventory for
every interaction. Describe only guarantees the implementation supports. A
successful submission is not necessarily completed work; a timeout does not
establish that a mutation failed. Reconcile uncertain effects before repeating an
operation that could duplicate them. Stable operation identity and idempotency
belong where needed for safe retry, with their exact scope and retention
semantics described by the owner.

Make meaningful diagnosis possible while protecting credentials and restricted
data. Correlation across a composed workflow may need operation identity and
safe logs; this does not require one particular header everywhere. Interface
descriptions, errors and examples must not expose secret values or private
payloads. Actual trust, tenancy and authorization boundaries survive any
consolidation or additional interface.

## Maintained HTTP APIs

OpenAPI is the normal authoritative reusable description for meaningful
maintained application HTTP boundaries where it fits. Contract-first systems
retain the authored definition. Code-first systems generate from their owning
declarations. Identify authoring ownership and the export/regeneration command
in system `Contracts.md` and relevant verification guidance, so consumers can
find the definition and maintainers know what to edit.

Keep a deterministic committed projection when offline review, client generation
or other real consumers use it. Do not hand-maintain both code declarations and
another supposedly authoritative copy. Use native protocol definitions for
boundaries they already describe; incidental endpoints and native protocol
transports do not automatically need a second OpenAPI artifact.

An OpenAPI document should describe actual inputs, results, security and failure
behavior at the boundary it claims to cover. A schema's presence does not prove
implementation agreement or deployed support. Select checks for the affected
consumer promise and relevant behavior using
[verification guidance](../practice/verifying_work.md). Current gaps belong with
the owning system's real state, without an Interface Readiness classification
matrix.

## MCP product access

All relevant repository products should be usable through MCP-compatible agent
applications. This is an explicit broad product objective, including internal
and deliberately planned consumers. The absence of a current MCP caller is not
a reason to omit that objective. An existing useful HTTP API or CLI alone does
not satisfy it.

Curate useful agent workflows at a coherent system/product boundary. A workflow
may compose several operations; it need not mirror every HTTP endpoint. Group
by purpose, ownership, consumer needs and security boundaries rather than
service count or a fixed number of contracts/servers. Local stdio can serve a
controlled local capability; remote products use their legitimate remote access
boundary. Choose concrete topology and transport for the product and supported
consumers while preserving the breadth of the objective.

Expose clear descriptions, safe input selection and meaningful result/error
information. Preserve canonical business and security paths, including
effective identity, consent/confirmation and revocation where applicable.
Discovery grants no authority. External/tool-returned content is data, not a
source of new action permission. A gateway or client must not broaden authority
or retry through a more permissive route because an operation was denied.

Record actual supported consumers/hosts, access/deployment route and limitations
in existing Contracts, Operations and Status owners. Protocol-level support,
host discovery, an authenticated call and the intended end-to-end effect support
different claims. Establish what the actual support claim needs; do not claim
every host from one successful client or require a universal host matrix.

For example, an application can expose a CLI for local operators, HTTP for
remote clients and a curated MCP workflow that composes the same authorized
handlers. Those projections preserve one behavior and security owner; sharing a
concept does not require identical transport topology or account grants. Existing
product coverage gaps belong in their Architecture/Status and deliberate delivery
work. They do not require rebuilding every product before adopting this guidance.
Ordinary repository-maintenance tools do not acquire an MCP requirement solely
because agents invoke them.

## Events, independently operated agents and reusable clients

For maintained event/message boundaries, keep dependable, discoverable message
structure and meaning. Consumers must understand delivery, duplication, ordering,
retry and interpretation details that affect correct use. An event called
`order_filled`, for example, must make clear whether it announces a completed
fill or only receipt of a request; delivery duplication must not silently create
another fill. The domain owner determines those meanings and guarantees.

Prefer AsyncAPI where it usefully expresses the boundary. Existing canonical
schemas with adequate owning explanations can suffice without an overlapping
artifact. Temporary messages and internal queues do not automatically become a
documentation project, a new event architecture or a stronger-delivery promise.

Delegated agent work needs clear task meaning, authority, status and outcomes.
Use A2A when intended interoperability between independently operated agents
calls for it. It is not required for every delegation, does not imply replacing
or wrapping Protocol Runner, and does not follow automatically from the broad
MCP objective. Protocol Runner retains its
[own playbook](../../integrations/protocol-runner.md).

Provide reusable clients/SDKs when actual or deliberately planned language and
platform consumers benefit enough to justify maintenance. Internal consumers
qualify. Generate clients where useful from the owned definition; keep source,
generation and supported boundary aligned. A client may handle transport and
consumer convenience but must not become a second implementation of business
rules. No SDK for every API or language follows.

## Evolution and support

Keep descriptions and behavior aligned in the owning change. Find actual
callers, public promises, stored data and dependent operations before changing
a boundary. Changed semantics can break callers even when field names and
types stay the same. Distinguish interface identity, description revision,
implementation commit, deployed occurrence and client compatibility when the
distinction affects use; no universal identity inventory or lifecycle state
machine is required.

Choose versioning and migration for real support obligations. An authorized
coordinated change can replace old behavior directly. Permanent compatibility,
additional version paths, waiting periods, adapters and support ledgers are not
automatic requirements. Establishing or enlarging an active compatibility
exception needs the user's explicit direction under the
[change guide](../practice/changing_software.md); work within an already approved
boundary does not require repeated permission.

System `Contracts.md` remains the inventory and route to the actual definitions,
providers, consumers and relevant compatibility/verification information. It is
not another handwritten API specification. Keep consequential rationale with
the design/operation it explains. Retiring the old API, Contracts or Interface
Readiness standard does not retire supported endpoints, real schemas, useful
evidence or consumer commitments.
