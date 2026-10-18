"""PS2 kernel call stubs get their names (consoles/ps2/syscalls.py): the stub's shape is recognised, the number in v1
names it, and the engine renames them all at once, everywhere the old names were (no compiler needed: nothing is
matched yet)."""

import json
import struct

from decompnet.app.engine import RomNetwork
from decompnet.consoles.ps2 import name_kernel_calls, syscalls

SET_V1 = 0x24030000


def stub(number: int, nop: bool = True) -> bytes:
    words = [SET_V1 | (number & 0xFFFF), syscalls.SYSCALL, syscalls.JR_RA] + ([syscalls.NOP] if nop else [])
    return struct.pack(f"<{len(words)}I", *words)


def test_a_kernel_stub_says_its_call_number():
    assert syscalls.stub_number(stub(0x64)) == 0x64 and syscalls.SYSCALLS[0x64] == "FlushCache"
    assert syscalls.stub_number(stub(-0x43)) == -0x43 and syscalls.SYSCALLS[-0x43] == "iSignalSema"  # interrupt calls
    assert syscalls.stub_number(stub(0x20, nop=False)) == 0x20
    assert syscalls.stub_number(stub(0x64)[:8] + struct.pack("<2I", 0x03E00008, 0x24020001)) is None  # more after it
    assert syscalls.stub_number(struct.pack("<4I", 0x27BDFFF0, 0, 0x03E00008, 0)) is None  # an ordinary function


def test_only_generated_names_are_replaced_and_a_second_stub_gets_a_suffix():
    functions = [("func_00100000", stub(0x64)), ("func_00100010", stub(0x64)), ("MyFlush", stub(0x20)),
                 ("func_00100030", stub(0x7FFF)), ("func_00100040", b"\0" * 16)]
    assert syscalls.stub_names(functions, taken=set()) == [("func_00100000", "FlushCache"),
                                                           ("func_00100010", "FlushCache_2")]
    assert syscalls.stub_names(functions[:1], taken={"FlushCache"}) == [("func_00100000", "FlushCache_2")]


def test_a_ps2_game_names_its_kernel_calls_once(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {"kind": "rom", "platform": "ps2", "big_endian": False, "gp": 0,
           "compiler": {"toolchain": "ee-gcc-2.96", "flags": ["-O2", "-G0"]}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug, name, root, config) VALUES('game', 'Game', ?, ?)",
                         (str(tmp_path), json.dumps(cfg))).lastrowid
        for name, vram, code in (("func_00100000", 0x100000, stub(0x64)), ("func_00100010", 0x100010, stub(0x20)),
                                 ("func_00100020", 0x100020, struct.pack("<4I", 0x27BDFFF0, 0, 0x03E00008, 0))):
            db.execute("INSERT INTO functions(project_id, name, size, n_insns, target, asm, vram) VALUES(?,?,16,4,?,'',?)",
                       (pid, name, code, vram))
            db.execute("INSERT INTO symbols(project_id, name, address, size, kind) VALUES(?,?,?,16,'func')",
                       (pid, name, vram))
        db.execute("INSERT INTO context_versions(project_id, version, header, message, created_at, unit) "
                   "VALUES(?, 1, 'void func_00100000(void);', 'basic types', 0, '')", (pid,))
        best = "void f(void) { func_00100000(); func_00100010(); }"
        db.execute("UPDATE functions SET best_source=? WHERE name='func_00100020'", (best,))
    assert name_kernel_calls(net, "game", log=lambda *_: None) == 2
    names = [r["name"] for r in net.store.all("SELECT name FROM functions ORDER BY vram")]
    assert names == ["FlushCache", "CreateThread", "func_00100020"]
    best = net.store.one("SELECT best_source FROM functions WHERE name='func_00100020'")["best_source"]
    assert best == "void f(void) { FlushCache(); CreateThread(); }"
    assert net._context(net._project("game")["id"], "")["header"] == "void FlushCache(void);"  # the shared header too
    assert name_kernel_calls(net, "game", log=lambda *_: None) == 0  # once
