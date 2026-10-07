# Security and private configuration

## What the handoff protects

The credential handoff keeps values out of ordinary agent messages, command
arguments, environment variables and printed tool results. Browser delivery
uses a private worker channel and returns status, account reference, credential
purpose and receiving origin. It does not return the credential or submit a form.

The worker, its protected-storage backend and the receiving browser necessarily
handle plaintext. This is not isolation from arbitrary malicious code running as
the credential-owning operating-system user. Code with that user's access may be
able to invoke the same keyring or DPAPI operations. JavaScript and Python also
cannot guarantee that every temporary copy has been erased from process memory.

The target page receives the value as soon as it is filled. A successful fill
does not establish successful authentication, the provider's trustworthiness or
permission for a later action. Use the selected account's actual authority and
admitted destinations. An admitted parent page does not admit an embedded frame.

## Protected storage

Windows custody uses current-user DPAPI. Encrypted files belong under the owning
user's private state directory. Their ciphertext is not appropriate source or
test data. Preserve the operating-system user's recovery needs when moving or
reinstalling a machine.

Linux custody uses the Secret Service API through `secretstorage`; it does not
silently fall back to a plaintext file. The user's session must provide an
unlocked Secret Service collection. On a headless host, the operator must supply
an explicit D-Bus/keyring session and secure unlock arrangement. Do not store an
unlock password beside an encrypted store or launch an empty-password keyring as
a production shortcut. See [credential setup](credentials.md).

Account configuration is not a grant of authority. It records destinations and
bindings the user has chosen; it does not turn a repo clone into permission to
administer an account. Changes to credentials, trust, permissions and external
resources retain their actual owners and approval conditions.

## State outside source

Keep real account registries, secret envelopes, keyrings, continuity bodies,
browser profiles, sessions, logs, screenshots, SSH identities and host pins out
of tracked source. The examples use invented identities. Path redaction alone
does not make an operational record public-safe: nonsecret account metadata and
machine topology can also disclose private information.

The toolkit uses explicit user configuration and user-specific storage locations.
Review those destinations before installation. `.gitignore` helps avoid accidental
commits; it is not a security boundary or a guarantee that sensitive files cannot
be force-added. Review staged content and Git history before a public release.

## Optional delivery surfaces

Clipboard handoff requires an explicit request for that path and a supported
desktop clipboard. It may expose the value to clipboard managers and other local
applications. Ordinary browser fill does not use the clipboard. Cleanup must
preserve newer clipboard content instead of indiscriminately clearing it.

Terminal handoff requires user-configured trusted SSH host identity and a declared
supported consumer. It is not a generic password-to-arbitrary-shell interface.
The selected account, HTTPS destination, process and waiting prompt must match
before delivery. See [terminal integration](integrations/terminal.md).

Protocol Runner and Git integration have separate security boundaries. A worker
contract does not sandbox a process; procedural completion does not certify its
output. Follow the selected runtime's own controls and [Runner's security guide](https://github.com/Vector-Mosaic/protocol-runner/blob/f2041eb8b60171a3d847e86e4f2b6b43427cc9ec/SECURITY.md).

## Reporting a problem

Do not include real credential values, account registries or session contents in
an issue or example. Reproduce a problem with synthetic local data and describe
the affected component and observed behavior. Maintainers should arrange a
private reporting route before public launch.
