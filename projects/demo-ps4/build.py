"""Build a shareable PS4-shaped ELF from Demo Quest's CC0 source.

The pinned public clang and lld compile/link the demo. Its section table is removed
and its e_type changed to Orbis EXEC so the importer exercises the loader-header
path, not a game's symbols. The adjacent map supplies the known function extents.
This is an importer fixture, not a bootable PS4 application; no SDK is included.
"""
from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.consoles.ps4.project_template.tools.compiler_flags import CL_FLAGS
from decompnet.limits import capped

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"


def main() -> None:
    """Write build/demo.json and the ELF/map used by console conformance tests."""
    compiler = toolchains.binary("clang-5.0.2")
    linker = compiler.with_name("ld.lld")
    BUILD.mkdir(exist_ok=True)
    obj, game = BUILD / "game.o", BUILD / "demoquest.elf"
    source = HERE.parent / "demo-n64" / "original" / "game.c"
    for cmd in ([str(compiler), *CL_FLAGS, "-c", str(source), "-o", str(obj)],
                [str(compiler), *CL_FLAGS, "-c", str(HERE / "extras.c"), "-o", str(BUILD / "extras.o")],
                [str(linker), "-e", "abs_s32", "-Ttext=0x1000", str(obj), str(BUILD / "extras.o"), "-o", str(game)]):
        subprocess.run(capped(cmd), check=True, timeout=120)
    with game.open("rb") as file:
        elf = ELFFile(file)
        code = next(s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD" and s["p_flags"] & 1)
        symbols = {s.name: (s["st_value"], s["st_size"])
                   for s in elf.get_section_by_name(".symtab").iter_symbols()
                   if s["st_info"]["type"] == "STT_FUNC" and s["st_size"]}
        rows = [{"a": f"{addr-code['p_vaddr']:x}", "end": f"{addr+size-code['p_vaddr']:x}", "n": name}
                for name, (addr, size) in sorted(symbols.items(), key=lambda item: item[1][0])]
    data = bytearray(game.read_bytes())
    struct.pack_into("<H", data, 16, 0xFE10)
    struct.pack_into("<Q", data, 40, 0)  # e_shoff
    struct.pack_into("<HHH", data, 58, 0, 0, 0)  # e_shentsize/e_shnum/e_shstrndx
    game.write_bytes(data)
    game.with_suffix(".functions.jsonl").write_text("".join(json.dumps(row)+"\n" for row in rows), encoding="utf-8")
    (BUILD / "demo.json").write_text(json.dumps({"game": game.name,
                                               "symbols": {name: addr for name, (addr, _) in symbols.items()}}, indent=2),
                                     encoding="utf-8")
    print(f"built {game.name}: {len(rows)} functions; no game data")


if __name__ == "__main__":
    main()
