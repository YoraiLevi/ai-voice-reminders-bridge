# Command and settings reference

Look-up material, not a walkthrough. If you are setting up for the first time, start at the
[README](../README.md).

Commands are written as `vb …`, the shortcut defined in [Install](install.md).

- [Everyday commands](#everyday-commands)
- [Every command](#every-command)
- [Exit codes](#exit-codes)
- [Where your credentials and state live](#where-your-credentials-and-state-live)
- [Starting over, and removing it](#starting-over-and-removing-it)
- [Limits](#limits)
- [Development](#development)

---

## Everyday commands

```
vb peek --box inbox      # inbox = your dictations; outbox = replies to you
vb tail -f               # follow the mailbox files
vb send "text"           # push one message through the outbound path
vb notify "done" --click https://example.com/pr/42
vb config show           # resolved settings, including every path
vb config set poll_interval 30
```

## Every command

| command | what it does |
|---|---|
| `run` | set up whatever is missing, then bridge continuously |
| `setup [--verify]` | the guided walkthrough: config, auth, lists, notifications |
| `verify` | prove a message round-trips, and nothing else |
| `doctor [--fix]` | survey every interface; read-only unless `--fix` |
| `status` | a glance at the spoke's health |
| `lists` | every backend list with its id |
| `peek --box inbox\|outbox [-n N]` | what is sitting in a box right now |
| `tail [-n N] [-f]` | follow the mailbox files |
| `send TEXT` | push one message through the outbound path |
| `notify TEXT [--click URL]` | send a push notification |
| `deliver FILE` | publish a file as a GitHub gist — **unlisted** by default, `--public` to list it — and send a tappable link. Needs the `gh` CLI, logged in. Unlisted is not private: anyone with the link can read it, and the link goes to your phone through the notification topic |
| `config show \| get \| set \| fields` | inspect and edit settings |
| `icloud-login` | establish the iCloud session, including two-factor |
| `vox-prompt` | the phone-side prompt, rendered for your install |
| `peer-prompt` | the prompt that makes a coding agent your peer |
| `radicale-server init \| start \| stop \| status \| url` | manage the self-hosted backend |
| `reset` / `uninstall` | start over / remove everything this put on this machine |

**Every command's `--help` carries its own caveats and the exit-code table.** They are the
source of truth; this page is a map.

## Exit codes

Uniform across every command, so you can script against them:

| code | meaning |
|---|---|
| **0** | success |
| **1** | nothing to do, or a transient failure worth retrying |
| **2** | you must act — usage, configuration, or something missing |

Two that surprise people, both deliberate:

- **`run --once` exiting 1** means the cycle ran fine and there was simply nothing new. Not a
  failure.
- **`verify` exiting 1** means the check never finished — a stall you chose not to wait out,
  or a Ctrl-C. Run it again.

## Where your credentials and state live

Everything private lives under one XDG state directory — `$XDG_STATE_HOME/vox-mailbox`, i.e.
`~/.local/state/vox-mailbox/` by default. Never in the repository, never in your mailbox.

| file | holds |
|---|---|
| `icloud.env` | your Apple ID and password |
| `pyicloud-cookies/` | the trusted iCloud session, so 2FA is not asked every time |
| `radicale.env` | the server URL, the account `init` created, and **its password in plaintext** — the client has to present it, so this copy cannot be a hash ([why](setup-radicale.md#where-the-password-lives)) |
| `ntfy-topic.txt` | your private notification topic |

Your **settings** are somewhere else entirely: `.claude/voice-bridge.json`, **under the
directory you run commands from**. `vb config show` prints every resolved path for your
install, which is the reliable way to find out where yours actually is.

## Starting over, and removing it

**`vb reset` deletes your settings *and your credentials*** — the saved Apple login and the
trusted 2FA session with it, so you will do the two-factor dance again on your phone. That is
the point of the command rather than an oversight. Your mailbox and its messages are never
touched.

**`vb uninstall` removes everything** this put on the machine.

Both list every path before they ask anything, and both want a word typed in full rather than
a `y`.

## Limits

Delivery is by convention — append and poll — not a confirmed queue, so there is no ack. One
phone per spoke; single-machine mailbox. Apple's private API can change without notice, which
is why the [Radicale backend](setup-radicale.md) exists as a same-contract fallback.

The honest, detailed account of what does and does not hold is in
[Architecture and limits](design.md).

## Development

```
uv run --extra dev pytest        # unit / contract / integration / e2e
uv run --extra dev ruff check voice_bridge tests
uv run --extra dev mypy voice_bridge
```

---

**Related:** [Install](install.md) · [Using the bridge](using.md) ·
[Architecture and limits](design.md) · [README](../README.md)
