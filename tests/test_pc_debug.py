"""A Windows game's own debug information (consoles/pc/debuginfo.py, pdb.py): a program database (Visual C++ 6.0's
PDB 2.0, and MSF 7.00 with .NET 2003's length-prefixed names and 2005's zero-terminated ones), the linker's map and
COFF symbols give a PC project the game's names, source files, prototypes and types.

The program databases here are made by hand, record by record, as Visual C++ lays them out (pdb.py says where that's
written down): CI has no Visual C++, and wibo runs 6.0's /Zi and /DEBUG only (7.1's linker and 2005-2010's /Zi stop at
Windows calls it lacks). The demo's own PDB, built with 6.0, is the end-to-end check where Visual C++ is installed.
"""

import json
import re
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import consoles, toolchains
from decompnet.app.forkengine import ForkEngine
from decompnet.consoles.pc import debuginfo, pdb, scratch
from decompnet.forks import ghidra

ROOT = Path(__file__).resolve().parent.parent
TEXT, DATA, BASE = 0x1000, 0x2000, 0x400000
SIGNATURE, AGE, GUID, STAMP = 0x6ADD4D25, 3, bytes(range(16)), 0x6ADD4D25
RESERVED = scratch.HEADER_NAMES


def _name(text: str, st: bool) -> bytes:
    raw = text.encode()
    return bytes([len(raw)]) + raw if st else raw + b"\0"


def _rec(kind: int, body: bytes) -> bytes:
    return struct.pack("<HH", len(body) + 2, kind) + body


def _types(st: bool) -> bytes:
    """Thing { int hp; float pos[2]; Thing *next; }, a pointer to it by its forward reference, enum Mode, and
    int __stdcall (Thing *, int)."""
    k = 0 if st else 1
    member = lambda kind, t, off, name: struct.pack("<HHI", kind, 3, t) + struct.pack("<H", off) + _name(name, st)  # noqa: E731
    enumerate_ = lambda value, name: struct.pack("<HHH", pdb.LF_ENUMERATE[k], 3, value) + _name(name, st)  # noqa: E731
    return b"".join([
        _rec(pdb.LF_ARRAY[k], struct.pack("<IIH", 0x40, 0x75, 8) + _name("", st)),  # 0x1000 float[2]
        _rec(pdb.LF_FIELDLIST, member(pdb.LF_MEMBER[k], 0x74, 0, "hp") + member(pdb.LF_MEMBER[k], 0x1000, 4, "pos")
             + member(pdb.LF_MEMBER[k], 0x1003, 12, "next")),  # 0x1001
        _rec(pdb.LF_STRUCTURE[k], struct.pack("<HHIIIH", 3, 0, 0x1001, 0, 0, 16) + _name("Thing", st)),  # 0x1002
        _rec(pdb.LF_POINTER, struct.pack("<II", 0x1008, 0x800A)),  # 0x1003 Thing * (by the forward reference)
        _rec(pdb.LF_ARGLIST, struct.pack("<III", 2, 0x1003, 0x74)),  # 0x1004
        _rec(pdb.LF_PROCEDURE, struct.pack("<IBBHI", 0x74, 7, 0, 2, 0x1004)),  # 0x1005 __stdcall
        _rec(pdb.LF_FIELDLIST, enumerate_(0, "MODE_IDLE") + enumerate_(2, "MODE_RUN")),  # 0x1006
        _rec(pdb.LF_ENUM[k], struct.pack("<HHII", 2, 0, 0x74, 0x1006) + _name("Mode", st)),  # 0x1007
        _rec(pdb.LF_STRUCTURE[k], struct.pack("<HHIIIH", 0, pdb.FORWARD, 0, 0, 0, 0) + _name("Thing", st)),  # 0x1008
    ])


