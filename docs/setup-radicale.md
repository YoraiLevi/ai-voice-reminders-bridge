# Set up the Radicale backend (self-hosted)

Radicale is a small CalDAV server that runs on your own machine. Your dictations never leave
your devices — they go phone → your machine, over a private tunnel, and nowhere else.

**Before you start this guide**, finish [Install](install.md) — every command here is typed
exactly as printed, and `vb` has to exist for that to be true.

You will also need **[Tailscale](https://tailscale.com/)**, installed and signed in to the
same tailnet on *both* this machine and the phone. Get that done first; step 2 assumes the
`tailscale` command exists and is logged in.

- [1. Start the server](#1-start-the-server)
- [2. Put HTTPS in front of it](#2-put-https-in-front-of-it)
- [3. Add the account on the phone](#3-add-the-account-on-the-phone)
- [4. Run the guided setup](#4-run-the-guided-setup)
- [5. Check it worked](#5-check-it-worked)
- [Where the password lives](#where-the-password-lives)

---

## 1. Start the server

```
vb radicale-server init
vb radicale-server start --background
vb radicale-server status
```

`init` asks you to **type a password** for the CalDAV account, hidden as you type. The
account name is `vox` unless you pass `--user`. **Write both down** — your phone needs them
in step 3, and see [Where the password lives](#where-the-password-lives) for what happens if
you lose it.

**Proof:** `status` prints a small table, and the line to read is **`reachable`**, which must
say `True`. It exits 0 when the server answers and 1 when it does not, so scripts should
gate on the exit code rather than on the text.

Two things worth knowing now:

- **`--background` detaches a process; it does not install a service.** A reboot kills the
  server and nothing restarts it. If the bridge cannot reach it tomorrow, run
  `vb radicale-server start --background` again.
- **`init` refuses to overwrite existing credentials.** `--force` rotates the password, which
  means the phone's CalDAV account stops connecting until you update it there too.

The server binds to **`127.0.0.1`** on purpose: nothing on your local network can reach it.
The next step is what lets your phone in, without opening the LAN at all.

## 2. Put HTTPS in front of it

**An iPhone refuses a CalDAV account over plain HTTP.** This is a fact from the device, not a
preference — a plain-HTTP account simply will not save, however correct everything else is.
So TLS goes in front, terminated by Tailscale:

```
tailscale serve --bg 5232
tailscale serve status
```

(`5232` is the port the server listens on, unless you changed it with `init --port`.)

**Proof:** `tailscale serve status` prints the HTTPS address your phone will use — something
like `https://your-node.tail1234.ts.net`. Write it down; step 3 needs it.

This is **tailnet-only**. It is not `tailscale funnel`, so nothing is published to the
internet.

If that fails, two causes cover almost everything:

- **The syntax changed across Tailscale releases.** `tailscale serve --bg <port>` is the
  current form; older releases wanted `tailscale serve --bg https:443 http://127.0.0.1:5232`.
  Run `tailscale serve --help` on the machine you are actually on rather than guessing.
- **Your tailnet needs HTTPS certificates enabled.** Check with `tailscale status --json` —
  `CertDomains` must be non-empty. If it is empty, turn HTTPS on in the Tailscale admin
  console; that is a web step nobody can do from the command line.

## 3. Add the account on the phone

The phone must be **on the tailnet** — Tailscale app installed and connected — or the
`.ts.net` name will not resolve.

On the iPhone: **Settings → Calendar → Accounts → Add Account → Other → Add CalDAV Account**

| field | value |
|---|---|
| **Server** | the node name from `tailscale serve status`, e.g. `your-node.tail1234.ts.net`. **No port**, and no `https://` — 443 is the default |
| **User Name** | the account `init` created — `vox` unless you changed it |
| **Password** | the password you typed during `init` |
| **Description** | anything you like |

**Leave "Use SSL" ON.** It is the default, and there is nothing to change under Advanced
Settings — that is exactly what step 2 bought you.

After it saves, make sure **Reminders** is enabled for the account.

**Proof:** the account appears in Settings without an error. The two lists are not there yet
— setup creates them next.

**If the account will not save**, the phone cannot reach the machine over the tunnel. Check
that Tailscale on the phone is connected, that `tailscale serve status` on the machine still
shows the proxy, and that the Server field is the bare `.ts.net` name with no port.

## 4. Run the guided setup

```
vb setup
```

In order, it will:

1. **Ask three settings** — the backend (answer `radicale`), this spoke's name, and your
   mailbox directory.
2. **Check the account exists and the server answers.** Either failure stops setup, prints
   the `radicale-server` command or commands to run, and asks you to re-run `vb setup`. Both
   stops exit 2 — you have something to go and do — so a scripted install can tell them from
   success.
3. **Offer to create the two lists for you.** Say yes. They sync to the phone through the
   CalDAV account you just added — no further phone step. (Say no and you create them
   yourself in a CalDAV client, exactly as the iCloud path does.)
4. **Select the two lists** — the ones it just created are chosen for you *by id*, and it
   says so rather than matching them by name.
5. **Offer push notifications.** Do [Notifications](notifications.md) **before you answer
   this**.
6. **A test message**, offered — it asks first and the step is skippable.
7. **The phone prompt**, printed. [Using the bridge](using.md) covers what to do with it.
8. **Start the bridge?** — Ctrl-C stops it and brings you back.

## 5. Check it worked

**Look at your phone.** Both lists — `Vox-Message-Outbox` and `Vox-Message-Inbox` — should
now appear in the Reminders app under the new account's section. That is the proof the CalDAV
account is genuinely working, and it is worth confirming before you go further.

Then, on the machine:

```
vb verify
```

One probe each way, then it cleans up after itself. Nothing is written to your config and it
asks you nothing, except if the backend stalls, when it offers to keep waiting.

**Watch the two contract legs and the closing line:**

```
  [ok  ] dictation reached the mailbox
  [ok  ] reply reached the outbox list
verified: a message makes the round trip.
```

Anything other than `verified:` on that last line names which leg failed. Two lines you may
also see are **not** failures: `[ -- ] notification not configured` just means you skipped
[notifications](notifications.md), and `[ ?  ] title not confirmed` means the check could not
reach a verdict — the message itself still arrived.

If you are scripting this rather than watching it, the exit codes are in the
[reference](reference.md#exit-codes).

**Now go to [Using the bridge](using.md).**

## Where the password lives

Two copies are stored and they are deliberately different:

- **The server's copy** is bcrypt-hashed in its user file, because a server only ever needs
  to *check* a password.
- **The client's copy** sits in plaintext in `radicale.env` (mode `0600` where the platform
  supports it), because the bridge has to *present* it to authenticate. There is no way
  around that: this is HTTP Basic auth.

So if you forget the password, `radicale.env` is where to read it back — and knowing a
plaintext credential lives there is part of deciding where this machine sits. Exact paths
are in the [reference](reference.md#where-your-credentials-and-state-live).

---

**Related:** [Notifications](notifications.md) · [Using the bridge](using.md) ·
[Command reference](reference.md) · [README](../README.md)
