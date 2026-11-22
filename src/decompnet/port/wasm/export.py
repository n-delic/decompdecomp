"""Export: the game as something to keep and share, without decompdecomp (docs/PORTING.md, "Export").

- "app": the native build (build.py) as a program for this computer's system: on macOS a universal app (Game.app,
  for Apple Silicon and Intel Macs) that asks for the player's ROM once, keeps the saves in its own Application
  Support folder and opens the game in the browser; on Linux and Windows a folder with the program and play.sh /
  play.bat.
- "window": on macOS, the same game in a window of its own (SDL3, consoles/n64/port_wasm/window/sdl.c) instead of
  the browser. The app is the game itself (Contents/MacOS/game): no script and no AppleScript. It reads its settings
  from Contents/Resources/port.env, asks for the ROM in SDL3's own file dialog and remembers it, and keeps its saves
  and log in its Application Support folder; SDL3 travels in the app (Contents/Frameworks, toolchains.toml `sdl3`).
- "web": the browser build (web.py): a folder with index.html and what it loads, to put on any web server (it
  needs http, not a file: page); the player chooses their ROM in the page.
- "ios": the browser build bundled in an offline iPhone/iPad Xcode project (ios.py), signed by the player.
- "miyoo": the native build for the Miyoo Mini family (miyoo.py): the folders to copy to its SD card (App/ for the
  stock OS and Onion, Tools/ for MinUI), with the project's ROM beside the program.
- "steamdeck": the native build for the Steam Deck (steamdeck.py), in a window of its own (SDL3, full screen): a
  folder to copy to the Deck and add to Steam as a non-Steam game, with the project's ROM beside the program.

The app and the browser build don't hold the ROM: each asks the player for their own. The Miyoo Mini's and the Steam
Deck's folders do, copied from the project, since a handheld has nothing to ask with: it's the user's own device. Both go to the save repository's port/wasm/export/,
which git ignores (they're built from the game: game data). Exporting again replaces that kind's folder.
"""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
import sys
import tempfile
from functools import wraps
from pathlib import Path

from ...network import NetworkError

KINDS = {"app": "a standalone app", "window": "a standalone app with its own window", "web": "a browser build", "miyoo": "a Miyoo Mini SD card folder", "steamdeck": "a Steam Deck folder", "ios": "an iPhone/iPad Xcode project"}
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

