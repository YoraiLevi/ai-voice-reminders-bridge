# voice-bridge — design

How `voice-bridge` is put together, what it guarantees, and where it deliberately stops.
This document is the architecture reference; the [README](../README.md) is the quick start.

**Reading it standalone:** you don't need prior context. Terms are defined where they first
appear, and every guarantee below says plainly whether it holds *in the code today* or is
*designed but not yet built*.

**This document does not cover installation or first run.** Those live in the
[README](../README.md), which is the quick start of record: how to invoke the tool with no
installation, and the single command that sets everything up and starts bridging. Read the
README first if you have never run this; come here for how it works and what it promises.

---

## 1. What it is

`voice-bridge` is a small, headless CLI that attaches one asynchronous **voice spoke** — named
`vox` — to an existing **file-mailbox** agent system, and federates it to your phone.

You dictate a Reminder on your phone. `voice-bridge` picks it up and appends it to the
mailbox as a line an AI agent reads. When that agent replies, the reply comes back as a
Reminder in a second list, plus a tappable push notification.

There is no AI inside `voice-bridge`. It is a message bridge: poll, append, push, sleep.

### What it is *not*

It is an **extension** of the [file-mailbox protocol][mailbox], not an implementation of it.
It does not create your agents, and it does not manage the conversation. It plugs a phone
spoke into a mailbox that already exists. It will create the bare mailbox *files* if you point
it at an empty directory — but never a peer to talk to.

### The async contract

This is a turn-based channel, not a chat. A reply may arrive a turn or more later. Every
message carries an `[HH:MM]` timestamp, and on any topic the newest message supersedes older
ones. Designing around that expectation is what keeps the loop simple and crash-tolerant.

---

## 2. Boundary — what it owns, what it defers

| Deferred to the mailbox protocol | Owned by voice-bridge |
|---|---|
| the mailbox and its append-only files | the gateway spoke (the poller) and its three edges |
| the peer agents (LLM sessions) | the phone transport (iCloud default, or Radicale) |
| join / liveness / mesh presence | the phone-side prompt, rendered with your settings |

The spoke has exactly **three edges**, and they are the whole surface worth abstracting:

1. **The bus** — two Reminders lists, reached through a `Transport`.
2. **The file mailbox** — read our inbox file, append to the peer's inbox file.
3. **The doorbell** — push notifications, sent through [ntfy](https://ntfy.sh) (hence the
   `ntfy_*` settings later in this document), so a reply is noticed without polling by hand.

Keeping the boundary this narrow is deliberate: the mailbox already provides durability and
ordering, so the bridge does not reimplement them.

---

## 3. Vocabulary and routing

There is no hard-coded "manager" role. The bridge talks to a **peer** identified by
configuration, so the same code works in a star topology or a mesh.

| setting | default | meaning |
|---|---|---|
| `spoke_name` | `vox` | our identity; we **read** `to-vox.md` |
| `route_to` | `manager` | the peer; we **write** to `to-manager.md` |
| `from_name` | `= spoke_name` | the tag on every line we write |

Every line the bridge writes into the mailbox has one physical-line form:

```
- [HH:MM] (vox) the text of the message
```

> **Multi-line dictations become one line.** The mailbox format is line-oriented, so a stray
> newline would split one message into two. Everything you dictate is joined into a single
> line before it is written. Expect paragraph breaks to disappear.

### "Inbox" means two different directions — read this once

The word *inbox* appears on both edges and points opposite ways. This trips up almost
everyone, so it is worth fixing in your head early:

**The rule: an inbox belongs to whoever READS it.** Every name below follows it, which is
what makes them consistent once you hold that one idea.

| term | whose it is | direction |
|---|---|---|
| **Vox-Message-Outbox** (`inbox_list`) | a list on your **phone** — *yours* | **you → the agent**: you dictate here, so it is your OUTbox |
| **Vox-Message-Inbox** (`output_list`) | a list on your **phone** — *yours* | **the agent → you**: answers arrive here, so it is your INbox |
| **`to-vox.md`** | a file in the **mailbox** — *the bridge's* | **the agent → us** — the bridge reads it |
| **`to-manager.md`** | a file in the **mailbox** — *the peer's* | **us → the agent** — the bridge writes it |

