# voice-bridge

**Add a voice spoke to a file-mailbox agent system.** Talk to the agents in your mailbox
from your phone, by voice — you dictate into a Reminders list, `voice-bridge` delivers it
into the mailbox, and replies come back as a reminder plus a tappable notification.

It **extends** the file-mailbox protocol
([agent-to-agent-communication-file-mailbox]) — it does *not* set up the mailbox or the
agents; it plugs a phone spoke (`vox`) into one that already exists. It exchanges messages
with a **peer** in the mailbox (a manager, by default; any spoke if you point `route_to`
elsewhere).

> **Async channel.** Replies can arrive a turn or more later; every message is timestamped
> `[HH:MM]` and the newest on a topic supersedes older ones.

**This page is the from-zero guide.** Follow it top to bottom for one of the two transports
and you end with a working bridge. Where the tool walks you through something itself, this
page says so and does not repeat it — it fills in only what a terminal cannot show you,
like the screens on your phone. Architecture, guarantees and known limits live in
[`docs/design.md`](docs/design.md).

---

## 0. Before you start

**A running file-mailbox with a peer joined.** If you don't have one, set that up first —
one paste, see [agent-to-agent-communication-file-mailbox]. `voice-bridge` attaches the
`vox` spoke to it. It will create the bare mailbox files if you point it at an empty
directory, but it never creates a *peer* — see [step 5](#5-your-peer) for that.

**An iPhone or iPad** with the Reminders app.

**Nothing else installed.** Everything below runs through `uvx`, which fetches the tool on
demand. If you don't have [`uv`](https://docs.astral.sh/uv/), install it first.

### How to type the command

Every example in this document is written as `voice-bridge <something>`. Nothing is
installed yet, so define the shorthand once and **read every `voice-bridge` below as
`$VB`**:

```bash
VB='uvx --from git+https://github.com/YoraiLevi/ai-voice-reminders-bridge voice-bridge'
$VB --help          # this document's `voice-bridge --help`
```

**If you cloned the repo instead**, read every `voice-bridge` below as
`uv run voice-bridge`:

```bash
uv run voice-bridge --help
```

Both forms are the same program. The difference matters in one place: **inside
`uv run`, the name `voice-bridge` works, and in your ordinary shell it does not** — `uv`
puts the script on the PATH for the command it is running and nowhere else. The tool knows
this and says so when it applies, printing a line like:

```
note: `voice-bridge` will not be on the PATH of the shell you type into -
read every `voice-bridge ...` below as `uv run voice-bridge ...`.
```

If you see that note, it is telling you the truth about *your* shell, and every command on
this page needs the prefix it names. If you don't see it, the plain name works and there is
nothing to do.

### Which transport?

|  | **iCloud** | **Radicale** (self-hosted) |
|---|---|---|
| what it is | your existing Apple account | a small CalDAV server on this machine |
| you need | an Apple ID and its password, plus 2FA on your phone | Tailscale on the machine and the phone |
| the two lists | **you create them on the phone** — Apple's API cannot | created for you |
| speed | fast | slower — it polls over the tunnel |
| your dictations | pass through Apple | never leave your own machines |

Pick one and follow **either** [step 1A](#1a-icloud-from-zero) **or**
[step 1B](#1b-radicale-from-zero). Steps 2 onward are shared. You can set up both later —
`setup --transport <name>` switches, and each transport keeps its own list selection.

---

## 1A. iCloud, from zero

### Make the two lists on the phone

Apple's API cannot create Reminders lists, so this is a manual step and it comes first.
In the Reminders app, add two lists:

- **Vox-Message-Outbox** — *you* dictate here. It is **your** outgoing.
- **Vox-Message-Inbox** — answers arrive here. It is **your** incoming.

The names read from your seat, not the tool's. You can use different names — you pick the
lists by hand during setup and the tool remembers which you chose, not what they are
called.

### Run the guided setup

```bash
voice-bridge setup
```

It walks you through the whole thing and asks only what it cannot infer. In order, it will:

1. **Ask three settings** — the transport (`icloud`), this spoke's name (`vox`), and your
   mailbox directory. Press Enter to keep any current value.
2. **Take your Apple credentials**, then log in. This is where **two-factor** happens: Apple
   pushes a code to your devices and setup asks for it. It needs your **main Apple ID
   password**, not an app-specific one. There is deliberately no `--password` flag —
   anything in a command line is readable by every other user on the machine.
3. **Show you your lists and ask which two carry messages.** Pick your outbox and your
   inbox. It never matches by name; it stores the id of what you chose.
4. **Offer push notifications.** See [step 2](#2-push-notifications-do-this-before-the-test)
   — subscribe on your phone *before* you answer.
5. **Send a test message round.**
6. **Print the phone prompt.**

If it stops early it tells you what is missing and which command to re-run. Re-running
`setup` is always safe: it only fills gaps.

**Sessions expire.** Apple does not document the lifetime. When a command asks for 2FA
again, run `voice-bridge icloud-login` and carry on.

Now go to [step 2](#2-push-notifications-do-this-before-the-test).

---

## 1B. Radicale, from zero

Radicale is a small CalDAV server that runs on this machine. Your dictations never leave
your own devices.

### Start the server

```bash
voice-bridge radicale-server init      # asks you to choose a password
voice-bridge radicale-server start --background
voice-bridge radicale-server status
```

`init` asks you to type a password for the CalDAV account, hidden as you type. The account
name is `vox` unless you pass `--user`. **Remember both** — your phone needs them in a
moment, and the password is stored hashed, so nothing can show it to you again.

`init` refuses to overwrite credentials that already exist. `--force` rotates the password,
which means the phone's CalDAV account stops connecting until you update it there too.

The server binds to **`127.0.0.1`**, deliberately: nothing on your local network can reach
it. The next step is what lets your phone in, and it does so without opening the LAN.

### Put HTTPS in front of it

**An iPhone refuses a CalDAV account over plain HTTP.** This is a fact from the device, not
a preference — a plain-HTTP account will not save, whatever else is correct. So TLS goes in
front, terminated by Tailscale:

```bash
tailscale serve --bg 5232      # the port the server is on - 5232 unless you changed it
tailscale serve status
```

`tailscale serve status` prints the HTTPS address your phone will use — something like
`https://your-node.tail1234.ts.net`. This is **tailnet-only**; it is not `tailscale funnel`,
so nothing is exposed to the internet.

Two things to check if that fails:

- **The syntax changed across Tailscale releases.** `tailscale serve --bg <port>` is the
  current form; older releases wanted `tailscale serve --bg https:443 http://127.0.0.1:5232`.
  Run `tailscale serve --help` on the machine you are actually on rather than guessing.
- **Your tailnet needs HTTPS certificates enabled.** Check with
  `tailscale status --json` — `CertDomains` must be non-empty. If it is empty, enable HTTPS
  in the Tailscale admin console; that is a web step nobody can do from the CLI.

### Add the account on the phone

The phone must be **on the tailnet** — the Tailscale app installed and connected — or the
`.ts.net` name will not resolve.

On the iPhone: **Settings → Calendar → Accounts → Add Account → Other → Add CalDAV Account**

| field | value |
|---|---|
| **Server** | your node's name from `tailscale serve status`, e.g. `your-node.tail1234.ts.net`. **No port** and no `https://` — 443 is the default |
| **User Name** | the account `init` created (`vox` unless you changed it) |
| **Password** | the password you chose when you ran `init` |
| **Description** | anything you like |

**Leave "Use SSL" ON.** It is the default, and there is nothing to change under Advanced
Settings — that is the point of the HTTPS step above.

After it saves, make sure **Reminders** is enabled for the account.

**If the account will not save**, the phone cannot reach the machine over the tunnel. Check
that the Tailscale app on the phone is connected, that `tailscale serve status` on the
machine still shows the proxy, and that the Server field is the `.ts.net` name with no port.

### Run the guided setup

```bash
voice-bridge setup
```

In order, it will:

1. **Ask three settings** — the transport (answer `radicale`), this spoke's name, and your
   mailbox directory.
2. **Check the server** is configured and answering. If it is not running it says so and
   gives you the command to start it.
3. **Offer to create the two lists for you.** Say yes. They appear on the phone through the
   CalDAV account you just added — no further phone step. (Say no and you create them
   yourself in a CalDAV client, exactly like the iCloud path.)
4. **Confirm which two lists carry messages** — the ones it just made, already selected.
5. **Offer push notifications.** See [step 2](#2-push-notifications-do-this-before-the-test).
6. **Send a test message round.**
7. **Print the phone prompt.**

**Check both lists appear on the phone** in the Reminders app, under the new account's
section, before moving on. That is the proof the CalDAV account is really working.

---

## 2. Push notifications (do this *before* the test)

Replies land in a Reminders list either way. Notifications are what make your phone *buzz*
when one arrives, instead of you remembering to look.

They are delivered by [ntfy](https://ntfy.sh). Setup offers you four options, including
generating a private topic for you and pointing at your own ntfy server.

**Install the ntfy app on your phone and subscribe to the topic before you answer that
question** — the test message setup sends immediately afterwards fires a banner, and if you
are not subscribed yet you will not see it and will not know whether it worked.

If you skip notifications entirely, everything still works; you just have to look at the
list yourself.

---

## 3. Prove it works

Guided setup already sends one message the whole way round. To check again at any time:

```bash
voice-bridge verify
```

One probe each way, then it cleans up after itself. No questions, nothing written to your
config: **exit 0 means a message made it, exit 2 means it did not.** It is not a `doctor`
check, because it *writes* — `doctor` only inspects.

```bash
voice-bridge doctor          # survey every interface; --fix repairs the safe ones
voice-bridge status          # a glance at the spoke's health
voice-bridge lists           # every list with its id
```

---

## 4. Teach the phone, then run the bridge

### Paste the prompt into the phone

```bash
voice-bridge vox-prompt
```

This prints instructions rendered with **your** list names, your account and your spoke's
name. Paste the whole thing into the Claude app (or whichever assistant you talk to) on the
phone. It tells the phone-side agent which list to dictate into, which to read, and how to
address them unambiguously.

Re-run it and paste again whenever you change lists or transports — the text differs per
transport, and a stale prompt is how a dictation ends up in a list nobody polls.

### Start the bridge

```bash
voice-bridge run
```

It announces the `vox` spoke into the mailbox, then prints an orientation block every time
it starts — your mailbox path, which file your peer reads, which file your peer writes,
whether a peer has ever written there, and the line it goes quiet on:

```
bridging: polling Vox-Message-Outbox every 10s
Ctrl-C to stop.
```

**Then it stays silent.** That is healthy. It does not narrate cycles where nothing
happened, because a log that scrolls while nothing is happening hides the moments when
something is. Errors and warnings still print.

`run` sets up whatever is missing before it starts, so a bare `voice-bridge run` on a fresh
machine takes you through the whole of step 1 and then bridges. `voice-bridge run --once`
does a single cycle and exits — **exit 1 there means "ran fine, nothing new"**, which is
normal, not a failure.

### Stopping it

**Ctrl-C.** That is the intended way to stop a foreground bridge; it announces its exit to
the peer and cleans up.

If replies were still waiting to be sent when you stopped, you will see:

```
kept 2 undelivered replies in <path>/to-vox.md - the next run delivers them.
```

**That is the bridge protecting your messages, not a failed cleanup.** It refuses to delete
anything it has not sent. Start it again and those replies go out, exactly once.

---

## 5. Your peer

Nothing is processed until a peer joins. Your dictations arrive in the mailbox and sit
there until an agent reads them.

When `run` creates a mailbox it writes **`PEER-PROMPT.md`** beside the mailbox files. Paste
that at a coding agent and it becomes your peer. If the file isn't there:

```bash
voice-bridge peer-prompt
```

It renders with your actual mailbox paths, so what it tells the agent is what this install
really uses.

---

## Everyday commands

```bash
voice-bridge peek --box inbox      # what is waiting in a box right now
voice-bridge tail -f               # follow the mailbox files
voice-bridge send "text"           # push one message through the outbound path
voice-bridge notify "done" --click https://example.com/pr/42
voice-bridge config show           # resolved settings
voice-bridge config set poll_interval 30
```

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
| `deliver FILE` | publish a file and send a tappable link |
| `config show \| get \| set \| fields` | inspect and edit settings |
| `icloud-login` | establish the iCloud session, including two-factor |
| `vox-prompt` | the phone-side prompt, rendered for your install |
| `peer-prompt` | the prompt that makes a coding agent your peer |
| `radicale-server init \| start \| stop \| status \| url` | manage the self-hosted backend |
| `reset` / `uninstall` | start over / remove everything this put on this machine |

**Exit codes are uniform**, so you can script against them: **0** success · **1** nothing to
do, or a transient failure worth retrying · **2** you must act (usage, configuration, or
something missing).

Every command's `--help` carries its own caveats and the exit-code table.

## Where your credentials and state live

Everything private lives under one XDG state directory —
`$XDG_STATE_HOME/vox-mailbox`, i.e. `~/.local/state/vox-mailbox/` by default. Never in the
repo, and never in your mailbox.

- **iCloud:** `icloud.env` holds your Apple ID and password; the trusted session caches in
  `pyicloud-cookies/`.
- **Radicale:** `radicale.env` holds the server URL and the account `init` created.
- **ntfy:** `ntfy-topic.txt` holds your private topic.

`voice-bridge config show` prints the resolved paths for your install. `reset` deletes the
settings so you can start over; `uninstall` removes everything, previewing exactly what it
will delete and asking you to type a word first.

## Tell your peer (one line)

Paste at your manager/agent once: *"the `vox` spoke is async/turn-based — timestamp replies,
and newest supersedes older on a topic."* That is all it needs; `voice-bridge` never touches
it.

## Limits

Delivery is by convention (append + poll), not a confirmed queue — there is no ack. One
phone per spoke; single-machine mailbox. Apple's private API can change without notice,
which is why Radicale exists as a same-contract fallback.

## Development

```bash
uv run --extra dev pytest        # unit / contract / integration / e2e
uv run --extra dev ruff check voice_bridge tests
uv run --extra dev mypy voice_bridge
```

Architecture, guarantees, and known limits: [`docs/design.md`](docs/design.md).

[agent-to-agent-communication-file-mailbox]: https://github.com/YoraiLevi/agent-to-agent-communication-file-mailbox
