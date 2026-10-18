"""How decompdecomp reaches the Linux desktop around it: file paths the user types, opening a folder in the file
manager. (Links to other sites open in the user's browser from the window itself: gtkwindow.py.)

decompdecomp runs on Linux only. Windows (through WSL 2) and Docker were supported until 1.0.2 and removed in the
rewrite: nobody tested them, and most of their code served the agent bridge that went with it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

KERNEL_RELEASE = Path("/proc/sys/kernel/osrelease")  # "...-microsoft-standard-WSL2" inside WSL
# 1.0.2 on Windows lived in a WSL distribution of its own; it updates itself into this version, which doesn't run there
WSL_DISTRO, WSL_USER = "decomp-wtf", "decomp"
OLD_VERSION_SPEC = "decompnet @ https://github.com/n-delic/decompdecomp/archive/refs/tags/v1.0.2-alpha.tar.gz"
WSL_GO_BACK = (f"wsl --terminate {WSL_DISTRO}\n"
               f"wsl -d {WSL_DISTRO} -u {WSL_USER} --exec /home/{WSL_USER}/.local/bin/uv tool install --force "
               f'--python 3.12 "{OLD_VERSION_SPEC}"')


def in_wsl() -> bool:
    """Running inside Windows' WSL, where this version isn't supported (1.0.4 brings Windows back)."""
    try:
        return "microsoft" in KERNEL_RELEASE.read_text(encoding="utf-8").lower()
    except OSError:
        return False


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