Note the deliberate crossing in the config: `inbox_list` names the list the BRIDGE reads,
which is YOUR outbox. The field names are written from the bridge's seat and the list names
from yours, and both are correct — a message leaving your outbox is arriving in its inbox.
So a dictation travels **your outbox → the peer's inbox file**, and a reply travels **the
bridge's inbox file → your inbox**.

Mesh presence is intentionally unimplemented — the bridge stays agnostic so it can adopt
liveness when the underlying protocol exposes it.

---

## 4. Architecture

Five runtimes participate. **Only the poller is ours.**

```
the phone persona   ← an AI app on your phone, using the rendered prompt   (not ours)
the bus (2 lists)   ← Apple CloudKit, or a Radicale CalDAV server          (not ours)
the poller (spoke)  ← Python; the thing we own, run, and test          ◀── OURS
the peer            ← an AI session joined to the mailbox                  (not ours)
the doorbell        ← a push-notification service                         (not ours)
```

The poller is the only clock in the system. Its loop is:

```
connect → poll the inbox list → append to the peer's mailbox file
        → drain our mailbox file → post replies to the outbox list + push
        → sleep
```

Reliability comes from the two **buffers** either side of the poller — the Reminders list and
the mailbox file. Both are durable and append-oriented, both are made idempotent by a record
of what has already been handled, and neither requires the other to be running. If the poller
stops, work accumulates safely at both ends and drains when it restarts.

### The transport seam

Everything that differs between backends is confined to one interface:

```
connect()                  establish a session (idempotent)
list_todo_lists()          enumerate lists as (name, id)
resolve_list(name, id)     find one list; by id when pinned
read_incomplete(list)      items awaiting handling
read_completed(list)       items already handled
add_todo(list, …)          create an item
complete(list, item_id)    mark an item handled
create_list(name)          create a list — not supported on every backend
```

Everything else — mailbox format, deduplication, notification, the poll/drain loop, the CLI —
is shared and backend-agnostic. Two real backends implement this (iCloud and CalDAV/Radicale),
plus an in-memory fake used by the tests.

`create_list` is the one operation backends genuinely disagree on: Apple exposes no
list-creation API, so the iCloud path raises a "not supported" error by design and the two
lists must be made by hand on the phone. Radicale creates them for you.

---

## 5. Configuration

Settings resolve in this order, first match winning:

```
--config PATH  →  $VOICE_BRIDGE_CONFIG  →  ./.claude/voice-bridge.json  →  built-in defaults
```

If you name a config file explicitly (by flag or environment variable) and it does not exist,
that is an error rather than a silent fallback — only the project-local default is allowed to
be absent.

| group | field | default | purpose |
|---|---|---|---|
| identity | `spoke_name` | `vox` | our name; names our inbox file |
| | `route_to` | `manager` | the peer we write to |
| | `from_name` | *(= `spoke_name`)* | tag on lines we write |
| phone bus | `inbox_list` | `Vox-Message-Outbox` | the list the bridge READS — *your* outbox, where you dictate |
| | `output_list` | `Vox-Message-Inbox` | the list the bridge WRITES — *your* inbox, where answers arrive |
| | `inbox_list_id` / `output_list_id` | *(empty)* | pin a list by ID — see *Ghost lists* |
| mailbox | `mailbox_dir` | `~/.agent-mail` | the shared mailbox directory |
| our state | `state_dir` | `$XDG_STATE_HOME/vox-mailbox` | credentials, cookies, bookkeeping |
| transport | `transport` | `icloud` | `icloud` or `radicale` |
| Radicale | `radicale_host` / `radicale_port` / `radicale_user` | `0.0.0.0` / `5232` / `vox` | self-hosted server settings |
| notification | `ntfy_server` | `https://ntfy.sh` | override to self-host |
| | `ntfy_title` / `ntfy_tags` / `ntfy_priority` | `Vox` / `robot` / `high` | banner presentation |
| | `ntfy_body_limit` | `-1` | cap banner length; `-1` = no cap |
| tuning | `reply_summary_limit` | `-1` | cap reminder title length; `-1` = no cap |
| | `poll_interval` | `10` | seconds between cycles |

