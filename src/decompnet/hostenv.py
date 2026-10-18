"""How decompdecomp reaches the Linux desktop around it: file paths the user types, opening a folder in the file
manager. (Links to other sites open in the user's browser from the window itself: gtkwindow.py.)

decompdecomp runs on Linux only. Windows (through WSL 2) and Docker were supported until 1.0.2 and removed in the
rewrite: nobody tested them, and most of their code served the agent bridge that went with it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def local_path(text: str) -> Path:
    """A file path the user gave (in a form, or pasted with quotes around it), as a path."""
    return Path(text.strip().strip('"').strip("'")).expanduser()


def shown_path(path: Path | str) -> str:
    """A path of decompdecomp's as the user reads it: the home folder as ~."""
    shown, home = str(path), str(Path.home())
    return "~" + shown[len(home):] if shown == home or shown.startswith(home + "/") else shown


def open_folder(path: Path) -> str | None:
    """Show a folder in the user's file manager. Returns why it couldn't, or None."""
    try:
        subprocess.Popen(["xdg-open", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return f"Open this folder in your file manager: {path}"
    return None
