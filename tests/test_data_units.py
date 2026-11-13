"""Data units (app/data_units.py): where a game's initialised data is, and its symbols' C judged against it, on a
homemade N64 image: a boot stub that clears .bss, one code segment, and its data after it."""
import hashlib
import json
import struct

import pytest

from decompnet import toolchains
from decompnet.app import data_units
from decompnet.app.engine import RomNetwork
from decompnet.consoles.n64.rom import bss_start
from decompnet.port.readiness import readiness

TEXT, DATA, BSS = 0x80000400, 0x80000500, 0x80000540
# lui $t0, 0x8000; addiu $t0, $t0, 0x540; lui $t1, 0; addiu $t1, $t1, 0x40; sw $zero, 0($t0); sw $zero, 4($t0) ...
STUB = struct.pack(">8I", 0x3C088000, 0x25080540, 0x3C090000, 0x25290040, 0xAD000000, 0xAD000004,
                   0x21080008, 0x2129FFF8)
# the data: a table of two pointers to the game's `boot` function and a count (at DATA+0x10), then zeros to .bss
GAME_DATA = struct.pack(">4I", TEXT, TEXT, 0, 0) + struct.pack(">I", 7) + bytes(0x40 - 0x14)


def image():
    rom = bytearray(0x1000) + STUB + bytes(0x100 - len(STUB)) + GAME_DATA + bytes(0x100)
    rom[0:4] = b"\x80\x37\x12\x40"
    rom[8:12] = TEXT.to_bytes(4, "big")
    return bytes(rom)


def test_the_boot_stub_says_where_bss_starts():
    assert bss_start(image()) == BSS
    assert bss_start(bytes(0x2000)) is None  # a game that boots its own way: found otherwise


def build(tmp_path):
    """The homemade project (test_data_agents builds it too)."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    rom = image()
    (tmp_path / "game.z64").write_bytes(rom)
    cfg = dict(kind="rom", platform="n64", big_endian=True,
               rom=dict(file="game.z64", sha1=hashlib.sha1(rom).hexdigest()),
               segments=[dict(name="main", rom_start=0x1000, rom_end=0x1100, vram=TEXT)],
               compiler={"toolchain": "gcc-2.7.2-kmc", "flags": ["-G0", "-mips3", "-mgp32", "-O2"]})
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                         ("game", "game", "mips", 1, str(tmp_path), json.dumps(cfg))).lastrowid
        # the game's code refers to the table, the count, and calls itself (a call says nothing of data)
        relocs = [[0, 5, DATA], [4, 6, DATA], [8, 5, DATA + 0x10], [12, 6, DATA + 0x10], [16, 4, TEXT]]
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,status,matched_source,relocs,meta) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (pid, "boot", 32, 8, TEXT, "matched",
                                                 "void boot(void) { D_80000500[0](); D_80000510++; }",
                                                 json.dumps(relocs), "{}"))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "boot", TEXT, 32, "func"))
    return net


@pytest.fixture
def project(tmp_path):
    net = build(tmp_path)
    yield net
    if hasattr(net, "autosave"):
        net.autosave.close()


def test_the_data_after_the_code_is_read_and_its_units_bounded_by_the_game_s_references(project):
    net = project
    with net.progress.view():
        proj = net.project_row("game")
        assert data_units.initialised_data(net, proj) == [{"rom_start": 0x1100, "vram": DATA, "size": 0x40}]
        unit = data_units.unit(net, "game", "D_80000500")
    assert unit["span"] == 0x10 and unit["next"] == "D_80000510" and unit["used_by"] == ["boot"]
    assert bytes.fromhex(unit["bytes"]) == GAME_DATA[:0x10] and not unit["bss"]


@pytest.fixture
def compiles():
    if not toolchains.installed("gcc-2.7.2-kmc"):
        pytest.skip("Pinned KMC compiler is not installed; never download it here.")


def test_a_matched_unit_is_kept_and_the_readiness_report_counts_it(project, compiles):
    net = project
    table = "extern void boot(void);\nvoid (*D_80000500[4])(void) = { boot, boot, 0, 0 };"
    with net.progress.view():
        wrong = data_units.compile_data(net, "game", "D_80000500", table.replace("boot, boot, 0", "boot, 0, 0"))
        assert not wrong.exact
        assert data_units.unit(net, "game", "D_80000500")["status"] == "open"
        assert data_units.compile_data(net, "game", "D_80000500", table).exact
        # a worse attempt after a match doesn't replace the matched C
        assert not data_units.compile_data(net, "game", "D_80000500", "s32 D_80000500[4] = {1};").exact
        unit = data_units.unit(net, "game", "D_80000500")
        assert unit["status"] == "matched" and unit["source"] == table
        report = readiness(net, "game")
    assert report["data"]["in_c"] == 1 and report["data"]["symbols"] == 1  # D_80000510 is still by address
    assert "Saved as its C" in data_units.describe(data_units.DataResult(True, size=16, span=16), "D_80000500")


def test_data_units_are_for_rom_projects(project):
    net = project
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE slug='game'", (json.dumps({"kind": "fork", "platform": "gc"}),))
    from decompnet.network import NetworkError
    with net.progress.view(), pytest.raises(NetworkError, match="project's own source files"):
        data_units.unit(net, "game", "D_80000500")
