# Credential custody and private browser handoff

The toolkit gives an agent a usable credential without putting its value in the
conversation. One private account registry supplies the account, permitted
origins and credential purposes. A small local worker retrieves the selected
value from OS-protected custody; the browser helper fills one observed field and
returns only its safe status. It does not submit forms or prove a successful login.

Use Python 3.11+ and Node 20.19+ for the browser integration. Run from a trusted
source checkout. Windows uses CurrentUser DPAPI with no Python dependencies;
Linux uses an unlocked Secret Service collection and the optional
`requirements-credentials-linux.txt` dependencies. No production backend stores
plaintext or silently falls back to another provider. The operating-system
principal, browser runtime, Python interpreter and this source are trusted.

## Account configuration and intake

Start with the offline interface:

```text
python scripts/tools/agent_credentials.py usage --format json
```

`--config` selects an explicit private registry. Otherwise the location is
`AGENT_FRAMEWORK_CONFIG_HOME/accounts.json`, or the OS default:
`%APPDATA%/AgentFramework/accounts.json` on Windows;
`$XDG_CONFIG_HOME/agent-framework/accounts.json` on Linux, falling back to
`~/.config/agent-framework/accounts.json`. Keep it outside the checkout.

```text
python scripts/tools/agent_credentials.py init
python scripts/tools/agent_credentials.py add --account example --origin https://example.test --credential password --credential totp
python scripts/tools/agent_credentials.py status --account example
python scripts/tools/agent_credentials.py storage-status
python scripts/tools/agent_credentials.py intake --account example --origin https://example.test --credential password
python scripts/tools/agent_credentials.py custody-status --account example --credential password
```

The example account and domain are fictional. `add` creates metadata and random
opaque references only; it neither signs up nor changes a provider password.
Repeat `--origin` only for destinations independently authorized for that same
account. Origins are canonical exact HTTPS origins, without paths, user info or
implicit wildcard/subdomain matching. `--mailbox` and `--username` declare the
actual identity fields independently. `--federated` prevents local password
custody for downstream federated accounts. A security answer requires an actual
provider binding (`--security-answer-bound`); a reserved unused answer cannot be
filled. Account metadata is private even though it contains no secret values.

Hidden `intake` requires a real interactive terminal with input and error output
attached. Echo fallback and redirected input are refused. It stores only absent
custody, never overwrites, and verifies private readback. No secret command-line
argument, environment input, reveal or export exists. If interrupted, use
`custody-status`: `present_unverified` means an exact stored item exists, not that
it matches the provider. Reconcile with the provider before retrying or changing
credentials. `status` reads metadata only; `storage-status` probes availability;
`custody-status` checks one reference's presence without requesting plaintext.
Locked or unavailable storage remains a distinct error rather than “absent.”

The [schema](../repo_platform/schemas/credentials/account-registry.schema.json) documents the
registry. The [synthetic example](../repo_platform/schemas/credentials/account-registry.example.json)
is explanatory; use `init`/`add` to generate your own namespace and references.
Runtime checks additionally enforce unique references/account IDs and canonical
origin, federation and purpose relationships. Set account `status` to `disabled`
or `recovery_required` in the private registry to stop automated delivery; this
changes local admission, not provider access. Preserve references during ordinary
metadata edits so existing custody remains bound to the same account and purpose.

## Windows storage

DPAPI encrypts each envelope for the current Windows user. A protected envelope
binds its registry namespace, exact account, purpose and opaque reference, and
its DPAPI entropy uses the same binding. Ciphertext lives beneath
`AGENT_FRAMEWORK_STATE_HOME/credentials`, otherwise
`%LOCALAPPDATA%/AgentFramework/credentials`. No separate master key is written.
The operating system owns user-profile protection and recovery. Copying these
files to another user or host is not a supported credential migration.

The default profile directories and inherited Windows permissions belong to the
current user. Configure private local directories, not shared folders or cloud
sync roots. Symlinks and Windows reparse points in custody paths are refused.
Files are installed as complete ciphertext; initial intake cannot replace an
existing destination. TOTP state updates use locked, atomic ciphertext replacement.

## Linux desktop and headless storage

Install the optional Python dependencies in your own environment:

```sh
python3 -m pip install -r requirements-credentials-linux.txt
```

Install a Secret Service provider such as GNOME Keyring using your distribution's
normal package manager. The same user needs a session D-Bus and an existing,
unlocked, password-protected collection. The toolkit connects directly through
[SecretStorage](https://secretstorage.readthedocs.io/en/latest/), selects the
registry's `secret_service_collection` (default
`/org/freedesktop/secrets/aliases/default`), and never creates or unlocks a
collection implicitly. A locked collection stops the operation. On a desktop,
unlock through the provider's normal user interface or login integration.

For a headless GNOME Keyring session, the owner can start a private session bus
and enter the existing keyring password with terminal echo disabled:

```sh
dbus-run-session -- bash
read -r -s -p 'Keyring password: ' keyring_password
printf '\n'
if [ -n "$keyring_password" ]; then
  printf '%s' "$keyring_password" | gnome-keyring-daemon --unlock --components=secrets
fi
unset keyring_password
# Run the toolkit in this shell or its children, sharing this session bus.
```

Do not use an empty keyring password, export the password, put it in a command
argument or save it alongside the collection. `printf` here is the shell builtin
and the non-exported variable is unset immediately. With a fresh user profile,
the provider may create its initial login collection on this explicit owner
operation; do not recreate an existing collection to work around an unlock
failure. The owner supplies its actual password. Headless unattended restarts
still require an explicitly chosen secure unlocking arrangement; this package
does not promise boot-time unlock or install a background service.

