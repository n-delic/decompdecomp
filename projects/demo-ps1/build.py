"""Build Demo Quest PS1, a PlayStation program (PS-X EXE) made only from code we may share: the same Demo Quest game,
main loop and tiny libc as the N64 demo (CC0, ../demo-rom/src, ../demo-n64/original) and zlib 1.1.4 (Zlib
license), compiled the way PSY-Q-era games were: GCC 2.7.2 for the PlayStation at -O2 -G8 (small globals
$gp-relative), its assembly through maspsx (Sony's ASPSX) and GNU as, all with the project's own compile command.

    uv run python projects/demo-ps1/build.py    ->  build/DEMOQ_PS1.EXE (no symbols, like a game) + build/demoq_ps1.elf
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

from decompnet import toolchains
from decompnet.compiler import run_in
from decompnet.project import compiler_config

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
DEMO = HERE.parent / "demo-rom" / "src"
GAME = HERE.parent / "demo-n64" / "original"
ZLIB = HERE.parent / "zlib-n64"
ZLIB_UNITS = ["adler32", "compress", "crc32", "deflate", "inflate", "infblock", "infcodes", "inffast", "inftrees",
              "infutil", "trees", "uncompr", "zutil"]
TOOLCHAIN = "gcc-2.7.2-psx"
LOAD = 0x80010000
STACK = 0x801FFF00
HEADER = 0x800
SECTOR = 2048  # a PS-X EXE's body is whole CD sectors
MARKER = b"Demo Quest PS1 - made for decompdecomp"


def run(argv, cwd=None):
    res = subprocess.run(argv, capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        sys.exit(f"failed: {' '.join(map(str, argv))}\n{res.stdout}{res.stderr}")


def main() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "src").mkdir(parents=True)
    for name, src in (("demo", DEMO), ("game", GAME), ("zlib", ZLIB / "original"), ("sysinclude", ZLIB / "sysinclude")):
        shutil.copytree(src, BUILD / "src" / name)
    shutil.copy(HERE / "psxrt.c", BUILD / "src" / "psxrt.c")
    binutils = toolchains.binary("binutils-mips").parent
    run([str(binutils / "mips-ps2-decompals-as"), "-EL", "-march=r3000", "-mabi=32", "-o", "crt0.o",
         str(HERE / "crt0.s")], cwd=BUILD)
    objs = ["crt0.o"]
    jobs = [("src/demo/main.c", ["-Isrc/game"]), ("src/demo/libc.c", []), ("src/psxrt.c", []),
            ("src/game/game.c", ["-Isrc/game"])]
    jobs += [(f"src/zlib/{u}.c", ["-Isrc/zlib"]) for u in ZLIB_UNITS]
    for src, extra in jobs:
        obj = f"{Path(src).stem}.o"
        cfg = compiler_config(BUILD, {"compiler": {"toolchain": TOOLCHAIN, "flags": ["-O2", "-G8", *extra]}})
        # the toolchain's own compile command, on the copies in build/ (src/sysinclude: the demo's tiny C library)
        argv = [a.replace("{src}", src).replace("{obj}", obj).replace("{include}", "src/sysinclude") for a in cfg.cmd]
        res = run_in(cfg, BUILD, argv, inputs=["src"], outputs=[obj])
        if res.returncode != 0:
            sys.exit(f"failed: {src}\n{res.stdout}{res.stderr}")
        objs.append(obj)
    elf = BUILD / "demoq_ps1.elf"
    run([str(binutils / "mips-ps2-decompals-ld"), "-EL", "-T", str(HERE / "exe.ld"), "-o", str(elf), *objs], cwd=BUILD)
    body_file = BUILD / "body.bin"
    run([str(binutils / "mips-ps2-decompals-objcopy"), "-O", "binary", "-j", ".text", "-j", ".rodata", "-j", ".data",
         "-j", ".sdata", str(elf), str(body_file)])
    body = body_file.read_bytes()
    body_file.unlink()
    body += bytes(-len(body) % SECTOR)
    spec = spec_from_file_location("demo_ps2_build", HERE.parent / "demo-ps2" / "build.py")
    ps2 = module_from_spec(spec)
    spec.loader.exec_module(ps2)
    from elftools.elf.elffile import ELFFile

    with open(elf, "rb") as f:
        syms = {s.name: s["st_value"] for s in ELFFile(f).get_section_by_name(".symtab").iter_symbols() if s.name}
    header = bytearray(HEADER)
    header[:8] = b"PS-X EXE"
    struct.pack_into("<10I", header, 0x10, syms["_start"], syms["_gp"], LOAD, len(body), 0, 0, syms["_bss_start"],
                     syms["_bss_end"] - syms["_bss_start"], STACK, 0)
    header[0x4C:0x4C + len(MARKER)] = MARKER
    game = BUILD / "DEMOQ_PS1.EXE"
    game.write_bytes(bytes(header) + body)
    ps2.write_key(BUILD, game, elf)
    print(f"built {game.relative_to(HERE)}: {len(body)} bytes of code and data, gp=0x{syms['_gp']:08X}")


if __name__ == "__main__":
    main()
