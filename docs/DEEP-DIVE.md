# voice-bridge — deep dive

A file-by-file walkthrough of how the bridge actually works, written to be **read
aloud**. Each section stands alone: ask "tell me about `config.py`" and the reader
can speak just that part. Where it helps, a few real lines of code are quoted — but
never a whole file. For the one-line file index and the quickstart, see the
`README.md`; this document is the *why* and the *how*, in detail.

---

## Overview — the architecture in four sentences

voice-bridge lets you talk to a **Claude Code session on your PC from your phone**,
using two Apple Reminders lists as a shared message bus: the phone drops a task into
a "To Claude" list, a small poller on the PC picks it up and writes it into a plain
text mailbox the manager session is watching, and the manager's replies flow back
out to a "From Claude" list you read on the phone. The heart of the system is a
**poller loop** (`pyicloud_bridge.py`) that every few seconds reads new reminders,
appends each as one line to `to-manager.md`, and drains any replies the manager
wrote into `to-phone.md` back out to the phone.

The single most important framing: **this channel is asynchronous and turn-based,
not a live call.** In the Claude voice app the phone only checks the reply list on
*its* turn — when you speak — so a reply the manager writes mid-thought doesn't
interrupt you; it surfaces the next time you talk. Because of that delay every reply
is stamped with a local `[HH:MM]` time, and the rule "the newest message on a topic
supersedes older ones" is baked into the phone-side prompt so you never act on a
stale instruction a later message already replaced.

There are **two transports that speak the same mailbox contract**: the primary one
(`pyicloud_bridge.py`) talks to Apple's modern CloudKit Reminders store — the one
the iPhone and Claude app really use — through Apple's private web API. The fallback
(`reminder_bridge.py` plus a self-hosted Radicale server) talks standard CalDAV, for
when the private API is unavailable. Both drop inbound requests into the same
`to-manager.md` and drain replies from the same `to-phone.md`, so everything
downstream of the transport is identical.

---

## `pyicloud_bridge.py` — the primary transport

This is the engine. It talks to the CloudKit Reminders store via the `pyicloud`
private-API library, reads new reminders, bridges them into the file mailbox, and
sends replies back. It is config-driven, so one copy of this script serves many
projects (see the routing section).

**What one poll cycle does** lives in `poll_inbox()`. For each new incomplete
reminder it does three things *in a deliberate order*:

1. record the reminder's id in the per-project **seen-file** — first, so a crash
   after this point can never re-emit the same item;
2. append **one line** to the mailbox in the grammar `- [HH:MM] (owner-phone) <text>`;
3. mark the source reminder **complete** — a second, independent idempotency guard,
   mirroring the macOS `reminder-watch` pattern this generalizes.

The comment in the code states the reasoning plainly:

```python
# 1) record id first — a crash after this can't re-emit
_append_seen(seen_path, rid)
...
# 3) mark complete (second idempotency guard, mirrors reminder-watch)
```

So dedupe is belt-and-suspenders: the seen-file is the primary guard, completion is
the backup. Even if marking-complete fails (it's wrapped in a try/except that only
warns), the item won't be re-bridged because its id is already in the seen-file.

**Reading the inbox — the subtle bit.** `_incomplete()` calls
`r.list_reminders(list_obj.id)` and then filters `if not rem.completed`
**client-side**:

```python
data = dict(r.list_reminders(list_obj.id))
return [rem for rem in data.get("reminders", []) if not rem.completed]
```

The docstring notes `list_reminders()` already returns only incomplete items, but it
filters defensively anyway in case that changes. This "read all, filter completed
ourselves" habit is a recurring theme — the CalDAV transport does the same thing for
a harder reason (iCloud 500-errors on a server-side "exclude completed" filter).

**Priming the connection.** `connect()` reads the Apple ID and password from the
creds file, constructs `PyiCloudService(...)` pointed at the cached-cookie directory,
and then does one easy-to-miss required step:

