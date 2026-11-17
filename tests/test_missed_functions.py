"""Adding the functions an N64 split left as data to a project that exists (engine find_missed_functions,
consoles/n64/code.py missed_functions), without importing the game again: homemade code, no game data. A racing
game's project gained 16 such functions only by a reimport, which reopens its work."""
import json

import pytest
import yaml

from decompnet.app.engine import RomNetwork
from decompnet.consoles.n64.code import missed_functions
from decompnet.network import NetworkError
from decompnet.rom import split
from decompnet.rom.export import _export_segments

# before the gap: lui $a0, 0x8000; addiu $a0, $a0, 0x1010 (the leaf's address); jr $ra
BEFORE = [0x3c048000, 0x24841010, 0x03e00008, 0]
LEAF = [0x24020001, 0x24420001, 0x03e00008, 0]
AFTER = [0x03e00008, 0]


def image():
    data = bytearray(0x2000)
    for rom, words in ((0x1000, BEFORE), (0x1010, LEAF), (0x1020, AFTER)):
        for i, w in enumerate(words):
            data[rom + 4 * i:rom + 4 * i + 4] = w.to_bytes(4, "big")
    return bytes(data)


def splat_config():
    return {"options": {"target_path": "rom.z64"}, "segments": [
        {"type": "bin", "start": 0},
        {"name": "a", "type": "code", "start": 0x1000, "vram": 0x80001000, "subsegments": [[0x1000, "c", "a"]]},
        {"type": "bin", "start": 0x1010},
        {"name": "b", "type": "code", "start": 0x1020, "vram": 0x80001020, "subsegments": [[0x1020, "c", "b"]]},
        {"type": "bin", "start": 0x1028}, [0x2000]]}


def test_the_search_writes_what_it_finds_into_splats_config(tmp_path):
    config = tmp_path / "game.yaml"
    config.write_text(yaml.safe_dump(splat_config()))
    assert missed_functions(config, image(), log=lambda *a: None) == [(0x1010, 0x1020, 0x80001010)]
    after = yaml.safe_load(config.read_text())
    assert (0x1010, 0x1020, 0x80001010) in split.code_ranges(config)
    assert not any(isinstance(s, dict) and s.get("type") == "bin" and s.get("start") == 0x1010 for s in after["segments"])
    # found again, nothing new
    assert missed_functions(config, image(), log=lambda *a: None) == []


def test_a_function_from_its_bytes_labels_its_own_branches():
    code = b"".join(w.to_bytes(4, "big") for w in (0x10800001, 0x24420001, 0x03e00008, 0))  # beqz $a0 to the jr
    fn = split.function_from_bytes("f", code, 0x1010, 0x80001010, "code_80001010")
    assert fn.labels == {0x80001018}
    assert ".L80001018" in split.function_asm(fn) and ". + 4" not in split.function_asm(fn)


def project(tmp_path):
    net = RomNetwork(tmp_path / "missed.db", tmp_path)
    rom = image()
    (tmp_path / "rom.z64").write_bytes(rom)
    (tmp_path / "game.yaml").write_text(yaml.safe_dump(splat_config()))
    cfg = {"kind": "rom", "platform": "n64", "big_endian": True, "gp": 0, "rom": {"file": "rom.z64"},
           "segments": [vars(split.Segment("a", 0x1000, 0x1010, 0x80001000)),
                        vars(split.Segment("b", 0x1020, 0x1028, 0x80001020))]}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES('game','game','mips',1,?,?)",
                         (str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,target,status,unit,relocs,asm) "
                   "VALUES(?,'a',?,?,16,?,'matched','a','[]','')", (pid, 0x80001000, 0x1000, rom[0x1000:0x1010]))
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,target,status,unit,relocs,asm) "
                   "VALUES(?,'b',?,?,8,?,'open','b','[]','')", (pid, 0x80001020, 0x1020, rom[0x1020:0x1028]))
    return net, pid


def test_a_project_gains_the_missed_function_and_keeps_its_work(tmp_path):
    net, pid = project(tmp_path)
    result = net.find_missed_functions("game", log=lambda *a: None)
    assert result["added"] == [{"name": "func_80001010", "vram": 0x80001010, "size": 16}]
    store = net.project_store(pid)
    row = store.one("SELECT * FROM functions WHERE name='func_80001010'")
    assert (row["rom_offset"], row["unit"], row["status"]) == (0x1010, "code_80001010", "open")
    assert row["target"] == image()[0x1010:0x1020]
    assert store.one("SELECT status FROM functions WHERE name='a'")["status"] == "matched"
    assert store.one("SELECT 1 FROM symbols WHERE name='func_80001010' AND kind='func'")
    segments = json.loads(store.one("SELECT config FROM projects WHERE id=?", pid)["config"])["segments"]
    assert [s["name"] for s in segments] == ["a", "code_80001010", "b"]
    assert net._rom(net.project_row("game"))[1][1].rom_end == 0x1020
    # the export's split keeps it as code
    known = [{"vram": r["vram"], "size": r["size"]} for r in store.all("SELECT vram,size FROM functions")]
    _export_segments(yaml.safe_load((tmp_path / "game.yaml").read_text()), known)
    # a second look adds nothing
    assert net.find_missed_functions("game", log=lambda *a: None)["added"] == []


def test_a_console_without_the_search_says_so(tmp_path, monkeypatch):
    net, _ = project(tmp_path)
    from decompnet.consoles import Console
    from decompnet.consoles.n64 import Nintendo64
    monkeypatch.setattr(Nintendo64, "missed_functions", Console.missed_functions)
    before = (tmp_path / "game.yaml").read_text()
    with pytest.raises(NetworkError, match="isn't supported"):
        net.find_missed_functions("game", log=lambda *a: None)
    assert (tmp_path / "game.yaml").read_text() == before
