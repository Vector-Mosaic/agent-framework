# A password for a verified downloader prompt

This optional adapter delivers an existing account password to a waiting `curl`
or GNU `wget` process in a Linux `tmux` pane. A Windows or Linux client uses the
same canonical [credential custody](../credentials.md). The remote consumer
requires Linux `/proc`, Python 3 and tmux; it does not require a daemon or install
a persistent helper. The fixed consumer source is sent as nonsecret SSH command
text. The password travels only through SSH stdin and an ephemeral tmux buffer.

The host, identity, provider account, download and data-use authorization belong
to the adopting user. This integration grants none of them. It is not a general
secret-to-shell tool and will not send to an arbitrary executable or shell prompt.

## Configure a trusted destination

Create an untracked, user-owned JSON file outside this repository:

```json
{
  "schema_version": 1,
  "targets": {
    "research-downloads": {
      "host": "worker.example.test",
      "port": 22,
      "username": "research",
      "identity_file": "/home/example/.ssh/downloads_ed25519",
      "host_key": "ssh-ed25519 REPLACE_WITH_VERIFIED_PUBLIC_HOST_KEY_BASE64"
    }
  }
}
```

Use an absolute identity path for the client OS (for example,
`C:/Users/example/.ssh/downloads_ed25519` on Windows). Obtain the full **public**
host key through a trusted channel; the placeholder above is intentionally not a
working key. Supported pins are Ed25519, NIST P-256 ECDSA and RSA (SHA-2 SSH
signatures). No trust-on-first-use, ignored host warning, agent forwarding or
ambient SSH configuration is used. The selected identity must already work
noninteractively; the command will not prompt for or expose its passphrase.

Use a normal unprivileged account and a task-owned pane. Only the intended
operator should control it during handoff. The host necessarily receives the
password, so trust that host and the operating account accordingly.

## Start the authorized download

Both downloaders use a closed option grammar. Config files, netrc credentials,
proxy routing, redirects, verbose traces and response headers cannot silently
change the delivery destination or retain credentials. The following command
shapes enter the downloader's own password prompt; they contain no password:

```sh
curl --disable --proto '=https' --proto-redir '=https' --proxy '' --noproxy '*' --max-redirs 0 --user example --output result.dat https://example.test/data
wget --no-config --no-netrc --no-proxy --max-redirect=0 --ask-password --user=example --output-document=result.dat https://example.test/data
```

The helper checks every URL operand against the account's exact admitted HTTPS
origin. Other supported output, timeout and rate-limit options are listed in
`downloader_arguments` in `repo_platform/credentials/terminal_remote.py`. Short
aliases and unspecified options are rejected; extend that one consumer only
when a necessary option can preserve the boundary. Redirected downloads need
their final authorized URL before starting this handoff. No automatic retry
widens the grammar or destination.

Observe the exact pane selector and actual password prompt, then invoke:

```text
python scripts/tools/credential_adapters.py terminal-paste --account example --origin https://example.test --credential password --targets /home/example/.config/agent-framework/terminal-targets.json --target research-downloads --pane download:0.0 --expected-prompt "ACTUAL OBSERVED PASSWORD PROMPT" --foreground-process wget --format json
```

Before reading custody, the pinned SSH peer verifies the pane, one matching
foreground downloader, its command and HTTPS origin, disabled terminal echo,
and exact visible prompt. Delivery rechecks the pane ID, process PID, process
start time and command identity after opening the second connection and again
after receiving the stdin value. It then pastes through a randomly named tmux
buffer, deletes it, and presses Enter. Cleanup is attempted on failure too.

There is necessarily a small interval between the final observation and tmux
delivery. This does not defend against hostile same-user software or another
operator actively replacing the target; exclusive operational ownership of the
pane and trusted host are prerequisites. Secret values are not logged, printed,
hashed in receipts, placed in argv/environment, or written to local/remote files.
The identity hash in the private probe covers the already nonsecret downloader
command, not a credential.

Passwords containing terminal control bytes are refused. Sending control-C,
escape or editing control characters could terminate or alter the waiting
consumer instead of entering a literal password.

`delivered_to_terminal` means the verified handoff completed. Provider acceptance,
download completion and integrity still require the surrounding task's evidence.
`terminal_delivery_uncertain` means **inspect the process before retrying**; a
second password could otherwise go to a different prompt. The tool never retries
that operation automatically. A failure has no raw SSH or subprocess output in
the result.
