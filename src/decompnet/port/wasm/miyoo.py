"""The Miyoo Mini build of a finished game (docs/PORTING.md, "Export"): the native build (build.py) compiled for the
Miyoo Mini family's ARM chip (Cortex-A7, 32-bit Linux) and packed as a folder for its SD card. It runs the user's own
ROM, which the card holds, never the export.

What it reuses: everything build.py makes up to the game's C (wasm2c's output, its function table and stubs), so a
Miyoo export after a Play rebuilds nothing of the game. What differs is only the host, compiled again for ARM with the
pinned Zig and glibc 2.28 (the oldest the Miyoo OSes ship):
- the kit's `miyoo/miyoo.c` in place of live.c: the screen, buttons and sound through the device's own SDL 1.2,
  opened at run time (nothing to link, nothing to download);
- the kit's `miyoo/mem32.c` in place of wasm2c's allocator: the memory's 4 GB can't be reserved on a 32-bit chip, so
  only what the game touches is mapped, at its own address (the module's low memory, the shadow stacks, the console's
  RAM), and the program is linked above them (IMAGE_BASE), as Miyoo64's ports are.

The pack (`package`) holds App/<Folder>/ (the program, launch.sh, config.json: the stock OS's and Onion's Apps) and
Tools/miyoomini/<Title>.pak/launch.sh (MinUI), and a README. The project's own ROM is copied in as
App/<Folder>/rom.z64, so the folder is ready for the card: it's the user's copy, for their own device, and the export
folder is never committed (git ignores it). A ROM put there by hand works too, in any byte order (.v64 and .n64: the
host converts them as it reads them). The device-side lessons (the panel turned 180 degrees, the
buttons as keys, sound through the audioserver, both cores at full clock) come from the Miyoo64 ports
(github.com/mr-finster/miyoo64, MIT), which found each on devices.
"""

from __future__ import annotations

import re
import shutil
import stat
from pathlib import Path

from ... import consoles, toolchains
from ...network import NetworkError
from . import build as native

TARGET = ["-target", "arm-linux-gnueabihf.2.28", "-mcpu=cortex_a7"]
# The program's own address, clear of the game's memory (mem32.c maps up to 0x12000000 and 0x80000000-0x80800000)
IMAGE_BASE = 0x40000000
FLAGS = ["-O2", "-g0", "-fno-strict-aliasing", "-fwrapv", "-fno-delete-null-pointer-checks", "-DPORT_MEM32=1",
         "-DPORT_MIYOO=1", "-Wno-everything"]
STEPS = (*native.STEPS, "Building for the Miyoo Mini")


def _folder(title: str) -> str:
    """A folder name for the card from the game's title: letters and digits."""
    return re.sub(r"[^A-Za-z0-9]+", "", title.title())[:32] or "Game"


def build(repo: Path, *, console: str = "n64", log=print, steps=STEPS, leave_out=(), library_c=None,
          library_names=None, function_headers=None, recomp=None) -> Path:
    """Build the game for the PC (only what changed), then its host for the Miyoo Mini: port/wasm/build/miyoo/game.
    `recomp`: the console's recompiler's output (play.recompiled), whose C runs the functions with no C yet, as in
    the native build (recomp.py); without it the interpreter runs them, several times slower on the Mini's chip."""
    native.build(repo, console=console, log=log, steps=steps, leave_out=leave_out, library_c=library_c,
                 library_names=library_names, function_headers=function_headers, recomp=recomp)
    repo = Path(repo).expanduser().resolve()
    kit = consoles.get(console).port_kit
    host = Path(kit.wasm)
    if not (host / "miyoo" / "miyoo.c").is_file():
        raise NetworkError(f"{consoles.get(console).name} has no Miyoo Mini host yet")
    native.Progress(log, steps).start(STEPS[-1])
    log("building the game for the Miyoo Mini (ARM, 32-bit Linux)")
    wabt = toolchains.ensure("wabt", log=log)
    out = repo / "port" / "wasm" / "build"
    rt = wabt / "share" / "wabt" / "wasm2c"
    units = [*sorted((out / "c").glob("game_*.c")), rt / "wasm-rt-impl.c", rt / "wasm-rt-exceptions-impl.c",
             *(p for p in sorted(host.glob("*.c")) if p.name != "live.c"), *native.game_host(host, repo),
             *sorted((host / "miyoo").glob("*.c")),
             out / "funcs.c", out / "stubs.c", out / "interp.c"]
    return native.cross_host(repo, "miyoo", "the Miyoo Mini", units, TARGET, FLAGS,
                             ["-fno-pie", "-no-pie", f"-Wl,--image-base=0x{IMAGE_BASE:X}", "-lpthread", "-ldl", "-lm"],
                             recomp, log)


