"""A PlayStation 3 program's imports and kernel calls (consoles/ps3/imports.py, nids.py): the NID hash, the import tables
a program carries, the stubs and `sc` functions recognised by their shape, and the engine naming them once and setting
the import stubs aside. A hand-made program: the free PPU toolchain ships no PSL1GHT stub libraries, so the demo
has no import (the tables are the SDK's layout, read from its linker script and `sprxlinker`)."""

import json
import struct

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.consoles import ps3
from decompnet.consoles.ps3 import elf as ps3elf
from decompnet.consoles.ps3 import imports, nids

from .test_ps3 import BLR, CODE, DATA, TEXT, program, words

PARAM, RECORD, LIBRARY, NID_LIST, SLOTS = (DATA + off for off in (0x310, 0x330, 0x3A0, 0x3C0, 0x3D0))
VIDEO = 0xE558748D  # cellSysutil's videoGetResolution in PSL1GHT's list
UNKNOWN = 0x12345678
STUB_A, STUB_B, CALL_GETPID, LEAF = TEXT + 0x80, TEXT + 0xA0, TEXT + 0xC0, TEXT
LIS_R12 = 0x3D800000
LWZ_R12 = 0x818C0000
TRAMPOLINE_TAIL = (0x800C0000, 0x804C0004, 0x7C0903A6, 0x4E800420)  # lwz r0,0(r12); lwz r2,4(r12); mtctr r0; bctr


def stub(slot: int) -> bytes:
    return words(LIS_R12 | (slot >> 16), LWZ_R12 | (slot & 0xFFFF), *TRAMPOLINE_TAIL)


def syscall_stub(number: int) -> bytes:
    return words(0x39600000 | number, 0x44000002, BLR)  # li r11,N; sc; blr


def with_imports() -> bytes:
    """The hand-made program with an import table for one library (two functions), two stubs on those slots, and a
    kernel call (getpid)."""
    image = bytearray(program())
    code = {STUB_A: stub(SLOTS), STUB_B: stub(SLOTS + 4), CALL_GETPID: syscall_stub(1)}
    for addr, body in code.items():
        image[0x1000 + addr - TEXT:0x1000 + addr - TEXT + len(body)] = body
    table = b"".join(struct.pack(">II", a, DATA + 0x100) for a in [*CODE, *code])
    image[0x3000:0x3000 + len(table)] = table
    data = bytearray(0x400)
    data[PARAM - DATA:PARAM - DATA + 32] = struct.pack(">8I", 0x28, imports.PRX_PARAM_MAGIC, 2, 0, 0, 0, RECORD,
                                                         RECORD + imports.RECORD_BYTES)
    data[RECORD - DATA:RECORD - DATA + 44] = struct.pack(">IHHII3I", 0x2C000001, 9, 2, 0, 0, LIBRARY, NID_LIST, SLOTS) \
        + bytes(44 - 28)
    data[LIBRARY - DATA:LIBRARY - DATA + 12] = b"cellSysutil\0"
    data[NID_LIST - DATA:NID_LIST - DATA + 8] = struct.pack(">II", VIDEO, UNKNOWN)
    image[0x3000 + 0x300:0x3000 + 0x400] = data[0x300:]
    image[56:58] = struct.pack(">H", 3)  # e_phnum: a third program header, the process parameter block's
    image[64 + 56 * 2:64 + 56 * 3] = struct.pack(">IIQQQQQQ", imports.PT_PROC_PRX_PARAM, 4, 0x3000 + PARAM - DATA, PARAM,
                                                 PARAM, 0x28, 0x28, 4)
    return bytes(image)


def test_the_nid_is_the_first_four_bytes_of_the_sha1_with_the_suffix_read_little_endian():
    assert nids.nid_of("sys_time_get_system_time") == 0x8461E528
    assert nids.nid_of("sys_process_exit") == 0xE6F2C1E7
    assert nids.nid_of("sys_process_is_stack") == 0x4F7172C9
    # the SDK's own list gives those three the NIDs the hash makes (under its names, not the canonical ones)
    listed = {nid for names in nids.database().values() for nid in names}
    assert {0x8461E528, 0xE6F2C1E7, 0x4F7172C9} <= listed


def test_the_sdks_lists_name_functions_and_kernel_calls():
    assert nids.name("cellSysutil", VIDEO) == "videoGetResolution" and nids.name("cellSysutil", UNKNOWN) is None
    assert nids.syscalls()[1] == "sys_process_getpid" and len(nids.database()) >= 20 and len(nids.syscalls()) >= 200
    libraries, calls = nids.from_sources({
        "ppu/sprx/libx/config.h": '#define LIBRARY_NAME "cellX"', "ppu/sprx/libx/exports.h": "EXPORT(xOne, 0x0a);\nEXPORT_VA(xTwo, 0xB, 3);",
        "ppu/sprx/liby/exports.h": "EXPORT(yOne, 0x1);",  # (no library name: left out)
        "ppu/include/lv2/syscalls.h": "#define SYSCALL_PROCESS_GETPID 1\n#define MAX_NUM_OF_SYSTEM_CALLS 1024"})
    assert libraries == {"cellX": {0x0A: "xOne", 0x0B: "xTwo"}} and calls == {1: "sys_process_getpid"}