def _streams(kind: str, st: bool, signature=SIGNATURE, age=AGE, guid=GUID) -> list[bytes]:
    """A program database's streams: one module (thing.cpp) with Thing_Update at .text+0x10, a library's Helper at
    .text+0, globals g_first and g_mode in .data, and typedefs."""
    new = kind == "MSF 7.00"
    s = (lambda st_kind, kind_2005: st_kind if st else kind_2005)  # noqa: E731
    info = struct.pack("<III", pdb.PDB_VC70 if new else 19970604, signature, age) + (guid if new else b"")
    records = _types(st)
    tpi = struct.pack("<5I", 20040203 if not st else 19961031, 56, 0x1000, 0x1009, len(records)).ljust(56, b"\0") \
        + records
    module = struct.pack("<I", 1 if st else 4) + b"".join([
        _rec(s(pdb.S_GPROC32_ST, pdb.S_GPROC32), struct.pack("<8IHB", 0, 0, 0, 0x10, 0, 0, 0x1005, 0x10, 1, 0)
             + _name("Thing_Update", st)),
        _rec(s(pdb.S_BPREL32_ST, pdb.S_BPREL32), struct.pack("<iI", 4, 0x1003) + _name("thing", st)),
        _rec(s(pdb.S_BPREL32_ST, pdb.S_BPREL32), struct.pack("<iI", 8, 0x74) + _name("count", st)),
        _rec(s(pdb.S_BPREL32_ST, pdb.S_BPREL32), struct.pack("<iI", -4, 0x74) + _name("i", st)),
        _rec(pdb.S_END, b""),
    ])
    procref = b""
    if kind == "PDB 2.0":  # its name after the record, padded to 4 (records() skips it)
        procref = _rec(pdb.S_PROCREF_ST, struct.pack("<IIHH", 0, 4, 1, 0)) + _name("Thing_Update", True)
        procref += bytes(-len(procref) % 4)
    globals_ = b"".join([
        _rec(s(pdb.S_PUB32_ST, pdb.S_PUB32), struct.pack("<IIH", 0 if st else 2, 0x10, 1) + _name("_Thing_Update@8", st)),
        _rec(s(pdb.S_PUB32_ST, pdb.S_PUB32), struct.pack("<IIH", 0 if st else 2, 0, 1) + _name("_Helper", st)),
        procref,
        _rec(s(pdb.S_GDATA32_ST, pdb.S_GDATA32), struct.pack("<IIH", 0x1003, 0, 2) + _name("g_first", st)),
        _rec(s(pdb.S_GDATA32_ST, pdb.S_GDATA32), struct.pack("<IIH", 0x1007, 4, 2) + _name("g_mode", st)),
        _rec(s(pdb.S_UDT_ST, pdb.S_UDT), struct.pack("<I", 0x1002) + _name("Thing", st)),
        _rec(s(pdb.S_UDT_ST, pdb.S_UDT), struct.pack("<I", 0x1003) + _name("ThingPtr", st)),
        _rec(s(pdb.S_UDT_ST, pdb.S_UDT), struct.pack("<I", 0x74) + _name("s32", st)),  # common.h's: left out
    ])
    sections = b"".join(struct.pack("<8sIIIIIIHHI", name, 0x100, va, 0, 0, 0, 0, 0, 0, flags)
                        for name, va, flags in ((b".text", TEXT, 0x60000020), (b".data", DATA, 0xC0000040)))
    obj = "C:\\game\\Release\\thing.obj"
    modinfo = struct.pack("<I", 0) + struct.pack("<HHiiIHHII", 1, 0, 0, 0x20, 0x60000020, 0, 0, 0, 0) \
        + struct.pack("<HHIIIHHIII", 0, 4, len(module), 0, 0, 1, 0, 0, 0, 0) + obj.encode() + b"\0" + obj.encode() + b"\0"
    modinfo += bytes(-len(modinfo) % 4)
    contributions = struct.pack("<I", pdb.SC_V60) + struct.pack("<HHiiIHHII", 1, 0, 0, 0x20, 0x60000020, 0, 0, 0, 0)
    sources = struct.pack("<HHHHI", 1, 1, 0, 1, 0) + _name("C:\\game\\src\\thing.cpp", not new and st)
    debug = struct.pack("<11H", *[0xFFFF] * 5, 5, *[0xFFFF] * 5)
    dbi = struct.pack("<iIIHHHHHHiiiiiIiiHHI", -1, pdb.DBI_VC70 if new else 19970606, age, 0xFFFF, 0, 0xFFFF, 0, 6, 0,
                      len(modinfo), len(contributions), 0, len(sources), 0, 0, len(debug), 0, 0, 0x14C, 0)
    dbi += modinfo + contributions + sources + debug
    return [b"", info, tpi, dbi, module, sections, globals_]


