"""Build Demo Quest PC, a Windows program (PE .exe) made only from code we may share: the same Demo Quest game as the
N64 demo (CC0, ../demo-n64/original) and a start-up file with the shapes PC games have (pcmain.c), compiled with
Microsoft Visual C++ 6.0 (/O2, the usual release flags of the time) and linked by its own linker at the usual
0x400000, without relocations, like PC games of the late 1990s; every function is kept (/OPT:NOREF), even ones
nothing calls, so the demo has all of Demo Quest to decompile. No C runtime: the demo needs none.

MSVC is proprietary: this needs the `msvc` toolchain (decompnet toolchain install msvc --accept-license).

    uv run python projects/demo-pc/build.py    ->  build/demoquest.exe (+ build/symbols.map with every symbol)
    uv run python projects/demo-pc/build.py 10.0SP1    ->  build/10.0SP1/demoquest.exe
    uv run python projects/demo-pc/build.py 6.0 --pdb  ->  build/pdb/demoquest.exe + demoquest.pdb (its debug symbols)

With a version of the `msvc2005` pack (8.0, 8.0SP1, 9.0, 9.0SP1, 10.0, 10.0SP1) it's Demo Quest as a game of 2005-2012:
compiled by that Visual C++, linked by 6.0's linker all the same. wibo runs 2005's and later linkers only up to their
first lstrcmpiW (wibo 1.2.0; their compilers run), and the program's Rich header names the compiler of every file
whichever linker wrote it, which is what the import reads.

The linker's map is symbols.map, not demoquest.map: the import reads a map named like the program as the game's
own symbols (consoles/pc/debuginfo.py), and the demo is a game without them unless it's built with --pdb.

--pdb builds it as a game that shipped its program database: compiled with /Zi, linked with /DEBUG (a PDB 2.0, as
Visual C++ 6.0 wrote them). Only 6.0's: under wibo 1.2.0, 7.1's linker stops at lstrcmpiA with /DEBUG, and 2005's and
later compilers at NdrClientCall2 with /Zi (their PDB server), so MSF 7.00 PDBs are tests/test_pc_debug.py's own.

    uv run python projects/demo-pc/build.py dll    ->  build/dll/demoquest.exe and build/dll/demoquest.dll

Demo Quest as a game with a DLL of its own beside its program: the same code compiled again by Visual C++ 2010 SP1 and
linked as a DLL that exports some of its functions by name, at 0x400000, the program's own address. So the import
moves it by its base relocations, as Windows does when two programs want one address, and builds its files with
the Visual C++ its Rich header names, not the program's (tests/test_pc.py).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from decompnet import toolchains
from decompnet.consoles.pc.scratch import compiler_pack

HERE = Path(__file__).resolve().parent
DLL = "dll" in sys.argv[1:]
PDB = "--pdb" in sys.argv[1:]
VERSION = next((a for a in sys.argv[1:] if a not in ("dll", "--pdb")), "6.0")
BUILD = HERE / "build" / ("dll" if DLL else "pdb" if PDB else "" if VERSION == "6.0" else VERSION)
EXPORTS = ("player_take_damage", "entity_default_hp", "clamp_s32")  # what the DLL exports, by their own names
DLL_VERSION = "10.0SP1"
GAME = HERE.parent / "demo-n64" / "original"
CFLAGS = ["/nologo", "/c", "/O2", "/Gd", "/W0"]


def run(argv, cwd=BUILD):
    res = subprocess.run([str(a) for a in argv], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        sys.exit(f"failed: {' '.join(map(str, argv))}\n{res.stdout}{res.stderr}")


def write_key() -> None:
    """build/demo.json: the game this build made and the address of every symbol in the linker's map, so
    tests/test_consoles.py knows which of the project's functions is which."""
    symbols = {m.group(1): int(m.group(2), 16)
               for m in re.finditer(r"^\s+\S+\s+(\S+)\s+([0-9a-f]{8}) ", (BUILD / "symbols.map").read_text(), re.M)}
    (BUILD / "demo.json").write_text(json.dumps({"game": "demoquest.exe", "symbols": symbols}, indent=1), encoding="utf-8")


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    for old in BUILD.iterdir():  # (another version's build, in a folder of its own, stays)
        if old.is_file():
            old.unlink()
    for src in (GAME / "game.c", GAME / "game.h", HERE / "pcmain.c"):
        shutil.copy(src, BUILD / src.name)
    msvc = toolchains.require("msvc") / "6.0" / "Bin"
    cl = toolchains.require(compiler_pack(VERSION)) / VERSION / "Bin"
    wibo = [str(toolchains.binary("wibo"))] if sys.platform != "win32" else []
    if PDB and VERSION != "6.0":
        sys.exit("--pdb builds with Visual C++ 6.0 only (see this file's docstring)")
    debug = (["/Zi", "/Fdvc60.pdb"], ["/DEBUG", "/PDB:demoquest.pdb"]) if PDB else ([], [])
    for src in ("game.c", "pcmain.c"):
        run([*wibo, next(p for p in (cl / "CL.EXE", cl / "cl.exe") if p.exists()), *CFLAGS, *debug[0], src])
    run([*wibo, msvc / "LINK.EXE", "/nologo", "/NODEFAULTLIB", "/ENTRY:start", "/SUBSYSTEM:CONSOLE", "/FIXED", "/OPT:NOREF",
         *debug[1], "/MAP:symbols.map", "/OUT:demoquest.exe", "pcmain.obj", "game.obj"])
    if DLL:
        objs = BUILD / "dll_objs"  # (a folder without a program of its own: the import looks in it, and finds .obj)
        objs.mkdir(exist_ok=True)
        dll_cl = toolchains.require(compiler_pack(DLL_VERSION)) / DLL_VERSION / "Bin"
        for src in ("game.c", "pcmain.c"):
            run([*wibo, next(p for p in (dll_cl / "CL.EXE", dll_cl / "cl.exe") if p.exists()), *CFLAGS,
                 f"/Fo{objs.name}/{Path(src).stem}.obj", src])
        run([*wibo, msvc / "LINK.EXE", "/nologo", "/NODEFAULTLIB", "/DLL", "/NOENTRY", "/BASE:0x400000", "/OPT:NOREF",
             *(f"/EXPORT:{name}" for name in EXPORTS), "/MAP:demoquest_dll.map", "/OUT:demoquest.dll",
             f"{objs.name}/pcmain.obj", f"{objs.name}/game.obj"])
    write_key()
    print(f"built {(BUILD / 'demoquest.exe').relative_to(HERE)} ({(BUILD / 'demoquest.exe').stat().st_size} bytes)")


if __name__ == "__main__":
    main()
