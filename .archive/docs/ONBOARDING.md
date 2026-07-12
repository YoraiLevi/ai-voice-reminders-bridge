# Onboarding — talk to your PC's Claude Code from your phone, across many projects

This is the from-zero, plain-English walkthrough. It assumes **no prior context**. By the end you will
be able to say something to your phone like *"on alpha, add a login button"* and have a Claude Code
session on your PC actually do it — and hear its reply back on your phone — for as many projects as you
like, all through **one** voice assistant.

Everything is driven by **one command** per project — `claude @SETUP-RADICALE.md` (recommended) or
`claude @SETUP.md` (iCloud fallback). The four shipped [`examples/`](../examples/README.md) are a 2×2
of {single project, multiple projects} × {Radicale, iCloud}; this doc uses them as the running example.

---

## 1. The one-paragraph mental model

You talk to the **Claude app on your phone**. It cannot reach your PC directly. So we use a shared
CalDAV mailbox — **Reminders lists** (backed either by a self-hosted **Radicale** server or by
**iCloud**): the phone drops a task into a list; a little program on your PC (the **poller**) sees it
and hands it to a **Claude Code "manager" session** running in your project; that session's reply goes
into a **second** list, which the phone reads back to you. It is **not** a live phone call — messages
arrive turn-by-turn, a little delayed, and each is timestamped. That is normal and expected.

```
  PHONE (Claude app)                              PC
  ┌───────────────────────┐        ┌──────────────────────────────────────┐
  │  "To Alpha"           │ ─────▶ │  poller  ─▶  to-manager.md  ─▶ manager │
  │   (you send here)     │        │                                session │
  │                       │        │                                  │     │
  │  "From Alpha"         │ ◀───── │  to-phone.md  ◀──────────────────┘     │
  │   (replies appear)    │        │   (poller drains this back to the list)│
  └───────────────────────┘        └──────────────────────────────────────┘
        one such PAIR of lists per project · one poller per project
```

**Each project gets its own pair of lists and its own poller.** For multiple projects, the phone
assistant holds a small **routing table** mapping each project to its list pair, so it knows which list
to drop your request into and which lists to read replies from.

---

## 2. The pieces — what must exist per project

For **each** project you want to control by voice, three things must line up:

1. **A project folder on your PC** containing `.claude/voice-bridge.json` — the config. It names:
   - `inbox_list` — the list the **phone sends to** (e.g. `To Alpha`)
   - `output_list` — the list **replies appear in** (e.g. `From Alpha`)
   - `mailbox_dir` — a folder on the PC where this project's mailbox files live
   - `name` / `from_name` — labels that keep this project's bookkeeping separate from others
   - `creds_env` — which `~/.auth` env file to use: `~/.auth/radicale.env` (Radicale) or
     `~/.auth/icloud.env` (iCloud). This one field is what selects the transport.
2. **Two lists** named **exactly** as `inbox_list` and `output_list`. On **Radicale** the setup
   **creates these for you**; on **iCloud** you make them by hand on the phone (iCloud won't let a
   client create a list).
3. **A running poller and manager session** for that project on the PC.

You do **not** hand-write any of this. `claude @SETUP-RADICALE.md` / `claude @SETUP.md` writes the
config (deriving distinct names from the folder name), provisions what it can, and starts the poller.

### Why each project's names must be DISTINCT

The poller writes two mailbox files inside `mailbox_dir`: `to-manager.md` (inbound) and `to-phone.md`
(outbound). These mailbox files are **not** auto-separated by project (only the tiny dedupe seen-files
are, under `state/<name>-seen.txt`). So if two projects shared one `mailbox_dir`, their two managers
would read the **same** `to-manager.md` and get each other's messages. That is why each example project
has **distinct list names** *and* a **distinct `mailbox_dir`** — derived automatically from the folder
name. One thing is safely **shared**: your account login. All projects of a transport point
`creds_env` / `cookie_dir` / `ntfy_topic_file` at the same `~/.auth` files, so you log in once.

---

## 3. Pick your quadrant (the four worked examples)

