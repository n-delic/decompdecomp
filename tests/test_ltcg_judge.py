"""The LTCG judge on what a whole existing decompilation brought out (an Xbox game's 4,250 harness-matched functions
judged again here): the original's in-image constants, two relocations in one instruction, vtables the game's link
folded, and a callback that names the wrong function. Each runs tools/dll2coff.py on a hand-made DLL and map
against a hand-made original, as test_ltcg.py does."""

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

from decompnet.consoles.pc import scratch as scratch_pe
from decompnet.consoles.pc.scratch import DIR32

from .test_ltcg import TOOLS, _coff, _pe, _tool

dll2coff = _tool("dll2coff")


def _judge(tmp_path: Path, orig: bytes, relocs: list, ours: bytes, sites: list[int], map_lines: list[str],
           data: bytes = bytes(16), extra: dict | None = None) -> tuple[list[tuple[int, str, int, int]], dict]:
    """One function G at 0x11000 in the original (`orig`, with functions.json's `relocs`) and at 0x10001000 in our
    DLL (`ours`, base relocations at `sites`, relative to BASE). Returns our object's relocations (offset, symbol,
    type, inline addend) and G's .ltcg.json entry."""
    exe = orig.ljust(0x40, b"\xcc") + data
    (tmp_path / "game.bin").write_bytes(exe)
    units = {"u": [{"name": "?G@@YAXXZ", "addr": 0x11000, "offset": 0, "size": len(orig), "relocs": relocs}]}
    units.update(extra or {})
    (tmp_path / "functions.json").write_text(json.dumps({"exe": str(tmp_path / "game.bin"),
                                                         "sha1": hashlib.sha1(exe).hexdigest(), "units": units}))
    (tmp_path / "u.dll").write_bytes(_pe([(".text", 0x1000, ours + b"\xcc" * 4), (".data", 0x2000, data)], sites))
    (tmp_path / "u.map").write_text(" Address         Publics by Value              Rva+Base       Lib:Object\n\n"
                                    " 0001:00000000       ?G@@YAXXZ                  10001000 f   a.obj\n"
                                    + "".join(line + "\n" for line in map_lines))
    src = tmp_path / "u.cpp"
    src.write_text("// FUNCTION: 0x11000\nvoid G()\n{\n}\n")
    out = tmp_path / "u.obj"
    subprocess.run([sys.executable, str(TOOLS / "dll2coff.py"), str(tmp_path / "functions.json"), "u",
                    str(tmp_path / "u.dll"), str(tmp_path / "u.map"), str(out), str(src)], check=True, cwd=tmp_path)
    obj = out.read_bytes()
    sections, names = _coff(obj)
    raw_at = struct.unpack_from("<I", obj, 20 + 20)[0]  # the first section's bytes
    got = [(off, names[i], t, struct.unpack_from("<i", obj, raw_at + off)[0]) for off, i, t in sections[0][1]]
    return got, json.loads(out.with_suffix(".ltcg.json").read_text())["functions"]["?G@@YAXXZ"]


def test_a_constant_the_original_relocates_gets_the_same_relocation_on_our_side(tmp_path):
    # cmp eax, [ecx+0xE6C80] (a struct offset); mov dword ptr [g_state], 0x10101 (a flag word): in a program loaded
    # at 0x10000 both numbers fall in the image, so the original's delinker relocated them; ours are plain numbers
    orig = bytes.fromhex("3b81806c0e00" "c705acd57500 01010100" "c3")
    ours = bytes.fromhex("3b81806c0e00" "c70500200010 01010100" "c3")
    relocs = [[2, DIR32, "_D_000E6C80", 0], [8, DIR32, "_D_0075D5AC", 0], [12, DIR32, "_D_00010101", 0]]
    got, side = _judge(tmp_path, orig, relocs, ours, [0x1008], [" 0002:00000000       _g_state                   "
                                                                "10002000     a.obj"])
    assert got == [(2, "_D_000E6C80", DIR32, 0), (8, "_g_state", DIR32, 0), (12, "_D_00010101", DIR32, 0)]
    assert side["constants"] == ["+0x2", "+0xC"] and side["unpaired"] == [] and side["conflicts"] == []
    # a field whose number differs from the original's is left alone: a real difference objdiff shows
    got, side = _judge(tmp_path, orig, relocs, ours.replace(bytes.fromhex("806c0e00"), bytes.fromhex("846c0e00")),
                       [0x1008], [" 0002:00000000       _g_state                   10002000     a.obj"])
    assert [r[0] for r in got] == [8, 12] and side["constants"] == ["+0xC"]
    assert side["unpaired"] == ["+0x2: a relocation in the original only"]


