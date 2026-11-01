"""The PlayStation 3 console without a compiler or a game: a hand-made PowerPC64 program and candidate objects (the
reader, the console's place in the registry, the splitter, the reference scan and the judge), and the real demo where
the free PPU compiler is installed (tests/test_consoles.py runs the demo end to end).
"""

import json
import struct
from pathlib import Path

import pytest

from decompnet import arch, consoles
from decompnet.arch import ppc64
from decompnet.consoles.ps3 import elf as ps3elf
from decompnet.consoles.ps3 import split as ps3split
from decompnet.rom import RomError
from decompnet.rom.symbols import Symbol, SymbolTable

ROOT = Path(__file__).resolve().parent.parent

TEXT, DATA = 0x11000, 0x13000  # where the hand-made program's segments are loaded (file offsets 0x1000 and 0x3000)
TOC = DATA + 0x100  # the program's TOC base
GLOBAL = DATA + 0x200  # a global, whose address the TOC slot at TOC + 8 holds

LI_3_5, BLR, NOP, MFLR_R0 = 0x38600005, 0x4E800020, 0x60000000, 0x7C0802A6
STD_R0, STDU_R1, ADDI_R1, LD_R0, MTLR_R0 = 0xF8010010, 0xF821FF91, 0x38210070, 0xE8010010, 0x7C0803A6
LWZ_R9_TOC, LWZ_R3_R9 = 0x81220008, 0x80690000  # lwz r9, 8(r2); lwz r3, 0(r9)
LD_R2_SAVE = 0xE8410028


def words(*ws: int) -> bytes:
    return b"".join(w.to_bytes(4, "big") for w in ws)


def bl(at: int, dest: int) -> int:
    return 0x48000001 | ((dest - at) & 0x03FFFFFC)


# the program: f_leaf, f_call (calls f_leaf), f_glob (loads the global through a TOC slot), then the global
F_LEAF = TEXT
F_CALL = TEXT + 0x10
F_GLOB = TEXT + 0x40
CODE = {
    F_LEAF: words(LI_3_5, BLR),
    F_CALL: words(MFLR_R0, STD_R0, STDU_R1, bl(F_CALL + 12, F_LEAF), LD_R2_SAVE, ADDI_R1, LD_R0, MTLR_R0, BLR),
    F_GLOB: words(LWZ_R9_TOC, LWZ_R3_R9, BLR),
}


def program(abi: str = "ilp32", sections: bool = False, comment: bytes = b"") -> bytes:
    """A big-endian ELF64 PowerPC64 executable: text and data segments, and at e_entry either Sony's table of
    (function, TOC) pairs or one ELFv1 function descriptor."""
    text = bytearray(0x100)
    for addr, code in CODE.items():
        text[addr - TEXT:addr - TEXT + len(code)] = code
    data = bytearray(0x400)
    if abi == "ilp32":
        table = b"".join(struct.pack(">II", a, TOC) for a in CODE)
        entry = DATA
        data[0:len(table)] = table
    else:
        entry = DATA
        data[0:24] = struct.pack(">QQQ", F_LEAF, TOC, 0)
    data[TOC - DATA + 8:TOC - DATA + 8 + (4 if abi == "ilp32" else 8)] = GLOBAL.to_bytes(4 if abi == "ilp32" else 8, "big")
    data[GLOBAL - DATA:GLOBAL - DATA + 4] = (42).to_bytes(4, "big")
    shnum, shoff, tail = 0, 0, b""
    if sections:  # .text, .comment and the section name table
        names = b"\0.text\0.comment\0.shstrtab\0"
        shoff = 0x3000 + len(data)
        heads = [bytes(64),
                 struct.pack(">IIQQQQIIQQ", 1, 1, 6, TEXT, 0x1000, len(text), 0, 0, 4, 0),
                 struct.pack(">IIQQQQIIQQ", 7, 1, 0, 0, shoff + 64 * 4, len(comment), 0, 0, 1, 0),
                 struct.pack(">IIQQQQIIQQ", 16, 3, 0, 0, shoff + 64 * 4 + len(comment), len(names), 0, 0, 1, 0)]
        tail = b"".join(heads) + comment + names
        shnum = 4
    header = b"\x7fELF" + bytes([2, 2, 1, 0x66]) + bytes(8) + struct.pack(
        ">HHIQQQIHHHHHH", 2, 21, 1, entry, 64, shoff, 0, 64, 56, 2, 64, shnum, 3 if sections else 0)
    phdrs = struct.pack(">IIQQQQQQ", 1, 5, 0x1000, TEXT, TEXT, len(text), len(text), 0x1000) + \
        struct.pack(">IIQQQQQQ", 1, 6, 0x3000, DATA, DATA, len(data), len(data) + 0x1000, 0x1000)
    image = bytearray(0x3000 + len(data))
    image[:len(header) + len(phdrs)] = header + phdrs
    image[0x1000:0x1000 + len(text)] = text
    image[0x3000:0x3000 + len(data)] = data
    return bytes(image) + tail


