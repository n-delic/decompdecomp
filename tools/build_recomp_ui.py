"""Build the optional recomp-ui menu model (MIT) from its pinned public source, for the game's own window.

The window host (consoles/n64/port_wasm/window/recomp_ui.h) opens the library it makes at run time when PORT_RECOMP_UI
names it, and takes the settings menu's navigation, pages and Quit confirmation from recomp-ui, the shared in-game
menu of the recomp ports; without it the built-in menu runs. Three plain C files, no ImGui, no GPU, no other library:
the system C compiler is all it needs (as the host's own build does). Where the user's own copy is, and the pin, are
in this file: change the commit here and in docs/PORTING.md together."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from decompnet import limits, toolchains

URL = "https://github.com/RetroPortingToolKit/recomp-ui.git"
REVISION = "16e2c867e474648e523e2cd21eca813ebe58c795"
SOURCES = ("src/common/recomp_runtime_ui.c", "src/common/launcher_theme_overrides.c",
           "src/common/recomp_runtime_settings.c")
NAME = "libdecompnet-recomp-ui" + (".dylib" if sys.platform == "darwin" else ".so")


def run(*args, cwd=None):
    subprocess.run(limits.capped([str(arg) for arg in args]), cwd=cwd, check=True, timeout=600)


def library() -> Path | None:
    """The built library, if there is one: the configured override, else the toolchains folder's copy."""
    override = os.environ.get("DECOMPNET_RECOMP_UI")
    candidates = (Path(override).expanduser(),) if override else (toolchains.root() / "recomp-ui" / NAME,)
    return next((path for path in candidates if path.is_absolute() and path.is_file()), None)


def build(output: Path) -> Path:
    if sys.platform == "win32":
        raise SystemExit("The window host with recomp-ui's menu is not on Windows yet (ROADMAP.md).")
    output = output.expanduser().resolve()
    source = output / ".build" / "recomp-ui"
    source.mkdir(parents=True, exist_ok=True)
    if not (source / ".git").exists():
        run("git", "init", "-q", cwd=source)
        run("git", "remote", "add", "origin", URL, cwd=source)
    run("git", "fetch", "--depth=1", URL, REVISION, cwd=source)
    run("git", "checkout", "--detach", REVISION, cwd=source)
    pending = output / ("pending-" + NAME)
    run("cc", "-shared", "-fPIC", "-O2", "-I", source / "src", "-I", source / "src" / "common",
        *(source / name for name in SOURCES), "-o", pending)
    licenses = output / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(source / "LICENSE", licenses / "recomp-ui-LICENSE")
    library = output / NAME
    pending.replace(library)
    return library


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, nargs="?", default=toolchains.root() / "recomp-ui",
                        help="Folder for the library")
    built = build(parser.parse_args().destination)
    print(f"Built {built}. Play opens the window with it when it is in the toolchains folder; for a custom folder, "
          f"set DECOMPNET_RECOMP_UI={built}.")


if __name__ == "__main__":
    main()