# The window app's settings, read by the game itself when it is opened with no ROM named (live.c, port_live_app).
MAC_WINDOW_SETTINGS = """PORT_WINDOW=1
PORT_TITLE={title}
PORT_APP={ident}
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
  <key>CFBundleExecutable</key><string>{program}</string>
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
    code = [app / "Contents" / "Frameworks" / "libSDL3.0.dylib", app / "Contents" / "Resources" / "game"]
    for target in [*(c for c in code if c.is_file()), app]:  # inside out: what the bundle holds, then the bundle
        done = subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(target)],
                              capture_output=True, text=True, timeout=120)
        if done.returncode:
            raise NetworkError(f"signing the app failed: {done.stderr.strip()[-400:]}")


def package_app(program: Path, dest: Path, title: str, platform: str = sys.platform, sdl: Path | None = None) -> Path:
    """The native program as an app for `platform` in `dest` (replaced): what the player opens. With `sdl` (the
    `sdl3` toolchain's folder, macOS only) the app is the game itself, in a window of its own, SDL3 inside it."""
    name, ident = _name(title), _ident(title)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if platform == "darwin":
        app = dest / f"{name}.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        if sdl:  # the app's program is the game, its library and its licence beside it, its settings in port.env
            shutil.copy2(program, app / "Contents" / "MacOS" / "game")
            (app / "Contents" / "Frameworks").mkdir()
            shutil.copy2(sdl / "libSDL3.0.dylib", app / "Contents" / "Frameworks" / "libSDL3.0.dylib")
            shutil.copy2(sdl / "LICENSE.txt", app / "Contents" / "Resources" / "SDL3-LICENSE.txt")
            _write(app / "Contents" / "Resources" / "port.env",
                   MAC_WINDOW_SETTINGS.format(title=" ".join(title.split()), ident=ident))
        else:
            shutil.copy2(program, app / "Contents" / "Resources" / "game")
            _write(app / "Contents" / "Resources" / "play.sh",
                   MAC_LAUNCHER.format(title=title.replace('"', "'"), ident=ident), executable=True)
        _write(app / "Contents" / "Info.plist", PLIST.format(title=title.replace("&", "and").replace("<", ""),
                                                            ident=ident, program="game" if sdl else "play"))
        heading = f"{title} for macOS"
        _write(dest / "How to open on a Mac.txt", MAC_HOW_TO_OPEN.format(title=title, name=name,
                                                                         underline="=" * len(heading)))
        if platform == sys.platform:
            if not sdl:
                _mac_stub(app / "Contents" / "MacOS" / "play")
            _sign(app)
        elif not sdl:  # packed elsewhere (tests): the script itself, which a Mac runs but can't keep signed
            shutil.copy2(app / "Contents" / "Resources" / "play.sh", app / "Contents" / "MacOS" / "play")
        return app
    exe = program.with_suffix(".exe") if platform == "win32" and program.with_suffix(".exe").is_file() else program
    shutil.copy2(exe, dest / ("game.exe" if platform == "win32" else "game"))
    if platform == "win32":
        _write(dest / "play.bat", BAT_LAUNCHER.format(title=title))
    else:
        _write(dest / "play.sh", SH_LAUNCHER.format(title=title), executable=True)
    return dest


def _game_c_only(inputs: dict) -> dict:
    """The browser build takes the game's C alone: recompiled C runs in a native host (the PC's, the Miyoo Mini's),
    not in the browser's (port/wasm/recomp.py)."""
    return {k: v for k, v in inputs.items() if k != "recomp"}


def make(repo: Path, kind: str, dest: Path, *, console: str, title: str, rom: dict, rom_file: Path | None,
         inputs: dict, simd: bool = False, log=print) -> tuple[Path, Path]:
    """Build the game in the save `repo` (only what changed) and pack it as `kind` into `dest` (replaced): (the
    folder, what to open in it). `inputs`: what the build takes besides the save (play.inputs); `rom`: the game's
    title, region and SHA-1, for the handhelds' READMEs; `rom_file`: the ROM the Miyoo Mini's and the Steam Deck's
    packs carry, or None (the player adds theirs)."""
    from ... import toolchains
    from . import build, ios, miyoo, steamdeck, web

    if kind == "window" and sys.platform != "darwin":
        raise NetworkError("An app with its own window is made on a Mac for now; on Linux and Windows, export the "
                           "standalone app (it plays in the browser)")
    if kind in ("app", "window"):
        sdl = toolchains.ensure("sdl3", log=log) if kind == "window" else None
        steps = (*build.STEPS, PACKING)
        # On macOS the app runs on Apple Silicon and Intel Macs alike, whichever kind exported it.
        program = build.build(repo, console=console, log=log, steps=steps, universal=True, runtime_image=rom_file, **inputs)
        build.Progress(log, steps).start(PACKING)
        log(f"packing {title} as an app for " + ("Apple Silicon and Intel Macs" if sys.platform == "darwin"
                                                  else "this computer") + (", in a window of its own" if sdl else ""))
        return dest, package_app(program, dest, title, sdl=sdl)
    if kind == "miyoo":
        steps = (*miyoo.STEPS, PACKING)
        program = miyoo.build(repo, console=console, log=log, steps=steps, runtime_image=rom_file, **inputs)
        build.Progress(log, steps).start(PACKING)
        log("packing the Miyoo Mini's SD card folders")
        version = " ".join(x for x in (rom.get("title"), rom.get("region"), rom.get("sha1") and f"SHA-1 {rom['sha1']}") if x)
        folder = miyoo.package(program, dest, title, version, rom_file)
        return folder, folder / "README.txt"
    if kind == "steamdeck":
        steps = (*steamdeck.STEPS, PACKING)
        program = steamdeck.build(repo, console=console, log=log, steps=steps, runtime_image=rom_file, **inputs)
        build.Progress(log, steps).start(PACKING)
        log("packing the Steam Deck's folder")
        version = " ".join(x for x in (rom.get("title"), rom.get("region"), rom.get("sha1") and f"SHA-1 {rom['sha1']}") if x)
        folder = steamdeck.package(program, dest, title, version, rom_file)
        return folder, folder / "README.txt"
    steps = (*web.STEPS, PACKING)
    folder = web.build(repo, console=console, log=log, steps=steps, simd=simd, runtime_image=rom_file, **_game_c_only(inputs))
    build.Progress(log, steps).start(PACKING)
    if kind == "ios":
        log("packing the offline iPhone/iPad Xcode project")
        return dest, ios.package(folder, dest, title)
    log("copying the browser build")
    folder = web.export(folder, dest)
    _write(folder / "README.txt", README_WEB.format(title=title) + (README_WEB_SIMD if simd else ""))
    return folder, folder / "index.html"


def export(net, slug: str, kind: str, log=print, simd: bool = False) -> dict:
    """Build the project's game (only what changed) and export it as `kind` ("app", "window", "web", "ios", "miyoo" or "steamdeck"): {"kind",
    "folder", "path" (what to open), "files"}. `simd`: a browser build also carries the faster build for browsers
    with WebAssembly SIMD (web.SIMD), which the page plays where it can. The project's repository also gets the
    workflow that makes the same export on GitHub for each release (workflows.py)."""
    from . import build, ios, miyoo, play, steamdeck, workflows

    if kind not in KINDS:
        raise NetworkError(f"export as one of: {', '.join(KINDS)}")
    repo, cfg, rom_file = play.game(net, slug)
    inputs = play.inputs(net, slug, log)
    console = cfg.get("platform", "n64")
    title = cfg.get("rom", {}).get("title") or slug
    out = repo / "port" / "wasm" / "export"
    out.mkdir(parents=True, exist_ok=True)
    build._write(out / ".gitignore", "*\n")  # built from the game: never committed
    folder, path = make(repo, kind, out / kind, console=console, title=title, rom=cfg.get("rom", {}),
                        rom_file=rom_file, inputs=inputs, simd=simd, log=log)
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    size = sum((folder / f).stat().st_size for f in files)
    log(f"exported {KINDS[kind]} to {path} ({len(files)} files, {size / 1e6:.1f} MB)"
        + ("; your ROM is in it" if kind in ("miyoo", "steamdeck") and any(f.endswith("/rom.z64") for f in files)
           else "; the ROM isn't in it: the player chooses their own"))
    if kind == "miyoo":
        steps_for_player = miyoo.instructions(folder, title)
    elif kind == "steamdeck":
        steps_for_player = steamdeck.instructions(folder, title)
    elif kind == "ios":
        steps_for_player = ios.instructions(folder)
    elif kind == "web":
        steps_for_player = [f"Serve {folder} over http and open it in Chrome or Edge (its README.txt says how): "
                            "browsers load WebAssembly only over http.", "The page asks the player for their ROM."]
        if simd:
            steps_for_player.append("Browsers with WebAssembly SIMD (Chrome 91, Firefox 89, Safari 16.4 and later) play "
                                    "the faster build in it; older ones the plain build, also in it.")
    else:
        steps_for_player = [f"Open {path} to play: it asks for your ROM the first time."]
        if kind == "window":
            steps_for_player.append("Keys: arrows steer, X is A, Z is B, Enter is Start, Space is Z, Q/W are L/R, "
                                    "I/J/K/L the C buttons; a gamepad works too. F toggles full screen.")
        if sys.platform == "darwin":
            steps_for_player.append("On another Mac, the first open needs Control-click > Open (or System Settings > "
                                    "Privacy & Security > Open Anyway): the app isn't signed by an Apple developer. "
                                    "\"How to open on a Mac.txt\" beside it says so for the player.")
    github = None
    try:
        github = workflows.write(repo, kind, sys.platform, workflows.manifest(net, slug, cfg, inputs, simd), log)
    except Exception as error:  # noqa: BLE001  the export itself is done; only its workflow is missing
        log(f"the export is done, but its GitHub workflow wasn't added: {error}")
    if github:
        steps_for_player.append(
            f"On GitHub, each published release makes this export again ({Path(github['workflow']).name}"
            + (", committed: push it" if github["committed"] else "") + ")"
            + ("; the repository's Settings > Pages > Source must be GitHub Actions, once."
               if kind == "web" else ", attached to the release."))
    return {"kind": kind, "folder": str(folder), "path": str(path), "files": files, "bytes": size,
            "system": {"app": sys.platform, "window": sys.platform, "web": "browser"}.get(kind, kind),
            "instructions": steps_for_player, "github": github}


def target_selection(value) -> list[str]:
    """Validate and deduplicate the targets. SIMD adds files to the same website export."""
    if not isinstance(value, list) or any(
        not isinstance(item, str) or (item not in KINDS and item != "simd") for item in value
    ):
        raise ValueError("Choose export targets from the Porting panel.")
    return list(dict.fromkeys(item for item in value if item != "simd" or "web" in value))


def export_selected(net, slug: str, selected: list[str], log) -> dict:
    """Run targets in order so their build files and release workflows can be updated safely."""
    from ...app.support import job_error

    kinds = [kind for kind in selected if kind in KINDS]
    results, failures = [], []

    def export_target(index, kind):
        def progress(value):
            log.check_cancel()
            steps = value.get("steps") or 1
            step = value.get("step") or 1
            fraction = min(1, (value.get("done") or 0) / value["total"]) if value.get("total") else 0
            percent = (step - 1 + fraction) / steps
            log.progress({"phase": f"{KINDS[kind]} ({index + 1}/{len(kinds)}) · {value['phase']}",
                          "done": round(100 * (index + percent)), "total": 100 * len(kinds)})

        @wraps(log)
        def target_log(message):
            log(message)

        target_log.progress = progress
        progress({"phase": "Preparing export"})
        return export(net, slug, kind, target_log, simd=kind == "web" and "simd" in selected)

    for index, kind in enumerate(kinds):
        log.check_cancel()
        try:
            results.append(export_target(index, kind))
        except Exception as error:  # noqa: BLE001  another target can still finish
            log.check_cancel()
            message = f"Could not export {KINDS[kind]}: {job_error(error)}"
            failures.append(message)
            log(message)
    if not results and failures:
        raise NetworkError("\n".join(failures))
    return {"exports": results, "failures": failures}


def export_from_save(repo: Path, kind: str, out: Path, ultralib: bool = False, log=print) -> Path:
    """The export of `kind` made from a save repository alone, as a GitHub runner makes it (workflows.py): what the
    build took from the project comes from the save's port/wasm/export.json, which an export from decompdecomp
    wrote. No ROM: the handhelds' packs ask the player for theirs, and the apps run the game's C alone.
    `ultralib`: install libultra's source (proprietary) when the save builds library code from it, as the user
    accepted in Setup where they exported. Returns the folder."""
    from .. import ultralib as ul
    from . import workflows

    if kind not in KINDS:
        raise NetworkError(f"export as one of: {', '.join(KINDS)}")
    repo = Path(repo).resolve()
    data = workflows.read_manifest(repo)
    wanted = {name: tuple(found) for name, found in (data.get("ultralib") or {}).items()}
    library_c = {}
    if wanted:
        folder = ul.root()
        if folder is None:
            if not ultralib:
                raise NetworkError(f"{len(wanted)} library functions build from ultralib's C: pass --ultralib to "
                                   "install libultra's source (its license is SGI's and Nintendo's)")
            from ... import toolchains
            toolchains.install(ul.PACK, accept_license=True, log=log)  # (--ultralib: the user accepts it)
            folder = ul.root()
        library_c = ul.units_from(wanted, folder)
    inputs = {"leave_out": set(data.get("leave_out") or ()), "library_c": library_c,
              "library_names": dict(data.get("library_names") or {}), "recomp": None}
    title = data.get("title") or repo.name
    folder, path = make(repo, kind, Path(out).resolve(), console=data.get("console") or "n64", title=title,
                        rom=data.get("rom") or {}, rom_file=None, inputs=inputs, simd=bool(data.get("simd")), log=log)
    log(f"exported {KINDS[kind]} to {path}")
    return folder