def test_a_second_relocation_in_an_instruction_is_written_as_the_originals(tmp_path):
    # mov dword ptr [g_w+0x28], offset g_x+0x4C: objdiff relates the first relocation and compares the second
    # field's bytes, the addend (0x4C in ours, 0 in the original's D_0073A1D0)
    orig = bytes.fromhex("c705e0e43f00 d0a17300" "c3")
    ours = bytes.fromhex("c70528200010 4c200010" "c3")
    relocs = [[2, DIR32, "_D_003FE4E0", 0], [6, DIR32, "_D_0073A1D0", 0]]
    lines = [" 0002:00000000       ?g_w@@3UW@@A               10002000     a.obj"]
    got, side = _judge(tmp_path, orig, relocs, ours, [0x1002, 0x1006], lines)
    assert got == [(2, "?g_w@@3UW@@A", DIR32, 0x28), (6, "_D_0073A1D0", DIR32, 0)]
    assert side["conflicts"] == [] and side["pairs"] == 2
    # a name that says another original address keeps it, so the data check (and the export) still see it
    lines.append(" 0002:00000040       _D_0073A1D8                10002040     a.obj")
    got, side = _judge(tmp_path, orig, relocs, bytes.fromhex("c70528200010 40200010" "c3"), [0x1002, 0x1006], lines)
    assert got[1] == (6, "_D_0073A1D8", DIR32, 0)
    assert side["data"] == ["+0x6: yours refers to _D_0073A1D8 (0x73A1D8) where the original refers to 0x73A1D0"]


def test_one_original_vtable_folded_where_ours_has_two_is_a_note(tmp_path):
    # mov [esi], offset ??_7A@@6B@; mov [edi], offset ??_7B@@6B@: the game's link folded A's and B's identical
    # vtables into one (0x3B11A0), our harness link keeps two
    orig = bytes.fromhex("c706a0113b00" "c707a0113b00" "c3")
    ours = bytes.fromhex("c70600200010" "c70710200010" "c3")
    relocs = [[2, DIR32, "_D_003B11A0", 0], [8, DIR32, "_D_003B11A0", 0]]
    lines = [" 0002:00000000       ??_7A@@6B@                 10002000     a.obj",
             " 0002:00000010       ??_7B@@6B@                 10002010     a.obj"]
    # an original address the project doesn't list as a vtable (and, here, can't be read as one): a conflict
    _, side = _judge(tmp_path, orig, relocs, ours, [0x1002, 0x1008], lines)
    assert side["folded"] == [] and side["conflicts"] == ["orig 0x3B11A0 -> yours 0x10002000 and 0x10002010"]
    # listed in config/<version>/vtables.csv (or vtables.json): a note, as the LTCG harness has it
    (tmp_path / "vtables.csv").write_text("vtable_va,n_slots\n0x003b11a0,6\n")
    _, side = _judge(tmp_path, orig, relocs, ours, [0x1002, 0x1008], lines)
    assert side["conflicts"] == [] and side["folded"] == ["orig 0x3B11A0 -> yours 0x10002000 and 0x10002010"]
    assert dll2coff.original_vtables(tmp_path / "functions.json") == {0x3B11A0}
    # without vtable names on our side it is the split global the mapping check is for
    plain = [x.replace("??_7A@@6B@", "_g_a      ").replace("??_7B@@6B@", "_g_b      ") for x in lines]
    _, side = _judge(tmp_path, orig, relocs, ours, [0x1002, 0x1008], plain)
    assert side["conflicts"] == ["orig 0x3B11A0 -> yours 0x10002000 and 0x10002010"]
    # unlisted, the original's address counts as a vtable when its first word is one of the game's functions
    names = {0x10002000: ("??_7A@@6B@", 0), 0x10002010: ("??_7B@@6B@", 0), 0x10002020: ("_g_c", 0)}
    fold = dll2coff.vtable_fold(set(), {0x18C470}, {0x3B11A0: 0x18C470, 0x3B2000: 0x3F800000}.get, names.get)
    assert fold(0x3B11A0, 0x10002000, 0x10002010)
    assert not fold(0x3B2000, 0x10002000, 0x10002010) and not fold(0x3B11A0, 0x10002000, 0x10002020)


def test_a_callback_naming_the_function_itself_says_so(tmp_path):
    # push offset G where the original pushes another function (0x11100): a real difference, worded as one
    orig = bytes.fromhex("6800110100" "c3")
    ours = bytes.fromhex("6800100010" "c3")
    _, side = _judge(tmp_path, orig, [[1, DIR32, "_func_00011100", 0]], ours, [0x1001], [])
    assert side["conflicts"] == ["+0x1: yours refers to this function itself, the original to 0x11100"]


