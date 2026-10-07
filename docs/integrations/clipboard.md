# Private human clipboard handoff

The optional clipboard adapter is for a person pasting into an authorized field,
or explicitly supplying an existing credential for protected intake. The
[native browser handoff](../credentials.md) sends values directly to the supported
browser field and does not use the clipboard.

Use the same canonical account registry and custody as `agent_credentials.py`.
Select the exact account, admitted HTTPS origin and credential purpose. Account
registration does not grant permission to use an account or read arbitrary
clipboard content.

```text
python scripts/tools/credential_adapters.py usage --format json
python scripts/tools/credential_adapters.py clipboard-paste --account example --origin https://example.test --credential password --backend windows
```

Run `clipboard-paste` in an interactive console. It reads protected custody,
copies privately, and waits for the person to paste and press Enter. Cleanup runs
on normal completion and console interruption. It does not submit a form or
establish successful authentication. There is no credential argument, reveal
command or clipboard content in the result.

`--credential totp_enrollment_code` copies a fresh code from pending enrollment;
`--credential totp_code` requires activated enrollment. The same process retains
clipboard ownership until the person finishes. No persistent clipboard daemon or
split copy/clear browser-worker calls are needed. Paste promptly because codes
expire; a copy does not activate enrollment or prove provider acceptance.

For explicitly authorized intake of the credential the owner has just supplied
on the clipboard:

```text
python scripts/tools/credential_adapters.py clipboard-intake --account example --origin https://example.test --credential password --backend windows --format json
```

This validates the account and absent destination before reading the clipboard,
stores through the canonical protected backend, and checks private readback.
Existing custody is never overwritten. `stored_unverified` means custody was
installed; provider acceptance is separate. Hidden console intake through
`agent_credentials.py intake` remains preferable when clipboard exposure is
unnecessary.

## Platform behavior

| Backend | Requirements | Cleanup |
| --- | --- | --- |
| `windows` | Windows desktop clipboard in the calling user's session | Compares and clears under one exclusive `OpenClipboard` lock; changed text is preserved. |
| `x11` | Linux graphical X11 session, `DISPLAY`, and `xclip` | Delivery owns a foreground selection process and terminates only that process. |
| `wayland` | Linux Wayland session, `WAYLAND_DISPLAY`, `wl-copy` and `wl-paste` | Delivery owns a foreground selection process and terminates only that process. |

Choose the backend explicitly; there is no fallback to a different user's or
display's clipboard. Headless Linux has no clipboard, but can use protected
custody and the terminal integration. Linux intake reports `preserved_unowned`
because a separate read followed by a global clear could delete newer user
content. Clear it manually after the safe intake result if desired. Linux
delivery reports `released_owned_selection`; this is deliberately different
from claiming that every desktop component erased its copy.

The clipboard is a temporary plaintext surface. Clipboard managers, history,
remote-desktop synchronization and same-user processes may retain or observe it.
Disable such retention for sensitive use; this adapter cannot erase copies held
by other software. Do not assume a browser controller's virtual clipboard is the
OS clipboard. A successful copy is not proof that a particular client can paste.

If cleanup fails, the stored credential or completed copy is not rolled back.
The result reports cleanup independently. Reconcile the actual clipboard
privately rather than printing it for diagnostics.
