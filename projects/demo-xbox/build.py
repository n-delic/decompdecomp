"""Build Demo Quest Xbox, an original Xbox program (XBE) made only from code we may share: the same Demo Quest game as
the N64 demo (CC0, ../demo-n64/original) and a start-up file with the shapes Xbox games have (xboxmain.c), compiled by
Visual C++ 7.1 (the XDK's compiler) with exactly the flags of consoles/xbox/scratch.py (CL_FLAGS) and linked by its own
linker into a plain PE at 0x10000, where every XBE loads; every function is kept (/OPT:NOREF). No XDK and no C runtime:
the demo needs none, and nothing here is Microsoft's but the compiler.

An XBE is not a PE, so the linked PE (build/demoquest.exe, an intermediate) is wrapped into one by this file, with
the standard library only: the image header, a certificate (title ID DQ-001, "Demo Quest", North America), a library
version table naming XAPILIB and XBOXKRNL at 1.0.5849 (names and numbers only: no library code, and the build
decompdecomp reads as Visual C++ 7.1), and each PE section copied to the XBE at its linked address (the code's absolute
addresses depend on it). The kernel thunk table is xboxmain.c's xboxkrnl_thunks (the map says where), which imports
NtClose by ordinal. The entry point and thunk address are stored XORed with the retail keys, as imagebld writes them.

MSVC is proprietary: this needs the `msvc` toolchain (decompnet toolchain install msvc --accept-license).

    uv run python projects/demo-xbox/build.py
        ->  build/demoquest.xbe (+ build/symbols.map with every symbol, build/demo.json, build/demoquest.exe)

    uv run python projects/demo-xbox/build.py ltcg
        ->  build/ltcg/demoquest.xbe (+ the same files): the program built as many Xbox games were, with /GL and linked
            with /LTCG, so the linker gives its functions register conventions of its own (the argument in EAX...) and
            the units of an import of it build in harness mode (forks/ltcg.py). 7.1's linker does that, through wine
            (the one in $DECOMPNET_WINE or on the PATH, in a prefix of its own: $DECOMPNET_WINE_PREFIX, else
            build/ltcg/.wine); wibo can't run it.

The linker is 6.0's, as demo-pc's: wibo 1.2.0 stops 7.1's LINK.EXE at its first lstrcmpiA (missing import). The PE is
only a step, and the 7.1 objects link the same.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from decompnet import toolchains, wine
from decompnet.consoles.xbox.scratch import BASE, CL_FLAGS, KEYS
from decompnet.forks.ltcg import XBOX_CL_FLAGS

HERE = Path(__file__).resolve().parent
LTCG = "ltcg" in sys.argv[1:]
BUILD = HERE / "build" / ("ltcg" if LTCG else "")
GAME = HERE.parent / "demo-n64" / "original"
TITLE_ID = 0x44510001  # "DQ-001"
EXECUTABLE, PRELOAD, WRITABLE = 4, 2, 1
CERT, SECTIONS, STRINGS, LIBS = 0x180, 0x360, 0x500, 0x580  # where the headers' parts sit, from the file's start


def run(argv, cwd=BUILD):
    res = subprocess.run([str(a) for a in argv], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        sys.exit(f"failed: {' '.join(map(str, argv))}\n{res.stdout}{res.stderr}")


def link_ltcg(msvc: Path, wibo: list) -> None:
    """Link with 7.1's linker and /LTCG (it generates the code of the /GL objects), through wine."""
    if sys.platform == "win32":
        runner, env = [], None
    else:
        found = wine.find()
        if found is None:
            sys.exit(wine.problem("The linker of link-time code generation") or "no wine")
        prefix = os.environ.get("DECOMPNET_WINE_PREFIX") or str(BUILD / ".wine")  # (never the app's prefix)
        runner, env = [found], {**os.environ, **wine.QUIET, "WINEPREFIX": prefix}
    res = subprocess.run([*runner, str(msvc / "link.exe"), "/nologo", "/NODEFAULTLIB", "/ENTRY:start", "/SUBSYSTEM:CONSOLE",
                          "/FIXED", "/OPT:NOREF", "/LTCG", f"/BASE:0x{BASE:X}", "/MAP:symbols.map", "/OUT:demoquest.exe",
                          "xboxmain.obj", "game.obj"], capture_output=True, text=True, cwd=BUILD, env=env)
    if res.returncode != 0:
        sys.exit(f"the LTCG link failed:\n{res.stdout}{res.stderr}")


def write_key() -> None:
    """build/demo.json: the game this build made and the address of every symbol in the linker's map, so
    tests/test_consoles.py knows which of the project's functions is which."""
    symbols = {m.group(1): int(m.group(2), 16)
               for m in re.finditer(r"^\s+\S+\s+(\S+)\s+([0-9a-f]{8}) ", (BUILD / "symbols.map").read_text(), re.M)}
    (BUILD / "demo.json").write_text(json.dumps({"game": "demoquest.xbe", "symbols": symbols}, indent=1), encoding="utf-8")


