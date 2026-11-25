"""decompdecomp: an app where AI agents decompile games you own into C that builds them again, byte for byte."""

import os
import sys

__version__ = "1.0.11-rr.1"

# A thread that gives up the GIL (every sqlite row step does) waits for it up to one switch interval for each busy
# Python thread ahead of it. At the default 5 ms, with a few compute threads running, a 1,700-row read took 18 s of
# wall time for 0.04 s of CPU, a three-statement claim write held the project's single writer for 38 s, and every
# lane and worker queued behind it (measured: 0.18 s at 0.5 ms). Every process of the app imports this package.
# DECOMP_SWITCH_INTERVAL (seconds) overrides it; 0 keeps Python's default.
try:
    _interval = float(os.environ.get("DECOMP_SWITCH_INTERVAL", "0.0005"))
except ValueError:
    _interval = 0.0005
if _interval > 0:
    sys.setswitchinterval(_interval)


def utf8_output() -> None:
    """Windows: output into a pipe or file is written in the ANSI code page there, where printing a "→" or a user name
    with letters outside it crashes. Write UTF-8 instead (a console window shows everything anyway)."""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
