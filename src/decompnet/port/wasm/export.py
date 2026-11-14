"""Export: the game as something to keep and share, without decompdecomp (docs/PORTING.md, "Export").

- "app": the native build (build.py) as a program for this computer's system: on macOS an app (Game.app) that asks
  for the player's ROM once, keeps the saves in its own Application Support folder and opens the game in the
  browser; on Linux and Windows a folder with the program and play.sh / play.bat.
- "web": the browser build (web.py): a folder with index.html and what it loads, to put on any web server (it
  needs http, not a file: page); the player chooses their ROM in the page.

Neither holds the ROM: each asks the player for their own. Both go to the save repository's port/wasm/export/,
which git ignores (they're built from the game: game data). Exporting again replaces that kind's folder.
"""

from __future__ import annotations

import re
import shutil
import stat
import sys
from pathlib import Path

from ...network import NetworkError

KINDS = {"app": "a standalone app", "web": "a browser build"}
PACKING = "Packing the export"


def _name(title: str) -> str:
    """A file name from the game's title: letters, digits, spaces and a few marks."""
    name = re.sub(r"[^\w .()-]+", "", title).strip(" .")
    return name[:60] or "Game"


def _ident(title: str) -> str:
    return "com.decompdecomp.port." + (re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower() or "game")


MAC_LAUNCHER = r"""#!/bin/bash
# {title}: the game, from your own ROM. It runs on this Mac only and shows in your browser.
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
PORT_LIVE=$port PORT_SAVE="$support/game.eep" "$here/../Resources/game" "$rom" 0 > "$support/play.log" 2>&1 &
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


def package_app(program: Path, dest: Path, title: str, platform: str = sys.platform) -> Path:
    """The native program as an app for `platform` in `dest` (replaced): what the player opens."""
    name, ident = _name(title), _ident(title)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if platform == "darwin":
        app = dest / f"{name}.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        shutil.copy2(program, app / "Contents" / "Resources" / "game")
        _write(app / "Contents" / "MacOS" / "play", MAC_LAUNCHER.format(title=title.replace('"', "'"), ident=ident),
               executable=True)
        _write(app / "Contents" / "Info.plist", PLIST.format(title=title.replace("&", "and").replace("<", ""),
                                                            ident=ident))
        return app
    exe = program.with_suffix(".exe") if platform == "win32" and program.with_suffix(".exe").is_file() else program
    shutil.copy2(exe, dest / ("game.exe" if platform == "win32" else "game"))
    if platform == "win32":
        _write(dest / "play.bat", BAT_LAUNCHER.format(title=title))
    else:
        _write(dest / "play.sh", SH_LAUNCHER.format(title=title), executable=True)
    return dest


def export(net, slug: str, kind: str, log=print) -> dict:
    """Build the project's game (only what changed) and export it as `kind` ("app" or "web"): {"kind", "folder",
    "path" (what to open), "files"}."""
    from . import build, play, web

    if kind not in KINDS:
        raise NetworkError(f"export as one of: {', '.join(KINDS)}")
    repo, cfg, _rom = play.game(net, slug)
    leave_out = play.library(net, slug, log)
    console = cfg.get("platform", "n64")
    title = cfg.get("rom", {}).get("title") or slug
    out = repo / "port" / "wasm" / "export"
    out.mkdir(parents=True, exist_ok=True)
    build._write(out / ".gitignore", "*\n")  # built from the game: never committed
    if kind == "app":
        steps = (*build.STEPS, PACKING)
        program = build.build(repo, console=console, log=log, steps=steps, leave_out=leave_out)
        build.Progress(log, steps).start(PACKING)
        log(f"packing {title} as an app for this computer")
        path = package_app(program, out / "app", title)
        folder = out / "app"
    else:
        steps = (*web.STEPS, PACKING)
        folder = web.build(repo, console=console, log=log, steps=steps, leave_out=leave_out)
        build.Progress(log, steps).start(PACKING)
        log("copying the browser build")
        folder = web.export(folder, out / "web")
        _write(folder / "README.txt", README_WEB.format(title=title))
        path = folder / "index.html"
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    size = sum((folder / f).stat().st_size for f in files)
    log(f"exported {KINDS[kind]} to {path} ({len(files)} files, {size / 1e6:.1f} MB); the ROM isn't in it: "
        "the player chooses their own")
    return {"kind": kind, "folder": str(folder), "path": str(path), "files": files, "bytes": size,
            "system": sys.platform if kind == "app" else "browser"}
