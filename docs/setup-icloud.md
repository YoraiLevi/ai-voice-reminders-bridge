# Set up the iCloud backend

Your dictations travel through your own Apple account, into the Reminders app you already
have. Nothing else to run and nothing to expose.

**Before you start this guide**, finish [Install](install.md) — every command here is typed
exactly as printed, and `vb` has to exist for that to be true.

- [1. Make the two lists on your phone](#1-make-the-two-lists-on-your-phone)
- [2. Run the guided setup](#2-run-the-guided-setup)
- [3. Check it worked](#3-check-it-worked)
- [When Apple asks for two-factor again](#when-apple-asks-for-two-factor-again)

---

## 1. Make the two lists on your phone

**Apple's API cannot create Reminders lists**, so this is a manual step and it comes first.
In the Reminders app, add two lists:

- **Vox-Message-Outbox** — *you* dictate here. It is **your** outgoing.
- **Vox-Message-Inbox** — answers arrive here. It is **your** incoming.

The names read from your seat, not the tool's. You may use different names: you pick the two
lists by hand in the next step, and the tool remembers *which lists you chose* rather than
what they are called.

**Proof:** both lists are visible in the Reminders app before you continue.

## 2. Run the guided setup

```
vb setup
```

It walks the whole thing and asks only what it cannot work out. In order:

1. **Three settings** — the backend (answer `icloud`), this spoke's name (`vox` is fine),
   and your mailbox directory. Press Enter to keep any value it shows.
2. **Your Apple credentials.** It asks before taking anything, and declining is fine — it
   names `vb icloud-login` for later and exits 0. This is where **two-factor** happens:
   Apple pushes a code to your devices and setup asks you for it.
   - It needs your **main Apple ID password**, not an app-specific one.
   - There is deliberately no `--password` flag: anything on a command line is readable by
     every other user on the machine.
3. **Your lists.** It shows what is in the account and asks which two carry messages. Pick
   your outbox and your inbox. It stores the *id* of each — it never matches by name, so a
   second list with the same title cannot silently steal your dictations.
4. **Push notifications.** Do [Notifications](notifications.md) **before you answer this** —
   it takes a minute and the next step is easier to believe if your phone buzzes.
5. **A test message**, offered — it asks first and the step is skippable.
6. **The phone prompt**, printed. [Using the bridge](using.md) covers what to do with it.
7. **Start the bridge?** — *"Start the bridge now? (Enter = yes, start it)"*. Yes takes over
   the terminal; Ctrl-C stops it and brings you back, and nothing is lost either way.

If it stops early it says what is missing and which command to re-run. **Re-running `vb
setup` is always safe** — it only fills gaps.

## 3. Check it worked

```
vb verify
```

One probe each way, then it cleans up after itself. Nothing is written to your config and it
asks you nothing, except if the account stalls, when it offers to keep waiting.

- **exit 0** — a message made the round trip.
- **exit 2** — it did not.
- **exit 1** — the check never finished. Run it again.

If something is off:

```
vb doctor            # survey every interface; --fix repairs the safe ones
vb lists             # every list in the account, with its id
```

**Now go to [Using the bridge](using.md)** — the phone prompt, the run loop, and what a real
dictation looks like end to end.

## When Apple asks for two-factor again

Sessions expire, and Apple does not document the lifetime. When a command asks for a code
again:

```
vb icloud-login
```

That re-establishes the trusted session and leaves everything else alone.

---

**Related:** [Notifications](notifications.md) · [Using the bridge](using.md) ·
[Command reference](reference.md) · [README](../README.md)