def _msf(kind: str, streams: list[bytes]) -> bytes:
    """The streams in the container: MSF 7.00's blocks with 32-bit numbers, or PDB 2.0's pages with 16-bit ones."""
    size = 512 if kind == "MSF 7.00" else 1024
    pages: list[bytes] = [b"", b"", b""]  # the header and the free-page maps

    def place(data: bytes) -> list[int]:
        first = len(pages)
        for at in range(0, len(data), size):
            pages.append(data[at:at + size])
        return list(range(first, len(pages)))
    lists = [place(s) for s in streams]
    if kind == "MSF 7.00":
        directory = struct.pack("<I", len(streams)) + b"".join(struct.pack("<I", len(s)) for s in streams) \
            + b"".join(struct.pack(f"<{len(b)}I", *b) for b in lists)
        blocks = place(directory)
        map_at = place(struct.pack(f"<{len(blocks)}I", *blocks))[0]
        pages[0] = pdb.MSF7 + struct.pack("<6I", size, 1, len(pages), len(directory), 0, map_at)
    else:
        directory = struct.pack("<HH", len(streams), 0) + b"".join(struct.pack("<II", len(s), 0) for s in streams) \
            + b"".join(struct.pack(f"<{len(b)}H", *b) for b in lists)
        blocks = place(directory)
        pages[0] = pdb.PDB2 + struct.pack("<IHHII", size, 1, len(pages), len(directory), 0) \
            + struct.pack(f"<{len(blocks)}H", *blocks)
    return b"".join(p.ljust(size, b"\0") for p in pages)


def _exe(record: bytes | None = None, symbols: bytes = b"", count: int = 0, timestamp: int = STAMP) -> bytes:
    """A 32-bit Windows program: .text at 0x401000, .rdata at 0x402000 with its debug directory naming `record`
    (a CodeView record), and a COFF symbol table of `count` records."""
    head = bytearray(0x400)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x40)
    table = 0x800 if count else 0
    struct.pack_into("<4sHHIIIHH", head, 0x40, b"PE\0\0", 0x14C, 2, timestamp, table, count, 0xE0, 0x102)
    opt = 0x58
    struct.pack_into("<H", head, opt, 0x10B)
    struct.pack_into("<I", head, opt + 16, TEXT)
    struct.pack_into("<I", head, opt + 28, BASE)
    struct.pack_into("<I", head, opt + 56, 0x3000)
    rdata = bytearray(0x200)
    if record is not None:
        struct.pack_into("<II", head, opt + debuginfo.DEBUG_DIRECTORY_AT, DATA, 28)
        struct.pack_into("<IIHHIIII", rdata, 0, 0, 0, 0, 0, debuginfo.CODEVIEW, len(record), DATA + 0x20, 0x620)
        rdata[0x20:0x20 + len(record)] = record
    for k, (name, va, raw, flags) in enumerate(((b".text", TEXT, 0x400, 0x60000020), (b".rdata", DATA, 0x600,
                                                                                       0x40000040))):
        struct.pack_into("<8sIIIIIIHHI", head, opt + 0xE0 + 40 * k, name, 0x200, va, 0x200, raw, 0, 0, 0, 0, flags)
    return bytes(head) + b"\xcc" * 0x200 + bytes(rdata) + symbols


def _nb10(signature=SIGNATURE, age=AGE) -> bytes:
    return b"NB10" + struct.pack("<III", 0, signature, age) + b"C:\\game\\Release\\thing.pdb\0"


