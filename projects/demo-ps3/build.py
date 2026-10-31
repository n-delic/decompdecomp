"""Build Demo Quest PS3, a PlayStation 3 PPU program made only from code we may share: the same Demo Quest game and
tiny libc as the N64 demo (CC0, ../demo-rom/src, ../demo-n64/original) and a start file of its own (start.c: a global
reached through the TOC, a call through a function pointer), compiled with the free ps3dev GCC at -O2 and linked by
its GNU ld at 0x10200.

    uv run python projects/demo-ps3/build.py    ->  build/demoquest.elf (stripped, like a game's decrypted EBOOT.ELF),
                                                    build/demoq_ps3.elf with symbols, build/demo.json for
                                                    tests/test_consoles.py

Needs the pinned ps3dev toolchain; it installs on first use (free software, GPL). It is not Sony's compiler: it makes
the usual 64-bit PowerPC program (64-bit pointers, 24-byte function descriptors), where a retail game has 32-bit
pointers and 8-byte descriptors. The reader and the judge take both; this demo exercises the first.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from decompnet import consoles, toolchains

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
GAME = HERE.parent / "demo-n64" / "original"
LIBC = HERE.parent / "demo-rom" / "src" / "libc.c"
TEXT = 0x10200
ELF_NAME = "demoquest.elf"


def run(argv, cwd=BUILD):
    res = subprocess.run([str(a) for a in argv], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        sys.exit(f"failed: {' '.join(map(str, argv))}\n{res.stdout}{res.stderr}")


def write_key(build: Path, game: Path, elf: Path) -> None:
    """build/demo.json: the game this build made and the address of every function's code and every datum, so
    tests/test_consoles.py knows which of the project's functions is which (the game itself is stripped). A function
    symbol is its descriptor's; the code is where the descriptor points."""
    from elftools.elf.elffile import ELFFile

    with open(elf, "rb") as f:
        image = ELFFile(f)
        opd = image.get_section_by_name(".opd")
        data = opd.data()
        symbols = {}
        for s in image.get_section_by_name(".symtab").iter_symbols():
            if not s.name:
                continue
            if s["st_shndx"] == image.get_section_index(".opd"):
                at = s["st_value"] - opd["sh_addr"]
                symbols[s.name] = int.from_bytes(data[at:at + 8], "big")
            else:
                symbols[s.name] = s["st_value"]
    (build / "demo.json").write_text(json.dumps({"game": game.name, "symbols": symbols}, indent=1), encoding="utf-8")


def main() -> None:
    missing = [str(path) for path in (GAME / "game.c", LIBC) if not path.exists()]
    if missing:
        sys.exit("missing demo sources: " + ", ".join(missing))
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir()
    ppu = toolchains.ensure("ps3dev-gcc", log=print) / "ppu" / "bin"
    flags = consoles.get("ps3").default_flags("ps3dev-gcc", 0)
    objs = []
    for src, extra in ((HERE / "start.c", []), (GAME / "game.c", []), (LIBC, ["-fno-builtin"])):  # (libc.c defines memcpy)
        run([ppu / "ppu-gcc", *flags, *extra, "-I", GAME, "-c", src, "-o", f"{src.stem}.o"])
        objs.append(f"{src.stem}.o")
    run([ppu / "ppu-ld", "-melf64ppc", "-e", "_start", "-z", "max-page-size=0x1000", f"--section-start=.text={TEXT:#x}",
         "-o", "demoq_ps3.elf", *objs])
    run([ppu / "ppu-strip", "-s", "-o", ELF_NAME, "demoq_ps3.elf"])
    write_key(BUILD, BUILD / ELF_NAME, BUILD / "demoq_ps3.elf")
    print(f"built build/{ELF_NAME} ({(BUILD / ELF_NAME).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
