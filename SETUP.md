# SETUP — one file, one command: `claude @SETUP.md`

**You are the MANAGER session for the project in the CURRENT folder.** These instructions are for
**you, the Claude Code agent**, not the human. Follow them top to bottom.

This ONE file works identically for the **first launch** (auto-configures this project) and for
**every relaunch** (detects it's already configured and just starts). The human copies this single
file into any new project folder and runs `claude @SETUP.md` — the same file, the same command,
every time. **The detect step below is what branches** first-launch vs relaunch; there is no manual
one-time-vs-every-launch decision for the human to make, and no `$VB` to hand-substitute.

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

## STEP 2 — FIRST-TIME SETUP (only when STEP 1 found no config)

1. **Locate the voice-bridge repo ONCE.** Check the common path first, then fall back to a bounded
   search. Stop at the first hit:
   - `~/source/voice-bridge` (most likely);
   - otherwise a **bounded** search under `~/source` and `~` for a directory containing BOTH
     `bootstrap.py` AND `pyicloud_bridge.py` (that pair identifies the repo). Do not scan the whole
     disk — a few likely roots, shallow depth.
2. **Run the bootstrap** from THIS project's folder (your cwd), using the repo path you just found
   as `<vb>`:
   ```
   uv run <vb>/bootstrap.py
   ```
   It writes `./.claude/voice-bridge.json` (deriving this project's distinct list names from the
   folder name) and records `vb_path` into it, then prints a final line of the form:
   ```
   SETUP_DONE lists_needed=To <Title>|From <Title>
   ```
   (bootstrap is idempotent — if it prints `ALREADY CONFIGURED` instead, just continue.)
3. **Tell the human the one manual step and WAIT.** The poller **cannot create Reminders lists**.
   Read the two names from that `lists_needed=...` line and tell the human, in plain words, to open
   the phone's Reminders app and create two lists named **exactly** `To <Title>` and `From <Title>`
   (a Radicale equivalent works too, for the CalDAV alt transport). **Wait for the human to confirm
   the two lists exist** before continuing. Once confirmed, go to **STEP 3**.

---

## STEP 3 — START (every launch — first launch AND relaunch land here)

The config now exists. Read `vb_path` and `mailbox_dir` from `./.claude/voice-bridge.json`.

1. **Start the poller in the BACKGROUND** (it must stay running the whole session — do NOT run it in
   the foreground, which blocks). The `--config` is always passed explicitly, so even started from
   the wrong directory it can never bind to the wrong project:
   ```
   uv run <vb_path>/pyicloud_bridge.py --config ./.claude/voice-bridge.json
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
- **Self-contained replies.** Make every reply COMPLETE and self-contained — full detail and
  context, never shorthand — so the phone assistant never has to guess on a follow-up. Reply paths
  (both write into `output_list`):
  - SHORT single line — instant local append; the warm poller drains it within one interval:
    ```
    printf '%s\n' 'your message' >> <mailbox_dir>/to-phone.md
    ```
  - LONGER / multi-line — send directly (the append is line-based and would split it):
    ```
    uv run <vb_path>/pyicloud_bridge.py --config ./.claude/voice-bridge.json --reply "your full message"
    ```
- **Auto-fetch missing content.** If a request refers to something you don't have in context, go get
  it (read the file, run the command) rather than replying "I don't have that."
- **Sending long CONTENT for review** (a design, a doc): write it to a Markdown file, then
  `<vb_path>/deliver_content.sh <file> "one-line summary"` — it publishes a gist, pushes a tappable
  link, and drops the summary+link into `output_list`.
- **If the poller prints "session needs 2FA":** run `uv run <vb_path>/pyicloud_login.py` once (the
  human supplies the 6-digit code), then continue.

---

**Remember: same file, same command (`claude @SETUP.md`), every launch.** STEP 1's detect is the
only branch — first launch runs STEP 2 once, every launch runs STEP 3.
