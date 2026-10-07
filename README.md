# Agent Framework

An instruction framework and practical toolkit for sustained work with AI agents.

Long projects need more than a good opening prompt. Decisions must survive new
conversations. Instructions need to preserve what the user meant. Tools need to
work against the right account, source and environment. Completed work needs to
remain usable when another attempt fails.

This repository brings those concerns together: the complete framework, its
practical application guides, and the tools that make the methods usable. It is a
portable adaptation of a working private development environment. Installation
uses your own paths, accounts and permissions.

**Preparation status:** this source is being prepared for its first public release.
The [qualification record](docs/qualification.md) distinguishes finished checks
from work still underway. No claim of improved model performance follows from
the framework's design or the toolkit's tests.

## Start here

- [Framework](docs/instructions/framework/README.md): purpose and routes to the
  full concepts and relationships. Read relevant owners as the work requires them.
- [Setup](docs/setup.md): preview and install instructions, preserve your existing
  guidance, and select private local state locations.
- [Practical guides](docs/instructions/README.md): collaboration, software changes,
  capability use, verification, continuity and repository organization.
- [Architecture](docs/architecture.md): what is included and how it fits together.
- [Security](docs/security.md): credential custody, trusted processes and limits.

The root [AGENTS.md](AGENTS.md) applies the framework to this repository itself.
Installing the home guidance is a separate, deliberate action. Merely reading or
cloning the source does not modify your agent configuration.

## Included tools

| Capability | Use |
|---|---|
| Sketchpad and to-do | Preserve a task's current understanding and remaining work across continuations. |
| Instruction delivery | Render, preview, install, update and remove the managed instruction section. |
| Tool discovery | Find the included commands and read their actual interfaces before use. |
| Credential handoff | Fill an observed browser field from protected, account-bound custody without putting the value in ordinary agent output. |
| Concurrent development | Optionally prepare and conditionally integrate work from separate Git workspaces. |
| Protocol Runner integration | Optionally execute an explicit work plan through the separately maintained [Protocol Runner](https://github.com/Vector-Mosaic/protocol-runner). |

Core Python commands run directly from this checkout; no service or build is
needed. Windows and Linux setup, credential backends and optional integrations
have distinct prerequisites in their operating guides. See
[credentials](docs/credentials.md), [concurrent development](docs/ops/concurrent_development.md)
and [Protocol Runner](docs/integrations/protocol-runner.md).

This is an independent project, not an OpenAI product. The framework does not
override the model, client, operating system or organization's governing rules.
