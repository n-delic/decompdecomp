"""Which decompdecomp this is: the release people install, or a development copy run from a git checkout.

The two are separate apps that never touch each other. A development copy has its own home (~/Decomp-dev), its own
first port, its own name and window class, and a highlighter of another colour in its icon. Until the rewrite they
shared everything, and a test run from the checkout quit the owner's running app with agents at work on GTA San
Andreas: a newer copy finding an older one running replaces it (cli.replace_older), within one home.
"""

from __future__ import annotations

import os
from pathlib import Path

# src/decompnet/app/edition.py -> the checkout's root, which has .git; installs (uv tool, /opt, an AppImage) don't
CHECKOUT_ROOT = Path(__file__).resolve().parents[3]
DEV = (CHECKOUT_ROOT / ".git").exists()

NAME = "decompdecomp dev" if DEV else "decompdecomp"
WINDOW_CLASS = "decompdecomp-dev" if DEV else "decompdecomp"  # the desktop entry's StartupWMClass
RELEASE_PORT, DEV_PORT = 6464, 6474
FIRST_PORT = int(os.environ.get("DECOMP_PORT") or (DEV_PORT if DEV else RELEASE_PORT))

RELEASE_HIGHLIGHT = (0xF6, 0xD4, 0x3A)  # the yellow block behind the second d of the icon
DEV_HIGHLIGHT = (0x3A, 0xC8, 0xF6)  # a development copy's: blue
HIGHLIGHT = DEV_HIGHLIGHT if DEV else RELEASE_HIGHLIGHT


def home_dir() -> Path:
    """Where projects and the app's own state live: DECOMP_HOME, else ~/Decomp (a development copy: ~/Decomp-dev)."""
    default = Path.home() / ("Decomp-dev" if DEV else "Decomp")
    return Path(os.environ.get("DECOMP_HOME") or default).expanduser()


def icon_svg() -> str:
    """The app's icon (static/icon.svg) with this edition's highlighter colour."""
    svg = (Path(__file__).with_name("static") / "icon.svg").read_text(encoding="utf-8")
    release, mine = ("#%02x%02x%02x" % c for c in (RELEASE_HIGHLIGHT, HIGHLIGHT))
    return svg.replace(release, mine)
