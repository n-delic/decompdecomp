"""Export: the game as something to keep and share, without decompdecomp (docs/PORTING.md, "Export").

- "app": the native build (build.py) as a program for this computer's system: on macOS a universal app (Game.app,
  for Apple Silicon and Intel Macs) that asks for the player's ROM once, keeps the saves in its own Application
  Support folder and opens the game in the browser; on Linux and Windows a folder with the program and play.sh /
  play.bat.
- "window": on macOS, the same app with the game in a window of its own (SDL2, consoles/n64/port_wasm/window/sdl.c)
  instead of the browser: SDL2 travels in the app (Contents/Frameworks, toolchains.toml `sdl2`), and the game is the
  app's own process (Contents/MacOS/game), so it has the app's name and icon in the Dock and its menu.
- "web": the browser build (web.py): a folder with index.html and what it loads, to put on any web server (it
  needs http, not a file: page); the player chooses their ROM in the page.
- "miyoo": the native build for the Miyoo Mini family (miyoo.py): the folders to copy to its SD card (App/ for the
  stock OS and Onion, Tools/ for MinUI), with the project's ROM beside the program.

The app and the browser build don't hold the ROM: each asks the player for their own. The Miyoo Mini's folder does,
copied from the project, since a handheld has nothing to ask with: it's the user's own card. Both go to the save repository's port/wasm/export/,
which git ignores (they're built from the game: game data). Exporting again replaces that kind's folder.
"""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from ...network import NetworkError

KINDS = {"app": "a standalone app", "window": "a standalone app with its own window", "web": "a browser build", "miyoo": "a Miyoo Mini SD card folder"}
PACKING = "Packing the export"


def _name(title: str) -> str:
    """A file name from the game's title: letters, digits, spaces and a few marks."""
    name = re.sub(r"[^\w .()-]+", "", title).strip(" .")
    return name[:60] or "Game"


def _ident(title: str) -> str:
    return "com.decompdecomp.port." + (re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower() or "game")


MAC_LAUNCHER = r"""#!/bin/bash
# {title}: the game, from your own ROM. It runs on this Mac only and shows in your browser. The app's own program
# (Contents/MacOS/play, MAC_STUB) runs this script, which sits beside the game in Contents/Resources.
here="$(cd "$(dirname "$0")" && pwd)"
support="$HOME/Library/Application Support/{ident}"
mkdir -p "$support"
rom="$(cat "$support/rom-path" 2>/dev/null)"
if [ ! -f "$rom" ]; then
  rom="$(osascript -e 'POSIX path of (choose file with prompt "Choose your ROM of {title}")' 2>/dev/null)" || exit 0
  printf '%s' "$rom" > "$support/rom-path"
fi
port=18642
while (echo > "/dev/tcp/127.0.0.1/$port") 2>/dev/null; do port=$((port + 1)); done
PORT_LIVE=$port PORT_SAVE="$support/game.eep" "$here/game" "$rom" 0 > "$support/play.log" 2>&1 &
game=$!
trap 'kill $game 2>/dev/null' EXIT INT TERM
for _ in $(seq 100); do (echo > "/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
if ! kill -0 $game 2>/dev/null; then
  rm -f "$support/rom-path"
  osascript -e 'display alert "{title} stopped as it started" message "See '"$support"'/play.log. Next time you open it, it asks for your ROM again."'
  exit 1
fi
open "http://127.0.0.1:$port/"
wait $game
"""

MAC_WINDOW_LAUNCHER = r"""#!/bin/bash
# {title}: the game, from your own ROM, in a window of its own. The app's program (Contents/MacOS/play, MAC_STUB) runs
# this script, which asks for the ROM once and then becomes the game (exec), so the game is the app's own process.
here="$(cd "$(dirname "$0")" && pwd)"
support="$HOME/Library/Application Support/{ident}"
mkdir -p "$support"
rom="$(cat "$support/rom-path" 2>/dev/null)"
if [ ! -f "$rom" ]; then
  rom="$(osascript -e 'POSIX path of (choose file with prompt "Choose your ROM of {title}")' 2>/dev/null)" || exit 0
  printf '%s' "$rom" > "$support/rom-path"
fi
case "$(head -c 4 "$rom" | od -An -tx1 | tr -d ' \n')" in
  80371240|37804012|40123780) ;;  # .z64, .v64, .n64
  *) rm -f "$support/rom-path"
     osascript -e 'display alert "That file isn'"'"'t an N64 ROM" message "Next time you open {title}, it asks for your ROM again."'
     exit 1 ;;
esac
cd "$support"
PORT_WINDOW=1 PORT_TITLE="{title}" PORT_SAVE="$support/game.eep" exec "$here/../MacOS/game" "$rom" 0 2> "$support/play.log"
"""