def test_references_the_link_left_unresolved_are_the_originals_and_unchecked(tmp_path):
    # G calls two functions of its unit nobody has written yet and reads a global only they define: linked with
    # /FORCE:UNRESOLVED, all three land at the DLL's image base
    orig = bytes.fromhex("e8 fb000000" "e8 26010000" "b9 a0f20100" "c3")  # call 0x11100; call 0x11130; mov ecx, D
    ours = bytes.fromhex("e8 fbefffff" "e8 f6efffff" "b9 00000010" "c3")  # call BASE; call BASE; mov ecx, BASE
    relocs = [[1, scratch_pe.REL32, "_func_00011100", 0], [6, scratch_pe.REL32, "_func_00011130@8", 0],
              [11, DIR32, "_D_0001F2A0", 0]]
    (tmp_path / "u.map.link.txt").write_text(
        'a.obj : error LNK2001: unresolved external symbol "void __cdecl func_00011100(void)" (?func_00011100@@YAXXZ)\n'
        "a.obj : error LNK2001: unresolved external symbol _func_00011130@8\n")
    got, side = _judge(tmp_path, orig, relocs, ours, [0x100B], [])
    assert [(o, n) for o, n, *_ in got] == [(1, "_func_00011100"), (6, "_func_00011130@8"), (11, "_D_0001F2A0")]
    assert side["conflicts"] == [] and side["data"] == [] and side["unresolved"] == ["+0x1", "+0x6", "+0xB"]
    report = json.loads((tmp_path / "u.ltcg.json").read_text())
    assert report["unresolved"] == ["?func_00011100@@YAXXZ", "_func_00011130@8"]


def test_unresolved_references_with_an_offset_are_unresolved_too(tmp_path):
    # mov eax, [A+4]; mov ecx, [B+4] with neither global defined yet: /FORCE:UNRESOLVED puts both at the image base,
    # so both read BASE+4. Read as an address of ours, one of ours paired with two of the original's (a conflict
    # for a function written right); they are the original's references, unchecked, like those at the base itself
    orig = bytes.fromhex("a1 04f20100" "8b0d 04300200" "c3")
    ours = bytes.fromhex("a1 04000010" "8b0d 04000010" "c3")
    relocs = [[1, DIR32, "_D_0001F200", 4], [7, DIR32, "_D_00023000", 4]]
    got, side = _judge(tmp_path, orig, relocs, ours, [0x1001, 0x1007], [])
    assert side["conflicts"] == [] and side["unpaired"] == [] and side["unresolved"] == ["+0x1", "+0x7"]
    assert [(o, n, a) for o, n, _, a in got] == [(1, "_D_0001F200", 4), (7, "_D_00023000", 4)]


def test_the_harness_link_goes_on_when_only_symbols_are_unresolved(tmp_path, monkeypatch):
    ltcg_tool = _tool("ltcg")
    monkeypatch.setattr(sys, "platform", "win32")  # the stand-in linker runs as it is, not through wine
    assert "/FORCE:UNRESOLVED" in ltcg_tool.LINK_FLAGS
    fake = tmp_path / "link.py"
    fake.write_text("import sys, pathlib\npathlib.Path(sys.argv[1]).write_text('dll')\nprint(sys.argv[2])\n"
                    "sys.exit(1)\n")
    out, log = tmp_path / "u.dll", tmp_path / "u.map.link.txt"
    unresolved = "a.obj : error LNK2001: unresolved external symbol _f\nu.dll : fatal error LNK1120: 1 unresolved externals"
    assert ltcg_tool.link(str(out), str(log), [sys.executable, str(fake), str(out), unresolved]) == 0
    assert log.read_text().startswith("a.obj : error LNK2001") and ltcg_tool.unresolved(log.read_text()) == ["_f"]
    other = "a.obj : error LNK2005: _f already defined in b.obj"
    assert ltcg_tool.link(str(out), str(log), [sys.executable, str(fake), str(out), other]) == 1


def test_the_brief_says_how_to_name_a_decorated_function():
    from decompnet.app.forkengine import _naming_hint

    hint = _naming_hint({"symbol": "_func_000115D0@8"}, {"name": "_func_000115D0@8"})
    assert "__stdcall, popping 8 bytes" in hint and "func_000115D0" in hint and "// FUNCTION: 0x000115D0" in hint
    assert "__cdecl" in _naming_hint({}, {"name": "_func_00011000"})
    assert _naming_hint({"symbol": "?Update@CAI@@QAEXXZ"}, {"name": "x"}) == ""


