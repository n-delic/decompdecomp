"""How decompdecomp reaches the Linux desktop around it: file paths the user types, opening a folder in the file
manager. (Links to other sites open in the user's browser from the window itself: gtkwindow.py.)

decompdecomp runs on Linux, and on Windows as the Linux install inside a WSL 2 distribution of the user's own (from
1.0.4). 1.0.2's own Windows installer, its distribution and Docker were removed in the rewrite: nobody tested them,
and most of their code served the agent bridge that went with it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

KERNEL_RELEASE = Path("/proc/sys/kernel/osrelease")  # "...-microsoft-standard-WSL2" inside WSL
# 1.0.2 on Windows lived in a WSL distribution of its own; it updates itself into this version, which doesn't run there
WSL_DISTRO, WSL_USER = "decomp-wtf", "decomp"
OLD_VERSION_SPEC = "decompnet @ https://github.com/n-delic/decompdecomp/archive/refs/tags/v1.0.2-alpha.tar.gz"
# (the last line turns off 1.0.2's tray icon: with the system packages this version's window uses, loading it put
# the system's old websockets ahead of uvicorn's, and 1.0.2 quit at start: GitHub #11)
WSL_GO_BACK = (f"wsl --terminate {WSL_DISTRO}\n"
               f"wsl -d {WSL_DISTRO} -u {WSL_USER} --exec /home/{WSL_USER}/.local/bin/uv tool install --force "
               f'--python 3.12 "{OLD_VERSION_SPEC}"\n'
               f"wsl -d {WSL_DISTRO} -u root --exec sh -c \"echo 'export DECOMP_TRAY=0' >> /etc/profile.d/{WSL_DISTRO}.sh\"")


def in_old_windows_install() -> bool:
    """Running in the WSL distribution 1.0.2's Windows installer made, which 1.0.2 updated into this version and where
    it doesn't run. Not in a WSL distribution of the user's own, where the Linux install is supported (README)."""
    try:
        wsl = "microsoft" in KERNEL_RELEASE.read_text(encoding="utf-8").lower()
    except OSError:
        return False
    return wsl and (os.environ.get("WSL_DISTRO_NAME") == WSL_DISTRO or Path.home() == Path("/home") / WSL_USER)


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
