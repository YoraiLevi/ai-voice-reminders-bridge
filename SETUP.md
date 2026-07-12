# SETUP — one file, one command: `claude @SETUP.md`

**You are the MANAGER session for the project in the CURRENT folder.** These instructions
are for **you, the Claude Code agent**, not the human. Follow them top to bottom.

This ONE file handles both transports and both first-launch and relaunch. The only
branches are **(a) which transport** (iCloud or Radicale) and **(b) STEP 1's detect**
(first launch vs relaunch). Keep it lean — do not run exploration you are not told to run.

> Read [`PROTOCOL.md`](PROTOCOL.md) once — it is the contract (async, newest-supersedes,
> the one reply path). The operating rules at the bottom of this file are its short form.

---

## STEP 1 — DETECT (always first)

- If **`./.claude/voice-bridge.json` exists** → **relaunch**. Read it, take `vb_path` and
  `mailbox_dir` from it (do NOT search the filesystem), and skip to **STEP 3 (START)**.
- If it does **NOT** exist → **first launch**. Go to **STEP 2**.

---

## STEP 2 — FIRST-TIME PROVISION (only when STEP 1 found no config)

1. **Locate the voice-bridge repo once.** Check `~/source/voice-bridge` first; otherwise a
   bounded search under `~/source` and `~` for a directory containing `bootstrap.py` +
   `pyicloud_bridge.py`. Stop at the first hit. Call it `<vb>`.
2. **Run the bootstrap** from THIS project's folder (your cwd). Pick the transport:

   **Radicale (recommended — self-provisioning, no phone step):**
   ```
   uv run <vb>/bootstrap.py --transport radicale
   ```
   In one step it writes `./.claude/voice-bridge.json`, connects to Radicale, and CREATES
   the two lists `To <Title>` / `From <Title>`. They sync to the phone via the one shared
   Radicale CalDAV account. Idempotent — a second run prints `ALREADY PROVISIONED`.

   **iCloud (fallback):**
   ```
   uv run <vb>/bootstrap.py
   ```
   It writes the config and prints `SETUP_DONE lists_needed=To <Title>|From <Title>`.
   iCloud FORBIDS creating lists over the API, so **tell the human the one manual step**:
   open the phone's Reminders app and create two lists named EXACTLY `To <Title>` and
   `From <Title>`. **Wait for the human to confirm** before continuing.

   > Prerequisite (one-time, per transport): the credentials + session under `~/.auth`.
   > Radicale: `radicale/OWNER-SETUP.md`. iCloud: `docs/OWNER-SETUP.md`.

---

## STEP 3 — START (every launch — first launch AND relaunch land here)

Read `vb_path` and `mailbox_dir` from `./.claude/voice-bridge.json`. Use the poller that
matches the transport (`creds_env` tells you: `radicale.env` → Radicale, `icloud.env` → iCloud).

1. **Start the poller in the BACKGROUND** (it must stay running the whole session; do NOT
   run it in the foreground, which blocks). `--config` is always passed explicitly, so it
   can never bind to the wrong project:
   ```
   uv run <vb_path>/reminder_bridge.py  --config ./.claude/voice-bridge.json   # Radicale
   uv run <vb_path>/pyicloud_bridge.py  --config ./.claude/voice-bridge.json   # iCloud
   ```
2. **Arm a Monitor** on this project's inbound mailbox, persistent, so a new line wakes you:
   ```
   Monitor(command: tail -f -n0 <mailbox_dir>/to-manager.md,
           description: "phone->PC bridge", persistent: true)
   ```
3. **LISTEN.** Each new inbound line is `- [HH:MM] (<from_name>) <text>` — a request from
   the phone. Do the work, then reply.

### Standard operating rules (the contract — see PROTOCOL.md)

- **Async / turn-based, not live.** Messages arrive when the phone next speaks; replies
  reach the phone a turn later. Everything is timestamped for this reason.
- **Newest supersedes.** If inbound lines stack up, the newest instruction wins.
- **Clarify misheard terms.** Inbound text is VOICE transcription and may contain misheard
  words (e.g. "dot claude" → "dot cloud"). When a technical term, file/config name, or
  proper noun looks wrong, STOP and ask through the bridge before acting.
- **Self-contained replies.** Make every reply COMPLETE — full detail and context — so the
  phone assistant never has to guess on a follow-up.
- **REPLY via the bridge so the ntfy banner fires.** Never write a raw CalDAV/Reminders
  todo. Two supported ways:
  - SHORT single line — append it; the running poller drains it within one interval:
    ```
    printf '%s\n' 'your message' >> <mailbox_dir>/to-phone.md
    ```
  - LONGER / multi-line — send directly (the append is line-based and would split it):
    ```
    uv run <vb_path>/<poller>.py --config ./.claude/voice-bridge.json --reply "your full message"
    ```
- **Auto-fetch missing content.** If a request refers to something you don't have, go get
  it (read the file, run the command) rather than replying "I don't have that."
- **Sending long CONTENT for review** (a design, a doc): write it to a Markdown file, then
  `<vb_path>/deliver_content.sh <file> "one-line summary"` — it publishes a gist, pushes a
  tappable link, and drops the summary+link into the output list.
- **iCloud only — if the poller prints "session needs 2FA":** run
  `uv run <vb_path>/pyicloud_login.py` once (the human supplies the 6-digit code), then continue.

---

**Remember: same file, same command (`claude @SETUP.md`), every launch.** Transport is
chosen once at STEP 2; STEP 1's detect is the only per-launch branch.