def read_pe(pe: bytes) -> tuple[int, list[tuple[str, int, int, bytes]], int]:
    """(entry point, [(name, address, size in memory, bytes)], timestamp) of a PE linked at BASE."""
    lfanew = struct.unpack_from("<I", pe, 0x3C)[0]
    nsec, stamp, opt_size = struct.unpack_from("<2xHI8xH", pe, lfanew + 4)
    entry, image_base = struct.unpack_from("<I", pe, lfanew + 24 + 16)[0], struct.unpack_from("<I", pe, lfanew + 24 + 28)[0]
    assert image_base == BASE, "the PE must be linked at the XBE base"
    sections = []
    for i in range(nsec):
        name, vsize, va, raw_size, raw_at = struct.unpack_from("<8sIIII", pe, lfanew + 24 + opt_size + 40 * i)
        sections.append((name.rstrip(b"\0").decode(), BASE + va, vsize, pe[raw_at:raw_at + min(raw_size, vsize)]))
    return BASE + entry, sections, stamp


def make_xbe(pe: bytes, thunk: int) -> bytes:
    """The XBE of the PE linked at BASE, its kernel thunk table at `thunk`."""
    entry, pe_sections, stamp = read_pe(pe)
    last = pe_sections[-1]
    sections = [(n, EXECUTABLE | PRELOAD if n == ".text" else WRITABLE | PRELOAD, va, vs, raw) for n, va, vs, raw in pe_sections]
    assert sections[0][2] >= BASE + 0x1000, "the headers must fit below the first section"
    head = bytearray(0x1000)
    image_size = (last[1] + last[2] + 0xFFF & ~0xFFF) - BASE
    head[0:4] = b"XBEH"
    struct.pack_into("<6I", head, 0x104, BASE, 0x1000, image_size, 0x178, stamp, BASE + CERT)
    struct.pack_into("<3I", head, 0x11C, len(sections), BASE + SECTIONS, 0x5)  # count, headers, init flags
    entry_key, thunk_key = KEYS["retail"]
    struct.pack_into("<I", head, 0x128, entry ^ entry_key)
    struct.pack_into("<I", head, 0x134, BASE)  # the PE's base address
    struct.pack_into("<2I", head, 0x150, BASE + STRINGS, BASE + STRINGS + 32)  # debug path and file name
    struct.pack_into("<I", head, 0x158, thunk ^ thunk_key)
    struct.pack_into("<3I", head, 0x160, 2, BASE + LIBS, BASE + LIBS + 16)
    struct.pack_into("<I", head, 0x168, BASE + LIBS + 16)  # the kernel's version record
    struct.pack_into("<I", head, 0x16C, BASE + LIBS)  # XAPI's
    head[STRINGS:STRINGS + 16] = b"D:\\demoquest.exe"
    head[STRINGS + 32:STRINGS + 45] = b"demoquest.exe"
    struct.pack_into("<3I", head, CERT, 0x1D0, stamp, TITLE_ID)
    head[CERT + 0x0C:CERT + 0x0C + 20] = "Demo Quest".encode("utf-16-le")
    struct.pack_into("<I", head, CERT + 0xA0, 1)  # North America
    names_at = SECTIONS + 56 * len(sections)
    counts = names_at + sum(len(s[0]) + 1 for s in sections)  # three zero words for the reference counts
    out = bytearray()
    for i, (name, flags, va, vsize, raw) in enumerate(sections):
        at = (va - BASE)
        struct.pack_into("<9I", head, SECTIONS + 56 * i, flags, va, vsize, at, len(raw), BASE + names_at,
                         BASE + counts, BASE + counts + 4, BASE + counts + 8)
        head[names_at:names_at + len(name)] = name.encode("ascii")
        names_at += len(name) + 1
        out = out.ljust(at - 0x1000, b"\0") + raw
    for i, lib in enumerate((b"XAPILIB", b"XBOXKRNL")):
        head[LIBS + 16 * i:LIBS + 16 * i + len(lib)] = lib
        struct.pack_into("<4H", head, LIBS + 16 * i + 8, 1, 0, 5849, 0x4000)
    return bytes(head) + bytes(out)


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    for old in BUILD.iterdir():
        if old.is_file():
            old.unlink()
    for src in (GAME / "game.c", GAME / "game.h", HERE / "xboxmain.c"):
        shutil.copy(src, BUILD / src.name)
    msvc = toolchains.require("msvc") / "7.1" / "Bin"
    link = toolchains.require("msvc") / "6.0" / "Bin"  # (7.1's linker stops under wibo, see the docstring)
    wibo = [str(toolchains.binary("wibo"))] if sys.platform != "win32" else []
    for src in ("game.c", "xboxmain.c"):
        run([*wibo, msvc / "cl.exe", *(XBOX_CL_FLAGS + ["/GL"] if LTCG else CL_FLAGS), src])
    if LTCG:
        link_ltcg(msvc, wibo)
    else:
        run([*wibo, link / "LINK.EXE", "/nologo", "/NODEFAULTLIB", "/ENTRY:start", "/SUBSYSTEM:CONSOLE", "/FIXED",
             "/OPT:NOREF", f"/BASE:0x{BASE:X}", "/MAP:symbols.map", "/OUT:demoquest.exe", "xboxmain.obj", "game.obj"])
    write_key()
    symbols = json.loads((BUILD / "demo.json").read_text(encoding="utf-8"))["symbols"]
    xbe = make_xbe((BUILD / "demoquest.exe").read_bytes(), symbols["_xboxkrnl_thunks"])
    (BUILD / "demoquest.xbe").write_bytes(xbe)
    print(f"built {(BUILD / 'demoquest.xbe').relative_to(HERE)} ({len(xbe)} bytes)")


if __name__ == "__main__":
    main()