# The app's program: a compiled stub that runs Resources/play.sh. A script can't be the app's program: macOS keeps a
# script's signature in extended attributes, which a zip, a download or AirDrop drops, and then Gatekeeper calls the
# app damaged. A compiled program carries its signature inside, so the signed bundle stays signed wherever it goes.
MAC_STUB = r"""#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(void) {
    char exe[PATH_MAX], real[PATH_MAX], script[PATH_MAX + 32];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, real)) return 1;
    snprintf(script, sizeof script, "%s/../Resources/play.sh", dirname(real));
    char *argv[] = {"/bin/bash", script, NULL};
    execv("/bin/bash", argv);
    perror("/bin/bash");
    return 1;
}
"""

MAC_HOW_TO_OPEN = """{title} for macOS
{underline}

{name}.app runs on Apple Silicon and Intel Macs with macOS 11 or later. It holds no ROM: the first time it opens, it
asks for your own ROM of the game.

The app isn't signed by an Apple developer, so a Mac that got it by download, AirDrop or a USB stick says it can't be
opened the first time. To open it anyway, once:
- Control-click (or right-click) {name}.app and choose Open, then Open again; or, on macOS 15 and later, try to open
  it, then go to System Settings > Privacy & Security and click Open Anyway beside its name.
- Or in Terminal: xattr -dr com.apple.quarantine "/path/to/{name}.app"

After that it opens like any other app.
"""

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>{title}</string>
  <key>CFBundleDisplayName</key><string>{title}</string>
  <key>CFBundleIdentifier</key><string>{ident}</string>
  <key>CFBundleExecutable</key><string>play</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
"""

SH_LAUNCHER = r"""#!/bin/sh
# {title}: the game, from your own ROM; it shows in your browser. Usage: ./play.sh [ROM] (it remembers the ROM).
here="$(cd "$(dirname "$0")" && pwd)"
rom="${{1:-$(cat "$here/rom-path" 2>/dev/null)}}"
if [ ! -f "$rom" ]; then echo "Usage: $0 path/to/your/rom.z64"; exit 2; fi
printf '%s' "$rom" > "$here/rom-path"
port=18642
PORT_LIVE=$port PORT_SAVE="$here/game.eep" "$here/game" "$rom" 0 &
game=$!
trap 'kill $game 2>/dev/null' EXIT INT TERM
sleep 1
(xdg-open "http://127.0.0.1:$port/" || echo "Open http://127.0.0.1:$port/ in your browser") >/dev/null 2>&1
wait $game
"""

BAT_LAUNCHER = """@echo off
rem {title}: the game, from your own ROM; it shows in your browser. Usage: play.bat [ROM] (it remembers the ROM).
set here=%~dp0
set rom=%~1
if "%rom%"=="" if exist "%here%rom-path.txt" set /p rom=<"%here%rom-path.txt"
if not exist "%rom%" (echo Usage: play.bat path\\to\\your\\rom.z64 & exit /b 2)
echo %rom%>"%here%rom-path.txt"
set PORT_LIVE=18642
set PORT_SAVE=%here%game.eep
start "" http://127.0.0.1:18642/
"%here%game.exe" "%rom%" 0
"""

README_WEB_SIMD = """
The files ending in -simd.wasm are a faster copy of the game for browsers with WebAssembly SIMD (Chrome 91, Firefox
89, Safari 16.4 and later): the page plays them there, and the plain ones in older browsers. Add ?nosimd to the
page's address to play the plain ones anyway.
"""

README_WEB = """{title}, for a web browser
======================

