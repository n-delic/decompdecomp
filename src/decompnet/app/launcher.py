"""Add decompdecomp to the apps menu (Linux): a .desktop entry that runs `decomp`, which opens the app's window.

The entry is named like the .deb's and the .rpm's (decompdecomp.desktop), so a desktop shows one decompdecomp even
when a package is installed too: the user's own entry takes the place of the system's. The entry older versions made
(decomp-wtf.desktop) goes: next to the package's, the owner's menu showed decompdecomp twice.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import edition

ENTRY_NAME = "decompdecomp"
OLD_ENTRY_NAME = "decomp-wtf"


def _decomp_exe() -> str:
    return shutil.which("decomp") or str(Path(sys.argv[0]).resolve())


def install_launcher() -> str:
    """Put decompdecomp in the apps menu (a .desktop entry and its icon). A development copy gets none: it's started
    from its checkout."""
    if edition.DEV:
        return "a development copy gets no launcher: run it with `uv run decomp` in the checkout"
    share = Path.home() / ".local" / "share"
    icons = share / "icons" / "hicolor" / "scalable" / "apps"
    apps = share / "applications"
    icons.mkdir(parents=True, exist_ok=True)
    apps.mkdir(parents=True, exist_ok=True)
    (icons / f"{ENTRY_NAME}.svg").write_text(edition.icon_svg(), encoding="utf-8")
    entry = apps / f"{ENTRY_NAME}.desktop"
    entry.write_text("[Desktop Entry]\nType=Application\nName=decompdecomp\n"
                     "Comment=AI agents decompile the games you own, byte for byte\n"
                     f"Exec=\"{_decomp_exe()}\"\nIcon={ENTRY_NAME}\nTerminal=false\nCategories=Development;\n"
                     f"StartupWMClass={edition.WINDOW_CLASS}\n", encoding="utf-8")
    os.chmod(entry, 0o755)
    for old in (apps / f"{OLD_ENTRY_NAME}.desktop", icons / f"{OLD_ENTRY_NAME}.svg"):
        old.unlink(missing_ok=True)
    return f"added decompdecomp to your apps menu ({entry})"