|                        | **Radicale** (recommended)              | **iCloud** (fallback)                        |
|------------------------|-----------------------------------------|----------------------------------------------|
| **SINGLE project**     | Ex 1 · [single-radicale](../examples/single-radicale/WALKTHROUGH.md) — `claude @SETUP-RADICALE.md` | Ex 2 · [single-icloud](../examples/single-icloud/WALKTHROUGH.md) — `claude @SETUP.md` |
| **MULTIPLE projects**  | Ex 3 · [multi-radicale](../examples/multi-radicale/WALKTHROUGH.md) ★ | Ex 4 · [multi-icloud](../examples/multi-icloud/WALKTHROUGH.md) ⚠ |

The deciding differences (full table in [`examples/README.md`](../examples/README.md)):

- **Radicale is self-provisioning** — Claude creates the two lists on the server for you, and they sync
  to the phone automatically. **No manual phone step.** And it has **no per-account throttle**, so any
  number of pollers can run at once.
- **iCloud cannot create lists** — so every iCloud project needs you to make its two Reminders lists on
  the phone by hand. A **single** iCloud project is fine; **multiple** iCloud pollers on one Apple ID
  can trigger an **auth storm** (Apple 503 throttling + forced re-2FA) — a real incident we hit. So for
  more than one project, **prefer Radicale**.

---

## 4. One-time account setup (once, for ALL projects of that transport)

- **Radicale (recommended):** run the Radicale server, seed `~/.auth/radicale.env` (server URL +
  Radicale user/pass), and add the ONE CalDAV account to the phone. Full detail:
  [`../radicale/OWNER-SETUP.md`](../radicale/OWNER-SETUP.md). Also set `~/.auth/ntfy-topic.txt` (a
  private topic string) and subscribe the phone's **ntfy** app to it — that is how long content pushes
  a tappable banner.
- **iCloud (fallback):**
  1. `~/.auth/icloud.env` with your Apple ID + password:
     ```
     ICLOUD_APPLE_ID=you@icloud.com
     ICLOUD_PASSWORD=your-main-apple-id-password
     ```
  2. `~/.auth/ntfy-topic.txt` + subscribe the phone's ntfy app to that topic.
  3. Seed the trusted session once (needs a 6-digit 2FA code), good ~60 days:
     ```
     uv run /path/to/voice-bridge/pyicloud_login.py
     ```
     Re-run only when the bridge later says "session needs 2FA". Full detail:
     [`OWNER-SETUP.md`](OWNER-SETUP.md).