def rel_object(text: bytes, name: str, relocs: list[tuple[int, str, int, int]], *, undefined=(), toc: bytes = b"",
               toc_relocs: list[tuple[int, str, int, int]] = ()) -> bytes:
    """A relocatable ELF64 big-endian PowerPC64 object with one function (the dotted symbol `.name`, as Sony's GCC
    names it), text relocations (offset, symbol, type, addend; the symbol `.toc` is the TOC's section) and TOC slots."""
    syms = ["", ".text", ".toc", "." + name, *undefined]
    index = {n: i for i, n in enumerate(syms)}
    strtab = b"\0"
    entries = [bytes(24)]
    for n in syms[1:]:
        at = len(strtab)
        strtab += n.encode() + b"\0" if not n.startswith(".t") else b""
        if n == ".text":
            entries.append(struct.pack(">IBBHQQ", 0, 3, 0, 1, 0, 0))
        elif n == ".toc":
            entries.append(struct.pack(">IBBHQQ", 0, 3, 0, 3, 0, 0))
        elif n == "." + name:
            entries.append(struct.pack(">IBBHQQ", at, 0x12, 0, 1, 0, len(text)))
        else:
            entries.append(struct.pack(">IBBHQQ", at, 0x10, 0, 0, 0, 0))
    symtab = b"".join(entries)

    def rela(rs):
        return b"".join(struct.pack(">QQq", off, (index[s] << 32) | t, add) for off, s, t, add in rs)

    names = b"\0.text\0.rela.text\0.toc\0.rela.toc\0.symtab\0.strtab\0.shstrtab\0"
    bodies = [(b"", 0, 0, 0, 0, 0, 0), (text, 1, 6, 0, 0, 4, 0), (rela(relocs), 4, 0x40, 5, 1, 8, 24),
              (toc, 1, 3, 0, 0, 8, 0), (rela(toc_relocs), 4, 0x40, 5, 3, 8, 24), (symtab, 2, 0, 6, 3, 8, 24),
              (strtab, 3, 0, 0, 0, 1, 0), (names, 3, 0, 0, 0, 1, 0)]
    name_at = {k: names.index(n) for k, n in enumerate([b"", b".text", b".rela.text", b".toc", b".rela.toc", b".symtab",
                                                         b".strtab", b".shstrtab"])}
    blob, offsets = b"", []
    for body, *_ in bodies:
        offsets.append(0x40 + len(blob))
        blob += body + bytes(-len(body) % 8)
    shoff = 0x40 + len(blob)
    heads = b""
    for k, (body, kind, flags, link, info, align, entsize) in enumerate(bodies):
        heads += struct.pack(">IIQQQQIIQQ", name_at[k] if k else 0, kind if k else 0, flags, 0, offsets[k], len(body),
                             link, info, align, entsize)
    # (.symtab's sh_info is the first non-local symbol: the dotted function and the externs are global)
    heads = heads.replace(b"", b"")
    header = b"\x7fELF" + bytes([2, 2, 1, 0x66]) + bytes(8) + struct.pack(
        ">HHIQQQIHHHHHH", 1, 21, 1, 0, 0, shoff, 0, 64, 0, 0, 64, len(bodies), 7)
    obj = bytearray(header + blob + heads)
    # section 5 (.symtab): sh_info = 3 (index of the first global symbol)
    sh5 = shoff + 64 * 5
    obj[sh5 + 44:sh5 + 48] = struct.pack(">I", 3)
    return bytes(obj)


def symbols_for() -> SymbolTable:
    return SymbolTable([Symbol("f_leaf", F_LEAF, 8, "func"), Symbol("f_call", F_CALL, 36, "func"),
                        Symbol("f_glob", F_GLOB, 12, "func"), Symbol("g", GLOBAL, 4, "data"),
                        Symbol("g_other", GLOBAL + 8, 4, "data")])


def reader(image: bytes):
    elf = ps3elf.parse(image)
    return lambda addr, n: ps3elf.read_at(image, elf, addr, n)