Credential, session, and bookkeeping paths all **derive** from `state_dir`, so moving that one
setting relocates all private state together. Nothing private is written into the repository.

Environment variables read: `VOICE_BRIDGE_CONFIG` and `XDG_STATE_HOME` (as above);
`RADICALE_PASSWORD` when initialising the self-hosted server. The CalDAV backend additionally
lets real environment variables override the credentials file; the iCloud backend reads
credentials only from the file.

**Ghost lists.** Deleting and recreating a list on the phone can leave two lists with the same
name and different underlying IDs, which makes resolve-by-name ambiguous. `lists` shows every
ID; pinning the one you want removes the ambiguity permanently.

---

## 6. CLI surface

| command | what it does |
|---|---|
| `run` | ensure everything is set up, then bridge continuously |
| `setup` | provision config, auth, and lists, then stop |
| `doctor` | survey every interface and report status |
| `status` | a glance at the spoke's health |
| `lists` | enumerate the backend's lists with their IDs |
| `peek` | show what is sitting in a box right now |
| `tail` | follow the mailbox files as they change |
| `send` | push one message through the outbound path |
| `notify` | send a push notification |
| `deliver` | publish a file and send a tappable link to it |
| `config show \| get \| set \| fields` | inspect and edit settings |
| `icloud-login` | establish the iCloud session, including two-factor |
| `vox-prompt` | print the phone-side prompt, rendered with your list names |
| `radicale-server init \| start \| stop \| status \| url` | manage the self-hosted backend |

Useful modifiers on `run`: `--once` (a single cycle, for scripting), `--dry-run`,
`--interval`, `--transport`, `--mailbox`, and `--set KEY=VALUE` to override settings inline.
Global flags — `--config`, `--verbose`, `--quiet`, `--log-file`, and `--json` on the commands
that emit structured output.

**Exit codes** follow one convention throughout: **0** success, **1** a transient or
"nothing to do" condition, **2** a usage or configuration error. `run --once` uses this to
make scripting easy — 0 means it moved something, 1 means there was nothing new.

`doctor` reports the worst severity it finds as its exit code, so it can gate a script.

### Self-bootstrapping

`run` is `setup` plus the loop. Both check each step and skip it if already satisfied, which
makes re-running safe and makes "repair the one broken thing" the normal recovery path.

```
1. MAILBOX   locate it (flag, prompt, or default)
2. FILES     create the mailbox files if missing
3. CONFIG    run the setup flow if absent
4. AUTH      establish a backend session if needed
5. LISTS     provision or disambiguate the two lists
6. JOIN      announce the spoke into the mailbox
7. LOOP      poll inbox → mailbox; drain mailbox → outbox + push
8. EJECT     on exit, announce departure and delete our own inbox file
```

Announcing on join and eject means your peer sees the spoke arrive and leave the way it sees
any other participant.

**What eject deletes, and why that is not a contradiction.** On a clean exit the bridge
appends a departure line to the peer's inbox file and then **deletes its own inbox file**
(`to-vox.md`). The mailbox is append-only, so deleting a file deserves an explanation: *our*
inbox exists only to be drained by us, and removing it signals we are no longer listening —
the same way an absent mailbox means an absent participant. Files belonging to anyone else
are **never** modified or removed; the bridge only ever appends to the peer's inbox. If the
bridge is killed rather than stopped cleanly, the file simply remains and is drained on the
next start.

---

## 7. Runtime files

Everything private lives under `state_dir`; only the mailbox files are shared.

| file | written by | purpose |
|---|---|---|
| `to-<peer>.md` | us | dictations we deliver **to** the peer |
| `to-<spoke>.md` | the peer | replies we drain **to** the phone |
| inbox seen-record | us | which backend items have already been bridged |
| reply cursor | us | how far through our inbox file we have read |
| credentials file | `radicale-server init` (Radicale) — **by hand (iCloud)** | backend account details |
| session cache | `icloud-login` | avoids repeating two-factor on every run |
| notification topic | **by hand** | the private topic banners are sent to |
| poller pid-file | `run` | lets `status` detect a live poller |
| Radicale config, users, storage, log | `radicale-server` | the self-hosted backend's own state |

