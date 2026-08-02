# voice-bridge

**Add a voice spoke to a file-mailbox agent system.** Talk to the agents in your mailbox from
your phone, by voice — you dictate into a Reminders list, `voice-bridge` delivers it into the
mailbox, and replies come back as a reminder, with a push notification if you set one up.

It **extends** the file-mailbox protocol
([agent-to-agent-communication-file-mailbox]) — it does *not* set up the mailbox or the
agents; it plugs a phone spoke (`vox`) into one that already exists. It exchanges messages
with a **peer** in the mailbox (a manager by default; any spoke if you point `route_to`
elsewhere).

> **Async channel.** Replies can arrive a turn or more later; every message is timestamped
> `[HH:MM]`, and the newest on a topic supersedes older ones.

## Start here

Three short guides, in order. Follow one backend the whole way and you end with a working
bridge.

1. **[Install](docs/install.md)** — the right shell, `uv`, the code, and one command that
   proves it works. Do this first; everything else assumes it.
2. Then **one** of these, all the way through:
   - **[Set up iCloud](docs/setup-icloud.md)** — uses the Apple account you already have.
   - **[Set up Radicale](docs/setup-radicale.md)** — a small server on your own machine,
     reached over Tailscale. Nothing leaves your devices.
3. **[Using the bridge](docs/using.md)** — teach the phone, start the bridge, and watch a
   real dictation make the round trip.

Along the way: **[Notifications](docs/notifications.md)** (two minutes, do it when setup
asks) · **[Command reference](docs/reference.md)** · **[Architecture and
limits](docs/design.md)**

## Which backend?

|  | **iCloud** | **Radicale** (self-hosted) |
|---|---|---|
| what it is | your existing Apple account | a small CalDAV server on this machine |
| you need | an Apple ID and its password, plus 2FA on your phone | Tailscale on the machine *and* the phone |
| the two lists | **you create them on the phone** — Apple's API cannot | created for you |
| speed | fast | slower — the phone syncs over the tunnel |
| your dictations | pass through Apple | never leave your own machines |

You can switch later with `setup --transport <name>`. **Switching clears the list
selection** — there is one selection in the config, not one per backend — so you pick your
two lists again. Setup says so when it happens.

## Before you start

- **A running file-mailbox with a peer joined.** If you don't have one, set that up first —
  one paste, see [agent-to-agent-communication-file-mailbox]. `voice-bridge` attaches the
  `vox` spoke to it. It will create the bare mailbox files if you point it at an empty
  directory, but it never creates a *peer*.
- **An iPhone or iPad** with the Reminders app, **and an assistant app on it** that you can
  talk to and that can read and write your Reminders lists — the Claude app, or whatever you
  use. That app is what you actually dictate to; `voice-bridge` carries what it writes.
- **[`uv`](https://docs.astral.sh/uv/)**, and on Windows **PowerShell rather than Command
  Prompt**. Nothing else is installed and there is nothing to clone —
  [Install](docs/install.md) walks it, including how to tell which shell you are actually in.

## What it looks like working

You say something to your phone. Within a poll interval it lands in the mailbox, and
`vb tail -f` shows it as

```
[manager] - [14:30] (vox) look at the failing test in test_poller.py
```

your peer answers in its own time, and the answer arrives back as a reminder on your phone
plus a banner. [Using the bridge](docs/using.md) walks it end to end.

## Tell your peer (one line)

Paste at your manager/agent once: *"the `vox` spoke is async/turn-based — timestamp replies,
and newest supersedes older on a topic."* That is all it needs; `voice-bridge` never touches
it.

## Development

The test, lint and type-check commands are in the
[reference](docs/reference.md#development). Architecture, guarantees and known limits:
[`docs/design.md`](docs/design.md).

[agent-to-agent-communication-file-mailbox]: https://github.com/YoraiLevi/agent-to-agent-communication-file-mailbox