`uv run` reads each script's inline dependencies and installs them into a throwaway/cached environment —
there is no virtualenv to manage. (Install `uv` first if you don't have it.)

---

## 5. Onboard a project — the one command

Copy the setup file into the project folder and run it there. That is the whole thing.

**Radicale (Ex 1 / Ex 3):**
```
# copy SETUP-RADICALE.md (repo root) into the project folder, then, in that folder:
claude @SETUP-RADICALE.md
```
- **First launch:** the agent finds the voice-bridge repo once (recording `vb_path`), runs
  `radicale_bootstrap.py` — which derives the distinct list names from the folder name, writes
  `.claude/voice-bridge.json` (`creds_env` → `~/.auth/radicale.env`), **and connects to Radicale to
  create the two VTODO lists** — then starts `reminder_bridge.py` and listens. **No phone step.**
- **Every relaunch:** the config exists, so it reads `vb_path` and goes straight to starting the poller.

**iCloud (Ex 2 / Ex 4):**
```
# copy SETUP.md (repo root) into the project folder, then, in that folder:
claude @SETUP.md
```
- **First launch:** the agent runs `bootstrap.py` (derives distinct names, writes the config with
  `creds_env` → `~/.auth/icloud.env`, records `vb_path`), then tells you the **one manual step** —
  create the two Reminders lists it names on the phone — and **waits** for you to confirm. Then it
  starts `pyicloud_bridge.py` and listens.
- **Every relaunch:** reads `vb_path` and starts the poller.

You can preview either bootstrap directly (both idempotent — safe to re-run):
```
uv run /path/to/voice-bridge/radicale_bootstrap.py   # from inside the project folder (Radicale)
uv run /path/to/voice-bridge/bootstrap.py            # from inside the project folder (iCloud)
```
Each per-example [`WALKTHROUGH.md`](../examples/single-radicale/WALKTHROUGH.md) traces exactly what
happens, step by step, for that quadrant.

---

## 6. Multiple projects — run them side by side

For Ex 3 / Ex 4, repeat section 5 for each project in its **own** Claude Code session (one per folder).
Because the list names and `mailbox_dir` are distinct (derived from each folder name), the two pollers
and two managers run at the same time without ever touching each other's messages.

- **Radicale (Ex 3, recommended):** `alpha/` and `beta/`, each `claude @SETUP-RADICALE.md`. Every list
  is created for you; no phone step; no throttle.
- **iCloud (Ex 4, ⚠):** `gamma/` and `delta/`, each `claude @SETUP.md`. Each needs its two lists made
  on the phone by hand, and you must hold **strict one-poller-per-Apple-ID discipline** — several
  iCloud pollers auto-re-authing on one account is what caused the 503/re-2FA auth storm. Give each
  iCloud project its own Apple ID (`creds_env`) if you truly need multi on iCloud.

---

## 7. Set up the phone to route to all projects

On the phone, save [`../examples/PHONE-ASSISTANT-PROMPT.md`](../examples/PHONE-ASSISTANT-PROMPT.md) (as
a Claude **Project** custom instruction, or paste it at the start of a voice chat). It contains a
**ROUTING TABLE** that maps each project to its list pair, plus a DEFAULT project for when you don't
name one. The shipped table is filled in for the multi-Radicale example (`alpha` / `beta`); the
multi-iCloud example uses the identical shape with `gamma` / `delta`.

Now, when you speak:
- *"On beta, run the tests"* → the assistant drops it in `To Beta`; beta's poller picks it up; that
  manager runs the tests.
- Every turn, the assistant silently reads **every** project's FROM list, and reads any reply back to
  you **saying which project it's from** ("beta says: tests pass").
- If it can't tell which project you meant, it **asks** rather than guessing.

To add a project = copy one `PROJECT:` block. To roll back to a single project = keep only the DEFAULT
block (the bridge then behaves exactly like the single-project examples).

---

## 8. The reply contract (how a manager answers)

Each new inbound line looks like `- [HH:MM] (<from_name>) <text>`. The manager does the work, then
**replies through the bridge so the ntfy banner fires** — never write a raw CalDAV todo yourself (it
would surface no notification). Both reply paths route through `send_reply(cfg, text, notify=True)`,
which creates the output-list VTODO **and** pushes ntfy:

- **SHORT single line** — instant local append; the warm poller drains it within one interval:
  ```
  printf '%s\n' 'your message' >> <mailbox_dir>/to-phone.md
  ```
- **LONGER / multi-line** — send directly (the append is line-based and would split it):
  ```
  # Radicale:
  uv run <vb_path>/reminder_bridge.py --config ./.claude/voice-bridge.json --reply "your full message"
  # iCloud:
  uv run <vb_path>/pyicloud_bridge.py --config ./.claude/voice-bridge.json --reply "your full message"
  ```
- **Long CONTENT for review** (a design, a doc): write it to a Markdown file, then
  `<vb_path>/deliver_content.sh <file> "one-line summary"` — publishes a gist, pushes a tappable link,
  and drops the summary+link into the output list.

Standard operating rules (carried by the SETUP files): async/turn-based; newest supersedes; clarify
misheard voice terms before acting; keep every reply complete and self-contained.

---

## 9. When things look wrong (quick checks)

- **A request never arrives.** Confirm the list name on the phone matches `inbox_list` exactly (run the
  poller with `--show-config`), and that the poller for that project is actually running.
- **The wrong session answers.** Two projects are sharing a `mailbox_dir` or a list name — make them
  distinct (section 2).
- **Replies don't come back.** Confirm the manager is appending to *its own* `to-phone.md` and that the
  poller is still up. Replies are timestamped and can lag a turn.
- **"session needs 2FA" (iCloud).** Re-run `pyicloud_login.py` once. If several iCloud pollers are
  running, **stop the extras first** — a swarm re-authing during recovery just re-triggers the throttle
  (Ex 4).
- **The private Apple API stops working, or iCloud throttling bites.** Switch to the Radicale transport —
  same mailbox contract, one `creds_env` swap. See [`../radicale/OWNER-SETUP.md`](../radicale/OWNER-SETUP.md).