**Two files you must create yourself.** Nothing in the tool writes them, and their absence is
quiet rather than loud:

- **iCloud credentials.** `icloud-login` *reads* your Apple ID and password from the
  credentials file and establishes the session; it does **not** capture or write them. Create
  the file first, or the command exits with an error naming the exact path it wanted. (The
  Radicale path is different — `radicale-server init` writes its own credentials for you.)
- **The notification topic.** With no topic file, push is a silent no-op: replies still land
  in the outbox list, but no banner ever appears and nothing complains.

Two independent deduplication mechanisms, because the two directions have different shapes:

- **Inbound** (phone → mailbox) is keyed by the backend item's ID, recorded once handled. The
  record is bounded so it cannot grow without limit, and it never drops an item still pending.
- **Outbound** (mailbox → phone) uses a **byte offset** into the append-only file rather than
  a line count, so a partially written final line is held back until it is complete instead of
  being delivered truncated.

---

## 8. Guarantees and limits

This section is deliberately blunt. The value of an unattended bridge is *justified
confidence*, so a guarantee that is only aspirational is worse than none. Everything below is
stated against **the code as it exists today**.

### What holds today

- **Delivery errs toward duplication, never silent loss, on a process crash.** The bridge
  appends to the mailbox *first*, and only then records the item as handled. If it dies in
  between, the item is delivered again next cycle — a visible duplicate line rather than a
  message that vanished.
- **Restarting is always safe.** Both directions are idempotent; stopping and restarting
  re-reads state from disk and continues.
- **A partially written line is never delivered.** The outbound reader holds an incomplete
  trailing line until it is finished, and notices if the file it is reading has shrunk.
- **A failed notification never costs you a message.** Push is best-effort and deliberately
  cannot break the reply path.
- **Nothing private lives in the repository.** Credentials and session state are confined to
  the state directory.

### What does *not* hold today — stated plainly

- **Not power-loss safe.** A write returns once the operating system has accepted it, which is
  not the same as it having reached the disk. If the machine loses power — rather than the
  process merely crashing — within the operating system's write-back window, a dictation can
  be marked handled while the mailbox line is lost. *A fix is designed and not yet built:*
  flush the append to disk before marking the item handled, which turns this into the same
  harmless duplicate as a process crash.
- **Settings and credential files are written in place, not atomically.** A crash during a
  write can leave a truncated file. *A fix is designed and not yet built:* write to a
  temporary file and rename over the original.
- **A failed "mark handled" on iCloud is silent.** The item is recorded locally as bridged
  regardless, so the message reaches your agent exactly once — but the Reminder can remain
  visible on your phone as though nothing happened. If you see a dictation that never
  disappears, check whether it was in fact delivered before re-dictating it.
- **A failed notification is quiet.** The reply still lands in the outbox list, but a banner
  that fails to send does not interrupt anything — pushing is deliberately best-effort so it
  can never break a reply. `notify` reports the reason and distinguishes "nothing configured"
  (exit 2) from "the send failed" (exit 1); the background loop stays silent by design.
- **The notification topic is a bearer secret.** Anyone who knows it can post to it. A leaked
  topic lets someone send banners that appear to come from your bridge, including a tappable
  link to anywhere — which is worth taking seriously precisely because you learn to trust
  these notifications. Use a long random topic, and self-host if the content matters.
- **A few commands report backend failures rawly.** `lists` and `peek` can surface an
  authentication or network error as an unformatted Python traceback rather than a clean
  message. *A fix is designed and not yet built.*
- **Delivery is by convention, not acknowledgement.** The bridge appends and polls; nothing
  confirms that a human or an agent actually read a message.
- **No presence detection.** The bridge cannot tell whether a peer is reading the mailbox at
  all. If nobody is, dictations are delivered correctly and simply never answered.
- **One phone per spoke, one machine per mailbox.** Running two pollers against the same
  account is unsupported and can trigger backend rate-limiting.
- **The iCloud backend uses a private, unofficial API** and can break without notice. The
  Radicale backend implements the same contract and is the supported fallback.

