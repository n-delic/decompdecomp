"""#261: a function's own data is read from its overlay, not from whichever overlay the ROM lists first.

An N64 game's overlays load at the same virtual address. A function in one of them can keep its switch's jump table
in .rodata right after its code, at an address another overlay's code shares. The judge must compare that table with
the bytes of the function's own overlay (where its `rom_offset` says they are), never the first segment that happens
to cover the address.
"""

from io import BytesIO
from types import SimpleNamespace

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.app.engine.core import Core
from decompnet.app.engine.judging import JudgeMixin
from decompnet.compiler import CompilerConfig, compile_source
from decompnet.elfobj import section_target
from decompnet.rom import match
from decompnet.rom.split import Segment
from decompnet.rom.symbols import Symbol, SymbolTable

pytestmark = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None,
                                reason="needs gcc-2.7.2-kmc to compile the candidate")

SOURCE = """
int pick(unsigned int x) {
    switch (x) {
    case 0: return 11;
    case 1: return 22;
    case 2: return 33;
    case 3: return 44;
    case 4: return 55;
    case 5: return 66;
    case 6: return 77;
    case 7: return 88;
    default: return 0;
    }
}
"""
NAME = "pick"
VRAM = 0x8018D800  # the address both overlays load at
RODATA = 0x100  # where the function's jump table sits inside its overlay
FIRST_ROM, SECOND_ROM = 0x1000, 0x2000  # the two overlays' bytes in the ROM


def _overlay():
    """The compiled candidate, linked as the second overlay: its target, and the ROM that holds both overlays."""
    tc = toolchains.get("gcc-2.7.2-kmc")
    tc_dir = str(toolchains.require(tc.id))
    cmd = [a.replace("{tc}", tc_dir) for a in tc.cc] + tc.flags + ["-I{include}", "{src}", "-o", "{obj}"]
    result = compile_source(CompilerConfig(cmd=cmd, ro_paths=(tc_dir,)), "", SOURCE)
    assert result.ok, result.log

    elf = ELFFile(BytesIO(result.obj))
    symtab = elf.get_section_by_name(".symtab")
    fn = next(s for s in symtab.iter_symbols() if s.name == NAME)
    shndx = {s.name: i for i, s in enumerate(elf.iter_sections()) if s.name in (".text", ".rodata")}
    vram = {shndx[".text"]: VRAM, shndx[".rodata"]: VRAM + RODATA}
    text = bytearray(elf.get_section_by_name(".text").data()[:fn["st_size"]])
    rodata = bytearray(elf.get_section_by_name(".rodata").data())

    relocs = []
    raw = match.raw_relocs(elf, shndx[".text"], fn["st_value"], fn["st_size"], bytes(text))
    for k, r in enumerate(raw):
        sym = r["symtab"].get_symbol(r["symidx"])
        dest = vram[sym["st_shndx"]] + sym["st_value"] + section_target("mips", raw, k)
        relocs.append((r["off"], r["type"], dest))
        at = r["off"]
        text[at:at + 4] = match._patch(int.from_bytes(text[at:at + 4], "big"), r["type"], dest).to_bytes(4, "big")
    for r in match.raw_relocs(elf, shndx[".rodata"], 0, len(rodata), bytes(rodata)):
        if r["type"] != match.R_MIPS_32:
            continue
        sym = r["symtab"].get_symbol(r["symidx"])
        dest = vram[sym["st_shndx"]] + sym["st_value"] + r["value"]
        at = r["off"]
        rodata[at:at + 4] = dest.to_bytes(4, "big")

    image = bytes(text).ljust(RODATA, b"\0") + bytes(rodata)
    rom = bytearray(SECOND_ROM + len(image))
    rom[FIRST_ROM:FIRST_ROM + len(image)] = b"\x3c\x0f\xe7\x00" * (len(image) // 4)  # lui $t7, 0xe700: another overlay's code
    rom[SECOND_ROM:SECOND_ROM + len(image)] = image
    segments = [Segment("code_8018D248", FIRST_ROM, FIRST_ROM + len(image), VRAM),
                Segment("code_8016E650", SECOND_ROM, SECOND_ROM + len(image), VRAM)]
    target = match.RomTarget(NAME, VRAM, bytes(text), relocs)
    fn_row = {"name": NAME, "vram": VRAM, "size": len(text), "rom_offset": SECOND_ROM}
    return result.obj, target, fn_row, rom, segments


def _judge(obj, target, fn, rom, segments):
    net = object.__new__(Core)
    net._roms = {1: (bytes(rom), segments)}
    net.is_rom = lambda proj: True
    net.console = lambda proj: SimpleNamespace(link_function=match.link)
    net._rom_target = lambda fn: target
    net.symbols = lambda project_id: SymbolTable([Symbol(NAME, VRAM, len(target.code), "func")])
    return JudgeMixin._evaluate(net, fn, {"id": 1}, obj)


def test_own_rodata_comes_from_the_functions_overlay():
    obj, target, fn, rom, segments = _overlay()
    candidate, exact, problems = _judge(obj, target, fn, rom, segments)
    assert exact, problems
    linked = match.LinkResult(candidate, exact, problems)
    assert match.diff(match.canonical_target(target, SymbolTable([Symbol(NAME, VRAM, 0, "func")])), linked).score == 0


def test_the_same_table_with_different_bytes_still_fails():
    obj, target, fn, rom, segments = _overlay()
    rom[SECOND_ROM + RODATA] ^= 0xFF
    candidate, exact, problems = _judge(obj, target, fn, rom, segments)
    assert not exact and any("differs from the ROM" in p for p in problems), problems
