# Dependencies and attribution

The source is adapted from the project's private instruction/tooling environment.
Private Git history, operational records and account data are not distributed.
The framework's source and associated documentation are released under the
[MIT License](LICENSE), copyright 2026 Justin Sublette.

The core Python tools use the standard library. Browser handoff uses Node.js
built-in modules. Git is an external runtime for optional concurrent development.
No dependency source or installed binary is vendored here.

| Optional dependency | Purpose | Upstream |
|---|---|---|
| SecretStorage | Linux Secret Service client | [SecretStorage](https://github.com/mitya57/secretstorage) |
| A Secret Service provider and D-Bus | User-owned Linux protected storage/session | The user's distribution/provider |
| pytest | Development checks | [pytest](https://github.com/pytest-dev/pytest) |
| jsonschema | Development schema checks | [jsonschema](https://github.com/python-jsonschema/jsonschema) |
| Playwright and its browser distribution | Optional real-browser development smoke | [Playwright](https://github.com/microsoft/playwright) |
| xclip or wl-clipboard | Optional Linux graphical clipboard | The user's distribution |
| OpenSSH, tmux, curl or GNU Wget | Optional guarded terminal delivery | The user's distribution |
| Protocol Runner | Optional explicit agent-work orchestration | [Protocol Runner](https://github.com/Vector-Mosaic/protocol-runner) |

Install dependencies through their own distributions; their license files and
transitive dependencies remain authoritative. This repository does not relicense
them. The exact qualification environment is recorded separately from compatible
version constraints.
