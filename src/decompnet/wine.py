"""wine: how Linux and macOS run the Windows compilers wibo can't, those of the packs toolchains.toml marks
`wine = true` (Visual C++ 2017).

wibo runs Visual C++ 6.0 to 2010 by itself. 2017's cl.exe calls much more of Windows: under a patched wibo a hello
world compiled but exited 99, and `#include <windows.h>` failed without a word, where an unmodified wine 9.0 built
both targets correctly. So these compilers run through wine, which the user installs (it's big, and their system
keeps it updated); a pack that needs it says how to get it before it's downloaded (toolchains.runtime_error).

What's here is the app's own, never the user's ~/.wine:
- a wine prefix in decompdecomp's data folder (prefix), created once when the pack is installed (prepare: wineboot
  takes a few seconds to a minute), without the Mono and Gecko installers wine would offer;
- a wine server kept running for it (`wineserver -p`, start_server): a compile takes ~0.2 s instead of the ~2.7 s
  of starting wine each time. It's started with no inherited pipes and in a session of its own: a server holding
  a build's output pipe would keep the build "running" for as long as it lives (forks/project.py reads to the end).
The projects' tools/compile.py does the same by itself before each compile (the server may have been stopped since),
and passes every path to cl.exe as Z:\\...: cl.exe reads /Users/... as a switch. Windows runs the compilers natively.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from decompnet.hostenv import NO_WINDOW

# where wine is when it isn't on the PATH when the app was started with: Homebrew's folders (Apple Silicon's, then
# Intel's), MacPorts' (/opt/local), the system's, and Gcenx's Wine apps in Applications (Homebrew disabled its Wine
# casks in September 2026 over Gatekeeper; Gcenx's own tap, gcenx/wine, still has them)
MAC_APPS = ("Wine Stable", "Wine Staging", "Wine Devel", "Wine Crossover")
PACKAGE_BINS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", "/usr/bin")
APP_FOLDERS = ("/Applications", str(Path.home() / "Applications"))
PLACES = (*PACKAGE_BINS,
          *(f"{folder}/{app}.app/Contents/Resources/wine/bin" for folder in APP_FOLDERS for app in MAC_APPS))
# what wine is told: no debug output (it goes to the compiler's log), and no offer to install Mono or Gecko when the
# prefix is made (a dialog nobody answers: cl.exe needs neither)
QUIET = {"WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml="}
PREPARE_SECONDS = 600  # wineboot on a slow computer: a minute measured in a 2-core workspace


def needed() -> bool:
    """Whether this computer runs Windows compilers through wine (everything but Windows)."""
    return sys.platform != "win32"


def find() -> str | None:
    """The wine command: $DECOMPNET_WINE, else on the PATH, else in the usual places (PLACES)."""
    if (own := os.environ.get("DECOMPNET_WINE")) and Path(own).is_file():
        return own
    return shutil.which("wine") or shutil.which("wine", path=os.pathsep.join(PLACES))


def server(wine: str) -> str | None:
    """The wineserver that goes with `wine`: beside it (Homebrew links both, as do Linux packages), or beside the
    program it links to, else on the PATH."""
    for folder in (Path(wine).parent, Path(wine).resolve().parent):
        if (folder / "wineserver").is_file():
            return str(folder / "wineserver")
    return shutil.which("wineserver")


def install_steps() -> str:
    """How to get wine on this computer, in a sentence or two."""
    if sys.platform == "darwin":
        return ("On a Mac: download Wine Stable (wine-stable-...-osx64.tar.xz) from "
                "https://github.com/Gcenx/macOS_Wine_builds/releases, put Wine Stable.app in Applications, and open "
                "it once (Homebrew's wine-stable cask was disabled in September 2026), or install a wine port from "
                "https://github.com/Gcenx/macports-wine with MacPorts. Stable, Staging and Devel all work. On Apple "
                "Silicon wine runs through Rosetta 2.")
    return ("On Linux: install your distribution's wine package (Ubuntu and Debian: `sudo dpkg --add-architecture "
            "i386 && sudo apt update && sudo apt install wine wine32:i386`; Fedora: `sudo dnf install wine`; Arch: "
            "`sudo pacman -S wine`).")


def problem() -> str | None:
    """Why the Windows compilers that need wine can't run here, with what to do; None: they can."""
    if not needed():
        return None
    wine = find()
    if wine is None:
        return ("Visual C++ 2017 runs through wine on Linux and macOS, and this computer has no wine. "
                + install_steps() + " Then try again.")
    if server(wine) is None:
        return f"wine is at {wine}, but its wineserver isn't beside it or on the PATH: reinstall wine, then try again."
    from . import toolchains

    if toolchains.current_platform() == "macos-arm64" and not toolchains._intel_mac_runtime_ready():
        toolchains._intel_mac_runtime_ready.cache_clear()  # (installed since: the next try sees it)
        return ("wine's Mac builds are Intel programs: on Apple Silicon they need Rosetta 2. Run "
                "`softwareupdate --install-rosetta` in Terminal, accept Apple's license if you agree, then try again.")
    return None


