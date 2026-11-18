"""The Steam Deck build of a finished game (docs/PORTING.md, "Export"): the native build (build.py) compiled for the
Steam Deck (SteamOS: 64-bit x86 Linux, the Deck's Zen 2 chip) and packed as a folder to copy to it and add to Steam
as a non-Steam game. It runs the user's own ROM, which the folder holds, never the export.

What it reuses: everything build.py makes up to the game's C (wasm2c's output, its function table and stubs), so a
Steam Deck export after a Play rebuilds nothing of the game. Only the host is compiled again, with the pinned Zig for
x86-64 Linux and glibc 2.28 (far older than any SteamOS), from any computer: the native host as the PC's, with its
window of its own (window/sdl.c, PORT_WINDOW) full screen (PORT_FULLSCREEN), the Deck's controls a gamepad through
Steam Input.

SDL3 isn't in the pack: SDL is opened at run time, and the Steam client keeps its own copy of it
(ubuntu12_64/libSDL3.so.0), which the launcher finds (as do a system's SDL3 and one in the folder's lib/). The pack
(`package`) is <Folder>/: the program, launch.sh (what Steam starts), the project's ROM as rom.z64 (the user's own
copy, for their own device; the export folder is never committed) and a README.
"""

from __future__ import annotations

import re
import shutil
import stat
from pathlib import Path

from ... import consoles, toolchains
from ...network import NetworkError
from . import build as native

TARGET = ["-target", "x86_64-linux-gnu.2.28", "-mcpu=znver2"]
FLAGS = ["-O2", "-g0", "-fno-strict-aliasing", "-fwrapv", "-fno-delete-null-pointer-checks", "-Wno-everything"]
STEPS = (*native.STEPS, "Building for the Steam Deck")


def _folder(title: str) -> str:
    """A folder name from the game's title: letters and digits, as Steam's file browser shows it."""
    return re.sub(r"[^A-Za-z0-9]+", "", title.title())[:32] or "Game"


def build(repo: Path, *, console: str = "n64", log=print, steps=STEPS, leave_out=(), library_c=None,
          library_names=None, function_headers=None, recomp=None) -> Path:
    """Build the game for the PC (only what changed), then its host for the Steam Deck: port/wasm/build/steamdeck/
    game. `recomp`: the console's recompiler's output (play.recompiled), whose C runs the functions with no C yet,
    as in the native build (recomp.py)."""
    native.build(repo, console=console, log=log, steps=steps, leave_out=leave_out, library_c=library_c,
                 library_names=library_names, function_headers=function_headers, recomp=recomp)
    repo = Path(repo).expanduser().resolve()
    host = Path(consoles.get(console).port_kit.wasm)
    if not (host / "window" / "sdl.c").is_file():
        raise NetworkError(f"{consoles.get(console).name} has no host with a window of its own yet")
    native.Progress(log, steps).start(STEPS[-1])
    log("building the game for the Steam Deck (x86-64 Linux)")
    wabt = toolchains.ensure("wabt", log=log)
    out = repo / "port" / "wasm" / "build"
    rt = wabt / "share" / "wabt" / "wasm2c"
    # the native build's own units (build.py, "building the host"): a 64-bit computer reserves wasm2c's memory itself
    units = [*sorted((out / "c").glob("game_*.c")), rt / "wasm-rt-impl.c", rt / "wasm-rt-mem-impl.c",
             rt / "wasm-rt-exceptions-impl.c", *sorted(host.glob("*.c")), *native.game_host(host, repo),
             *sorted(host.glob("window/*.c")), out / "funcs.c", out / "stubs.c", out / "interp.c"]
    return native.cross_host(repo, "steamdeck", "the Steam Deck", units, TARGET, FLAGS,
                             ["-lpthread", "-ldl", "-lm"], recomp, log)


