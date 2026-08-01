# voice-bridge

**Add a voice spoke to a file-mailbox agent system.** Talk to the agents in your mailbox
from your phone, by voice — you dictate into a Reminders list, `voice-bridge` delivers it
into the mailbox, and replies come back as a reminder, with a push notification if you set
one up.

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

**An iPhone or iPad** with the Reminders app, **and an assistant app on it that you can
talk to and that can read and write your Reminders lists** — the Claude app, or whatever you
use. That app is the thing you actually dictate to; `voice-bridge` carries what it writes.

**Nothing else installed.** Everything below runs through `uvx`, which fetches the tool on
demand. If you don't have [`uv`](https://docs.astral.sh/uv/), install it — the installers
are one line each and the link has the current form for your platform. Then prove it, because
everything after this depends on it:

```
uv --version
```

### Pick a directory and stay in it

**Your settings live in `.claude/voice-bridge.json` under the directory you run the command
from.** Not in your home directory, not in the state directory — *there*. Make a directory
now and run every `voice-bridge` command on this page from it:

```bash
mkdir -p ~/voice-bridge && cd ~/voice-bridge          # bash / zsh
```

```powershell
New-Item -ItemType Directory -Force ~/voice-bridge; cd ~/voice-bridge    # PowerShell
```

Get this wrong and nothing warns you. Run `setup` in one directory and `run` in another,
and the second one finds no config and **starts a fresh guided setup, writing a second
config into that directory** — so you end up with two half-installs and no error to tell you
which is which. Commands that need a finished install, like `verify`, instead refuse with a
*"No list is selected for:"* message naming each unfilled role.

To use one config from anywhere, set `VOICE_BRIDGE_CONFIG` to its full path, or pass
`--config PATH` every time.

### How to type the command

Every example in this document is written as `voice-bridge <something>`. Nothing is
installed yet, so pick the line for your shell and **read every `voice-bridge` below as the
shorthand it defines**.

**bash / zsh:**

```bash
vb() { uvx --from 'voice-bridge[all] @ git+https://github.com/YoraiLevi/ai-voice-reminders-bridge' voice-bridge "$@"; }
vb --help          # this document's `voice-bridge --help`
```

**PowerShell:**

```powershell
function vb { uvx --from 'voice-bridge[all] @ git+https://github.com/YoraiLevi/ai-voice-reminders-bridge' voice-bridge @args }
vb --help
```

**The function lasts only for this terminal window.** This guide spans installing
Tailscale, steps on your phone, and commands you will come back to days later — open a new
window and `vb` is simply gone, with a "not recognized" error and nothing to explain it.
Re-paste the line above in any new terminal.

**If you cloned the repo instead**, read every `voice-bridge` below as
`uv run --extra all voice-bridge`, and **run it from the repo root** — `uv run` needs the
project's own `pyproject.toml` beside it. That root *is* your "one directory" from above, so
your settings land in `<repo>/.claude/voice-bridge.json`; ignore the `~/voice-bridge`
suggestion, you already have a directory. Same in both shells:

```
uv run --extra all voice-bridge --help
```

**`[all]` and `--extra all` are not optional.** The base package has **no dependencies at
all**: each transport pulls its own backend, so a plain install fetches nothing either
transport needs and the first command that touches one fails with *"pyicloud not
installed"* or *"install voice-bridge[server]"*. `[all]` covers both. If you want only one,
`[icloud]` covers the Apple path and `[caldav,server]` covers Radicale.

Both forms are the same program. One difference is worth knowing: **inside `uv run`, the
name `voice-bridge` works, and in your ordinary shell it does not** — `uv` puts the script
on the PATH for the command it is running and nowhere else. The tool detects this and prints
a note beginning:

```
note: `voice-bridge` will not be on the PATH of the shell you type into -
```

…followed by the exact form to type in *your* situation. If you see it, use the form it
names. If you don't, the plain name works and there is nothing to do.

### Which transport?

|  | **iCloud** | **Radicale** (self-hosted) |
|---|---|---|
| what it is | your existing Apple account | a small CalDAV server on this machine |
| you need | an Apple ID and its password, plus 2FA on your phone | Tailscale on the machine and the phone |
| the two lists | **you create them on the phone** — Apple's API cannot | created for you |
| speed | fast | slower — it polls over the tunnel |
| your dictations | pass through Apple | never leave your own machines |

