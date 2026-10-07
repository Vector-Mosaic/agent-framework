# Dependencies and attribution

The source is adapted from the project's private instruction/tooling environment.
Private Git history, operational records and account data are not distributed.
The source's reuse license will be settled before public publication.

The core Python tools use the standard library. Browser handoff uses Node.js
built-in modules. Git is an external runtime for optional concurrent development.
No dependency source or installed binary is vendored here.

| Optional dependency | Purpose | Upstream |
|---|---|---|
| SecretStorage | Linux Secret Service client | [SecretStorage](https://github.com/mitya57/secretstorage) |
| A Secret Service provider and D-Bus | User-owned Linux protected storage/session | The user's distribution/provider |
| pytest | Development checks | [pytest](https://github.com/pytest-dev/pytest) |
| jsonschema | Development schema checks | [jsonschema](https://github.com/python-jsonschema/jsonschema) |
| Protocol Runner | Optional explicit agent-work orchestration | [Protocol Runner](https://github.com/Vector-Mosaic/protocol-runner) |

Install dependencies through their own distributions; their license files and
transitive dependencies remain authoritative. This repository does not relicense
them. The exact qualification environment is recorded separately from compatible
version constraints.