```python
list(r.lists())  # REQUIRED: primes the service before list_reminders
```

Without that call the Reminders service 400-errors on the first real query. If the
cached session has expired, `connect()` detects `api.requires_2fa` and raises a
`BridgeError` telling the owner to re-run `pyicloud_login.py` — it never tries to do
2FA at runtime.

**Sending a reply — `send_reply()`.** A reply becomes a **new reminder** in the
output list with `priority=1` (which means "needs input"). Two subtleties:

- Every reply is **timestamped**: `stamp = datetime.now().strftime("[%H:%M] ")` is
  prepended before writing. This is the async-safety timestamp — it lets both you and
  the voice assistant judge how old a message is.
- After writing the reminder it calls `_notify_push()` to fire a **real phone
  banner** via ntfy.sh, because — as the code comments — an iOS Reminder with only a
  due-date fires no notification on its own.

**The ntfy banner truncation.** `_notify_push()` has a specific, well-commented
trick. iOS notification banners clip at roughly 150 characters and clip *mid-word*,
which looks like a silent cut-off. So the body is trimmed on a **word boundary** with
an ellipsis:

```python
if len(body) > 150:
    body = body[:149].rsplit(" ", 1)[0].rstrip() + "…"
```

The full reply text still lives in the output-list reminder; only the banner is
shortened. The whole push is best-effort — the entire function is wrapped so it
"never breaks the reply on it" if ntfy is unreachable.

**Draining replies — `drain_replies()`.** This is the *fast path* for the manager.
Instead of paying a network round-trip for every short reply, the manager can just
append a line to `to-phone.md` locally; the warm loop drains that file, sending each
new line as an output-list reminder. Dedupe here is **by line index**, so the file is
append-only and identical reply texts aren't collapsed together.

**The four inspection/test modes** are worth naming because the owner may ask for
them:

- `--show-config` prints the fully resolved settings and exits.
- `--dry-run` (`dry_run()`) touches **no network**: it feeds a fake reminder through
  the formatter into a temp mailbox and shows the exact line that *would* be written.
- `--once` does a single poll with an **exit-code contract**: exit 1 = nothing new,
  exit 0 = new item(s) bridged.
- `--selftest` (`selftest()`) is a **live** end-to-end proof: it creates a temp
  reminder in the real inbox list, polls it into a *temp* mailbox and *temp*
  seen-file (never the real ones), asserts the mailbox line was written and the
  source reminder got marked complete, then cleans up by deleting the temp reminder.

**The loop** (bottom of `main()`) is deliberately crash-resistant: it reconnects
each cycle, catches `BridgeError` and generic exceptions separately, prints a
"will retry" message, and sleeps — so a transient auth blip or network fault doesn't
kill the poller.

**Gotcha to remember:** pyicloud in this build can read and write lists but **cannot
create** them. `_require_list()` raises a clear error saying the list must be created
once by hand on the iPhone. The list names are an exact-string contract.

---

## `config.py` — one install, many projects

This module is why a single checkout can serve many projects. It lifts every
per-project setting — the two list names, the mailbox directory, the credential and
cookie locations, the ntfy topic file, the poll cadence — out of the code and into a
small JSON file.

**Resolution order** (first that exists wins), from `_find_config_path()`:

1. an explicit path passed to `load_config(path=...)` or `--config PATH`;
2. the `$VOICE_BRIDGE_CONFIG` environment variable;
3. `./.claude/voice-bridge.json` relative to the current working directory;
4. nothing found → pure built-in defaults.

Every field has a default (in the `DEFAULTS` dict), so a config can override only
what it needs — even an empty `{}` is valid. The `~` in any path field is expanded.
And a firm rule: **no secrets live in the config or the repo** — only the *locations*
of the credential, cookie, and topic files under `~/.auth`; the secrets stay in those
files.

