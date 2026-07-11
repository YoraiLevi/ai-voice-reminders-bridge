# voice-bridge — owner setup (primary / pyicloud path)

Goal: talk to the Claude phone app and have it drop a task your PC "manager" Claude
Code session picks up, with replies coming back to your phone. This is the ~10-min
one-time human setup. Do the steps in order.

The channel is **asynchronous** — replies can arrive a turn or more later and are
timestamped. That's expected; see the README's "This channel is ASYNCHRONOUS" note.

---

## 1. Create the two Reminders lists ON THE PHONE (by hand)

In the **Reminders** app, create two lists named **exactly** as your config's
`inbox_list` and `output_list` (defaults):

- `To Claude`   (phone → PC)
- `From Claude` (PC → phone)

The exact strings are the contract. The Claude app / pyicloud **cannot create
lists** (only items in existing lists), so this must be done by hand once.

Optionally set the iOS default Reminders list to `To Claude`
(**Settings → Apps → Reminders → Default List**) so the Claude app's "add a
reminder" lands in the inbox.

## 2. Put the credentials under `~/.auth` (never in the repo)

Create `~/.auth/icloud.env` with your **MAIN** Apple ID credentials (pyicloud's
private-API path needs the real password, not an app-specific one):

```
ICLOUD_APPLE_ID=you@icloud.com
ICLOUD_PASSWORD=your-main-apple-id-password
```

(You can instead keep the password in `~/.auth/icloud-main.env` under the same key
— `pyicloud_login.py` reads either.) Nothing here is committed or printed; only a
masked `apple_id=y***@…` ever appears in logs.

Also create `~/.auth/ntfy-topic.txt` with one line — a private ntfy.sh topic string
(any hard-to-guess slug) — so the bridge can push real banners to your phone.
Install the **ntfy** app on the phone and subscribe to that same topic.

## 3. Seed the trusted session (one-time 2FA)

```
uv run pyicloud_login.py
```

Apple sends a 6-digit code to your trusted devices. When prompted, drop it into
`~/.auth/2fa_code.txt` (a manager session can write it for you once you read it
aloud). On success the session is TRUSTED and cached under
`~/.auth/pyicloud-cookies` for ~60 days; `pyicloud_login.py` then prints the
Reminders lists it can see — confirm `To Claude` and `From Claude` appear.

Re-run this step only when the bridge later reports "session needs 2FA" (~every
60 days, or after an Apple ID password change).

## 4. Point a project at the bridge

Copy the example config into the project you want to bridge:

```
cp examples/project-proposals.voice-bridge.json /path/to/project/.claude/voice-bridge.json
```

Edit `name` and the list names if needed. Verify the resolved settings:

```
uv run pyicloud_bridge.py --config /path/to/project/.claude/voice-bridge.json --show-config
```

## 5. Run the bridge

From the project's directory (so `./.claude/voice-bridge.json` is auto-found):

```
uv run /path/to/voice-bridge/pyicloud_bridge.py --interval 60
```

Leave it running. Anything you add to `To Claude` from the phone appears as a line
in `<mailbox_dir>/to-manager.md` within the interval, waking the manager session.
Replies the manager appends to `<mailbox_dir>/to-phone.md` become items in your
`From Claude` list (timestamped, priority = needs input), which you read on the
phone.

Give the manager session the **PC-side prompt** and the phone the **phone-side
prompt** from `BRIDGE-INSTRUCTIONS.md`.

## If the private API is unavailable

Use the CalDAV alt transport (`reminder_bridge.py` + a self-hosted Radicale
server) — same mailbox contract, one env var swap. See `../radicale/OWNER-SETUP.md`.
Run `uv run probe.py` first to see whether plain iCloud CalDAV can see your lists
(on a modern account it usually can't — that's why Radicale exists).
