"""Small foundation helpers with no internal dependencies: reading KEY=VALUE
credential/env files and masking secrets for logs."""

from __future__ import annotations

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


def masked(secret: str) -> str:
    """A log-safe rendering of an id/email: first char + a hint, rest hidden.
    Never returns the full value."""
    s = str(secret)
    if not s:
        return "(unset)"
    head = s[0]
    tail = ""
    if "@" in s:
        tail = "@" + s.split("@", 1)[1][:1] + "…"
    return f"{head}***{tail} (hidden)"