**The frozen `Config` dataclass** holds the fully-resolved, absolute-path settings.
`load_config()` merges `{**DEFAULTS, **data}`, expands the paths, and builds it.
`as_display()` / `print_resolved()` give the human-readable dump that `--show-config`
prints.

**The one clever line — namespaced seen-files.** This is what lets multiple projects
share one mailbox directory without their dedupe state colliding:

```python
slug = _slug(merged["name"])
state_dir = mailbox_dir / "state"
...
seen_file=state_dir / f"{slug}-seen.txt",
reply_seen_file=state_dir / f"{slug}-reply-seen.txt",
```

`_slug()` lowercases the project `name` and turns any run of non-alphanumerics into a
single dash. So a project named "example-project" gets
`state/example-project-seen.txt`, and a different project gets its own file. The
seen-files are *derived*, never set directly in the JSON. (Note the honest limit here,
covered in the routing section: it's the *seen-files* that are namespaced, not the
mailbox files themselves.)

You can also run `python config.py [PATH]` directly to print exactly what a given
config resolves to — the quickest way to sanity-check a setup.

---

## `deliver_content.sh` — the gist + ntfy + voice content-delivery pattern

This shell script solves a specific problem: how do you get **long, readable
content** — a design, a doc, a walkthrough — to someone who is interacting by voice?
Two facts collide. A Reminder can't fire a real push notification on its own, and the
voice assistant **can't open a private web link**. So the script does all three
delivery modes at once, every time:

1. **Publishes the Markdown as an unlisted GitHub gist** — a link that renders nicely
   on both phone and desktop. It parses the URL out of `gh gist create` output and
   fails loudly if that comes back empty.
2. **Pushes a tappable ntfy notification** whose tap opens the gist. Note the same
   banner-truncation care as the Python side — it trims to ~130 chars on a space
   boundary with `cut` + `sed`:
   ```bash
   if [ "${#BODY}" -gt 130 ]; then BODY="$(printf '%s' "$BODY" | cut -c1-129 | sed 's/ [^ ]*$//')…"; fi
   ```
3. **Drops the full text plus the link into the output list** via
   `pyicloud_bridge.send_reply(..., notify=False)`. The `notify=False` is deliberate
   — the ntfy beep already fired in step 2, so this avoids a duplicate. The full text
   is inline *because the voice assistant has no web access to a private gist* — it
   can only read aloud what's actually in the reminder. The link is there for the
   owner's own eyes.

It's config-driven: it resolves the ntfy topic file by calling `config.load_config()`
inline in a small Python heredoc, and passes any `--config` argument straight through
to the Python helpers. Usage:
`deliver_content.sh [--config PATH] <markdown-file> "<one-line summary>"`.

---

## `docs/BRIDGE-INSTRUCTIONS.md` — the two prompt templates

This file isn't code — it's the two **prompts** that make the human side of the
bridge work. It's arguably as load-bearing as any script, because the behavior of the
phone assistant is entirely a matter of prompt discipline.

**The phone-side prompt** (saved in the Claude app as a Project instruction or pasted
at the start of a voice chat) turns the phone Claude into an *invisible interpreter*.
Its rules, distilled:

- The **only** tools it may use are the two Reminders lists — never Calendar, Notes,
  Mail. Anything about "my plans / my project / my tasks / what's next / the roadmap"
  refers to work Claude Code is doing on the PC, so it must be *forwarded*, never
  answered from another app or a guess.
- **Check the reply list on every single turn** — its #1 duty, its first action every
  time you speak, without being asked. Read new items aloud as if Claude Code is
  talking directly, then mark them done so they're never read twice.
- Honor the **async rule**: replies can arrive a turn late, each is timestamped, and
  when multiple new items exist the newest **supersedes** older ones on the same topic
  — read the newest, mark all done, don't act on a replaced instruction.
- **Keep the wiring invisible** — never say "I added a reminder" or "let me check your
  list."