def test_a_claim_on_a_unit_that_doesnt_build_is_released_with_a_note(tmp_path):
    from decompnet.app.engine import RomNetwork
    from decompnet.app.forkengine import ForkEngine

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    agent = net.authenticate(net.register_agent("a1"))
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('p','P',?,'{}')",
                         (str(tmp_path),)).lastrowid
        fid = db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,meta,asm) "
                         "VALUES(?,?,4,1,'open','u','{}','')", (pid, "_func_00011000")).lastrowid
        db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fid, agent.id, 0, 1e12))
    engine = ForkEngine.__new__(ForkEngine)
    engine.net = net
    fn = net.store.one("SELECT * FROM functions WHERE id=?", fid)
    said = engine._release_stuck({"id": pid}, fn, agent, "u", "ninja: x\nFAILED: build/u.dll\nlink: error LNK2005")
    assert "released" in said
    assert net.store.all("SELECT * FROM claims WHERE function_id=?", fid) == []
    note = net.store.one("SELECT text FROM notes WHERE function_id=?", fid)["text"]
    assert note.startswith("u didn't build before this change either") and "FAILED: build/u.dll" in note


def test_jump_table_entries_going_elsewhere_are_a_difference(tmp_path):
    # two cases swapped in the table: same bytes outside the table, labels at the same places, and objdiff (which
    # doesn't compare where relocations go) reads 100%; the rebuilt program would differ
    from .test_ltcg import F1, F2, RELOC_SITES, _project, _run_dll2coff

    src, _ = _project(tmp_path)
    assert _run_dll2coff(tmp_path, src)[1]["functions"]["?F2@@YAHH@Z"]["conflicts"] == []
    f2 = F2.replace(bytes.fromhex("2d100010 2e100010"), bytes.fromhex("2e100010 2d100010"))
    text = F1 + b"\xcc" * (0x20 - len(F1)) + f2 + b"\xcc" * 8
    (tmp_path / "u.dll").write_bytes(_pe([(".text", 0x1000, text), (".data", 0x2000, bytes(8))], RELOC_SITES))
    assert _run_dll2coff(tmp_path, src)[1]["functions"]["?F2@@YAHH@Z"]["conflicts"] == [
        "+0x10: goes to +0xE in yours, +0xD in the original (a jump table entry)",
        "+0x14: goes to +0xD in yours, +0xE in the original (a jump table entry)"]


def _cut(tmp_path: Path, source: str, obj: str) -> dict:
    """dll2coff on one function G (`ret`) at 0x11000, ours at 0x10001000 from map object `obj`, the unit's source
    `source` (u.cpp): its .ltcg.json."""
    exe = b"\xc3".ljust(0x40, b"\xcc")
    (tmp_path / "game.bin").write_bytes(exe)
    units = {"u": [{"name": "?G@@YAXXZ", "addr": 0x11000, "offset": 0, "size": 1, "relocs": []}]}
    (tmp_path / "functions.json").write_text(json.dumps({"exe": str(tmp_path / "game.bin"),
                                                         "sha1": hashlib.sha1(exe).hexdigest(), "units": units}))
    (tmp_path / "u.dll").write_bytes(_pe([(".text", 0x1000, b"\xc3" + b"\xcc" * 4)], []))
    (tmp_path / "u.map").write_text(" Address         Publics by Value              Rva+Base       Lib:Object\n\n"
                                    f" 0001:00000000       ?G@@YAXXZ                  10001000 f   {obj}\n")
    (tmp_path / "u.cpp").write_text(source)
    out = tmp_path / "u.obj"
    subprocess.run([sys.executable, str(TOOLS / "dll2coff.py"), str(tmp_path / "functions.json"), "u",
                    str(tmp_path / "u.dll"), str(tmp_path / "u.map"), str(out), "u.cpp"], check=True, cwd=tmp_path)
    return json.loads(out.with_suffix(".ltcg.json").read_text())


def test_a_helper_between_the_annotation_and_its_function_doesnt_lose_the_function(tmp_path):
    # `// FUNCTION:`, then an inline helper (inlined, so not in the map), then the function: the annotation named
    # the helper, the function wasn't cut out, the object only called it, and its diff read 100% against nothing
    source = ("// FUNCTION: 0x11000\nstatic inline int Tail(int x)\n{\n    return x;\n}\nvoid G()\n{\n}\n")
    side = _cut(tmp_path, source, "00_u.obj")
    assert "?G@@YAXXZ" in side["functions"] and side["problems"] == []
    # the original's name from a stand-in (another object of the harness) isn't the unit's function
    side = _cut(tmp_path, source, "01_u_standins.obj")
    assert "?G@@YAXXZ" not in side["functions"]
    assert side["problems"] == ["0x00011000 ?Tail@@ isn't in the map (not called from the driver, or inlined everywhere)"]
