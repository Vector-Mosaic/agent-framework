# Qualification record

Status: standalone preparation and Windows/Linux qualification complete. No
public release has been made from this tree.

Qualification uses the standalone source and synthetic state. Real user accounts,
global instructions, ongoing research and existing services are outside the test
targets. The result will identify the Windows and Linux environments actually
used, the checks completed, any unresolved failures and the source identity.

| Area | Current status |
|---|---|
| Full instruction adaptation and owner links | Complete framework preserved; adaptations reviewed by their owners; local Markdown links resolve. |
| Setup preview/install/update/remove | Windows focused checks and Linux contract suite passed; existing instruction bytes preserved and conflicting overrides refused. |
| Exact-task sketchpad/to-do and tool discovery | Windows and Linux checks passed with isolated task/configuration state, including actual CLI usage and no-argument discovery. |
| Windows DPAPI and Linux Secret Service | Actual protected roundtrips passed, including absent-only storage, independent origin admission and pending/active TOTP behavior. |
| Private browser transport and guarded field delivery | Windows native Codex browser password fill and readonly refusal passed. Linux real Firefox passed seven checks: password fill, duplicate-field refusal, independent frame/page admission, pending/active TOTP and preservation of the password. Exact synthetic custody cleanup passed. |
| Optional clipboard and terminal | Isolated Windows and Linux X11 clipboard operations passed. Actual Linux curl/tmux consumer passed, including wrong/stale target refusal and removal of its temporary buffer. |
| Concurrent Git integration | Windows focused scenarios and Linux suite passed, including conditional publication, preserved local work and interrupted refresh recovery against temporary local repositories. |
| Versioned Protocol Runner integration | Example work plan validated using the existing pinned Runner core; all local input references resolve. No live model/Runner mission was launched for this adaptation. |
| Exact public-source privacy and dependency review | 92 explicitly staged text files reviewed before the first commit; no private ancestry, account/state files or private operational bindings. |

## Environments and source

The qualified runtime source is
`361099057024cc6851050e6b5a42d375548c23e5`. Its fresh history starts at
`274359ca512af24f470d1000240a7ec73cdd1258`; no private Git ancestry was imported.
The final Linux run verified its source bytes against that exact commit archive.
Unchanged Windows/Linux results were reused after focused fixture corrections
and one Linux storage fix: accepting the provider's `xdg:schema` metadata while
keeping every credential-binding attribute exact. The changed reader passed
eleven custody checks, real Secret Service operations and the complete real
browser smoke. Later documentation-only commits do not change this runtime.

| Environment | Observed runtime |
|---|---|
| Windows | OS build 26200, Python 3.12.10, Node 20.19.0, Git 2.53.0 |
| Linux | Ubuntu 24.04.5, Python 3.12.3, Node 22.23.2, Git 2.43.0 |
| Linux protected storage | SecretStorage 3.3.3, GNOME Keyring 46.1, private D-Bus and an unlocked collection with a nonempty synthetic password |
| Linux browser | Playwright 1.63.0 and Firefox 155, headless with ordinary security settings |
| Linux clipboard | Xvfb 21.1.12 and xclip 0.13, task-owned graphical session |

The Windows browser check used the native Codex browser controller, the real
public helper and DPAPI-backed synthetic custody. It filled a masked password
field without submitting the form. The Linux browser test uses the same helper
and worker with a thin Playwright timeout adapter. It does not establish support
for an unavailable desktop client on a headless host.

Windows clipboard qualification used an isolated window station and desktop;
it did not read or write the interactive user's clipboard. Linux clipboard
qualification covers X11. The optional Wayland implementation has not been
exercised against a live Wayland compositor.
Use the selected session backend, not an automatic fallback.

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

This record will report scoped behavior rather than a general claim that the
framework improves agent reasoning or guarantees successful work.