- **Forward automatically** — never require a trigger phrase; err toward forwarding.
- The **one thing it stops for**: clarifying a possibly-misheard technical term, file
  name, or proper noun (voice transcription mishears "dot claude" as "dot cloud").
  Confirm the exact wording *before* forwarding — a misheard term sends wrong work.

There's an honest footnote: in Voice Mode the app only acts on *its* turn, so it
can't watch the list while you're silent — the async nature isn't a bug to fix, it's
a property to design around.

**The PC-side prompt** is for a fresh Claude Code manager session. It tells the
manager to start the poller, `Monitor` the `to-manager.md` mailbox with `tail -f`,
treat any `(owner-phone)` line as a phone request, stop and clarify misheard terms,
and — crucially — make **every reply complete and self-contained** so the voice
assistant never has to guess or fill gaps. It documents the fast path (a local
`printf >> to-phone.md` append the loop drains in ~10s) versus the direct path
(`--reply` for longer/multi-line messages that a line-based append would split), and
how to use `deliver_content.sh` for anything long.

---

## `docs/OWNER-SETUP.md` — the ~10-minute human setup

The step-by-step the owner follows once, for the primary (pyicloud) path:

1. **Create the two Reminders lists by hand on the phone** — named *exactly* as the
   config's `inbox_list` and `output_list`. The exact strings are the contract, and
   pyicloud can't create lists, so this is a manual one-time step. Optionally set the
   iOS default Reminders list to "To Claude" so the Claude app's adds land in the
   inbox.
2. **Put credentials under `~/.auth`** — `icloud.env` with the **main** Apple ID
   password (the private-API path needs the real password, not an app-specific one),
   plus `ntfy-topic.txt` with a private topic slug, and install the ntfy app on the
   phone subscribed to that topic.
3. **Seed the trusted session** with `pyicloud_login.py` — a one-time interactive 2FA
   (details below), good for ~60 days.
4. **Point a project at the bridge** by copying the example config into the project's
   `.claude/` and verifying with `--show-config`.
5. **Run the bridge** from the project's directory with `--interval 60`.

It ends by pointing at the CalDAV alternative if the private API is ever unavailable,
and recommends running `probe.py` first to see whether plain iCloud CalDAV can even
see your lists (on a modern account it usually can't — which is the whole reason
Radicale exists).

---

## The CalDAV fallback — `_caldav.py`, `probe.py`, `reminder_bridge.py`, `radicale/`

These four make up the **alternative transport**, used only when the CloudKit
private-API path is unavailable. They speak the same mailbox contract as the primary
bridge; only the bus underneath changes.

