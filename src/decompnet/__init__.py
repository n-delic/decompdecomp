"""decompdecomp: an app where AI agents decompile games you own into C that builds them again, byte for byte."""

import sys

__version__ = "1.0.4-alpha.1"


def utf8_output() -> None:
    """Windows: output into a pipe or file is written in the ANSI code page there, where printing a "→" or a user name
    with letters outside it crashes. Write UTF-8 instead (a console window shows everything anyway)."""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")

