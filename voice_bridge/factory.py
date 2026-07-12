"""Pick the Transport implementation for a config. The only place that names both
concrete adapters; everything else depends on the Transport interface."""

from __future__ import annotations

from .config import Config
from .transport import Transport


def make_transport(cfg: Config) -> Transport:
    if cfg.transport == "radicale":
        from .caldav import CalDAVTransport

        return CalDAVTransport(cfg)
    from .icloud import ICloudTransport

    return ICloudTransport(cfg)