def test_the_import_tables_are_read_from_the_process_parameter_block():
    found = imports.read_imports(with_imports())
    assert [(i.library, i.nid, i.slot) for i in found] == [("cellSysutil", VIDEO, SLOTS), ("cellSysutil", UNKNOWN, SLOTS + 4)]
    assert [i.symbol for i in found] == ["cellSysutil_videoGetResolution", "cellSysutil_12345678"]
    assert imports.read_imports(program()) == [] and imports.read_imports(b"nothing") == []  # (no tables: no imports)


def test_a_stub_says_its_slot_and_a_kernel_call_its_number():
    slots = {SLOTS, SLOTS + 4}
    assert imports.stub_slot(stub(SLOTS + 4), slots) == SLOTS + 4
    assert imports.stub_slot(stub(SLOTS + 8), slots) is None and imports.stub_slot(CODE[LEAF], slots) is None
    assert imports.syscall_number(syscall_stub(145)) == 145 and imports.syscall_number(CODE[LEAF]) is None
    assert imports.syscall_number(words(0x39600001, BLR)) is None  # (an li with no sc)


def test_generated_names_are_replaced_and_a_second_stub_gets_a_suffix():
    found = imports.read_imports(with_imports())
    functions = [("func_a", stub(SLOTS)), ("func_b", stub(SLOTS)), ("func_c", syscall_stub(1)), ("mine", stub(SLOTS + 4)),
                 ("func_d", syscall_stub(9999)), ("func_e", CODE[LEAF])]
    renames, stubs = imports.names(functions, found, taken=set())
    assert renames == [("func_a", "cellSysutil_videoGetResolution"), ("func_b", "cellSysutil_videoGetResolution_2"),
                       ("func_c", "sys_process_getpid")]
    assert stubs == {"func_a", "func_b"}  # (the kernel call is set aside by its `sc`, not here)