This folder plays the game in a web browser, from your own copy of its ROM: nothing in it is the ROM, and the page
asks you for yours (it stays on your computer; the browser remembers it for next time, and keeps the game's saves).

Browsers only load WebAssembly over http, so serve this folder rather than opening index.html as a file. For
example, in this folder:

    python3 -m http.server 8000

then open http://localhost:8000/ in a current browser. Chrome and Edge 137 and later and Safari 27 play it with
WebAssembly JavaScript Promise Integration; other browsers play app-asyncify.wasm, the same game made to pause
without it, which can be slower. It can go on any static web server the same way.

Keys: arrows steer, X is A, Z is B, Enter is Start, Space is Z, Q and W are L and R, I/J/K/L are the C buttons; a
gamepad works too.
"""


def _write(path: Path, text: str, executable: bool = False) -> None:
    path.write_text(text, encoding="utf-8", newline="\n" if not path.suffix == ".bat" else "\r\n")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _mac_stub(path: Path) -> None:
    """Compile MAC_STUB to `path` for both kinds of Mac, with the compiler that builds the host (build.py)."""
    from . import build
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "play.c"
        source.write_text(MAC_STUB, encoding="utf-8")
        done = subprocess.run([*build.host_compiler(None), *build.host_arch_flags(True), "-O2", "-o", str(path),
                               str(source)], capture_output=True, text=True, timeout=120)
    if done.returncode:
        raise NetworkError(f"compiling the app's launcher failed: {done.stderr.strip()[-400:]}")


def _sign(app: Path) -> None:
    """Sign the game, then the app around it, with an ad-hoc signature (no Apple developer identity): the bundle is
    sealed, so a Mac can tell it arrived whole, and Gatekeeper offers Open Anyway instead of calling it damaged."""
    code = [app / "Contents" / "Frameworks" / "libSDL2-2.0.0.dylib", app / "Contents" / "MacOS" / "game",
            app / "Contents" / "Resources" / "game"]
    for target in [*(c for c in code if c.is_file()), app]:  # inside out: what the bundle holds, then the bundle
        done = subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(target)],
                              capture_output=True, text=True, timeout=120)
        if done.returncode:
            raise NetworkError(f"signing the app failed: {done.stderr.strip()[-400:]}")


def package_app(program: Path, dest: Path, title: str, platform: str = sys.platform, sdl: Path | None = None) -> Path:
    """The native program as an app for `platform` in `dest` (replaced): what the player opens. With `sdl` (the
    `sdl2` toolchain's folder, macOS only) the game plays in a window of its own, SDL2 inside the app."""
    name, ident = _name(title), _ident(title)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if platform == "darwin":
        app = dest / f"{name}.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        launcher = MAC_WINDOW_LAUNCHER if sdl else MAC_LAUNCHER
        if sdl:  # the game is the app's own process, its library beside it with its licence
            shutil.copy2(program, app / "Contents" / "MacOS" / "game")
            (app / "Contents" / "Frameworks").mkdir()
            shutil.copy2(sdl / "libSDL2-2.0.0.dylib", app / "Contents" / "Frameworks" / "libSDL2-2.0.0.dylib")
            shutil.copy2(sdl / "LICENSE.txt", app / "Contents" / "Resources" / "SDL2-LICENSE.txt")
        else:
            shutil.copy2(program, app / "Contents" / "Resources" / "game")
        _write(app / "Contents" / "Resources" / "play.sh",
               launcher.format(title=title.replace('"', "'"), ident=ident), executable=True)
        _write(app / "Contents" / "Info.plist", PLIST.format(title=title.replace("&", "and").replace("<", ""),
                                                            ident=ident))
        heading = f"{title} for macOS"
        _write(dest / "How to open on a Mac.txt", MAC_HOW_TO_OPEN.format(title=title, name=name,
                                                                         underline="=" * len(heading)))
        if platform == sys.platform:
            _mac_stub(app / "Contents" / "MacOS" / "play")
            _sign(app)
        else:  # packed elsewhere (tests): the script itself, which a Mac runs but can't keep signed
            shutil.copy2(app / "Contents" / "Resources" / "play.sh", app / "Contents" / "MacOS" / "play")
        return app
    exe = program.with_suffix(".exe") if platform == "win32" and program.with_suffix(".exe").is_file() else program
    shutil.copy2(exe, dest / ("game.exe" if platform == "win32" else "game"))
    if platform == "win32":
        _write(dest / "play.bat", BAT_LAUNCHER.format(title=title))
    else:
        _write(dest / "play.sh", SH_LAUNCHER.format(title=title), executable=True)
    return dest


def export(net, slug: str, kind: str, log=print, simd: bool = False) -> dict:
    """Build the project's game (only what changed) and export it as `kind` ("app", "web" or "miyoo"): {"kind",
    "folder", "path" (what to open), "files"}. `simd`: a browser build also carries the faster build for browsers
    with WebAssembly SIMD (web.SIMD), which the page plays where it can."""
    from ... import toolchains
    from . import build, miyoo, play, web

    if kind not in KINDS:
        raise NetworkError(f"export as one of: {', '.join(KINDS)}")
    repo, cfg, rom_file = play.game(net, slug)
    leave_out = play.library(net, slug, log)
    console = cfg.get("platform", "n64")
    title = cfg.get("rom", {}).get("title") or slug
    out = repo / "port" / "wasm" / "export"
    out.mkdir(parents=True, exist_ok=True)
    build._write(out / ".gitignore", "*\n")  # built from the game: never committed
    if kind == "window" and sys.platform != "darwin":
        raise NetworkError("An app with its own window is made on a Mac for now; on Linux and Windows, export the "
                           "standalone app (it plays in the browser)")
    if kind in ("app", "window"):
        sdl = toolchains.ensure("sdl2", log=log) if kind == "window" else None
        steps = (*build.STEPS, PACKING)
        # On macOS the app runs on Apple Silicon and Intel Macs alike, whichever kind exported it.
        program = build.build(repo, console=console, log=log, steps=steps, leave_out=leave_out, universal=True)
        build.Progress(log, steps).start(PACKING)
        log(f"packing {title} as an app for " + ("Apple Silicon and Intel Macs" if sys.platform == "darwin"
                                                  else "this computer") + (", in a window of its own" if sdl else ""))
        path = package_app(program, out / kind, title, sdl=sdl)
        folder = out / kind
    elif kind == "miyoo":
        steps = (*miyoo.STEPS, PACKING)
        program = miyoo.build(repo, console=console, log=log, steps=steps, leave_out=leave_out)
        build.Progress(log, steps).start(PACKING)
        log("packing the Miyoo Mini's SD card folders")
        rom = cfg.get("rom", {})
        version = " ".join(x for x in (rom.get("title"), rom.get("region"), rom.get("sha1") and f"SHA-1 {rom['sha1']}") if x)
        folder = miyoo.package(program, out / "miyoo", title, version, rom_file)
        path = folder / "README.txt"
        steps_for_player = miyoo.instructions(folder, title)
    else:
        steps = (*web.STEPS, PACKING)
        folder = web.build(repo, console=console, log=log, steps=steps, leave_out=leave_out, simd=simd)
        build.Progress(log, steps).start(PACKING)
        log("copying the browser build")
        folder = web.export(folder, out / "web")
        _write(folder / "README.txt", README_WEB.format(title=title) + (README_WEB_SIMD if simd else ""))
        path = folder / "index.html"
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    size = sum((folder / f).stat().st_size for f in files)
    log(f"exported {KINDS[kind]} to {path} ({len(files)} files, {size / 1e6:.1f} MB)"
        + ("; your ROM is in it" if kind == "miyoo" and any(f.endswith("/rom.z64") for f in files)
           else "; the ROM isn't in it: the player chooses their own"))
    if kind == "web":
        steps_for_player = [f"Serve {folder} over http and open it in Chrome or Edge (its README.txt says how): "
                            "browsers load WebAssembly only over http.", "The page asks the player for their ROM."]
        if simd:
            steps_for_player.append("Browsers with WebAssembly SIMD (Chrome 91, Firefox 89, Safari 16.4 and later) play "
                                    "the faster build in it; older ones the plain build, also in it.")
    elif kind in ("app", "window"):
        steps_for_player = [f"Open {path} to play: it asks for your ROM the first time."]
        if kind == "window":
            steps_for_player.append("Keys: arrows steer, X is A, Z is B, Enter is Start, Space is Z, Q/W are L/R, "
                                    "I/J/K/L the C buttons; a gamepad works too. F toggles full screen.")
        if sys.platform == "darwin":
            steps_for_player.append("On another Mac, the first open needs Control-click > Open (or System Settings > "
                                    "Privacy & Security > Open Anyway): the app isn't signed by an Apple developer. "
                                    "\"How to open on a Mac.txt\" beside it says so for the player.")
    return {"kind": kind, "folder": str(folder), "path": str(path), "files": files, "bytes": size,
            "system": {"app": sys.platform, "window": sys.platform, "web": "browser"}.get(kind, kind), "instructions": steps_for_player}