def prefix() -> Path:
    """decompdecomp's own wine prefix (the C: drive and registry wine keeps), with its toolchains (as their
    downloads are: .downloads), so a scratch home's toolchains ($DECOMPNET_TOOLCHAINS) have their own."""
    from . import toolchains

    return toolchains.root() / ".wine"


def environment(where: Path | None = None) -> dict[str, str]:
    """The environment wine runs in: this computer's, with the app's prefix and QUIET."""
    return {**os.environ, "WINEPREFIX": str(where or prefix()), **QUIET}


def start_server(wine: str, where: Path | None = None) -> bool:
    """Keep a wine server running for the prefix (`wineserver -p`). One already running stays: the new one sees it
    and leaves (exit 2, in milliseconds). Returns whether one runs now."""
    ws = server(wine)
    if ws is None:
        return False
    try:
        code = subprocess.run([ws, "-p"], env=environment(where), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, start_new_session=True, timeout=30,
                              creationflags=NO_WINDOW).returncode
    except (OSError, subprocess.TimeoutExpired):
        return False
    return code in (0, 2)


def prepare(log=print, where: Path | None = None) -> Path:
    """The prefix, made if it isn't yet (wineboot), with its server running. Raises RuntimeError if wine can't."""
    if why := problem():
        raise RuntimeError(why)
    wine = find()
    where = where or prefix()
    if not (where / "system.reg").is_file():
        log(f"  preparing wine for the Windows compilers (once; up to a minute): its own prefix in {where}")
        where.parent.mkdir(parents=True, exist_ok=True)
        # its output to a file, not a pipe: the server and services wineboot starts outlive it, holding what it was
        # given. wine writes the registry (system.reg) when its server stops: that server, started by wineboot, stops
        # a few seconds after it, and is waited for (-w) before the one that stays starts
        quiet = {"stdin": subprocess.DEVNULL, "creationflags": NO_WINDOW}
        with tempfile.TemporaryFile() as said:
            try:
                done = subprocess.run([wine, "wineboot", "--init"], env=environment(where), stdout=said, stderr=said,
                                      timeout=PREPARE_SECONDS, **quiet)
                if (ws := server(wine)) is not None:
                    subprocess.run([ws, "-w"], env=environment(where), stdout=said, stderr=said, timeout=60, **quiet)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError(f"wine couldn't prepare its prefix in {where}: {error}") from None
            said.seek(0)
            tail = said.read().decode(errors="replace").strip()[-600:]
        if done.returncode or not (where / "system.reg").is_file():
            raise RuntimeError(f"wine couldn't prepare its prefix in {where} (wineboot exited with {done.returncode}): "
                               f"{tail}")
    start_server(wine, where)
    return where
