# Install — get to a working command

You do this once. It ends with you being able to type **`vb --help`** and see output; every
other guide in this set uses `vb`, literally, so you never have to translate anything.

- [1. Open the right shell](#1-open-the-right-shell)
- [2. Get `uv`](#2-get-uv)
- [3. Pick a directory and stay in it](#3-pick-a-directory-and-stay-in-it)
- [4. Get the code](#4-get-the-code)
- [5. Define `vb`, and prove it](#5-define-vb-and-prove-it)
- [If `vb` stops working](#if-vb-stops-working)

---

## 1. Open the right shell

**Windows: use PowerShell, not Command Prompt.** They look alike and are not alike — the
lines below are a syntax error in `cmd.exe`, which reports `'function' is not recognized`
and leaves you with nothing to do about it.

Open **Windows Terminal** and pick the *PowerShell* tab, or press `Win+R` and run `pwsh`
(falling back to `powershell` if that is not found).

**You are in the right place when your prompt starts with `PS`:**

```
PS C:\Users\you>
```

If it starts with `C:\Users\you>` and no `PS`, you are in Command Prompt — type `pwsh` and
press Enter.

**macOS / Linux:** the default Terminal is fine. Anything bash- or zsh-like works.

## 2. Get `uv`

Everything runs through [`uv`](https://docs.astral.sh/uv/) — it fetches Python and the
dependencies for you. Install it from that page (one line for every platform), then prove
it, because nothing below works without it:

```
uv --version
```

A version number means you are ready. `not recognized` / `command not found` means the
install did not land on this shell's PATH — close the window, open a new one, and try again
before anything else.

**You also need `git`**, for the next step. Same check:

```
git --version
```

If that one fails, install [Git](https://git-scm.com/downloads) and open a fresh terminal.

## 3. Pick a directory and stay in it

**Your settings live in `.claude/voice-bridge.json` under the directory you run commands
from.** Not your home directory, not a system location — *there*. This matters more than it
sounds: run setup in one place and the bridge in another, and the second one finds no
config, **starts a fresh setup, and writes a second config where you happen to be standing**.
Two half-installs and nothing says so.

The next step picks that directory for you: it is the folder the code lands in, and you run
everything from inside it. (If you take the no-clone route in the box below instead, make a
folder yourself — `voice-bridge` anywhere convenient — and `cd` into it before going on.)

> If you would rather keep one config and run from anywhere, set the `VOICE_BRIDGE_CONFIG`
> environment variable to its full path, or pass `--config PATH` on every command.

## 4. Get the code

Clone the repository and go into it. **This is the path that works today**, and the rest of
this guide assumes you are standing in that folder:

```
git clone https://github.com/YoraiLevi/ai-voice-reminders-bridge
cd ai-voice-reminders-bridge
```

<details>
<summary><b>Installing without a clone (after this branch is merged)</b></summary>

`uvx` can run the tool straight from the repository with nothing checked out — but only once
the package is on the repository's **default branch**. Until then it fails with *"does not
appear to be a Python project"*, because the default branch does not contain it yet.

When it is merged, skip the clone in step 4, `cd` into a folder of your own (step 3), and
use this instead of step 5:

```powershell
function vb { uvx --from 'voice-bridge[all] @ git+https://github.com/YoraiLevi/ai-voice-reminders-bridge' voice-bridge @args }
```

```bash
vb() { uvx --from 'voice-bridge[all] @ git+https://github.com/YoraiLevi/ai-voice-reminders-bridge' voice-bridge "$@"; }
```

Everything else in every guide is unchanged, because they all say `vb`.

</details>

## 5. Define `vb`, and prove it

Paste the line for your shell. It defines `vb` as a shortcut for the real command, so every
guide can print commands you type **exactly as written**.

**PowerShell:**

```powershell
function vb { uv run --extra all voice-bridge @args }
```

**bash / zsh:**

```bash
vb() { uv run --extra all voice-bridge "$@"; }
```

Now prove it:

```
vb --help
```

You should get a usage block listing `run`, `setup`, `verify`, `doctor` and the rest. The
first run takes a moment — `uv` is building the environment.

### Why `--extra all` is in there

**It is not optional and it is not decoration.** The package itself has *no* dependencies:
each backend is pulled in separately, so without `--extra all` the tool installs fine and
then fails the first time it tries to reach your phone, with *"pyicloud not installed"* or
*"install voice-bridge[server]"*. `all` covers both backends. It is baked into `vb`, so you
will not have to think about it again.

## If `vb` stops working

**`vb` lives only in the terminal window you defined it in.** Open a new window — or reboot,
or come back tomorrow — and it is gone, with `The term 'vb' is not recognized`.

Nothing is broken. Re-paste the line from [step 5](#5-define-vb-and-prove-it), from inside
the project directory. That is the whole fix, and you will do it more than once.

> **Why not install it properly on the PATH?** You can, but `uv run` deliberately puts the
> program on the PATH only for the command it is running — which is why typing a bare
> `voice-bridge` in your own shell does not work even though it works inside `uv run`. `vb`
> is the honest shortcut around that. If the tool ever detects the mismatch it prints a note
> beginning `note: `voice-bridge` will not be on the PATH of the shell you type into -`
> followed by the exact form to use.

---

**Next:** pick your backend and follow one guide the whole way —
[iCloud](setup-icloud.md) or [Radicale](setup-radicale.md). If you have not chosen, the
comparison table is in the [README](../README.md#which-backend).