LAUNCH = r"""#!/bin/sh
# {title} on the Miyoo Mini family, from your own ROM: App/{folder}/launch.sh. Every launcher in this pack (the
# Apps entry, MinUI's .pak) ends up here. What the game writes stays in this folder: its saves in game.eep, its log
# in play.log. Made by decompdecomp's Miyoo Mini export.
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1
ROM=""
for f in rom.z64 *.z64 *.n64 *.v64 /mnt/SDCARD/Roms/N64/*.z64; do
    [ -f "$f" ] && {{ ROM="$f"; break; }}
done
if [ -z "$ROM" ]; then
    echo "$(date) no ROM: put your copy of {title} in $DIR as rom.z64" > play.log
    exit 1
fi
# Sound: the Mini's SDL 1.2 has OSS (/dev/dsp) through the OS's audioserver and libpadsp.so (Miyoo64's ports found
# no sound without it where the OS doesn't preload it).
unset LD_PRELOAD
if pgrep audioserver > /dev/null 2>&1; then
    for PADSP in /mnt/SDCARD/miyoo/lib/libpadsp.so /customer/lib/libpadsp.so; do
        if [ -f "$PADSP" ]; then export LD_PRELOAD="$PADSP"; break; fi
    done
fi
export SDL_AUDIODRIVER=dsp
# Both cores at full clock while the game runs (the OS sets its own governor back afterwards)
{{ echo performance > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor; }} 2>/dev/null
CPU1=/sys/devices/system/cpu/cpu1/online
[ -f "$CPU1" ] && [ "$(cat "$CPU1" 2>/dev/null)" = 0 ] && {{ echo 1 > "$CPU1"; }} 2>/dev/null
chmod +x ./game 2>/dev/null
echo "$(date) $ROM" > play.log
PORT_SAVE="$DIR/game.eep" exec ./game "$ROM" 0 >> play.log 2>&1
"""

MINUI = r"""#!/bin/sh
# {title} for MinUI: its SDL turns the panel itself, so the game doesn't. The game is in App/{folder}.
export PORT_ROTATE=0
exec /mnt/SDCARD/App/{folder}/launch.sh
"""

README = """{title} for the Miyoo Mini
==========================

The game, compiled for the Miyoo Mini family (Mini, Mini Plus, Mini v4, Mini Flip) from its decompilation. Nothing
here is the game's ROM: you need your own copy.

Install
-------

1. Copy the App folder (and Tools, for MinUI) to the root of your Miyoo's SD card, merging them with the folders
   already there.
2. {rom_step}
3. Start {title} from Apps (stock OS, Onion) or Tools (MinUI).

Buttons
-------

A is A, B is B, the d-pad steers, START is START, L1 is L, R1 is R, L2 and R2 are Z, X is C-up, Y is C-left, SELECT
is C-down. MENU (or START and SELECT together) quits; the game's own saves are kept in App/{folder}/game.eep.

If it doesn't start, App/{folder}/play.log says why.
"""


def _write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def package(program: Path, dest: Path, title: str, rom: str = "", rom_file: Path | None = None) -> Path:
    """The SD card's folders for the program in `dest` (replaced): App/<Folder>/ and Tools/miyoomini/<Title>.pak/,
    with the project's ROM (`rom_file`) as App/<Folder>/rom.z64 when given. `rom` names its version for the README."""
    folder = _folder(title)
    if dest.exists():
        shutil.rmtree(dest)
    app = dest / "App" / folder
    app.mkdir(parents=True)
    shutil.copy2(program, app / "game")
    (app / "game").chmod(0o755)
    _write(app / "launch.sh", LAUNCH.format(title=title, folder=folder), executable=True)
    label = title.replace("\\", "").replace('"', "'")
    _write(app / "config.json", '{\n  "label": "%s",\n  "launch": "launch.sh",\n  "description": "%s, '
           'from its decompilation"\n}\n' % (label, label))
    pak = dest / "Tools" / "miyoomini" / f"{re.sub(r'[^\w .-]+', '', title).strip() or folder}.pak"
    _write(pak / "launch.sh", MINUI.format(title=title, folder=folder), executable=True)
    if rom_file is not None:
        shutil.copyfile(rom_file, app / "rom.z64")
        step = (f"Your ROM is already in App/{folder}/ as rom.z64 (copied from your project). It's your own copy: "
                "keep this folder to yourself.")
    else:
        step = (f"Put your ROM of {title} in App/{folder}/ as rom.z64 ({rom or 'the version this export is for'}). "
                "A .v64 or .n64 dump works too; so does a .z64 in Roms/N64/.")
    _write(dest / "README.txt", README.format(title=title, folder=folder, rom_step=step))
    return dest


def instructions(dest: Path, title: str) -> list[str]:
    """What the player does with the pack, one step a line, for the Porting tab to show under the export's log."""
    folder = _folder(title)
    has_rom = (dest / "App" / folder / "rom.z64").is_file()
    return [f"Copy the App and Tools folders from {dest} to the root of your Miyoo's SD card, merging them with the "
            "folders already there (on a Mac, merge with Terminal, `cp -R`: Finder's Replace deletes the card's folder).",
            f"Your ROM is already in App/{folder}/ as rom.z64." if has_rom
            else f"Put your ROM of {title} in App/{folder}/ on the card as rom.z64.",
            f"Start {title} from Apps (stock OS, Onion) or Tools (MinUI). If it doesn't start, App/{folder}/play.log "
            "on the card says why."]
