# Radicale server — auto-start on login (Windows, no admin)

The Radicale CalDAV server is the **shared backend** every voice-bridge project depends on — it
must be running for the phone ↔ PC bridge to work. This makes it start automatically at every
login, so it **survives reboots**, without needing administrator rights.

> Scope: this configures only the SERVER. Per-project **pollers** are started by each project's
> `@SETUP-RADICALE.md` prompt when you open that project's session. Tailscale auto-starts on its
> own. There is no iCloud dependency.

## Why the Startup folder (not Task Scheduler / a Windows Service)
Registering a Task Scheduler task or a Windows Service requires **administrator elevation**
(`Register-ScheduledTask` fails with "Access is denied" from a normal shell). The per-user
**Startup folder** needs no elevation — a launcher placed there runs at every login in your user
context, which is exactly right for a user-owned server reachable over your own Tailscale.

## Setup (one time)

**1. A launcher batch file** that starts the server. Put it somewhere stable, e.g. the repo's
`radicale/` folder as `start-radicale.cmd`:

```bat
@echo off
"C:\Users\<you>\.local\bin\uv.exe" run "C:\path\to\voice-bridge\radicale\run_radicale.py"
```

Point the `uv.exe` path and the `run_radicale.py` path at your actual locations. `run_radicale.py`
chdir's to its own directory, so the working directory does not matter. (Radicale binds
`0.0.0.0:5232` per its `config`; the Tailscale HTTPS `serve` proxies 443 → 5232.)

**2. A hidden launcher in the Startup folder** so no console window flashes at login. Create
`radicale-voice-bridge.vbs` in
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\` containing one line:

```vbscript
CreateObject("Wscript.Shell").Run "C:\path\to\voice-bridge\radicale\start-radicale.cmd", 0, False
```

`0` = run hidden (no window); `False` = don't wait. This runs the launcher at every login.

## Verify

Run the `.vbs` (or log out and back in), wait ~10 seconds for `uv` to start, then:

```bash
curl -u <radicale-user>:<radicale-pass> http://127.0.0.1:5232/     # expect HTTP 302
```

A `302` means the server is up. It now runs **independently of any Claude Code session** and comes
back after every reboot. (First `uv run` on a fresh machine installs the deps once, then caches
them, so subsequent starts are fast — allow a few extra seconds the very first time.)

## Disable
Delete `radicale-voice-bridge.vbs` from the Startup folder. It stops auto-starting at the next
login; to stop the currently-running server, end the `run_radicale.py` `python`/`uv` process in
Task Manager.