def _rsds(guid=GUID, age=AGE) -> bytes:
    return b"RSDS" + guid + struct.pack("<I", age) + b"C:\\game\\Release\\thing.pdb\0"


KINDS = [("PDB 2.0", True, _nb10), ("MSF 7.00", True, _rsds), ("MSF 7.00", False, _rsds)]


@pytest.mark.parametrize("kind,st,record", KINDS, ids=["vc6", "vc7.1", "vc2005"])
def test_a_program_database_gives_names_source_files_prototypes_and_types(tmp_path, kind, st, record):
    """Visual C++ 6.0's PDB 2.0, and MSF 7.00 with .NET 2003's length-prefixed names and 2005's zero-terminated ones:
    the same program reads the same from each."""
    game = tmp_path / "GAME.EXE"
    game.write_bytes(_exe(record()))
    (tmp_path / "thing.pdb").write_bytes(_msf(kind, _streams(kind, st)))  # (the name the CodeView record gives)
    found = debuginfo.find(game, game.read_bytes(), RESERVED, log=lambda *_: None)
    info = found.info
    assert found.source == f"thing.pdb ({kind})"
    named = {n.address: (n.name, n.function) for n in info.named}
    assert named == {0x401000: ("Helper", True), 0x401010: ("Thing_Update", True), 0x402000: ("g_first", False),
                     0x402004: ("g_mode", False)}
    assert info.units == {0x401000: "C:/game/src/thing.cpp", 0x401010: "C:/game/src/thing.cpp"}
    assert info.functions[0x401010] == {
        "source": "C:/game/src/thing.cpp", "prototype": "int __stdcall Thing_Update(Thing *thing, int count)",
        "params": ["thing in stack+0x4", "count in stack+0x8"], "locals": ["int i (stack-0x4)"]}
    header = info.header
    assert "struct Thing { /* 0x10 bytes */" in header and "float pos[2]; /* 0x4 */" in header
    assert "Thing *next; /* 0xC */" in header and "MODE_RUN = 2," in header
    assert "typedef Thing *ThingPtr;" in header and "typedef struct Thing Thing;" in header
    assert "extern Thing *g_first;" in header and "extern enum Mode g_mode;" in header
    assert "s32" not in header  # common.h has it
    assert header.rstrip().endswith("int __stdcall Thing_Update(Thing *thing, int count);")  # (not Helper: no type)
    assert info.summary() == "debug symbols: 2 functions named, 4 types, 1 source file"


def test_another_builds_program_database_is_left_out(tmp_path):
    """A PDB's addresses are its own build's: one whose signature, GUID or age isn't the program's is never used."""
    said = []
    for record, kind in ((_nb10(age=AGE + 1), "PDB 2.0"), (_nb10(signature=1), "PDB 2.0"), (_rsds(guid=bytes(16)),
                                                                                             "MSF 7.00")):
        game = tmp_path / "game.exe"
        game.write_bytes(_exe(record))
        (tmp_path / "game.pdb").write_bytes(_msf(kind, _streams(kind, True)))
        assert debuginfo.find(game, game.read_bytes(), log=said.append) is None
    assert len(said) == 3 and all("another build" in s for s in said)
    (tmp_path / "game.pdb").write_bytes(b"not a pdb")
    assert debuginfo.find(game, game.read_bytes(), log=said.append) is None and "isn't a program database" in said[-1]


MAP = """ thing

 Timestamp is 6add4d25 (Sun Oct 25 02:28:21 2026)

 Preferred load address is 00400000

  Address         Publics by Value              Rva+Base     Lib:Object

 0001:00000000       _Helper                    00401000 f   LIBC:helper.obj
 0001:00000010       ?Update@Thing@@QAEXH@Z     00401010 f   thing.obj
 0001:00000040       ??0Thing@@QAE@XZ           00401040 f   thing.obj
 0001:00000060       ??4Thing@@QAEAAV0@ABV0@@Z  00401060 f   thing.obj
 0002:00000000       ?g_first@@3PAUThing@@A     00402000     thing.obj
 0002:00000010       __real@3f800000            00402010     thing.obj

 Static symbols

 0001:00000080       _count_things              00401080 f   thing.obj
"""