def test_a_ps3_game_names_its_imports_once_and_sets_the_stubs_aside(tmp_path):
    (tmp_path / "game.elf").write_bytes(with_imports())
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {"kind": "rom", "platform": "ps3", "big_endian": True, "gp": 0, "rom": {"program": "game.elf"},
           "compiler": {"toolchain": "ps3dev-gcc", "flags": ["-O2"]}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug, name, root, config) VALUES('game', 'Game', ?, ?)",
                         (str(tmp_path), json.dumps(cfg))).lastrowid
        for vram, code in ((STUB_A, stub(SLOTS)), (CALL_GETPID, syscall_stub(1)), (LEAF, CODE[LEAF])):
            db.execute("INSERT INTO functions(project_id, name, size, n_insns, target, asm, vram) VALUES(?,?,?,?,?,'',?)",
                       (pid, f"func_{vram:08X}", len(code), len(code) // 4, code, vram))
            db.execute("INSERT INTO symbols(project_id, name, address, size, kind) VALUES(?,?,?,?,'func')",
                       (pid, f"func_{vram:08X}", vram, len(code)))
        db.execute("INSERT INTO context_versions(project_id, version, header, message, created_at, unit) "
                   "VALUES(?, 1, ?, 'basic types', 0, '')", (pid, f"void func_{STUB_A:08X}(void);"))
    assert ps3.name_system_calls(net, "game", log=lambda *_: None) == 2
    rows = {r["vram"]: r for r in net.store.all("SELECT vram, name, status, meta FROM functions")}
    assert rows[STUB_A]["name"] == "cellSysutil_videoGetResolution" and rows[STUB_A]["status"] == "asm"
    assert "no C is behind it" in json.loads(rows[STUB_A]["meta"])["asm_code"]
    assert rows[CALL_GETPID]["name"] == "sys_process_getpid" and rows[LEAF]["name"] == f"func_{LEAF:08X}"
    assert net._context(net._project("game")["id"], "")["header"] == "void cellSysutil_videoGetResolution(void);"
    assert ps3.name_system_calls(net, "game", log=lambda *_: None) == 0  # once


def test_the_program_parameter_block_is_found_by_its_magic():
    image = bytearray(with_imports())
    image[0x3000 + 0x314:0x3000 + 0x318] = bytes(4)  # a block without the magic
    assert imports.read_imports(bytes(image)) == []
    assert ps3elf.is_ps3_elf(bytes(image))


@pytest.mark.parametrize("cut", [0x3000 + 0x330, 0x3000 + 0x3A0])
def test_a_damaged_table_gives_no_wrong_names(cut):
    image = bytearray(with_imports())
    image[cut:cut + 16] = bytes(16)  # the record's header, or the library's name
    assert imports.read_imports(bytes(image)) == []


# What a decrypted retail program showed (a PS3 game of 22,000 functions, SDK 2.5.0): its stubs build the slot's address
# with li and oris and save the TOC first; its section names and .comment are zeros, as decryption restores only what
# its segments hold; and its process parameters say the SDK.
def retail_stub(slot: int) -> bytes:
    hi, lo = (slot + 0x8000) >> 16, slot & 0xFFFF
    return words(0x39800000, 0x658C0000 | hi, 0x818C0000 | lo,  # li r12,0; oris r12,r12,hi; lwz r12,lo(r12)
                 0xF8410028, 0x800C0000, 0x804C0004, 0x7C0903A6, 0x4E800420)  # std r2,40(r1); ...; mtctr r0; bctr


def test_a_retail_stub_is_known_by_the_slot_it_builds_and_every_stub_starts_a_function():
    slots = {0x75FB40, 0x75FB44}
    assert imports.stub_slot(retail_stub(0x75FB40), slots) == 0x75FB40  # (lo 0xFB40: oris 0x76, then -0x4C0)
    code = words(BLR) + retail_stub(0x75FB40) + retail_stub(0x75FB44)  # (nothing calls the second)
    assert imports.stub_starts(code, [(0x1000, 0, len(code))], slots) == {0x1004, 0x1024}


def test_a_retail_programs_nameless_sections_still_say_where_its_code_is():
    from decompnet.consoles.ps3 import split

    image = bytearray(program(sections=True))
    names = b"\0.text\0.comment\0.shstrtab\0"
    image[len(image) - len(names):] = bytes(len(names))  # (decrypted: the name table is zeros)
    elf = ps3elf.parse(bytes(image))
    assert not any(n.startswith(".") for n in elf.sections) and any(s.flags & ps3elf.EXECUTABLE for s in elf.sections.values())
    assert split.code_ranges(bytes(image), elf) == [(TEXT, 0x1000, 0x100)]


def test_the_sdk_is_read_from_the_process_parameters():
    param = struct.pack(">IIII", 0x20, 0x13BCC5F6, 0x9000, 0x00250001) + bytes(16)
    elf = ps3elf.Ps3Elf(0, [ps3elf.Segment(ps3elf.PROCESS_PARAM, 0, 0, 0, len(param), len(param))], {}, 0x66)
    assert ps3elf.sdk_version(param, elf) == "2.5.0"


def test_the_title_comes_from_the_discs_param_sfo(tmp_path):
    from .test_psp import _sfo

    usrdir = tmp_path / "PS3_GAME" / "USRDIR"
    usrdir.mkdir(parents=True)
    (usrdir / "EBOOT.ELF").write_bytes(program())
    (tmp_path / "PS3_GAME" / "PARAM.SFO").write_bytes(_sfo({"TITLE": "Demo Quest", "TITLE_ID": "BCUS00000"}))
    picked = ps3.PS3.read_game(usrdir / "EBOOT.ELF")
    assert (picked.info["title"], picked.info["game_code"], picked.info["region"]) == ("Demo Quest", "BCUS00000",
                                                                                    "North America")


def test_a_game_built_with_a_minimal_toc_builds_with_it_and_search_flags_tries_it():
    """A retail game's globals were reached through a table of each file's own in r30 (`std r30,-16(r1); lwz r30,N(r2)`,
    GCC's -mminimal-toc): a getter matched only with the flag. The import reads it off the code."""
    from decompnet import consoles
    from decompnet.app.engine.compilers import CompilerMixin

    getter = words(0xFBC1FFF0, 0x83C28000, 0x813E8000, 0xEBC1FFF0, 0x80690000, 0x7C6307B4, BLR)
    elf = ps3elf.Ps3Elf(0, [ps3elf.Segment(1, 5, 0, 0x10000, len(getter) * 8, len(getter) * 8)], {}, 0x66)
    assert ps3elf.minimal_toc(getter * 8, elf) and not ps3elf.minimal_toc(getter * 7 + bytes(len(getter)), elf)
    cfg = {"platform": "ps3", "rom": {"minimal_toc": True}}
    assert "-mminimal-toc" in consoles.default_flags(cfg, "ps3dev-gcc")
    assert "-mminimal-toc" not in consoles.default_flags({"platform": "ps3", "rom": {}}, "ps3dev-gcc")
    assert ["-O2", "-mminimal-toc"] in CompilerMixin.flag_variants(None, "ps3dev-gcc", ["-O2"], ps3.PS3.flag_extras)
