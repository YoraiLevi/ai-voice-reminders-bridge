# Using the bridge

Setup built the thing. This is the thing working — teach the phone, start the bridge, say
something, watch it arrive.

Every command here is typed exactly as printed; `vb` comes from [Install](install.md).

- [1. Teach the phone](#1-teach-the-phone)
- [2. Give yourself a peer](#2-give-yourself-a-peer)
- [3. Start the bridge](#3-start-the-bridge)
- [4. Actually use it](#4-actually-use-it)
- [Stopping it](#stopping-it)
- [When a dictation does not arrive](#when-a-dictation-does-not-arrive)

---

## 1. Teach the phone

```
vb vox-prompt
```

This prints instructions rendered with **your** list names, your account and your spoke's
name. Paste the whole thing into the assistant app on your phone — the Claude app, or
whatever you talk to.

It tells the phone-side agent which list to dictate into, which to read, and how to address
them without guessing. That last part matters more than it looks: an account can hold two
lists with the same title, and a wrong guess sends your words somewhere nobody reads.

**Re-run it and paste again whenever you change lists or backends.** The text differs per
backend, and a stale prompt is exactly how a dictation ends up in a list nobody polls.

## 2. Give yourself a peer

**Nothing is processed until a peer joins.** Your dictations arrive in the mailbox and sit
there until an agent reads them — the bridge carries messages, it does not answer them.

```
vb peer-prompt
```

Paste what that prints at a coding agent and it becomes your peer. It renders with your
actual mailbox paths, so what it tells the agent is what this install really uses.

If the bridge had to *create* your mailbox — a fresh directory rather than one you already
had — it also drops the same text in **`PEER-PROMPT.md`** beside the mailbox files, so it is
still there tomorrow after the startup output has scrolled away.

## 3. Start the bridge

```
vb run
```

Every start prints an orientation block before going quiet, and announces the `vox` spoke
into the mailbox: your mailbox path, which file your peer reads, which file your peer writes,
whether a peer has ever written there, and finally

```
bridging: polling Vox-Message-Outbox every 10s
Ctrl-C to stop.
```

The interval there is your `poll_interval` setting, not a fixed number.

**Then it stays silent, and that is healthy.** It does not narrate cycles where nothing
happened — a log that scrolls while nothing is happening hides the moments when something
is. Errors and warnings still print.

Two things about `run` worth knowing:

- **On a machine with no config at all it takes you through guided setup first**, then
  bridges. Anything *else* missing it names and exits 2 — unselected lists, or credentials it
  cannot find — rather than trying to fix it.
- **`vb run --once`** does a single cycle and exits. **Exit 1 there means "ran fine, nothing
  new"**, which is a normal result, not a failure.

## 4. Actually use it

Worth doing once deliberately, so you know what normal looks like. With the bridge running in
one terminal:

1. **Say something to the assistant app on your phone** — ask it to put a message in your
   dictation list. It writes a reminder into **Vox-Message-Outbox**.
2. **Wait one poll interval** — the number in the `bridging:` line you just saw. The bridge
   reads the reminder, appends it to your peer's inbox file, and completes the reminder on the
   phone, so *the item disappearing from the list is the receipt* that it was picked up.
3. **Watch it land.** In another terminal, run `vb tail -f`. Your words appear as one line:

       [manager] - [14:30] (vox) <what you said>

   The `[manager]` at the front is `tail`'s label for *which mailbox file* the line came from
   — your peer's inbox, which is where your dictations go.
4. **Your peer replies** into the mailbox, in its own time. This is an async channel: the
   answer may be a turn or more later, and every message is timestamped so the newest on a
   topic supersedes older ones.
5. **The reply reaches your phone** as a new reminder in **Vox-Message-Inbox**, plus a banner
   if you set up [notifications](notifications.md).

That is the whole loop.

## Stopping it

**Ctrl-C.** That is the intended way to stop a foreground bridge; it announces its exit to
the peer and cleans up after itself.

If replies were still waiting to be sent when you stopped, you will see:

```
kept 2 undelivered replies in <path>/to-vox.md - the next run delivers them.
```

**That is the bridge protecting your messages, not a failed cleanup.** It refuses to delete
anything it has not sent. Start it again and those replies go out, exactly once.

## When a dictation does not arrive

Work down this list; each step tells you something the one above it cannot.

```
vb peek --box inbox      # inbox = your dictations; outbox = replies to you
```

- **Empty**, and your words are not in `vb tail -f` either → the phone never wrote it. Check
  the assistant app actually created a reminder, in the list the prompt named.
- **Your dictation is sitting there** → the bridge is not reading the list you think it is.
  Re-run `vb setup` and re-pick, or `vb lists` to see every list with its id.

Nothing arriving *back*?

- **`vb status`** — a glance at the spoke's health, including whether anything is reading
  the mailbox.
- **No peer has ever written** → that is [step 2](#2-give-yourself-a-peer). Your dictations
  are safe in the mailbox file; nothing is lost, nothing is answering.
- **`vb doctor`** — surveys every interface and names what is wrong; `--fix` repairs the
  safe items.

---

**Related:** [Command reference](reference.md) · [Notifications](notifications.md) ·
[Architecture and limits](design.md) · [README](../README.md)
