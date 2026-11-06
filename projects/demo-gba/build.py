"""Build Demo Quest GBA, a Game Boy Advance ROM made only from code we may share: the same Demo Quest game, main
loop and tiny libc as the N64 demo (CC0, ../demo-rom/src, ../demo-n64/original) and zlib 1.1.4 (Zlib license),
compiled the way GBA games were: the C preprocessor, agbcc (pret's rebuild of the GBA's GCC) at -O2
-mthumb-interwork, and the GNU assembler, as in pret's projects.

The header's 156-byte logo area is blank (Nintendo's artwork stays out of this repository), and the complement
checksum at 0xBD is computed from the title and version here.

    uv run python projects/demo-gba/build.py    ->  build/demoquest.gba (+ build/demoq_gba.elf with symbols,
                                                    build/demo.json for tests/test_consoles.py)

Needs the pinned agbcc and Arm GNU toolchains; they install on first use (free software).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from decompnet import toolchains
from decompnet.consoles.gba import header

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
DEMO = HERE.parent / "demo-rom" / "src"
GAME = HERE.parent / "demo-n64" / "original"
ZLIB = HERE.parent / "zlib-n64"
ZLIB_UNITS = ["adler32", "compress", "crc32", "deflate", "inflate", "infblock", "infcodes", "inffast", "inftrees",
              "infutil", "trees", "uncompr", "zutil"]
ROM_NAME = "demoquest.gba"


def run(argv, cwd=BUILD):
    res = subprocess.run([str(a) for a in argv], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        sys.exit(f"failed: {' '.join(map(str, argv))}\n{res.stdout}{res.stderr}")


def write_key(build: Path, game: Path, elf: Path) -> None:
    """build/demo.json: the game this build made and the address of every symbol, so tests/test_consoles.py knows
    which of the project's functions is which (the game itself is stripped, like a real one)."""
    from elftools.elf.elffile import ELFFile

    with open(elf, "rb") as f:
        symbols = {s.name: s["st_value"] for s in ELFFile(f).get_section_by_name(".symtab").iter_symbols() if s.name}
    (build / "demo.json").write_text(json.dumps({"game": game.name, "symbols": symbols}, indent=1), encoding="utf-8")


def main() -> None:
    missing = [str(path) for path in (DEMO / "main.c", DEMO / "libc.c", GAME / "game.c", ZLIB / "original" / "zlib.h")
               if not path.exists()]
    if missing:
        sys.exit("missing demo sources: " + ", ".join(missing)
                 + " (for zlib, run projects/zlib-n64/prepare.py first)")
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir()
    agbcc = toolchains.ensure("agbcc", log=print)
    arm = toolchains.ensure("arm-gnu", log=print) / "bin"
    cpp, assembler = arm / "arm-none-eabi-cpp", arm / "arm-none-eabi-as"
    jobs = [(HERE / "gbart.c", []), (DEMO / "main.c", [GAME]), (DEMO / "libc.c", []), (GAME / "game.c", [GAME])]
    jobs += [(ZLIB / "original" / f"{unit}.c", [ZLIB / "sysinclude", ZLIB / "original"]) for unit in ZLIB_UNITS]
    objs = []
    for src, includes in jobs:
        stem = src.stem
        run([cpp, "-nostdinc", "-undef", "-I", agbcc / "include",
             *[arg for include in includes for arg in ("-I", include)], src, "-o", f"{stem}.i"])
        run([agbcc / "bin" / "agbcc", *toolchains.get("agbcc").flags, "-fhex-asm", f"{stem}.i", "-o", f"{stem}.s"])
        run([assembler, "-mcpu=arm7tdmi", "-o", f"{stem}.o", f"{stem}.s"])
        objs.append(f"{stem}.o")
    shutil.copy(HERE / "crt0.s", BUILD / "crt0.s")
    run([assembler, "-mcpu=arm7tdmi", "-o", "crt0.o", "crt0.s"])
    run([arm / "arm-none-eabi-ld", "-T", HERE / "rom.ld", "-o", "demoq_gba.elf", "crt0.o", *objs])
    run([arm / "arm-none-eabi-objcopy", "-O", "binary", "demoq_gba.elf", ROM_NAME])
    rom = bytearray((BUILD / ROM_NAME).read_bytes())
    if any(rom[header.LOGO_START:header.LOGO_END]):
        sys.exit("the built ROM's logo area isn't blank: Nintendo's logo must never be in this repository")
    rom[header.CHECKSUM] = header.checksum(rom)
    (BUILD / ROM_NAME).write_bytes(bytes(rom))
    write_key(BUILD, BUILD / ROM_NAME, BUILD / "demoq_gba.elf")
    print(f"built build/{ROM_NAME} ({len(rom)} bytes)")


if __name__ == "__main__":
    main()