def targets(image: bytes) -> dict[int, ppc64.PpcTarget]:
    """The splitter's functions as judge targets, by address."""
    functions, _, _ = ps3split.split(image, log=lambda *_: None)
    return {f.vram: ppc64.PpcTarget(f.name, f.vram, f.code, f.refs, f.ptr, f.toc) for f in functions}


# ----------------------------------------------------------------------------------------------------- the reader

def test_a_ps3_elf_is_read_and_a_self_is_refused():
    image = program()
    assert ps3elf.is_ps3_elf(image) and not ps3elf.is_ps3_elf(image[:0x20])
    data, info = ps3elf.read_elf(image, "EBOOT.ELF")
    assert data == image and info["platform"] == "ps3" and info["title"] == "PlayStation 3 game"
    assert info["abi"] == "ilp32" and info["toc"] == TOC and info["descriptors"] == 3 and len(info["sha1"]) == 40
    with pytest.raises(RomError, match="decrypt your own copy"):
        ps3elf.read_elf(b"SCE\0" + bytes(64), "EBOOT.BIN")
    assert consoles.get("ps3").sniff(b"SCE\0" + bytes(64), b"")  # (so the import says why instead of "not a game")


def test_the_code_is_the_lowest_segment_with_a_payload_and_the_descriptor_says_the_abi():
    elf = ps3elf.parse(program())
    assert elf.code.vaddr == TEXT and elf.osabi == 0x66
    descriptor = ps3elf.module_descriptor(program(), elf)
    assert descriptor.abi == "ilp32" and descriptor.functions == list(CODE) and descriptor.toc == TOC
    lp64 = ps3elf.module_descriptor(program("lp64"), ps3elf.parse(program("lp64")))
    assert lp64.abi == "lp64" and lp64.functions == [F_LEAF] and lp64.toc == TOC


def test_the_compiler_is_guessed_from_the_comment_when_the_headers_survive():
    sony = b"GCC: (GNU) 4.1.1 (CELL 4.1.2.7, RTTI, $Rev: 1826 $)\0"
    assert ps3elf.read_elf(program(sections=True, comment=sony), "game.elf")[1]["compiler_guess"] == "PS3-GCC-4.1.1"
    assert ps3elf.read_elf(program(sections=True, comment=b"GCC: (GNU) 7.2.0\0"))[1]["compiler_guess"] == "PS3DEV"


# --------------------------------------------------------------------------------------------------- the console

def test_the_console_is_asked_before_the_ps2_and_reads_the_program():
    ps3 = consoles.get("ps3")
    order = [c.id for c in consoles.startable()]
    assert order.index("ps3") < order.index("ps2")
    assert consoles.identify(program()) is ps3
    assert ps3.arch == "ppc64" and ps3.big_endian and ps3.m2c_target() == "" and arch.get("ppc64").id == "ppc64"
    assert ps3.default_flags("ps3dev-gcc", 0)[-1] == "-falign-functions=4"
    picked = ps3.read_game(program(), "EBOOT.ELF")
    assert picked.big_endian and picked.filename == "game.elf" and picked.info["small_data"] is False
    with pytest.raises(RomError, match="rebuildable"):
        ps3.export_project(None, "x", Path("."))


# ------------------------------------------------------------------------------------------------- the splitter

def test_the_splitter_finds_the_descriptors_functions_and_what_they_refer_to():
    image = program()
    functions, data, _ = ps3split.split(image, log=lambda *_: None)
    assert [f.vram for f in functions] == list(CODE)
    call = next(f for f in functions if f.vram == F_CALL)
    assert (call.refs[0][1], call.refs[0][2]) == (ppc64.R_PPC64_REL24, F_LEAF) and "bl func_00011000" in call.asm
    glob = next(f for f in functions if f.vram == F_GLOB)
    assert glob.refs == [(2, ppc64.R_PPC64_TOC16, GLOBAL, TOC + 8)] and f"# D_{GLOBAL:08X}" in glob.asm
    assert [d.vram for d in data] == [GLOBAL] and data[0].name == f"D_{GLOBAL:08X}"
    assert ps3split.code_from_asm(call.asm) == (F_CALL, call.code)  # the stored listing rebuilds the function


def test_functions_the_descriptor_does_not_list_come_from_calls_and_prologues():
    image = bytearray(program())
    extra = TEXT + 0x4C  # a function nothing lists and nothing calls: found by its prologue after a return
    image[0x1000 + 0x4C:0x1000 + 0x4C + 16] = words(MFLR_R0, STD_R0, STDU_R1, BLR)
    table = ps3elf.module_descriptor(bytes(image), ps3elf.parse(bytes(image)))
    assert extra not in table.functions
    functions, _, _ = ps3split.split(bytes(image), log=lambda *_: None)
    assert extra in {f.vram for f in functions}