The Secret Service provider owns encryption and collection files. Registry and
lock files are owner-only on Linux, and the toolkit rejects permissive registry
files and shared custody directories. Lock state is beneath
`AGENT_FRAMEWORK_STATE_HOME`, otherwise `$XDG_STATE_HOME/agent-framework` or
`~/.local/state/agent-framework`. Secret Service metadata includes opaque
namespace/reference plus account/purpose; it is not a place for sensitive values.
See the provider's security and recovery guidance for at-rest guarantees.

## Browser API

Import the module inside the trusted Node runtime that owns the supported browser
controller, and select the actual observed tab and field first:

```javascript
const { createCredentialHandoff } = await import('/absolute/checkout/repo_platform/credentials/BrowserHandoff.mjs');
const credentials = createCredentialHandoff({
  python: '/absolute/path/to/python',
  config: '/absolute/private/config/accounts.json',
});
await credentials.fillCredential('example', 'password', { tab, field });
```

Windows Node import uses a `file:///C:/.../BrowserHandoff.mjs` URL, with spaces
URL-encoded where needed; Python and config are ordinary absolute filesystem
paths. Explicit `python` is recommended to select the environment containing
Linux dependencies. The default is `python` on Windows and `python3` on Linux.
Named exports use default configuration and interpreter when those are suitable.
No import creates an account, reads secrets, installs anything or starts a service.

`tab` exposes async `url()`. `field` exposes `count()`, `isVisible()`,
`isEnabled()`, read-only `evaluate(fn)` and `fill(value, {timeoutMs})`.
Supported Codex browser-controller objects implement this interface. For
Playwright, use a thin adapter that maps `timeoutMs` to Playwright's `timeout`;
no provider-specific browser code is required. Client tracing, request/response
logging and automatic screenshots must not record credential operations. The
client and its fill implementation are trusted to keep supplied values private.

The field must be unique, visible, enabled and editable. Both the outer tab's
and the receiving document's exact HTTPS origins are independently admitted by
the same account; an allowed page does not admit an arbitrary iframe. The helper
rechecks the full tab and document URLs plus field metadata after custody access,
then fills without submission. Credential bytes travel through an inherited
private pipe (or verified local Unix socketpair), separate from stdout/stderr.
Raw errors from browser operations are never forwarded because they may include
input. A fill error returns `browser_fill_uncertain`: inspect the page before
retrying. Field delivery does not prove provider acceptance.

These checks narrow ordinary stale-target errors; they do not make a DOM update
and remote fill atomic, establish that provider content is trustworthy, or defend
against malicious code already running inside an admitted browser/runtime.

## TOTP lifecycle

A separately declared `totp` reference belongs to its own account, including a
federated downstream account. Use the browser API:

```javascript
await credentials.totpStatus('example', { tab });
await credentials.captureTotpEnrollment('example', { tab, secretField });
// Complete the provider's enrollment process; observe acceptance first.
await credentials.confirmTotpEnrollment('example', { tab });
await credentials.fillCredential('example', 'totp_code', { tab, field });
```

`secretField` is the single observed same-origin manual setup key or `otpauth://`
URI field. Capture stores it privately as **pending**. It never returns the seed
or changes the provider. The helper supports SHA1/SHA256/SHA512, 6 or 8 digits
and explicit periods. Use a synchronized system clock. A pending factor cannot
be used as an ordinary active challenge. Confirm only after observed acceptance;
the toolkit cannot independently prove that provider state from a local flag.

`replacePendingTotpEnrollment` is an explicit recapture only after establishing
that the previous provider attempt did not complete. It cannot replace a
different active seed. Same-seed capture is idempotent. If acceptance is uncertain,
reconcile with the provider before changing custody.

Human enrollment code entry uses the optional
[clipboard adapter](integrations/clipboard.md) in one explicit interactive session.
There are no cross-process browser `copy`/`clear` commands: on Linux the clipboard
owner must remain alive, so the adapter keeps that lifetime together. Ordinary
password and active TOTP field filling never uses the clipboard. [Trusted terminal
handoff](integrations/terminal.md) is also an optional explicit adapter, not a browser side effect.

## Protections and limits

This is a transcript-exposure boundary, not a sandbox against the local user or
arbitrary agent shell execution. Trusted processes must handle plaintext in
memory. JavaScript/Python strings and copies cannot be guaranteed erased; buffer
zeroing reduces some leftovers without claiming perfect memory erasure. A
compromised local principal can use DPAPI or an unlocked Secret Service, alter
configuration/source, instrument a browser or read process memory. Such a
principal is outside the protection claim. The registry does not grant authority
for sign-in, changes, spending or enrollment; the user's request and permissions
remain separate. Private browser filling does not bypass provider challenges.

Keep source/interpreters trusted, use least-privilege user accounts, protect
registry and keyring recovery, and rotate affected provider credentials if
exposure is suspected. Never attach custody blobs, keyrings, registry records,
transcripts, screenshots or real secrets to a public issue or release.

## Verification

The focused Python contracts cover origin admission, registry/ref collisions,
metadata drift, swapped envelopes, absent-only hidden intake, echoed/redirected
input refusal, pending/active state, RFC6238 vectors and symlink rejection.
Browser tests use synthetic custody to check private transport, field selection,
changed targets and raw-error redaction. `platform_custody_smoke.py` exercises
actual OS custody against a fresh synthetic registry only. It deletes its exact
synthetic stored items after the check. It is never pointed at a real registry.

```text
python -m unittest discover -s tests/credentials -p test_custody.py
node --test tests/credentials/browser-handoff.test.mjs
```

An actual Windows Codex native-browser synthetic password fill and readonly
refusal were observed during extraction. Linux qualification uses the same
worker and helper with real Secret Service and a real browser's supported
locator API; see [qualification](qualification.md) for the exact platforms and
results. A browser-controller contract test alone is not a claim that every
client/version is supported.
