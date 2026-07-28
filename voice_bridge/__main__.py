"""Entry point for `python -m voice_bridge`.

The console script is the normal way in, but a module invocation must not
silently succeed at nothing - which is exactly what `python -m voice_bridge.cli`
did before it grew a `__main__` guard: it imported the module, defined `main`,
called nothing, and exited 0.
"""

from .cli import main

raise SystemExit(main())
