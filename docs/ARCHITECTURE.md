# Vox voice bridge — full architecture (plain-spoken)

Written to be read aloud. It covers every moving piece, how they connect, how data flows end to
end, and exactly how the auto-heal poller works.

## The goal
Let the owner talk to Claude Code (his coding agent on the PC) by voice, through the Claude app on his
phone, as if he's talking straight to it. **Vox** is the phone-side voice assistant that acts as the
invisible interpreter between the owner and the PC.

## The core idea
Neither side talks to the other directly. Instead they pass notes through two shared to-do lists that
both can reach — a mailbox. The phone drops requests in **"To Claude"**; the PC drops replies in
**"From Claude"**. That's the whole contract.

## The moving pieces (apps + data stores)
- **Vox** — the voice assistant living in the Claude iOS app on the phone. It hears the owner, silently
  writes his requests into "To Claude", reads replies out of "From Claude", and speaks them back. To
  the owner it feels like one continuous conversation with Claude Code.
- **The two lists (the bus)** — "To Claude" (phone → PC) and "From Claude" (PC → phone).
- **Dual channel (for the MANAGER only)** — the manager's mailbox lists exist on TWO transports at once:
  - **FAST = iCloud Reminders** (Apple's native Reminders, reached through Apple's private web API via a
    library called *pyicloud*). This is the manager's PRIMARY day-to-day channel — it syncs fast, and it
    notifies via ntfy. This is where "To Claude" / "From Claude" live.
  - **BACKUP = Radicale** (a small self-hosted CalDAV server running on the PC, reached over Tailscale
    HTTPS). The manager's backup lists here are renamed **"To Backup" / "From Backup"**. Used ONLY as a
    fallback if the iCloud fast lane fails, AND for native alarms (see below). It does NOT fire on every
    message — the two channels are intentionally separate, not a parallel dual-send.
- **The pollers** (small Python loops on the PC) — they watch the lists. Every ~10 seconds the iCloud
  poller reads "To Claude", writes each NEW message into a plain text file (the manager's mailbox), then
  marks that reminder done. The backup poller does the same for "To Backup".
- **The manager** — the Claude Code session on the PC. It watches the mailbox file; when a new line
  appears it does the work and replies by writing to "From Claude".
- **ntfy** — a free push-notification service, independent of Apple. On every reply the PC also pushes an
  INSTANT banner to the phone via ntfy — that's the fast channel's alert (arrives in seconds, no Apple
  dependency).
- **Native alarm** — the BACKUP (CalDAV) channel attaches a real alarm (a VALARM) to its reply, which
  iOS Reminders fires as a native banner+sound. Apple's iCloud API can't attach that kind of alarm — which
  is exactly why the backup channel is kept alive: it's the reliable native-alarm path.
- **Vox Instructions list** — a third list holding Vox's own operating rules (GLOBAL RULES), a ROUTING
  TABLE, and the one BOOTSTRAP line. Vox reads it at the start of each chat to pick up its latest
  instructions. Now on iCloud.
- **The routing table** — one line per project: `project = To-list / From-list`. It lets Vox send a
  request to the RIGHT project when several run side by side.
- **Workers (w1/w2/w3)** — separate Claude Code sessions, each with its OWN To/From lists (e.g. "To W3")
  on the BACKUP channel, so they can self-join without ever touching the owner's Apple credentials.
- **Config files** — each project has a `voice-bridge.json` naming its lists, the pinned list ids, its
  mailbox folder, and where credentials live.

## What lives on Radicale (two DISTINCT things — read this precisely)
Radicale is the ONE self-hosted CalDAV system, and it hosts **two different kinds of channels** — don't
collapse it to "just the manager's backup":
1. **The manager's fallback channel** — "To Backup" / "From Backup". A pure fallback for the manager, used
   only if the iCloud fast lane fails, plus the native-alarm path. The manager's *primary* is iCloud+ntfy.
2. **The standalone worker channels** — "To W3" / "From W3" (and any other worker). These live on Radicale
   **permanently, as the worker's ONLY channel.** Workers were never meant to be on iCloud and need no fast
   lane: keeping them on Radicale means only ONE iCloud poller exists (no Apple credentials in worker
   sessions, no multi-poller 2FA/503 auth-storm).

So the mental model is **NOT** "iCloud primary, Radicale backup." It is: **iCloud = the manager's fast
lane; Radicale = the single home for BOTH the manager's fallback AND every worker's permanent channel.**

## Data flow, end to end
1. The owner speaks. Vox silently writes the request into **"To Claude"** (iCloud).
2. The iCloud **poller** (PC) reads it, appends one line to the **mailbox file**, marks the reminder done.
3. The **manager** (Claude Code) is watching that file — it wakes, does the work.
4. The manager writes the reply into **"From Claude"** (iCloud) AND pushes an **ntfy** banner. For
   alarm-worthy replies it ALSO writes a copy to **"From Backup"** (CalDAV), which fires a **native alarm**.
5. Vox reads "From Claude", and speaks the reply to the owner. Loop closes.

## The auto-heal poller — exactly how it finds the right list
**The problem it solves:** when the owner deletes and recreates "To Claude" on his phone, the new list gets a
brand-new internal id, and the OLD one lingers as an invisible "ghost" — the phone hides it, but the API
still returns it. A poller pinned to the old id would then read the wrong, empty list and miss messages.

**The signal it uses:** the poller lists EVERY list the account has (ghosts included) and filters by the
NAME "To Claude". Among the same-named lists, it picks **the one that has UNREAD (incomplete) items**.
That works because the LIVE list — the one the phone is actually writing to — is where new messages land,
so it has unread items; the ghosts are empty. (It can't use timestamps: the list objects expose no
creation or modified time, so unread-count is the usable signal.)

**Resolution order:** (1) auto-heal — the same-named list with unread items; (2) the pinned id, if set;
(3) the first same-named list.

**Honest edge cases:**
- If NO same-named list currently has unread items (idle, or just after the poller drained them), it
  can't distinguish — but that's harmless: there's nothing to fetch then, and the NEXT real message lands
  as "unread" on the live list and gets caught on the following poll.
- If TWO same-named lists had unread items at the same moment (rare — the phone writes to just one), it
  picks the one with more.
- It applies to the INBOX ("To Claude"). The OUTPUT ("From Claude") uses the pinned id, because
  unread-count doesn't indicate liveness for a list the PC writes to (replies get read). From Claude
  moves rarely (the owner reads it, doesn't recreate it).

## Why this shape
- **Two channels** because each transport has one thing the other lacks: iCloud is fast but its API can't
  set a firing alarm; CalDAV is slower but can. Together: fast delivery (iCloud+ntfy) + reliable native
  alarm (backup).
- **Lists as the bus** because both the phone and the PC can reach a shared Reminders list, and it needs
  no always-on server between them beyond what already syncs.
- **Workers on the backup channel** so agents self-provision without the owner's Apple credentials.