def test_a_function_can_be_cut_and_joined_again():
    functions, _, _ = ps3split.split(program(), log=lambda *_: None)
    call = next(f for f in functions if f.vram == F_CALL)
    row = {"name": call.name, "vram": call.vram, "target": call.code, "asm": call.asm, "rom_offset": call.rom,
           "relocs": json.dumps([list(r[:3]) for r in call.refs]), "meta": json.dumps(consoles.get("ps3").function_meta(call))}
    head, tail = ps3split.split_function(row, F_CALL + 16, "func_tail")
    assert head.code + tail.code == call.code and head.refs and not tail.refs
    with pytest.raises(ValueError):
        ps3split.split_function(row, F_CALL + 2, "func_bad")
    joined = ps3split.join_saved_functions([row], F_CALL, F_CALL + len(call.code), lambda a, n: None)
    assert joined.code == call.code and joined.refs == call.refs


# ----------------------------------------------------------------------------------------------------- the judge

def judge(obj: bytes, name: str, image: bytes | None = None, at: int = F_GLOB):
    image = image or program()
    return ppc64.link(obj, name, targets(image)[at], symbols_for(), reader(image))


def glob_object(slot_symbol: str = "g", slot_type: int = 1, **kw) -> bytes:
    return rel_object(words(LWZ_R9_TOC & 0xFFFF0000, LWZ_R3_R9, BLR), "f_glob", [(2, ".toc", 47, 0)],
                      undefined=[slot_symbol], toc=bytes(8), toc_relocs=[(0, slot_symbol, slot_type, 0)], **kw)


def test_a_candidate_that_reaches_the_global_through_the_toc_matches():
    res = judge(glob_object(), "f_glob")
    assert res.exact and not res.problems and res.candidate.code == CODE[F_GLOB]


def test_a_candidate_that_reaches_another_global_does_not():
    res = judge(glob_object("g_other"), "f_glob")
    assert not res.exact and any("g_other" in p for p in res.problems)


def test_a_candidate_naming_nothing_the_game_knows_is_told_so():
    res = judge(glob_object("somewhere"), "f_glob")
    assert not res.exact and any("not a known symbol" in p for p in res.problems)


def test_a_call_matches_the_right_callee_and_takes_the_linkers_toc_restore():
    code = words(MFLR_R0, STD_R0, STDU_R1, 0x48000001, NOP, ADDI_R1, LD_R0, MTLR_R0, BLR)  # (the compiler's nop after bl)
    obj = rel_object(code, "f_call", [(12, "f_leaf", 10, 0)], undefined=["f_leaf"])
    res = judge(obj, "f_call", at=F_CALL)
    assert res.exact and res.candidate.code == CODE[F_CALL]  # the game has ld r2,40(r1) where the object has a nop
    wrong = rel_object(code, "f_call", [(12, "f_glob", 10, 0)], undefined=["f_glob"])
    res = judge(wrong, "f_call", at=F_CALL)
    assert not res.exact and any("you call f_glob where the original calls f_leaf" in p for p in res.problems)


def test_different_instructions_next_to_a_right_reference_still_fail():
    obj = rel_object(words(LWZ_R9_TOC & 0xFFFF0000, 0x80690004, BLR), "f_glob", [(2, ".toc", 47, 0)],
                     undefined=["g"], toc=bytes(8), toc_relocs=[(0, "g", 1, 0)])
    assert not judge(obj, "f_glob").exact


def test_eight_byte_slots_and_descriptors_work_too():
    image = program("lp64")
    elf = ps3elf.parse(image)
    code = words(0xE9220008, LWZ_R3_R9, BLR)  # ld r9, 8(r2): a pointer-sized slot
    refs = ppc64.scan_refs(code, F_GLOB, TOC, 8, reader(image),
                           lambda a: any(s.vaddr <= a < s.vaddr + s.memsz for s in elf.loads))
    target = ppc64.PpcTarget("f_glob", F_GLOB, code, refs, 8, TOC)
    obj = rel_object(words(0xE9220000, LWZ_R3_R9, BLR), "f_glob", [(2, ".toc", 63, 0)], undefined=["g"], toc=bytes(8),
                     toc_relocs=[(0, "g", 38, 0)])  # ELFv1: an ADDR64 slot
    res = ppc64.link(obj, "f_glob", target, symbols_for(), reader(image))
    assert res.exact and refs[0][3] == TOC + 8


