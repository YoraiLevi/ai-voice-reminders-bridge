# voice-bridge via self-hosted Radicale — owner setup (ALT transport)

**Why this exists.** The iCloud CalDAV path is dead for phone↔PC on a modern
account: Apple keeps Reminders in a newer **CloudKit** store, and CalDAV sees a
different, older store — a list your phone makes is invisible over CalDAV, and vice
versa. `probe.py` confirms this per-account. The robust alt is to skip iCloud
entirely: run a tiny **Radicale** CalDAV server on the PC that BOTH your phone (as a
Reminders account) and the PC bridge read/write. Same lists, same bytes, no
CloudKit. The primary transport is `pyicloud_bridge.py`; use this only if that is
unavailable.

```
 phone Reminders                 PC
 ┌────────────────┐   CalDAV     ┌──────────────────────────────┐
 │ CalDAV account │ ◀─over the──▶│  Radicale  :5232  (run_...)   │
 │ To Vox      │   network    │      ▲                        │
 │ From Vox    │              │      │ same lists             │
 └────────────────┘              │  reminder_bridge.py ──▶ mailbox
                                 └──────────────────────────────┘
```

Everything downstream of Radicale is unchanged — `probe.py` and
`reminder_bridge.py` just point at Radicale instead of iCloud via one env var.

List names come from your `voice-bridge.json` (`inbox_list` / `output_list`); the
examples below use the defaults `To Vox` / `From Vox`.

---

## Reachability — how the phone reaches Radicale (READ FIRST)

The phone must reach the server both at home and off the LAN. Options, best first:

- **Tailscale (recommended):** if the PC and phone are on the same tailnet, address
  the server by the PC's MagicDNS name over port 5232 — works from anywhere on any
  network, because the tailnet is an encrypted WireGuard overlay:
  ```
  http://<pc-name>.<tailnet>.ts.net:5232/
  ```
- **LAN-only fallback:** the PC's LAN IP, e.g. `http://192.168.x.x:5232/` — works
  only on the same Wi-Fi.
- **Last resort:** a public tunnel (`cloudflared tunnel` / `ngrok http 5232`) giving
  an HTTPS URL. This exposes the server to the internet, so keep the htpasswd auth on
  and ideally add TLS.

Because the tailnet link is already encrypted, Radicale runs plain HTTP on the wire
here. **Do not port-forward 5232 to the public internet** without TLS.

---

## One-time setup (on the PC)

All commands run from `radicale/`. `uv` handles deps.

### 1. Create the Radicale user (password prompted, never stored in git)

```
uv run make_user.py
```

Asks for a username (default `claude`) and a password (twice). Writes a **bcrypt
hash** to `_secrets/users` — git-ignored; the plaintext is never written or printed.
(For a scripted setup, set `RADICALE_USER` / `RADICALE_PASSWORD` in the env first.)

### 2. Start the server

```
uv run run_radicale.py
```

`uv` auto-installs Radicale + bcrypt the first time. It binds `0.0.0.0:5232` (all
interfaces, incl. the tailnet) and serves until Ctrl-C. Leave it running (or wrap it
in a Task Scheduler job / `nssm` service). Storage is on-disk under
`_data/collections/` (git-ignored).

### 3. Point the bridge at Radicale, create the two lists

Put the Radicale URL + creds where the bridge reads them — in your `creds_env`
file (`~/.auth/icloud.env` by default; the env names are reused, only the values
change):

```
ICLOUD_CALDAV_URL=http://127.0.0.1:5232/
ICLOUD_APPLE_ID=claude
ICLOUD_APP_PASSWORD=<the password from step 1>
```

Then, from `radicale/`:

```
uv run init_lists.py            # creates the inbox + output lists (idempotent)
```

### 4. Confirm GREEN, then run the poller

```
uv run probe.py                 # expect: VERDICT: GREEN
uv run reminder_bridge.py       # the poller — leave it running
```

---

## Phone setup (do once)

1. **Settings → Calendar → Accounts → Add Account → Other → Add CalDAV Account.**
   (Reminders shares the CalDAV account list with Calendar.)
2. Fill in:
   - **Server:** the reachable URL host from "Reachability" (e.g.
     `<pc-name>.<tailnet>.ts.net:5232`)
   - **User Name / Password:** the Radicale user from step 1
   - Tap **Next / Save.** If iOS warns about an unencrypted connection, allow it
     (the tailnet itself is encrypted).
3. Open **Reminders** — under the new account you'll see `To Vox` and
   `From Vox` (from `init_lists.py`), or create them there.
4. **Set the iOS default Reminders list to `To Vox`**
   (**Settings → Apps → Reminders → Default List**) so the Claude app's
   "add a reminder" lands in the inbox.

---

## The ONE thing to test live (needs your phone)

Whether the **phone Claude app** writes to a **Radicale-backed** default list. The
app targets the iOS *default* Reminders list; step 4 points that at `To Vox`, but
Apple has never documented whether the app's Reminders tool can target a *non-iCloud*
CalDAV list. **If it can't:** add items via the native **Reminders app** (type or
dictate into `To Vox`) — still phone-native, and the bridge behaves identically.