### Backend differences

| | iCloud (default) | Radicale (self-hosted) |
|---|---|---|
| setup | Apple ID + one-time two-factor | run a CalDAV server, add the account on the phone |
| lists | must be created by hand on the phone | created for you |
| reachability | works anywhere | needs a private network or tunnel to your server |
| session | expires periodically — watch for a re-authentication prompt | stable |

The iCloud session's lifetime is **not a fixed, documented number**. Treat "it expires
eventually" as the only safe assumption and re-authenticate when prompted.

**Honest comparison:** iCloud is the default because it needs no server and works from
anywhere, but it is the higher-friction option in practice — you must create the two lists by
hand, write the credentials file yourself, re-authenticate when the session lapses, and accept
an unofficial API that can change without warning. Radicale costs you a server to run and a
network path to reach it, and gives you a stable, documented backend that provisions itself.
If you are comfortable self-hosting, Radicale is the smoother long-term road.

---

## 9. When a dictation goes unanswered

Nothing acknowledges delivery end-to-end, so "I dictated something and nothing came back" is
the failure you are most likely to meet. Work through it in this order — each step
distinguishes a different cause, and the early steps are the common ones.

> **Silence is not the same as "no reply."** The most frequent cause is a reply that arrived
> correctly but never announced itself, because push is best-effort and fails invisibly.
> **Always check the outbox list before assuming nothing happened.**

1. **Look in the outbox list on your phone** (the list named by the `output_list` setting —
   note the setting is spelled `output_`, not `outbox_`). If the reply is sitting there, delivery worked
   end to end and only the notification failed — check that a notification topic is
   configured, and that the notification server is reachable.
2. **Is the dictation still sitting in your inbox list?** Two different meanings:
   - The bridge is not running, or cannot reach the backend → check `status`, then `doctor`.
   - It *was* delivered but the "mark handled" step failed silently — a known limitation. Look
     at the mailbox before re-dictating, or you will send it twice.
3. **Look in the peer's inbox file** with `tail`. If your line is there, the bridge did its
   whole job and the question is whether anything is *reading* that file.
4. **Is a peer actually attached?** The bridge cannot detect this, and it is a very common
   cause: a correctly delivered dictation with no agent reading the mailbox simply sits there
   forever. Confirm an agent is running and joined to the same mailbox directory.
5. **Run `doctor`.** It surveys every interface at once — credentials, session, lists,
   mailbox — and reports the worst thing it finds. Use it to catch what the targeted checks
   above missed.
6. **Check the log.** Run with `--verbose`, or point `--log-file` somewhere durable, and look
   for authentication failures and backend errors around the time you dictated.

If step 3 shows the line delivered and step 4 confirms a peer is attached, the bridge has done
everything it promises — the remaining question is on the agent's side, not this tool's.

---

## 10. Testing model

Every capability is tested against a controllable oracle, and the network is touched only
where a real server can be embedded.

| layer | oracle |
|---|---|
| unit | none — pure functions: settings, line format, deduplication, clipping |
| contract | an in-memory fake transport — the full poll/drain cycle and the bootstrap machine |
| integration | a real, embedded Radicale server — genuine list and item operations |
| end-to-end | the actual CLI in a subprocess |

Apple is deliberately excluded from automated testing: two-factor authentication and a private
API cannot live in continuous integration. The transport seam is what keeps that exclusion
from becoming a coverage hole — the iCloud path runs the same shared logic the fake and
Radicale paths verify, so only the thin backend adapter is unexercised. That adapter is
therefore the one part of the system whose first real test is a live run.

---

## 11. Deliberately open

- **Multi-line replies** — currently one line per message, matching the mailbox format. A
  block delimiter would be added only if real usage demands it.
- **State directory on Windows** — functional, but not yet using the platform-native location.
- **Mesh presence** — deferred until the mailbox protocol itself exposes liveness, so routing
  stays configuration-driven rather than guessing.
- **Credentials at rest** — stored as plain text with restrictive permissions where the
  platform supports them; stronger at-rest protection is an open item.

[mailbox]: https://github.com/YoraiLevi/agent-to-agent-communication-file-mailbox
