"""Build Demo Quest PSP, a PSP program made only from code we may share: the same Demo Quest game, main loop and tiny
libc as the N64 demo (CC0, ../demo-rom/src, ../demo-n64/original) and zlib 1.1.4 (Zlib license), compiled with the
PSP SDK's GCC 1.3.1 at -O2 -G8, all with the toolchain's own compile command.

    uv run python projects/demo-psp/build.py    ->  build/BOOT.BIN (a PRX, no symbols, like a game) + build/demoq_psp.elf

With a toolchain id (pspsnc-1.2.7503), the game's C built with that compiler instead, at the same -O2 -G8, into
build/<toolchain>/. The start-up code (crt0.s) is always the SDK's assembler's, and the SDK's GNU linker links
every build: SN's own linker (pspld) doesn't run under wibo, and the module it would make is the same layout.

A PSP game's program is a relocatable module (PRX), which the PSP's loader places and relocates: the demo is one
too. It's linked once at 0 with its relocations kept (ld -q), and those become the PSP's kind (prx_from_link), the
way the SDK's prxgen makes a module; and once at 0x08804000, where the loader puts a game's main module, for the
symbols' addresses (build/demo.json) and to check that the module relocated there is the same program.
"""

from __future__ import annotations

import shutil
import struct
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
TOOLCHAIN = "psp-gcc-1.3.1"
BASE = 0x08804000  # where the PSP's loader puts a game's main module (consoles/psp/prx.py, USER_BASE)
# ELF facts prx_from_link needs
ET_PSP_PRX, SHT_NULL, SHT_PSP_REL, SHF_ALLOC = 0xFFA0, 0, 0x700000A0, 2
R_MIPS_GPREL16 = 7  # $gp-relative: nothing for the loader to do


def compile_all(build: Path, toolchain: str, cfg_flags: list[str]) -> list[str]:
    """Every source compiled with `toolchain` (and crt0.s assembled by the SDK's GCC) in decompdecomp's compiler
    sandbox, on copies in `build`."""
    objs = []
    jobs = [("src/crt0.s", []), ("src/demo/main.c", ["-Isrc/game"]), ("src/demo/libc.c", []),
            ("src/game/game.c", ["-Isrc/game"])]
    jobs += [(f"src/zlib/{u}.c", ["-Isrc/zlib"]) for u in ZLIB_UNITS]
    for src, extra in jobs:
        obj = f"{Path(src).stem}.o"
        tc = TOOLCHAIN if src.endswith(".s") else toolchain
        cfg = compiler_config(build, {"compiler": {"toolchain": tc, "flags": [*cfg_flags, *extra]}})
        # the toolchain's own compile command, on the copies in build/ (src/sysinclude: the demo's tiny C library)
        argv = [a.replace("{src}", src).replace("{obj}", obj).replace("{include}", "src/sysinclude") for a in cfg.cmd]
        res = run_in(cfg, build, argv, inputs=["src"], outputs=[obj])
        if res.returncode != 0:
            sys.exit(f"failed: {src}\n{res.stdout}{res.stderr}")
        objs.append(obj)
    return objs


def link(build: Path, objs: list[str], out: str, *extra: str) -> None:
    """The SDK's own linker (it knows the Allegrex's objects), run in the sandbox like its compiler: a 32-bit program."""
    cfg = compiler_config(build, {"compiler": {"toolchain": TOOLCHAIN}})
    ld = str(toolchains.path(TOOLCHAIN) / "bin" / "psp-ld")
    res = run_in(cfg, build, [ld, "-EL", *extra, "-T", "prx.ld", "-o", out, *objs], inputs=[*objs, "prx.ld"],
                 outputs=[out])
    if res.returncode != 0:
        sys.exit(f"failed: linking {out}\n{res.stdout}{res.stderr}")


def prx_from_link(data: bytes) -> bytes:
    """A program linked at 0 with its relocations kept (ld -q) made a PSP module, as the SDK's prxgen does: ELF type
    0xFFA0, and each relocation table of a loaded section the PSP's kind (SHT_PSP_REL), whose entries keep the
    relocation's type and the segment its offset is in and whose address it adds (segment 0, the only one), with the
    offset counted from that segment. The symbol table is hidden: the demo ships without its names, like most games.
    $gp-relative relocations go: the loader has nothing to do for them."""
    import io

    from elftools.elf.elffile import ELFFile

    elf = ELFFile(io.BytesIO(data))
    out = bytearray(data)
    struct.pack_into("<H", out, 16, ET_PSP_PRX)
    shoff = elf["e_shoff"]
    for index, section in enumerate(elf.iter_sections()):
        if section["sh_type"] in ("SHT_SYMTAB", "SHT_STRTAB") and index != elf["e_shstrndx"]:
            struct.pack_into("<II", out, shoff + index * elf["e_shentsize"], 0, SHT_NULL)  # no name, no type
            continue
        if section["sh_type"] != "SHT_REL":
            continue
        target = elf.get_section(section["sh_info"])
        if not target["sh_flags"] & SHF_ALLOC:
            continue
        entries = []
        for rel in section.iter_relocations():
            if rel["r_info_type"] != R_MIPS_GPREL16:  # the segment is based at 0: offsets are addresses already
                entries.append(struct.pack("<II", rel["r_offset"], rel["r_info_type"]))
        table = b"".join(entries)
        struct.pack_into("<I", out, shoff + index * elf["e_shentsize"] + 4, SHT_PSP_REL)
        struct.pack_into("<I", out, shoff + index * elf["e_shentsize"] + 20, len(table))
        start = section["sh_offset"]
        out[start:start + len(table)] = table
    return bytes(out)


def out_dir(toolchain: str) -> Path:
    return BUILD if toolchain == TOOLCHAIN else BUILD / toolchain


def main() -> None:
    import argparse
    from importlib.util import module_from_spec, spec_from_file_location

    ap = argparse.ArgumentParser()
    ap.add_argument("toolchain", nargs="?", default=TOOLCHAIN)
    toolchain = ap.parse_args().toolchain
    build = out_dir(toolchain)
    if build.exists():
        shutil.rmtree(build)
    (build / "src").mkdir(parents=True)
    for name, src in (("demo", DEMO), ("game", GAME), ("zlib", ZLIB / "original"), ("sysinclude", ZLIB / "sysinclude")):
        shutil.copytree(src, build / "src" / name)
    shutil.copy(HERE / "crt0.s", build / "src" / "crt0.s")
    shutil.copy(HERE / "prx.ld", build / "prx.ld")
    objs = compile_all(build, toolchain, ["-O2", "-G8"])
    link(build, objs, "demoq_psp.prx.elf", "-q")  # at 0, its relocations kept
    link(build, objs, "demoq_psp.elf", f"--section-start=.text=0x{BASE:X}")  # where the loader would put it, for the key
    game = build / "BOOT.BIN"
    game.write_bytes(prx_from_link((build / "demoq_psp.prx.elf").read_bytes()))
    spec = spec_from_file_location("demo_ps2_build", HERE.parent / "demo-ps2" / "build.py")
    ps2 = module_from_spec(spec)
    spec.loader.exec_module(ps2)
    ps2.write_key(build, game, build / "demoq_psp.elf")
    print(f"built {game.relative_to(HERE)} ({game.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