def test_the_linkers_map_names_functions_and_their_object_files(tmp_path):
    game = tmp_path / "game.exe"
    game.write_bytes(_exe())
    (tmp_path / "Game.map").write_text(MAP)
    found = debuginfo.find(game, game.read_bytes())
    assert found.source == "Game.map"
    assert {n.address: n.name for n in found.info.named} == {
        0x401000: "Helper", 0x401010: "Thing::Update", 0x401040: "Thing::Thing", 0x401080: "count_things",
        0x402000: "g_first"}  # (an operator, a constant of the compiler's: no real name pairs with them)
    assert found.info.units == {0x401000: "helper.obj", 0x401010: "thing.obj", 0x401040: "thing.obj",
                                0x401080: "thing.obj"}
    said = []
    (tmp_path / "Game.map").write_text(MAP.replace("6add4d25", "12345678"))
    assert debuginfo.find(game, game.read_bytes(), log=said.append) is None and "another build" in said[0]


def test_a_moved_dlls_map_names_its_functions_where_the_project_has_them():
    """A DLL the project moved (another program has its address) is read at its new address: its map's addresses,
    the linker's preferred ones, move with it."""
    info = debuginfo.from_map(MAP, 0x6ADD4D25, image_base=0x10400000)
    assert {n.address: n.name for n in info.named}[0x10401010] == "Thing::Update"
    assert 0x10401000 in info.units and 0x401000 not in info.units
    assert {n.address for n in debuginfo.from_map(MAP, 0x6ADD4D25, image_base=0x400000).named} >= {0x401000}


def _coff_symbols(function=0x10, datum=4) -> tuple[bytes, int]:
    """.file thing.c, then a function and a datum (their values); the long name in the string table."""
    def symbol(name: bytes, value: int, section: int, kind: int, storage: int, aux: int = 0) -> bytes:
        return struct.pack("<8sIhHBB", name, value, section, kind, storage, aux)
    long_name = b"_count_things"
    table = (symbol(b".file", 0, -2, 0, debuginfo.C_FILE, 1) + b"src\\thing.c".ljust(18, b"\0")
             + symbol(struct.pack("<II", 0, 4), function, 1, 0x20, debuginfo.C_EXTERNAL)
             + symbol(b"_g_count", datum, 2, 0, debuginfo.C_EXTERNAL))
    strings = long_name + b"\0"
    return table + struct.pack("<I", 4 + len(strings)) + strings, 4


def test_coff_symbols_in_the_program_name_functions_and_their_source_files(tmp_path, monkeypatch):
    symbols, count = _coff_symbols()
    game = tmp_path / "game.exe"
    game.write_bytes(_exe(symbols=symbols, count=count))
    found = debuginfo.find(game, game.read_bytes())
    assert found.source == "the program's COFF symbols"
    assert {n.address: (n.name, n.function) for n in found.info.named} == {0x401010: ("count_things", True),
                                                                           0x402004: ("g_count", False)}
    assert found.info.units == {0x401010: "src/thing.c"}
    # Microsoft's linker (a Rich header says it was) writes each symbol's address in the image, not in its section
    monkeypatch.setattr(scratch, "rich_builds", lambda data: [(0x04, 8168, 1)])
    symbols, count = _coff_symbols(TEXT + 0x10, DATA + 4)
    game.write_bytes(_exe(symbols=symbols, count=count))
    assert {n.address for n in debuginfo.find(game, game.read_bytes()).info.named} == {0x401010, 0x402004}


def test_a_decorated_symbol_is_the_real_name_names_py_pairs_with_it():
    assert [debuginfo.plain_name(s) for s in ("_f", "_f@8", "@f@8", "f", "?Update@Town@@QAEXH@Z",
                                              "?Run@Inner@Outer@@QAEXXZ", "??1CFoo@@QAE@XZ", "??0CFoo@@QAE@XZ",
                                              "??4CFoo@@QAEAAV0@ABV0@@Z", "?f@?$vector@H@std@@QAEXXZ", "__imp__Sleep@4",
                                              "__real@3f800000", "??_C@_04LDF@keep?$AA@")] == \
        ["f", "f", "f", "f", "Town::Update", "Outer::Inner::Run", "CFoo::~CFoo", "CFoo::CFoo", None, None, None, None,
         None]


