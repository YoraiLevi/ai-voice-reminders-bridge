# examples — four worked examples (a 2×2 of single/multi × Radicale/iCloud)

This folder ships **four complete, self-bootstrapping example projects**, one per quadrant of
{single project, multiple projects} × {Radicale, iCloud}. Every quadrant is onboarded **hands-off with
one command** (`claude @SETUP-RADICALE.md` or `claude @SETUP.md`) — the Claude Code agent locates the
repo, writes the config, provisions what it can, starts the poller, and listens.

Full beginner walkthrough from zero: [`../docs/ONBOARDING.md`](../docs/ONBOARDING.md).

```
examples/
├── README.md                     ← you are here (the 2×2 index)
├── PHONE-ASSISTANT-PROMPT.md     ← ONE phone prompt (routing table) — used by BOTH multi examples
│
├── single-radicale/   (Ex 1)  ← RECOMMENDED baseline · claude @SETUP-RADICALE.md
│   ├── WALKTHROUGH.md
│   └── myapp/.claude/voice-bridge.json      (To Myapp / From Myapp · radicale.env)
│
├── single-icloud/     (Ex 2)  ← fallback · claude @SETUP.md
│   ├── WALKTHROUGH.md
│   └── myapp/.claude/voice-bridge.json      (To Myapp / From Myapp · icloud.env)
│
├── multi-radicale/    (Ex 3)  ← RECOMMENDED for multi-project · claude @SETUP-RADICALE.md ×2
│   ├── WALKTHROUGH.md
│   ├── alpha/.claude/voice-bridge.json      (To Alpha / From Alpha · radicale.env)
│   └── beta/.claude/voice-bridge.json       (To Beta  / From Beta  · radicale.env)
│
└── multi-icloud/      (Ex 4)  ← works, but NOT recommended (throttle risk) · claude @SETUP.md ×2
    ├── WALKTHROUGH.md
    ├── gamma/.claude/voice-bridge.json      (To Gamma / From Gamma · icloud.env)
    └── delta/.claude/voice-bridge.json      (To Delta / From Delta · icloud.env)
```

---

## The 2×2 at a glance

|                        | **Radicale** (recommended)                          | **iCloud** (fallback)                                       |
|------------------------|-----------------------------------------------------|------------------------------------------------------------|
| **SINGLE project**     | **Ex 1 — [single-radicale](single-radicale/WALKTHROUGH.md)**<br>`claude @SETUP-RADICALE.md` | **Ex 2 — [single-icloud](single-icloud/WALKTHROUGH.md)**<br>`claude @SETUP.md` |
| **MULTIPLE projects**  | **Ex 3 — [multi-radicale](multi-radicale/WALKTHROUGH.md)** ★<br>`claude @SETUP-RADICALE.md` per project | **Ex 4 — [multi-icloud](multi-icloud/WALKTHROUGH.md)** ⚠<br>`claude @SETUP.md` per project |

### Comparison — the three questions that decide it

| quadrant | self-provisioning? (lists made for you) | manual phone list step? | throttle-safe for multi? |
|----------|:---------------------------------------:|:-----------------------:|:------------------------:|
| **Ex 1 · single · Radicale** | **yes** — Claude creates both lists on the server | **none** | n/a (single) |
| **Ex 2 · single · iCloud**   | no — iCloud forbids list creation | **yes** — make 2 lists on the phone | n/a (single) — one poller is fine |
| **Ex 3 · multi · Radicale** ★ | **yes** — every list created automatically | **none** | **yes** — Radicale has no per-account limit |
| **Ex 4 · multi · iCloud** ⚠  | no | **yes** — 2 lists per project, by hand | **NO** — several pollers on one Apple ID caused a real auth storm (503 + forced re-2FA) |

**Bottom line:** Radicale is self-provisioning and throttle-free — use it. iCloud is the fallback if you
won't run a Radicale server; single-iCloud is fine, but **multi-iCloud carries a real throttle/2FA risk**
on the shared Apple ID (see Ex 4) — prefer multi-Radicale for more than one project.

---

## What every quadrant shares

- **One command, first launch AND relaunch.** `claude @SETUP-RADICALE.md` / `claude @SETUP.md` — the
  only branch is whether `.claude/voice-bridge.json` already exists. Details in each `WALKTHROUGH.md`.
- **Distinct names per project** (`inbox_list`, `output_list`, `mailbox_dir`, `name`, `from_name`) so
  side-by-side pollers never cross wires. Derived automatically from the folder name. The multi
  walkthroughs explain the mailbox-collision trap this protects against.
- **Shared `~/.auth`** — `creds_env`, `cookie_dir`, `ntfy_topic_file` point at the same files, so one
  account (Radicale or Apple) serves every project's poller.
- **Reply through the bridge so ntfy fires.** Never write a raw CalDAV todo. Replies go through
  `send_reply(cfg, text, notify=True)` — via `reminder_bridge.py --reply` (Radicale) or
  `pyicloud_bridge.py --reply` (iCloud), or a `printf … >> to-phone.md` append the running poller
  drains. Either path creates the output-list VTODO **and** pushes the ntfy banner.

## Multi-project: the phone routes to all projects

Both multi examples use the one shared [`PHONE-ASSISTANT-PROMPT.md`](PHONE-ASSISTANT-PROMPT.md). Its
**ROUTING TABLE** maps each project to its list pair (`alpha`/`beta` for Ex 3; swap in `gamma`/`delta`
for Ex 4). Adding a project = one block; rolling back to a single project = keep only the DEFAULT block.

## One-time account setup (once, for ALL projects of that transport)

- **Radicale:** run the server, seed `~/.auth/radicale.env`, add the one CalDAV account to the phone —
  [`../radicale/OWNER-SETUP.md`](../radicale/OWNER-SETUP.md).
- **iCloud:** `~/.auth/icloud.env` (Apple ID + password) + one-time 2FA seed via `pyicloud_login.py` +
  `~/.auth/ntfy-topic.txt` — [`../docs/OWNER-SETUP.md`](../docs/OWNER-SETUP.md).
