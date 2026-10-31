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
