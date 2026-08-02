# Install — get to a working command

You do this once. It ends with you being able to type **`vb --help`** and see output; every
other guide in this set uses `vb`, literally, so you never have to translate anything.

- [1. Open the right shell](#1-open-the-right-shell)
- [2. Get `uv`](#2-get-uv)
- [3. Pick a directory and stay in it](#3-pick-a-directory-and-stay-in-it)
- [4. Define `vb`, and prove it](#4-define-vb-and-prove-it)
- [If you would rather have the source](#if-you-would-rather-have-the-source)
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

If it starts with `C:\Users\you>` and no `PS`, you are in Command Prompt. Type `pwsh` and
press Enter — and if that reports `'pwsh' is not recognized`, type `powershell` instead,
which every Windows install has.

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

## 3. Pick a directory and stay in it

**Your settings live in `.claude/voice-bridge.json` under the directory you run commands
from.** Not your home directory, not a system location — *there*. This matters more than it
sounds: run setup in one place and the bridge in another, and the second one finds no
config, **starts a fresh setup, and writes a second config where you happen to be standing**.
Two half-installs and nothing says so.

So make one now and stay in it:

```bash
mkdir -p ~/voice-bridge && cd ~/voice-bridge          # bash / zsh
```

```powershell
New-Item -ItemType Directory -Force ~/voice-bridge; cd ~/voice-bridge    # PowerShell
```

> If you would rather keep one config and run from anywhere, set the `VOICE_BRIDGE_CONFIG`
> environment variable to its full path, or pass `--config PATH` on every command.

## 4. Define `vb`, and prove it

**There is nothing to download.** `uvx` fetches and runs the tool on demand, so the whole
install is one line that teaches your shell a shortcut. Paste the one for your shell:

**PowerShell:**

```powershell
function vb { uvx --from 'voice-bridge[all] @ git+https://github.com/YoraiLevi/ai-voice-reminders-bridge' voice-bridge @args }
```

**bash / zsh:**

```bash
vb() { uvx --from 'voice-bridge[all] @ git+https://github.com/YoraiLevi/ai-voice-reminders-bridge' voice-bridge "$@"; }
```

Now prove it:

```
vb --help
```

You should get a usage block listing `run`, `setup`, `verify`, `doctor` and the rest. The
first run takes a moment while `uv` builds the environment; later runs are fast.

Every guide in this set prints its commands as `vb something`, so from here on you type what
you see.

### Why `[all]` is in there

**It is not optional and it is not decoration.** The package itself has *no* dependencies:
each backend is pulled in separately, so without it the tool installs fine and then fails the
first time it tries to reach your phone, with *"pyicloud not installed"* or *"install
voice-bridge[server]"*. `all` covers both backends.

It is baked into `vb` — spelled `[all]` in the `uvx` form and `--extra all` in the clone form
— so you will not have to think about it again.

## If you would rather have the source

Cloning is for reading or changing the code — it is not needed to *use* the tool. You also
need [Git](https://git-scm.com/downloads) for this route (`git --version` to check).

```
git clone https://github.com/YoraiLevi/ai-voice-reminders-bridge
cd ai-voice-reminders-bridge
```

That checkout is then your one directory from [step 3](#3-pick-a-directory-and-stay-in-it),
and `vb` is defined against it instead — run this from inside the clone:

```powershell
function vb { uv run --extra all voice-bridge @args }
```

```bash
vb() { uv run --extra all voice-bridge "$@"; }
```

Everything else in every guide is unchanged, because they all say `vb`.

## If `vb` stops working

**`vb` lives only in the terminal window you defined it in.** Open a new window — or reboot,
or come back tomorrow — and it is gone, with `The term 'vb' is not recognized`.

Nothing is broken. `cd` back to your directory and re-paste the line from
[step 4](#4-define-vb-and-prove-it). That is the whole fix, and you will do it more than
once.

> **Why not just install it on the PATH?** You can — but neither route above puts a
> `voice-bridge` command in your shell, and both are deliberate about it. `uvx` runs the tool
> without installing it at all, and `uv run` puts the program on the PATH only for the
> command it is running. That is why a bare `voice-bridge` fails in your own shell even when
> it works inside `uv run`, and `vb` is the honest shortcut around it. If the tool ever
> detects the mismatch it says so, in a note beginning
> `note: `voice-bridge` will not be on the PATH of the shell you type into -` followed by the
> exact form to use.

---

**Next:** pick your backend and follow one guide the whole way —
[iCloud](setup-icloud.md) or [Radicale](setup-radicale.md). If you have not chosen, the
comparison table is in the [README](../README.md#which-backend).