LAUNCH = r"""#!/bin/bash
# {title} on the Steam Deck, from your own ROM. Add this file to Steam (Desktop Mode: Steam > Games > Add a Non-Steam
# Game to My Library > Browse) and play it in Game Mode. What the game writes stays in this folder: its saves in
# game.eep, its log in play.log. Made by decompdecomp's Steam Deck export.
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1
say() {{
    echo "$(date) $1" >> play.log
    if command -v kdialog > /dev/null 2>&1; then kdialog --title "{title}" --error "$1"
    elif command -v zenity > /dev/null 2>&1; then zenity --error --title="{title}" --text="$1"; fi
}}
ROM=""
for f in rom.z64 *.z64 *.n64 *.v64; do
    [ -f "$f" ] && {{ ROM="$f"; break; }}
done
: > play.log
if [ -z "$ROM" ]; then
    say "No ROM: put your copy of {title} in $DIR as rom.z64."
    exit 1
fi
# SDL3, opened by the game as it starts: one in lib/ here, the system's, else the Steam client's own copy
if [ -z "$PORT_SDL" ]; then
    for SDL in "$DIR/lib/libSDL3.so.0" /usr/lib/libSDL3.so.0 /usr/lib64/libSDL3.so.0 \
               /usr/lib/x86_64-linux-gnu/libSDL3.so.0 "$HOME/.steam/root/ubuntu12_64/libSDL3.so.0" \
               "$HOME/.steam/steam/ubuntu12_64/libSDL3.so.0" "$HOME/.local/share/Steam/ubuntu12_64/libSDL3.so.0"; do
        [ -f "$SDL" ] && {{ export PORT_SDL="$SDL"; break; }}
    done
fi
# what the Steam client's copy needs beside it, after the system's own libraries
[ -n "$PORT_SDL" ] && export LD_LIBRARY_PATH="${{LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}}$(dirname "$PORT_SDL")"
chmod +x ./game 2>/dev/null
echo "$(date) $ROM, SDL3: ${{PORT_SDL:-none found}}" >> play.log
PORT_WINDOW=1 PORT_FULLSCREEN="${{PORT_FULLSCREEN:-1}}" PORT_TITLE="{title}" PORT_SAVE="$DIR/game.eep" \
    ./game "$ROM" 0 >> play.log 2>&1
status=$?
[ $status = 5 ] && say "{title} couldn't open its window (SDL3 wasn't found or didn't start): $DIR/play.log says why."
exit $status
"""

README = """{title} for the Steam Deck
{underline}

The game, compiled for the Steam Deck (SteamOS) from its decompilation. Nothing here is the game's ROM: you need your
own copy.

Install
-------

1. Copy the {folder} folder to the Deck, for example to /home/deck/Games/ (a USB stick, a microSD card or the
   network: in Desktop Mode, Dolphin opens each).
2. {rom_step}
3. In Desktop Mode, open Steam > Games > Add a Non-Steam Game to My Library > Browse, choose
   {folder}/launch.sh (show All Files), and add it.
4. Back in Game Mode, start {title} from your library's Non-Steam shortcuts.

Buttons
-------

Steam Input's gamepad layout: A is A, B, X and Y are B, the left stick and the d-pad steer, the right stick is the C
buttons, L1 is L, R1 is R, L2 and R2 are Z, START (the menu button) is START. The Steam button's Exit Game quits;
the game's own saves are kept in {folder}/game.eep.

If it doesn't start, {folder}/play.log says why. It needs SDL3, which the Steam client carries (it looks there by
itself); a copy of libSDL3.so.0 in {folder}/lib/ is used first.
"""


def _write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def package(program: Path, dest: Path, title: str, rom: str = "", rom_file: Path | None = None) -> Path:
    """The folder for the Deck in `dest` (replaced): <Folder>/ with the program and launch.sh, the project's ROM
    (`rom_file`) as <Folder>/rom.z64 when given, and a README. `rom` names its version for the README."""
    folder = _folder(title)
    if dest.exists():
        shutil.rmtree(dest)
    game = dest / folder
    game.mkdir(parents=True)
    shutil.copy2(program, game / "game")
    (game / "game").chmod(0o755)
    safe = re.sub(r'[\\"$`]+', "", title).strip() or folder  # (inside the script's double quotes)
    _write(game / "launch.sh", LAUNCH.format(title=safe), executable=True)
    if rom_file is not None:
        shutil.copyfile(rom_file, game / "rom.z64")
        step = (f"Your ROM is already in {folder}/ as rom.z64 (copied from your project). It's your own copy: keep "
                "this folder to yourself.")
    else:
        step = (f"Put your ROM of {title} in {folder}/ as rom.z64 ({rom or 'the version this export is for'}). A "
                ".v64 or .n64 dump works too.")
    heading = f"{title} for the Steam Deck"
    _write(dest / "README.txt", README.format(title=title, folder=folder, rom_step=step,
                                              underline="=" * len(heading)))
    return dest


def instructions(dest: Path, title: str) -> list[str]:
    """What the player does with the pack, one step a line, for the Porting tab to show under the export's log."""
    folder = _folder(title)
    has_rom = (dest / folder / "rom.z64").is_file()
    return [f"Copy the {folder} folder from {dest} to your Steam Deck, for example to /home/deck/Games/.",
            f"Your ROM is already in {folder}/ as rom.z64." if has_rom
            else f"Put your ROM of {title} in {folder}/ on the Deck as rom.z64.",
            f"In Desktop Mode: Steam > Games > Add a Non-Steam Game to My Library > Browse, choose {folder}/launch.sh "
            f"(show All Files). Then play {title} in Game Mode; if it doesn't start, {folder}/play.log says why."]
