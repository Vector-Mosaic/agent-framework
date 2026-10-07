# Repository conventions

These owners make repository structure and recurring interactions predictable
without imposing one engineering procedure on every system. They apply the
[framework](../framework/README.md) while preserving system purpose, actual
consumer contracts and native configuration. Read the relevant owner directly;
these four documents are not a required sequence or exhaustive taxonomy.

| Need | Convention and immediate boundary |
|---|---|
| Locate or add source, components, manifests, infrastructure or generated output | [Repository structure](repository_structure.md). Keep system-first ownership, the existing meaningful roots and consumed manifest identities; deliberately revise the layout owner before adding a top-level folder. Reuse native metadata instead of maintaining overlapping copies. |
| Create or update system/subsystem knowledge | [System documentation](system_documentation.md). Preserve full system meaning, stable document roles, indexing and useful navigation. Required files may remain truthfully empty until needed; every new system receives Vocabulary. |
| Build, expose or use a repository-owned tool | [Tool interfaces](tool_interfaces.md). Provide safe, complete discovery and predictable invocation, semantic selectors and clear effects/failure/retry behavior. Text and JSON expose the same essential information. |
| Design or evolve a maintained product interface | [Application interfaces](application_interfaces.md). Apply shared conventions where they help real interaction, maintain meaningful HTTP definitions and broad MCP coverage for relevant products, and preserve actual behavior/security ownership and consumer obligations. |

In an adopting multi-system repository, `systems/` owns system implementation; `docs/` owns canonical long-form knowledge;
`repo_platform/` holds justified shared implementation; `infra/` holds shared
infrastructure; `scripts/` holds stable entrypoints; `artifacts/` supplies
predictable temporary/derived placement. The full layout and naming rules live
in repository_structure, including retired-root handling and existing secondary
roots. Do not infer that a listed directory or metadata field must be populated
without a real need.

Current truth and execution details remain outside this router:

- An adopting project's system documents own accepted meanings, interfaces,
  data, security, deployment and operating knowledge. This toolkit's owners are
  reached through the [docs index](../../INDEX.md).
- [Setup](../../setup.md), [credential handling](../../credentials.md), the
  linked operation guides and actual tool usage describe how to use included
  capabilities with user-owned configuration.
- [Changing software](../practice/changing_software.md) explains dependency/effect
  tracing, meaningful compatibility, data evolution and complete retirement.
- [Verifying work](../practice/verifying_work.md) explains appropriate evidence,
  useful timing and reuse, with the actual test/runtime owners retaining execution.
- [Concurrent Development](../agent_workflow/concurrent_development.md) owns shared
  source behavior; its operations and Runner participation remain with their owners.

Schema and catalog declarations keep the meanings their actual consumers use.
A documentation consolidation does not silently reinterpret existing paths,
commands, IDs, privacy or operational permissions. When a convention changes,
trace and update affected interpreters, callers and instruction mirrors in the
same owning change. Preserve complete useful system knowledge rather than
rewriting it from a shorter generic guide.