def test_a_function_pointer_slot_holds_a_descriptor_the_game_reads_through():
    image = bytearray(program())
    desc = DATA + 0x300  # a descriptor for f_leaf, and the TOC slot at +8 pointing at it instead of the global
    image[0x3000 + 0x300:0x3000 + 0x308] = struct.pack(">II", F_LEAF, TOC)
    image[0x3000 + 0x108:0x3000 + 0x10C] = desc.to_bytes(4, "big")
    image = bytes(image)
    assert judge(glob_object("f_leaf"), "f_glob", image).exact
    assert not judge(glob_object("f_call"), "f_glob", image).exact


def test_a_function_missing_from_the_object_is_said():
    res = judge(glob_object(), "nothing")
    assert res.candidate is None and "no function named `nothing`" in res.problems[0]


# ----------------------------------------------------------------------------------------- hand-written assembly

def test_system_calls_and_linker_stubs_are_assembly():
    assert "system call" in ppc64.asm_reason(words(0x39600001, 0x44000002, BLR))
    assert "count register" in ppc64.asm_reason(words(0x3D800001, 0x818C0000, 0x7D8903A6, 0x4E800420))
    assert ppc64.asm_reason(CODE[F_CALL]) is None and consoles.get("ps3").asm_reason(CODE[F_LEAF]) is None


# ----------------------------------------------------------------------------------------------- the real demo

def demo_program() -> bytes | None:
    key = ROOT / "projects" / "demo-ps3" / "build" / "demo.json"
    if not key.exists():
        return None
    return (key.parent / json.loads(key.read_text())["game"]).read_bytes()


def test_without_section_headers_the_demo_splits_the_same_way():
    """A retail program's section names are fragments: the descriptor, calls and prologues must find the same
    functions the section names (and the program's own descriptors) do."""
    image = demo_program()
    if image is None:
        pytest.skip("build projects/demo-ps3 first (needs the ps3dev toolchain)")
    with_sections, _, _ = ps3split.split(image, log=lambda *_: None)
    bare = bytearray(image)
    bare[40:48] = bytes(8)  # e_shoff = 0
    bare[60:64] = bytes(4)  # e_shnum, e_shstrndx
    without, _, _ = ps3split.split(bytes(bare), log=lambda *_: None)
    have, found = {f.vram for f in with_sections}, {f.vram for f in without}
    # what only the section headers know: functions nothing calls and no prologue shows (leaf libc code); the
    # descriptor lists just the entry, so calls and prologues must find most of the rest
    assert len(found & have) >= 0.8 * len(have), sorted(have - found)


# ---------------------------------------------------------------------------------- the program's own symbols

def test_the_programs_own_names_name_what_the_splitter_finds():
    from decompnet.consoles.ps3 import symbols

    names, functions = symbols.from_lines(["f_leaf = 0x00011000; // type:func size:0x8", "g = 0x00013200; // size:0x4"])
    assert names == {F_LEAF: "f_leaf", GLOBAL: "g"} and functions == {F_LEAF}
    found, data, _ = ps3split.split(program(), log=lambda *_: None, names=names, named=functions)
    assert [f.name for f in found][:1] == ["f_leaf"] and [d.name for d in data] == ["g"]
    call = next(f for f in found if f.vram == F_CALL)
    assert "bl f_leaf" in call.asm and ps3split.code_from_asm(call.asm) == (F_CALL, call.code)


def test_a_demo_that_kept_its_symbols_imports_with_their_names(tmp_path):
    """A function's symbol points at its descriptor in .opd, not at its code (symbols.py): the import names the
    functions and data by them."""
    from decompnet.app.engine import RomNetwork
    from decompnet.consoles.ps3 import symbols

    key = ROOT / "projects" / "demo-ps3" / "build" / "demo.json"
    elf = key.parent / "demoq_ps3.elf"
    if not elf.exists():
        pytest.skip("build projects/demo-ps3 first (needs the ps3dev toolchain)")
    expected = json.loads(key.read_text())["symbols"]
    named = {n.name: n.address for n in symbols.read(elf.read_bytes())}
    assert named["abs_s32"] == expected["abs_s32"] and named["g_toc_probe"] == expected["g_toc_probe"]
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(elf, confirmed=True, log=lambda *_: None)
    row = net.store.one("SELECT name FROM functions WHERE project_id=? AND vram=?", net.project_row(slug)["id"],
                        expected["abs_s32"])
    assert row["name"] == "abs_s32"