**`_caldav.py`** is the shared plumbing — credentials, connection, and list discovery
— so `probe.py` and `reminder_bridge.py` stay small and share one code path. Notable
points: it reads `ICLOUD_APP_PASSWORD` (an **app-specific** password on iCloud, since
iCloud CalDAV has no OAuth — contrast the primary path, which needs the *main*
password), it supports an `ICLOUD_CALDAV_URL` override so the exact same code can
point at a self-hosted Radicale server instead of iCloud, and `Creds.masked()` is the
only representation ever meant for logs — the password is never printed. `todo_lists()`
enumerates only VTODO-capable collections (a missing component set means "supports
all" per the CalDAV RFC, so it's kept).

**`probe.py`** is a read-only **GO/NO-GO feasibility check** — it never creates,
completes, or deletes anything. It answers three questions in order: can we
authenticate? do we see any VTODO-capable lists at all? is the configured inbox list
visible and readable? Then it prints one verdict — GREEN (viable, run the bridge) or
a specific RED. The most important RED is `CLOUDKIT-INVISIBLE`: auth works and other
lists are visible, but the inbox list you made on a modern iPhone is CloudKit-backed
and simply invisible over CalDAV — which is the signal to switch to Radicale. Notice
it uses the same **read-all-then-filter-completed-client-side** trick, and the
comment says why: "iCloud 500s on the server-side 'exclude completed' filter."

**`reminder_bridge.py`** is the CalDAV twin of `pyicloud_bridge.py` — same
`poll_inbox` / `send_reply` / `drain_replies` shape, same timestamped replies, same
seen-file-first dedupe ordering. The differences are mechanical: it dedupes by VTODO
**UID** instead of reminder id, and it marks complete by editing the iCalendar
component directly (`status = COMPLETED`, `percent-complete = 100`, add a `completed`
timestamp, then `save()`) — an object-type-agnostic, iCloud-safe way to PUT the
change. It has no ntfy push and no `--selftest`; otherwise the contract is identical.

**`radicale/`** is the self-hosted CalDAV server that makes the fallback actually
robust. The key insight in its `OWNER-SETUP.md`: iCloud CalDAV is effectively dead for
phone-to-PC on a modern account because Reminders live in a newer CloudKit store that
CalDAV can't see. The fix is to skip iCloud entirely and run a tiny Radicale server
that *both* the phone (added as a CalDAV account) and the PC bridge read and write —
same lists, same bytes, no CloudKit. The pieces:

- `run_radicale.py` launches Radicale bound to `0.0.0.0:5232` (all interfaces,
  including the Tailscale adapter);
- `config` is the Radicale config — htpasswd auth (bcrypt), owner-only rights, on-disk
  storage, plain HTTP because the only reachable path is meant to be the encrypted
  Tailscale tailnet;
- `make_user.py` writes the single user's **bcrypt** htpasswd entry, taking the
  password from `$RADICALE_PASSWORD` or an interactive prompt — never hardcoded, never
  printed, git-ignored;
- `init_lists.py` creates the two VTODO lists from the PC side (idempotent), so the
  whole loop can be proven with no phone involved. It refuses to run against an
  `icloud.com` URL as a safety guard.

The reachability story matters: Tailscale MagicDNS is the recommended way for the
phone to reach the PC server from anywhere, with LAN-IP and public-tunnel as
fallbacks. The one thing that genuinely needs a live phone test: whether the Claude
*app* (as opposed to the native Reminders app) can write to a non-iCloud CalDAV list
— Apple has never documented it. If it can't, you dictate into the native Reminders
app instead, and the bridge behaves identically.

---

## THE MULTI-PROJECT ROUTING MECHANISM

This is the part worth being precise and honest about, because it's easy to overstate.

**How a project plugs in.** A project declares a single JSON file at
`.claude/voice-bridge.json`. `config.py` resolves it in this order: an explicit
`--config PATH`, then `$VOICE_BRIDGE_CONFIG`, then `./.claude/voice-bridge.json` in
the current working directory, then built-in defaults. So the normal flow is: you
`cd` into the project, run the poller, and it auto-finds that project's config in the
cwd's `.claude/`. The config sets the project `name`, which two Reminders lists it
talks to (`inbox_list` / `output_list`), the tag written into each mailbox line
(`from_name`), the mailbox directory and file names, and the credential locations.

**How multiple projects avoid colliding — the part that genuinely works.** Every
project's **dedupe state is namespaced** by a slug derived from its `name`. In
`config.py`:

```python
slug = _slug(merged["name"])
seen_file = state_dir / f"{slug}-seen.txt"
reply_seen_file = state_dir / f"{slug}-reply-seen.txt"
```

So even if two projects share `~/.claude/message-protocol`, project A's record of
"which reminders I've already bridged" lives in `state/project-a-seen.txt` and
project B's in `state/project-b-seen.txt` — they never overwrite each other's memory.
And because different projects can set different `inbox_list` / `output_list` names
and a different `from_name` tag, they can genuinely operate over **different Reminders
lists** with differently-labeled mailbox lines.

**The honest current state — is there an automatic router? No.** There is **no
central dispatcher** that reads an incoming voice request, figures out which project
it's about, and routes it to the right poller. What exists is simpler and worth
stating plainly:

- **Each project runs its own poller process**, launched from that project's
  directory (`uv run pyicloud_bridge.py --interval 60`), bound to that project's
  config. "Routing" is really just *which lists a given poller watches and which
  mailbox files it writes* — it is configuration and convention, not code that
  decides.
- There is no logic anywhere that inspects a reminder's content and dispatches it.
  A reminder reaches a project **only because that project's poller is the one
  watching that list.** Separation between projects is achieved by giving each one
  **distinct list names** (and/or distinct mailbox files), not by any smart routing.

**A real sharp edge to flag.** The seen-*files* are namespaced by slug, but the
**mailbox files are not** — `to_manager` and `to_phone` default to the literal
`to-manager.md` / `to-phone.md` for *every* project. So if you ran two projects with
the **default list names and the default mailbox files**, both pollers would read the
same "To Claude" list and both would append into the same `to-manager.md` — the
namespaced seen-files prevent their *dedupe bookkeeping* from colliding, but not the
*content*. To truly run two projects side by side you must give each one **different
`inbox_list`/`output_list` names** (so they read different phone lists) and/or
**different `to_manager`/`to_phone` file names** (so their mailboxes are separate).
The machinery to do this cleanly is all present in the config; what's absent is any
automatic router that would do it for you or warn you if you didn't.

So: **multi-project support is real at the configuration layer** (one install, per-
project JSON, namespaced dedupe state, per-project lists/tags/mailbox files) — and
**the automatic content-router is aspirational / does not exist.** Today the model is
"one poller per project, pointed at its own lists," and it's on the operator to keep
those lists distinct.

---

## Honest limitations and rough edges

These are the real ones, stated so the owner can plan around them rather than be
surprised.

- **2FA session expiry (~60 days).** The primary transport runs on a cached trusted
  pyicloud session. It lasts roughly 60 days (or until the Apple ID password changes),
  then the bridge reports "session needs 2FA" and the poller can't authenticate until
  someone re-runs `pyicloud_login.py` and feeds it a fresh 6-digit code. There's no
  auto-renewal — you watch for that error and re-trust. Set a reminder before day 60.
- **pyicloud is a private, unofficial API.** It talks to Apple's internal web
  endpoints, which Apple can change at any time with no notice. That's precisely why
  the CalDAV/Radicale fallback exists and is documented — it's the escape hatch if
  Apple breaks the private path.
- **The poller isn't a Windows service yet.** It's just a foreground process you leave
  running (`uv run pyicloud_bridge.py`); kill it to stop. There's no packaged
  installer and no Task Scheduler / `nssm` auto-start at logon yet — the README and
  the ops notes both call this out as a known gap. If the machine reboots, someone has
  to restart the poller by hand.
- **Reminders can't push on their own.** An iOS Reminder with only a due-date fires no
  banner, which is why the ntfy side-channel exists at all. If the ntfy topic file is
  missing or ntfy.sh is unreachable, replies still land in the list — you just won't
  get a beep. The push is deliberately best-effort and never blocks a reply.
- **Banner truncation is a real constraint, not a bug.** iOS clips notification
  banners at ~150 chars mid-word; the code trims on a word boundary with an ellipsis
  so nothing looks silently cut, and keeps the full text in the reminder itself. Worth
  knowing when a banner looks abbreviated — the complete message is in the list.
- **Lists must be created by hand.** Neither pyicloud (this build) nor the Claude app
  can create a Reminders list — only items in an existing one. The two contract lists
  are a one-time manual setup on the phone, and their names are an exact-string
  contract; a typo means the bridge can't find the list.
- **The CalDAV fallback has one untested assumption.** Whether the Claude *app*
  (versus the native Reminders app) can write to a non-iCloud CalDAV list is
  undocumented by Apple; if it can't, you fall back to dictating into the native
  Reminders app. The bridge itself works either way.
</content>
</invoke>
