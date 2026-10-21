"""How decompdecomp reaches the host desktop: file paths the user types and opening a folder in the file manager.
(Links to other sites open in the user's browser from the window itself.)"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

KERNEL_RELEASE = Path("/proc/sys/kernel/osrelease")  # "...-microsoft-standard-WSL2" inside WSL
# 1.0.2 on Windows lived in a WSL distribution of its own; it updates itself into this version, which doesn't run there
WSL_DISTRO, WSL_USER = "decomp-wtf", "decomp"
OLD_VERSION_SPEC = "decompnet @ https://github.com/n-delic/decompdecomp/archive/refs/tags/v1.0.2-alpha.tar.gz"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # background tools must not flash consoles on Windows


def console_python(exe: str | None = None) -> str:
    """The Python to run build steps with: on Windows the app runs as pythonw.exe, which has no console, so every
    console program a build step under it starts (a compiler, a linker) opens a console window of its own. The
    python.exe beside it shares the build's hidden console instead."""
    exe = exe or sys.executable
    path = Path(exe)
    if path.name.lower() == "pythonw.exe" and (console := path.with_name("python.exe")).is_file():
        return str(console)
    return exe
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


def find_uv() -> str | None:
    """Find uv from a desktop launch too, before its bin directory has reached the desktop's PATH."""
    found = shutil.which("uv")
    if found:
        return found
    name = "uv.exe" if sys.platform == "win32" else "uv"
    for folder in (Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"):
        if (folder / name).is_file():
            return str(folder / name)
    return None


def local_path(text: str) -> Path:
    """A file path the user gave (in a form, or pasted with quotes around it), as a path."""
    return Path(text.strip().strip('"').strip("'")).expanduser()


def shown_path(path: Path | str) -> str:
    """A path of decompdecomp's as the user reads it: the home folder as ~."""
    shown, home = str(path), str(Path.home())
    return "~" + shown[len(home):] if shown == home or shown.startswith((home + "/", home + "\\")) else shown


def stop_process_tree(proc: subprocess.Popen) -> None:
    """Stop a cancelled tool and its children, which can otherwise keep the user's input file open on Windows."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, creationflags=NO_WINDOW)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def open_folder(path: Path) -> str | None:
    """Show a folder in the user's file manager. Returns why it couldn't, or None."""
    try:
        opener = "explorer.exe" if sys.platform == "win32" else "xdg-open"
        subprocess.Popen([opener, str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return f"Open this folder in your file manager: {path}"
    return None
