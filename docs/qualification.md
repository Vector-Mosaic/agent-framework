---
doc_horizon: current
---

# Qualification and support

This page owns current support claims, their limits and the procedures for
checking affected behavior. Detailed run records, source identities and test
transcripts are reached through the [historical index](History/INDEX.md); retrieve
the relevant record only when the task needs it. The [November 10 continuity
record](History/qualification.md#november-10-continuity-maintenance) and
[first-release record](History/qualification.md#first-release-environments-and-source)
retain their exact earlier source and platform scopes.

Qualification uses this standalone source and synthetic task, profile and state
fixtures. Real user accounts, global instructions, ongoing research and existing
services are outside those fixtures. Instructions express intended behavior;
tests and observed use establish only their actual bounded properties.

| Area | Current support and boundary |
|---|---|
| Framework and practical guidance | Complete conceptual owners and public adaptations are retained. The total-meaning definition includes cumulative construction, deliberate coexistence and conditional precedence. Local routes connect the actual owners. |
| Home delivery | One authored home carrier supplies orientation, depth-2 guidance, explicit bindings and recovery. The common entrypoint supplies operating guidance throughout the adopted scope. Managed updates preserve unrelated home bytes and refuse conflicting overrides or edits. |
| Sketchpad and to-do | Empty sketchpads are valid; only admitted developing ideas belong. Authorized verified promotion clears them fully. To-do retains unfinished Now / Next / Parked work; legacy substantive material needs reconciliation before normalization. Revision-bound reads, conditional writes and interruption recovery remain supported. |
| Current tooling verification | Focused continuity and delivery tests pass on Linux and Windows using synthetic state. [Current update evidence](History/qualification.md#november-11-total-meaning-and-continuity-update) owns the exact source and observed results. |
| Windows DPAPI and Linux Secret Service | Existing qualified protected-custody implementations are unchanged. Their exact account, origin and pending/active TOTP boundaries remain; this update makes no new real-account claim. |
| Browser credential delivery | Existing qualified Windows native-browser and Linux Firefox routes are unchanged. Guarded field delivery does not submit a form or establish support for every browser client. |
| Clipboard and terminal | Existing isolated Windows and Linux X11 clipboard and Linux terminal results remain applicable to unchanged consumers. Live Wayland qualification remains open; no automatic fallback is selected. |
| Concurrent Git integration and optional Runner | Existing integration implementations and their qualified scopes are unchanged. No live Runner/model mission is part of this update. |
| Public adaptation | User-configured paths, accounts, hosts, project scope and interpreter selection remain explicit. Private installation state, grants and operational inventory are excluded. |

Tests of the public toolkit do not establish every native client's instruction
assembly, compaction, delegation or compliance. A configured depth instruction
is not a native runtime cap. Qualify delivery in the selected installation using
[entry and continuation checks](ops/codex_instruction_delivery.md#qualify-entry-and-continuation).
The framework's design and successful tooling tests do not establish improved
model reasoning or guaranteed success on arbitrary work.

## Reproduce the relevant checks

Use a disposable checkout and private test state. Select the checks for the
component being changed; these commands are not a requirement to repeat every
check after a documentation edit.

```text
python -m pip install -r requirements-dev.txt
python -m pytest repo_platform/tooling/tests -q
python -m unittest discover -s repo_platform/concurrent_development/tests
python -m pytest tests/credentials/test_custody.py tests/credential_adapters/test_adapters.py -q
node --test tests/credentials/browser-handoff.test.mjs
```

For an actual storage/browser check on Linux, install the optional credential
dependencies and use a disposable, unlocked Secret Service collection as
described in [credential setup](credentials.md). The fixtures must receive new
absolute configuration and state paths, never an existing account registry.

```text
python tests/credentials/platform_custody_smoke.py qualify --config ABSOLUTE_NEW_CONFIG --state-root ABSOLUTE_NEW_STATE
node tests/credentials/browser_real_smoke.mjs --config ABSOLUTE_NEW_CONFIG --python ABSOLUTE_PYTHON --engine firefox
```

The real-browser fixture additionally needs Playwright and its browser installed
in the test environment (for example, `npm install --no-save --no-package-lock
playwright@1.63.0`, then `npx playwright install firefox`). It may use
`PLAYWRIGHT_MODULE` to select an already installed module. No production tool
depends on Playwright. Linux X11 smoke requires a disposable graphical session:
`python tests/credential_adapters/linux_clipboard_smoke.py x11`. The terminal
consumer fixture is `python tests/credential_adapters/linux_terminal_smoke.py`;
it owns a private tmux server and never sends a real credential.

The isolated Windows clipboard fixture is
`python tests/credential_adapters/windows_clipboard_smoke.py`. Creating its
separate window station requires an administrator; the ordinary clipboard
adapter does not. The fixture never elevates itself or uses the interactive
clipboard.

Report scoped behavior and actual limits. Keep completed execution evidence in
the historical owner and current support implications here.
