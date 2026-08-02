"""Pick the Transport implementation for a config. The only place that names both
concrete adapters; everything else depends on the Transport interface."""

from __future__ import annotations

from .config import Config
from .transport import NotSupportedError, Transport

#: The closed set, named where the adapters are named so it cannot drift from them.
#:
#: It exists because the preamble accepted `rad` and echoed "ACCEPTED - transport =
#: rad". Two separate faults in that one line: a prompt that printed its own closed
#: set and then took anything, and this factory, which fell through to iCloud for
#: ANY unrecognised value - so a typo did not fail, it silently selected the other
#: backend. Validating the input alone would have left the second one live for
#: hand-edited configs and for every other door into a transport.
TRANSPORTS: tuple[str, ...] = ("icloud", "radicale")


def make_transport(cfg: Config) -> Transport:
    if cfg.transport == "radicale":
        from .caldav import CalDAVTransport

        return CalDAVTransport(cfg)
    if cfg.transport == "icloud":
        from .icloud import ICloudTransport

        return ICloudTransport(cfg)
    # Never a silent default. Handing back a backend the user did not name is the
    # nothing-is-ever-chosen-for-you rule, one layer below the pickers.
    raise NotSupportedError(
        f"unknown transport {cfg.transport!r} - expected one of {', '.join(TRANSPORTS)}. "
        f"Set it with:  voice-bridge config set transport {TRANSPORTS[0]}"
    )