def test_a_method_named_by_the_debug_information_pairs_by_its_class():
    """names.py: Town::Update (functions.json's "names") is ?Update@Town@@... and nothing else's Update."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pc_template_names", scratch.TEMPLATE / "tools" / "names.py")
    names = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(names)
    real = names.real_names({"names": {"func_00401010": "Town::Update", "func_00401020": "Update"}})
    assert names.address_of(b"?Update@Town@@QAEXH@Z", real) == b"func_00401010"
    assert names.address_of(b"?Update@@YAXXZ", real) == b"func_00401020"
    assert names.address_of(b"?Update@Ped@@QAEXXZ", real) == b"func_00401020"  # (as before: by its own name)


def test_functions_go_in_the_games_own_source_files():
    fns = [scratch.Function(a, 0x10, f"_func_{a:08X}") for a in (0x401000, 0x401010, 0x401020, 0x401030, 0x401040)]
    sources = {0x401010: "C:/game/src/thing.cpp", 0x401020: "C:/game/lib/thing.c", 0x401040: "C:/game/src/thing.cpp"}
    got = scratch.units(fns, None, sources)
    # before the first a file of its own (as without debug information); after a function's object, the same one
    assert {u: [f.addr for f in fs] for u, fs in got.items()} == {
        "text_00401000": [0x401000], "thing": [0x401010, 0x401040], "thing_2": [0x401020, 0x401030]}


def test_a_project_gets_the_names_the_types_and_each_functions_brief(tmp_path):
    root, version = tmp_path / "p", "game"
    (root / "config" / version).mkdir(parents=True)
    (root / "include").mkdir()
    (root / "include" / "common.h").write_text(scratch.TYPES_H)
    game = tmp_path / "game.exe"
    game.write_bytes(_exe(_rsds()))
    (tmp_path / "game.pdb").write_bytes(_msf("MSF 7.00", _streams("MSF 7.00", False)))
    info = debuginfo.find(game, game.read_bytes(), RESERVED).info
    functions = [scratch.Function(0x401000, 0x10, "_func_00401000"), scratch.Function(0x401010, 0x10, "_func_00401010@8")]
    assert scratch.debug_names(info, functions) == {"D_00402000": "g_first", "D_00402004": "g_mode",
                                                    "func_00401000": "Helper", "func_00401010": "Thing_Update"}
    scratch.write_debug(root, version, info)
    common = (root / "include" / "common.h").read_text()
    assert common.rstrip().endswith('#include "debug_types.h"\n\n#endif') and common.count("#endif") == 2
    assert "#define DEBUG_TYPES_H" in (root / "include" / "debug_types.h").read_text()
    pc = consoles.get("pc")
    fp = SimpleNamespace(root=root, manifest={"version": version})
    assert pc.debug_of(fp, version, "_func_00401000") is None  # (no prototype: a library's)
    known = pc.debug_of(fp, version, "_func_00401010@8")
    lines = pc.debug_brief(known)
    assert "prototype: int __stdcall Thing_Update(Thing *thing, int count);" in lines
    assert any("include/debug_types.h" in line for line in lines)
    # get_function's brief carries them (ForkEngine.brief)
    engine = SimpleNamespace(_meta=ForkEngine._meta)
    fn = {"meta": json.dumps({"symbol": "_func_00401010@8"}), "name": "_func_00401010@8"}
    assert ForkEngine._debug_brief(engine, fp, {"platform": "pc"}, fn) == lines
    assert ForkEngine._debug_brief(engine, fp, {"platform": "gc"}, fn) == []


def test_a_pc_games_pdb_dropped_with_it_goes_beside_it(tmp_path, monkeypatch):
    """The page sends the game and its .pdb or .map in one upload; the server puts them beside the game, where its
    import looks."""
    from starlette.testclient import TestClient

    from decompnet.app import window
    from decompnet.app.engine import RomNetwork
    from decompnet.app.server import build

    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    mcp, _, security, _ = build(net, home / ".decomp", 6497, "s3cret")
    c = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                   base_url="http://127.0.0.1:6497",
                   headers={"User-Agent": f"Mozilla/5.0 AppleWebKit/605.1.15 {window.TOKEN_AGENT}s3cret", "X-Decomp": "1"})
    assert c.post("/api/settings", json={"autopilot": False}).status_code == 200
    seen = {}

    def imported(self, game, **kw):
        seen.update({p.name: p.read_bytes() for p in game.parent.iterdir()})
        return "s"
    monkeypatch.setattr(type(net), "import_rom", imported)
    extras = json.dumps([["game.pdb", 3], ["game.map", 2]])
    j = c.post(f"/api/import?confirmed=1&name=game.exe&extras={extras}", content=b"MZ-game" + b"pdb" + b"mp").json()
    for _ in range(300):
        if (state := c.get(f"/api/job?id={j['job']}").json())["state"] != "running":
            break
    assert state["state"] == "done" and seen == {"game.exe": b"MZ-game", "game.pdb": b"pdb", "game.map": b"mp"}
    bad = c.post(f"/api/import?confirmed=1&name=game.exe&extras={json.dumps([['x.dll', 3]])}", content=b"MZ-gamex.d")
    assert bad.status_code == 400 and ".pdb or .map" in bad.json()["error"]


@pytest.mark.skipif(toolchains.installed("msvc") is None or toolchains.installed("objdiff-cli") is None
                    or not ghidra.available()
                    or (sys.platform != "win32" and toolchains.installed("wibo") is None),
                    reason="needs Visual C++ (msvc, proprietary), Ghidra and objdiff")
def test_the_demo_built_with_its_pdb_imports_with_its_names_files_and_types(tmp_path):
    """Demo Quest PC linked with /DEBUG by Visual C++ 6.0, imported as the app does: its project has the demo's
    names, its two source files and its structs; a brief gives a function's prototype, and C written with the game's
    own names and types matches."""
    from decompnet.app.engine import RomNetwork

    subprocess.run([sys.executable, str(ROOT / "projects" / "demo-pc" / "build.py"), "6.0", "--pdb"], check=True,
                   capture_output=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(ROOT / "projects" / "demo-pc" / "build" / "pdb" / "demoquest.exe", confirmed=True,
                          log=lambda *_: None)
    root = Path(net.project_row(slug)["root"])
    info = json.loads((root / "config" / "demoquest" / "functions.json").read_text())
    names = info["names"]
    assert {"entity_spawn", "game_tick", "settings_get", "mix_score", "g_player", "g_entities"} <= set(names.values())
    assert set(info["units"]) == {"game", "pcmain"} and (root / "src" / "auto" / "game.cpp").exists()
    types = (root / "include" / "debug_types.h").read_text()
    assert "struct Entity { /* 0x1C bytes */" in types and "extern struct Player g_player;" in types
    assert '#include "debug_types.h"' in (root / "include" / "common.h").read_text()
    spawn = "_" + next(k for k, v in names.items() if v == "entity_spawn")
    brief = net.get_function(spawn, slug)
    assert "prototype: struct Entity *entity_spawn(short type, int x, int y);" in brief
    assert "parameters live in: type in stack+0x4, x in stack+0x8, y in stack+0xc" in brief
    agent, _ = net.local_agent("pc-agent")
    code = re.search(r"Entity \*entity_spawn\(.*?\n}\n", (ROOT / "projects" / "demo-n64" / "original" / "game.c")
                     .read_text(), re.S).group(0)
    code = code.replace("MAX_ENTITIES", "32").replace("ENTITY_FLAG_ACTIVE", "1").replace("s16", "short") \
        .replace("s32", "int").replace("NULL", "0")
    res = net.attempt(agent, spawn, code, slug)
    assert res["matched"], res["text"]