Pick one and follow **either** [step 1A](#1a-icloud-from-zero) **or**
[step 1B](#1b-radicale-from-zero). Steps 2 onward are shared.

You can switch later with `setup --transport <name>`. **Switching clears the list
selection** — there is one selection in the config, not one per transport — so you pick your
two lists again on the new transport. Setup says so when it happens.

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

```
voice-bridge setup
```

It walks you through the whole thing and asks only what it cannot infer. In order, it will:

1. **Ask three settings** — the transport (`icloud`), this spoke's name (`vox`), and your
   mailbox directory. Press Enter to keep any current value.
2. **Offer to take your Apple credentials**, then log in. It asks first, and declining is
   fine — it names `voice-bridge icloud-login` for later and exits 0. This is where
   **two-factor** happens: Apple pushes a code to your devices and setup asks for it. It
   needs your **main Apple ID password**, not an app-specific one. There is deliberately no
   `--password` flag — anything in a command line is readable by every other user on the
   machine.
3. **Show you your lists and ask which two carry messages.** Pick your outbox and your
   inbox. It never matches by name; it stores the id of what you chose.
4. **Offer push notifications.** See [step 2](#2-push-notifications-do-this-before-the-test)
   — subscribe on your phone *before* you answer.
5. **Offer to send a test message round** — it asks first, and the step is skippable.
6. **Print the phone prompt.**
7. **Offer to start the bridge** — *"Start the bridge now? (Enter = yes, start it)"*. Saying
   yes takes over the terminal; Ctrl-C stops it and brings you back here, and nothing on this
   page is lost either way.

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

```
voice-bridge radicale-server init      # asks you to choose a password
voice-bridge radicale-server start --background
voice-bridge radicale-server status
```

`status` prints a small table; the line to look at is **`reachable`**, which must say
`True`. It also exits 0 when the server answers and 1 when it does not, so scripts should
gate on the exit code rather than on the text.

**`--background` detaches a process; it does not install a service.** The server dies with a
reboot and nothing restarts it — so if you come back tomorrow and the bridge cannot reach it,
run `radicale-server start --background` again.

`init` asks you to type a password for the CalDAV account, hidden as you type. The account
name is `vox` unless you pass `--user`. **Write both down** — your phone needs them in a
moment.

Two copies of that password are stored, and they are different on purpose. The **server's**
copy is bcrypt-hashed in its user file, because a server only ever needs to *check* a
password. The **client's** copy sits in plaintext in `radicale.env` (permissions `0600`
where the platform supports it), because the bridge has to *present* it to authenticate.
So: if you forget it, `radicale.env` is where to look — and knowing a plaintext credential
lives there is part of deciding where this machine sits.

`init` refuses to overwrite credentials that already exist. `--force` rotates the password,
which means the phone's CalDAV account stops connecting until you update it there too.

The server binds to **`127.0.0.1`**, deliberately: nothing on your local network can reach
it. The next step is what lets your phone in, and it does so without opening the LAN.

### Put HTTPS in front of it

**You need [Tailscale](https://tailscale.com/) here** — installed and signed in to the same
tailnet on *both* this machine and the phone. Do that first; the commands below assume the
`tailscale` CLI exists and is logged in.

**An iPhone refuses a CalDAV account over plain HTTP.** This is a fact from the device, not
a preference — a plain-HTTP account will not save, whatever else is correct. So TLS goes in
front, terminated by Tailscale:

```
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

```
voice-bridge setup
```

In order, it will:

1. **Ask three settings** — the transport (answer `radicale`), this spoke's name, and your
   mailbox directory.
2. **Check the account exists and the server answers.** Either failure stops setup and
   prints the `radicale-server` command or commands to run, then asks you to re-run `setup`.
   Both stops exit 2 — you have something to go and do — so a scripted install can tell them
   apart from success.
3. **Offer to create the two lists for you.** Say yes. They appear on the phone through the
   CalDAV account you just added — no further phone step. (Say no and you create them
   yourself in a CalDAV client, exactly like the iCloud path.)
4. **Select the two lists** — the ones it just created are chosen for you, by id, and it
   says so rather than matching them by name.
5. **Offer push notifications.** See [step 2](#2-push-notifications-do-this-before-the-test).
6. **Offer to send a test message round** — it asks first, and the step is skippable.
7. **Print the phone prompt.**
8. **Offer to start the bridge** — Ctrl-C stops it and brings you back here.

**Check both lists appear on the phone** in the Reminders app, under the new account's
section, before moving on. That is the proof the CalDAV account is really working.

---

## 2. Push notifications (do this *before* the test)

Replies land in a Reminders list either way. Notifications are what make your phone *buzz*
when one arrives, instead of you remembering to look.

They are delivered by [ntfy](https://ntfy.sh). Setup offers a menu — a private topic it
generates for you, a topic you already subscribe to, your own ntfy server, or skip.

**Setup prints the generated topic on screen, just above the question** — a long `vox-…`
string. Install the ntfy app on your phone, tap **+** to subscribe to a topic, paste that
exact string, and *then* answer the question in the terminal. The test message setup sends immediately afterwards fires a banner, and if you are
not subscribed yet you will not see it and will not know whether it worked.

If you skip notifications entirely, everything still works; you just have to look at the
list yourself.

---

## 3. Prove it works

Guided setup already sends one message the whole way round. To check again at any time:

```
voice-bridge verify
```

One probe each way, then it cleans up after itself. Nothing is written to your config and
it asks you nothing — except if the transport stalls, when it offers to keep waiting: **exit 0 means a message made it, exit 2 means it did not, and exit 1 means the
check never finished** — a stall you chose not to wait out, or a Ctrl-C. Run it again. It is
not a `doctor` check, because it *writes* — `doctor` only inspects.

```
voice-bridge doctor          # survey every interface; --fix repairs the safe ones
voice-bridge status          # a glance at the spoke's health
voice-bridge lists           # every list with its id
```

---

## 4. Teach the phone, then run the bridge

### Paste the prompt into the phone

```
voice-bridge vox-prompt
```

This prints instructions rendered with **your** list names, your account and your spoke's
name. Paste the whole thing into the Claude app (or whichever assistant you talk to) on the
phone. It tells the phone-side agent which list to dictate into, which to read, and how to
address them unambiguously.

Re-run it and paste again whenever you change lists or transports — the text differs per
transport, and a stale prompt is how a dictation ends up in a list nobody polls.

### Start the bridge

```
voice-bridge run
```

Every start prints an orientation block before it goes quiet, and announces the `vox` spoke
into the mailbox — your mailbox path, which file your peer reads, which file your peer writes,
whether a peer has ever written there, and the line it goes quiet on:

```
bridging: polling Vox-Message-Outbox every 10s
Ctrl-C to stop.
```

The interval in that line is your `poll_interval` setting, not a fixed number.

**Then it stays silent.** That is healthy. It does not narrate cycles where nothing
happened, because a log that scrolls while nothing is happening hides the moments when
something is. Errors and warnings still print.

**On a machine with no config at all, `run` takes you through guided setup first** and then
bridges — so a bare `voice-bridge run` is a legitimate way to start from zero. Anything else
missing it *names and exits 2* rather than fixing: unselected lists, or credentials it cannot
find. (On iCloud you still make the two lists on the phone yourself — no command can create
them.)

`voice-bridge run --once` does a single cycle and exits — **exit 1 there means "ran fine,
nothing new"**, which is normal, not a failure.

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

```
voice-bridge peer-prompt
```

Paste what that prints at a coding agent and it becomes your peer. It renders with your
actual mailbox paths, so what it tells the agent is what this install really uses.

If `run` had to *create* your mailbox — a fresh directory rather than the existing one from
step 0 — it also drops the same text in **`PEER-PROMPT.md`** beside the mailbox files, so it
is still there tomorrow after the startup output has scrolled away.

---

## 6. Actually use it

Everything above builds the thing. This is the thing working, and it is worth doing once
deliberately so you know what normal looks like.

With the bridge running in one terminal:

1. **Say something to the assistant app on your phone** — ask it to put a message in your
   dictation list. It writes a reminder into **Vox-Message-Outbox**.
2. **Wait one poll interval** (10 seconds by default). The bridge reads the reminder, appends
   it to your peer's inbox file in the mailbox, and completes the reminder on the phone — so
   *the item disappearing from the list is the receipt* that it was picked up.
3. **Watch it land.** In another terminal, run `voice-bridge tail -f`. Your words appear as
   one line:

       [manager] - [14:30] (vox) <what you said>

   The `[manager]` at the front is `tail`'s label for *which mailbox file* the line came
   from — your peer's inbox, which is where your dictations go.
4. **Your peer replies** into the mailbox, in its own time — this is an async channel, so
   the answer may be a turn or more later.
5. **The reply reaches your phone** as a new reminder in **Vox-Message-Inbox**, plus a push
   notification if you set one up in step 2.

That is the whole loop. If a dictation never appears in step 3, `voice-bridge peek --box
inbox` shows what the bridge can currently see on the phone — an empty list there means the
phone never wrote it, and a full one means the bridge is not reading the list you think it
is.

---

## Everyday commands

```
voice-bridge peek --box inbox      # inbox = your dictations; outbox = replies to you
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
| `deliver FILE` | publish a file as a GitHub gist — secret by default, `--public` to change that — and send a tappable link. Needs the `gh` CLI, logged in |
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
- **Radicale:** `radicale.env` holds the server URL, the account `init` created, and **its
  password in plaintext** — see [step 1B](#1b-radicale-from-zero) for why that copy has to be
  reversible.
- **ntfy:** `ntfy-topic.txt` holds your private topic.

`voice-bridge config show` prints the resolved paths for your install.

**`reset` deletes your settings *and your credentials*** — the saved Apple login and the
trusted 2FA session with it, so you will do the two-factor dance again on your phone. That is
the point of the command rather than an extra tier, and your mailbox and its messages are
never touched. `uninstall` removes everything. Both list every path before they ask, and both
want a word typed in full rather than a `y`.

## Tell your peer (one line)

Paste at your manager/agent once: *"the `vox` spoke is async/turn-based — timestamp replies,
and newest supersedes older on a topic."* That is all it needs; `voice-bridge` never touches
it.

## Limits

Delivery is by convention (append + poll), not a confirmed queue — there is no ack. One
phone per spoke; single-machine mailbox. Apple's private API can change without notice,
which is why Radicale exists as a same-contract fallback.

## Development

```
uv run --extra dev pytest        # unit / contract / integration / e2e
uv run --extra dev ruff check voice_bridge tests
uv run --extra dev mypy voice_bridge
```

Architecture, guarantees, and known limits: [`docs/design.md`](docs/design.md).

[agent-to-agent-communication-file-mailbox]: https://github.com/YoraiLevi/agent-to-agent-communication-file-mailbox
