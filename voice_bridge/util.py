"""Small foundation helpers with no internal dependencies: reading KEY=VALUE
credential/env files, writing files atomically, and masking secrets for logs.

Two distinct readers live here on purpose. `read_env` keeps the shell-ish
conveniences (strip whitespace, drop surrounding quotes) that suit a config-style
env file. `read_secret` returns the value **verbatim**, because a password is not
a shell token - quotes and spaces in it are content, and silently removing them
produces a login failure with no visible cause.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    """Parse a `KEY=VALUE` file into a dict. Blank lines and `#` comments are
    ignored; surrounding whitespace and a leading `export ` are stripped. A
    missing file yields an empty dict (absent creds are handled by the caller)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def read_kv(path: Path, key: str) -> str | None:
    """One value from a KEY=VALUE file, or None."""
    return read_env(path).get(key)


def read_secret(path: Path, key: str) -> str | None:
    """One value from a `KEY=VALUE` file, **verbatim** - no stripping, no unquoting.

    Only the first `=` splits, so a value containing `=` survives intact. Use this
    for passwords and tokens; use `read_env` for ordinary settings.
    """
    if not path.exists():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.lstrip().startswith("#"):
            continue
        line = raw
        if line.startswith("export "):
            line = line[len("export ") :]
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.rstrip("\r")  # only the line ending, never the value
    return None


def write_env(path: Path, values: dict[str, str], *, mode: int = 0o600) -> None:
    """Merge `values` into a `KEY=VALUE` file, writing them back verbatim.

    Keys already in the file that are not being changed are preserved, so writing
    a password never silently drops an unrelated setting. The file is created
    owner-only where the platform supports it - it holds a reusable credential in
    cleartext (at-rest hardening is tracked separately).
    """
    existing: dict[str, str] = {}
    order: list[str] = []
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw[len("export ") :] if raw.startswith("export ") else raw
            name, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                k = name.strip()
                if k not in existing:
                    order.append(k)
                existing[k] = value.rstrip("\r")

    for k, v in values.items():
        if k not in existing:
            order.append(k)
        existing[k] = v

    atomic_write(path, "".join(f"{k}={existing[k]}\n" for k in order), mode=mode)


def atomic_write(path: Path, text: str, *, mode: int | None = None) -> None:
    """Write `text` to `path` all-or-nothing.

    A plain `write_text` truncates the target before the new bytes land, so a
    crash in that window leaves a half-written file - for the config that means
    every later command fails until a human repairs it (FMA-11). Writing a
    complete temporary file in the *same directory* and then `os.replace`-ing it
    over the target makes the update atomic: readers see either the old file or
    the new one, never a partial one. Same directory matters because `os.replace`
    is only atomic within a filesystem.

    Durability against power loss is a separate concern from atomicity; see the
    mailbox append path for that.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # never leave litter beside the real file
        raise


def masked(secret: str) -> str:
    """A log-safe rendering of an id/email: first char + a hint, rest hidden.
    Never returns the full value."""
    s = str(secret)
    if not s:
        return "(unset)"
    head = s[0]
    tail = ""
    if "@" in s:
        tail = "@" + s.split("@", 1)[1][:1] + "..."
    return f"{head}***{tail} (hidden)"
