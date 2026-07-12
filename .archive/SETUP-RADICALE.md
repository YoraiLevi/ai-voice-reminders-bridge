# SETUP (Radicale) — one file, one command: `claude @SETUP-RADICALE.md`

**You are the MANAGER session for the project in the CURRENT folder.** These instructions are for
**you, the Claude Code agent**, not the human. Follow them top to bottom.

This is the **Radicale** variant of `SETUP.md`. The difference is the whole point: Radicale is a
self-hosted CalDAV server, and a CalDAV client is **allowed to CREATE lists** — so this flow
provisions a brand-new project **end-to-end with ZERO manual server/phone steps**. There is **no**
"go make two Reminders lists on the phone" step: the self-provision creates them on Radicale, and
because the phone already has the ONE shared Radicale CalDAV account added, the two lists sync to
Reminders on the phone automatically.

This ONE file works identically for the **first launch** (auto-provisions this project) and for
**every relaunch** (detects it's already provisioned and just starts). The human copies this single
file into any new project folder and runs `claude @SETUP-RADICALE.md` — same file, same command,
every time. **STEP 1's detect is the only branch.**

Do the steps in order. Keep it lean — do not run verification/exploration you are not told to run.

---

## STEP 1 — DETECT (always first)

Check whether this project is already configured:

- If **`./.claude/voice-bridge.json` exists** → this is a **relaunch**. Read that file, take
  `vb_path` from it (this is the voice-bridge repo path — **do NOT search the filesystem for it**),
  and skip straight to **STEP 3 (START)**. If the file exists but somehow has no `vb_path`, only
  then fall through to STEP 2's locate-the-repo action to find it once.
- If **`./.claude/voice-bridge.json` does NOT exist** → this is a **first launch**. Go to **STEP 2**.

---

## STEP 2 — FIRST-TIME PROVISION (only when STEP 1 found no config)

1. **Locate the voice-bridge repo ONCE.** Check the common path first, then fall back to a bounded
   search. Stop at the first hit:
   - `~/source/voice-bridge` (most likely);
   - otherwise a **bounded** search under `~/source` and `~` for a directory containing BOTH
     `radicale_bootstrap.py` AND `reminder_bridge.py` (that pair identifies the repo). Do not scan
     the whole disk — a few likely roots, shallow depth.
2. **Run the Radicale self-provision** from THIS project's folder (your cwd), using the repo path you
   just found as `<vb>`:
   ```
   uv run <vb>/radicale_bootstrap.py
   ```
   In ONE step this: derives this project's distinct list names from the folder name; writes
   `./.claude/voice-bridge.json` (with `creds_env` pointing at `~/.auth/radicale.env` and recording
   `vb_path` so nothing ever searches again); and **connects to Radicale and creates the two VTODO
   lists** `To <Title>` / `From <Title>` if missing. It is idempotent — a second run that finds the
   config and both lists prints `ALREADY PROVISIONED: <name>` and exits 0.
3. **NO phone step.** Unlike the iCloud path, you do **not** ask the human to create any lists. The
   two lists now exist on Radicale and sync to the phone's Reminders via the shared CalDAV account.
   The script prints the start command; go straight to **STEP 3**.

> Prerequisite (one-time, for ALL Radicale projects — assume it's already done unless the provision
> fails to connect): the Radicale server is running and `~/.auth/radicale.env` carries
> `ICLOUD_CALDAV_URL` + the Radicale user/pass; the phone has that ONE CalDAV account added. Full
> owner setup: `radicale/OWNER-SETUP.md`. If the provision errors on connect, tell the human to
> confirm the server is up (`radicale/OWNER-SETUP.md`) — do not try to fix it blindly.

---

## STEP 3 — START (every launch — first launch AND relaunch land here)

The config now exists. Read `vb_path` and `mailbox_dir` from `./.claude/voice-bridge.json`.

1. **Start the poller in the BACKGROUND** (it must stay running the whole session — do NOT run it in
   the foreground, which blocks). The `--config` is always passed explicitly, so even started from
   the wrong directory it can never bind to the wrong project:
   ```
   uv run <vb_path>/reminder_bridge.py --config ./.claude/voice-bridge.json
   ```
2. **Arm a Monitor** on this project's inbound mailbox (`<mailbox_dir>/to-manager.md`, using the
   `mailbox_dir` from the config), persistent, so a new line wakes you:
   ```
   Monitor(command: tail -f -n0 <mailbox_dir>/to-manager.md,
           description: "phone->PC bridge", persistent: true)
   ```
3. **LISTEN.** Each new inbound line looks like `- [HH:MM] (<from_name>) <text>` — it is a request
   from the phone for THIS project. Do the work, then reply.

### Standard operating rules (carry these — they are the contract, not optional)

- **Async / turn-based, not live.** Messages arrive when the phone next speaks; your replies reach
  the phone a turn later. Everything is timestamped for exactly this reason.
- **Newest supersedes.** If several inbound lines stack up, the newest instruction wins over an
  older conflicting one.
- **Clarify misheard terms.** Inbound text is VOICE transcription and may contain misheard words
  (e.g. "dot claude" → "dot cloud"). When a technical term, file/config name, or proper noun looks
  wrong or ambiguous, **STOP and ask through the bridge before acting** — a wrong premise scales
  into wrong work.
- **Project-name tagging.** Replies are automatically stamped `[HH:MM][<project-name>]` so a phone
  assistant juggling several projects can always tell which one answered.
- **Self-contained replies.** Make every reply COMPLETE and self-contained — full detail and
  context, never shorthand — so the phone assistant never has to guess on a follow-up.
- **REPLY via the bridge's `send_reply` so notifications fire.** Do **NOT** write a raw CalDAV todo
  yourself. Send replies through `reminder_bridge.send_reply(cfg, text, notify=True)`, which creates
  the output-list VTODO **and** pushes the ntfy banner. The two supported ways to invoke it:
  - CLI (simplest): `uv run <vb_path>/reminder_bridge.py --config ./.claude/voice-bridge.json --reply "your full message"`
  - SHORT single line: append it to `<mailbox_dir>/to-phone.md` — the running poller drains each new
    line through `send_reply` within one interval:
    ```
    printf '%s\n' 'your message' >> <mailbox_dir>/to-phone.md
    ```
  Either path fires the ntfy notification; a raw `caldav` write would NOT, so the phone would get no
  banner. Never bypass `send_reply`.
- **Auto-fetch missing content.** If a request refers to something you don't have in context, go get
  it (read the file, run the command) rather than replying "I don't have that."
- **Sending long CONTENT for review** (a design, a doc): write it to a Markdown file, then
  `<vb_path>/deliver_content.sh <file> "one-line summary"` — it publishes a gist, pushes a tappable
  link, and drops the summary+link into the output list.

---

**Remember: same file, same command (`claude @SETUP-RADICALE.md`), every launch.** STEP 1's detect
is the only branch — first launch runs STEP 2 once (which self-provisions config **and** lists, no
phone step), every launch runs STEP 3.
