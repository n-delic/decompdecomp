"""C++ classes from a Visual C++ program's run-time type information (consoles/pc/rtti.py) and from its code (the
vtables constructors store, the fields their methods use), what agents read about them (get_class, the brief's
lines, guess_struct) and Ghidra's drafts of their methods as `Class::method` (arch/x86.py). The program is made by
hand here: two classes, one deriving from the other and from a second, nested base, so no compiler or game is
needed."""

import json
import struct

import pytest

from decompnet import classes, consoles
from decompnet.app import tools
from decompnet.app.engine import RomNetwork
from decompnet.arch import x86
from decompnet.consoles.pc import rtti, scratch
from decompnet.forks.project import ForkProject
from decompnet.network import NetworkError

BASE = 0x400000
TEXT, RDATA, DATA = 0x401000, 0x402000, 0x403000
DIR32, REL32 = 6, 0x14

# the code: constructors, a function that builds an object on its stack, and the virtual functions (`ret` each)
BASE_CTOR, DERIVED_CTOR, LOCAL_USER = 0x401000, 0x401010, 0x401040
BASE_F, BASE_G, DERIVED_G, DERIVED_H, DERIVED_SECOND, INNER_F = 0x401030, 0x401034, 0x401038, 0x40103C, 0x401048, 0x40104C
# the RTTI: type descriptors (in .data), base class descriptors and arrays, hierarchy descriptors, locators, vtables
TD_BASE, TD_DERIVED, TD_INNER = 0x403000, 0x403020, 0x403040
BCD_BASE, BCD_DERIVED, BCD_INNER = 0x402000, 0x402020, 0x402040
BCA_BASE, BCA_DERIVED, BCA_INNER = 0x402060, 0x402070, 0x402080
CHD_BASE, CHD_DERIVED, CHD_INNER = 0x402090, 0x4020A0, 0x4020B0
COL_BASE, COL_DERIVED, COL_DERIVED_8, COL_INNER = 0x4020C0, 0x4020E0, 0x402100, 0x402120
VT_BASE, VT_DERIVED, VT_DERIVED_8, VT_INNER = 0x402144, 0x402150, 0x402160, 0x402168
TYPE_INFO_VTABLE = 0x402170


def _put(blob: bytearray, start: int, addr: int, data: bytes) -> None:
    blob[addr - start:addr - start + len(data)] = data


def _words(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}I", *values)


def _sections() -> dict[int, bytearray]:
    text, rdata, data = bytearray(0x200), bytearray(0x400), bytearray(0x200)
    _put(text, TEXT, BASE_CTOR, b"\xC7\x01" + _words(VT_BASE) + b"\xC3")  # mov dword ptr [ecx], Base's vtable
    # push esi; mov esi, ecx; call Base::Base; mov [esi], Derived's vtable; mov [esi+8], its second; pop esi; ret
    _put(text, TEXT, DERIVED_CTOR, b"\x56\x8B\xF1\xE8" + struct.pack("<i", BASE_CTOR - (DERIVED_CTOR + 8))
         + b"\xC7\x06" + _words(VT_DERIVED) + b"\xC7\x46\x08" + _words(VT_DERIVED_8) + b"\x5E\xC3")
    _put(text, TEXT, LOCAL_USER, b"\xC7\x45\xF8" + _words(VT_DERIVED) + b"\xC3")  # mov [ebp-8], ...: a local object
    for f in (BASE_F, DERIVED_SECOND, INNER_F):
        _put(text, TEXT, f, b"\xC3")
    # the fields their methods use through `this` (ecx): Base's u32 at +4, Derived's s16 at +0x10 and float at +0xC
    _put(text, TEXT, BASE_G, b"\x8B\x41\x04\xC3")  # mov eax, [ecx+4]; ret
    _put(text, TEXT, DERIVED_G, b"\x0F\xBF\x41\x10")  # movsx eax, word ptr [ecx+0x10]
    _put(text, TEXT, DERIVED_H, b"\xD9\x41\x0C\xC3")  # fld dword ptr [ecx+0xc]; ret
    for td, name in ((TD_BASE, b"Base"), (TD_DERIVED, b"Derived"), (TD_INNER, b"Inner@Outer")):
        _put(data, DATA, td, _words(TYPE_INFO_VTABLE, 0) + b".?AV" + name + b"@@\0")
    for bcd, td in ((BCD_BASE, TD_BASE), (BCD_DERIVED, TD_DERIVED), (BCD_INNER, TD_INNER)):
        _put(rdata, RDATA, bcd, _words(td, 0, 0, 0xFFFFFFFF, 0, 0))
    _put(rdata, RDATA, BCA_BASE, _words(BCD_BASE))
    _put(rdata, RDATA, BCA_DERIVED, _words(BCD_DERIVED, BCD_BASE, BCD_INNER))  # itself, then its bases
    _put(rdata, RDATA, BCA_INNER, _words(BCD_INNER))
    for chd, count, bca in ((CHD_BASE, 1, BCA_BASE), (CHD_DERIVED, 3, BCA_DERIVED), (CHD_INNER, 1, BCA_INNER)):
        _put(rdata, RDATA, chd, _words(0, 0, count, bca))
    for col, offset, td, chd in ((COL_BASE, 0, TD_BASE, CHD_BASE), (COL_DERIVED, 0, TD_DERIVED, CHD_DERIVED),
                                 (COL_DERIVED_8, 8, TD_DERIVED, CHD_DERIVED), (COL_INNER, 0, TD_INNER, CHD_INNER)):
        _put(rdata, RDATA, col, _words(0, offset, 0, td, chd))
    # each vtable follows the word pointing at its locator; its virtual functions run until a word that isn't code
    _put(rdata, RDATA, VT_BASE - 4, _words(COL_BASE, BASE_F, BASE_G))
    _put(rdata, RDATA, VT_DERIVED - 4, _words(COL_DERIVED, BASE_F, DERIVED_G, DERIVED_H))
    _put(rdata, RDATA, VT_DERIVED_8 - 4, _words(COL_DERIVED_8, DERIVED_SECOND))
    _put(rdata, RDATA, VT_INNER - 4, _words(COL_INNER, INNER_F, 0))
    return {TEXT: text, RDATA: rdata, DATA: data}


def _exe() -> bytes:
    """The program as a file: a DOS header, the PE headers (32-bit x86) and the three sections."""
    sections = _sections()
    head = bytearray(0x400)
    head[:2], head[0x3C:0x40], head[0x40:0x44] = b"MZ", _words(0x40), b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, 0x44, scratch.MACHINE_I386, len(sections), 0, 0, 0, 0xE0, 0x102)
    opt = 0x58
    struct.pack_into("<H", head, opt, scratch.PE32_MAGIC)
    struct.pack_into("<I", head, opt + scratch.ENTRY_AT, TEXT - BASE)
    struct.pack_into("<I", head, opt + scratch.IMAGE_BASE_AT, BASE)
    struct.pack_into("<I", head, opt + scratch.IMAGE_SIZE_AT, 0x4000)
    body, offset = b"", len(head)
    for k, (va, blob) in enumerate(sections.items()):
        flags = scratch.CODE_AND_EXECUTABLE if va == TEXT else 0x40000040
        struct.pack_into("<8sIIIIIIHHI", head, opt + 0xE0 + scratch.SECTION_HEADER_BYTES * k,
                         [b".text", b".rdata", b".data"][k], len(blob), va - BASE, len(blob), offset + len(body),
                         0, 0, 0, 0, flags)
        body += bytes(blob)
    return bytes(head) + body


RELOCS = {  # as functions.json keeps them: (offset, type, symbol, addend)
    BASE_CTOR: [[2, DIR32, f"_D_{VT_BASE:08X}", 0]],
    DERIVED_CTOR: [[4, REL32, f"@func_{BASE_CTOR:08X}@8", 0], [10, DIR32, f"_D_{VT_DERIVED:08X}", 0],
                   [17, DIR32, f"_D_{VT_DERIVED_8:08X}", 0]],
    LOCAL_USER: [[3, DIR32, f"_D_{VT_DERIVED:08X}", 0]],
}


SIZES = {BASE_CTOR: 0x10, DERIVED_CTOR: 0x20, BASE_F: 4, BASE_G: 4, DERIVED_G: 4, DERIVED_H: 4, LOCAL_USER: 8,
         DERIVED_SECOND: 4, INNER_F: 4}


def _functions(name=lambda a: f"@func_{a:08X}@8") -> list[dict]:
    """The functions as functions.json keeps them."""
    return [{"name": name(a), "addr": a, "offset": a - TEXT + 0x400, "size": size, "relocs": RELOCS.get(a, [])}
            for a, size in SIZES.items()]


def test_rtti_names_each_class_its_vtables_bases_and_constructors():
    found = rtti.recover(scratch.read_pe(_exe()), relocs=RELOCS)
    assert set(found.by_vtable) == {VT_BASE, VT_DERIVED, VT_INNER}
    assert {vt: found.names[vt] for vt in found.by_vtable} == \
        {VT_BASE: "Base", VT_DERIVED: "Derived", VT_INNER: "Outer::Inner"}
    base, derived = found.by_vtable[VT_BASE], found.by_vtable[VT_DERIVED]
    assert base.slots == [BASE_F, BASE_G] and derived.slots == [BASE_F, DERIVED_G, DERIVED_H]
    assert derived.parent == VT_BASE and derived.bases == ["Base", "Outer::Inner"] and base.parent is None
    # the second base's vtable is Derived's too, stored at +8; its functions are Derived's
    assert derived.others == {8: VT_DERIVED_8} and found.roles[DERIVED_SECOND] == [(VT_DERIVED, "slot 0 of its vtable at +0x8")]
    # constructors store the vtable into the object (Derived's both of its own); a local object's store isn't one
    assert base.ctors == {BASE_CTOR} and derived.ctors == {DERIVED_CTOR}
    # a virtual function a subclass inherits is the base's; an override and a constructor are the subclass's
    assert classes.owner(found, BASE_F) == (base, "virtual function 0")
    assert classes.owner(found, DERIVED_G) == (derived, "virtual function 1")
    assert classes.owner(found, DERIVED_CTOR) == (derived, "a constructor or destructor")
    assert classes.owner(found, LOCAL_USER) == (None, "")


def test_a_program_without_rtti_has_no_classes():
    sections = _sections()
    sections[DATA][:] = bytes(len(sections[DATA]))  # no type descriptors: built without /GR
    pe = scratch.PE(b"".join(sections.values()), BASE, 0x4000, TEXT, 0, [
        scratch.Section(".text", TEXT, 0x200, 0, 0x200, True), scratch.Section(".rdata", RDATA, 0x400, 0x200, 0x400, False),
        scratch.Section(".data", DATA, 0x200, 0x600, 0x200, False)])
    found = rtti.recover(pe)
    assert not found.by_vtable
    assert classes.listing(found, hex) == "No C++ classes found in the program's run-time type information."
    # its code still has them: the vtables its constructors store into `this` (named by address), the base class a
    # constructor calls with the same `this`, a second base's vtable stored further in, the fields of their methods
    rtti.read_code(pe, _functions(), found)
    assert set(found.by_vtable) == {VT_BASE, VT_DERIVED, VT_DERIVED_8}  # (Inner's vtable no function stores)
    base, derived = found.by_vtable[VT_BASE], found.by_vtable[VT_DERIVED]
    assert found.names[VT_DERIVED] == f"Class_{VT_DERIVED:08X}" and found.found_from == rtti.STORED
    assert derived.slots == [BASE_F, DERIVED_G, DERIVED_H] and derived.parent == VT_BASE and derived.others == {8: VT_DERIVED_8}
    assert base.ctors == {BASE_CTOR} and derived.ctors == {DERIVED_CTOR}
    assert {o: f.ctype() for o, f in base.fields.items()} == {4: "u32"}
    assert {o: f.ctype() for o, f in derived.fields.items()} == {4: "u32", 8: "u32", 0xC: "float", 0x10: "s16"}
    assert found.find(DERIVED_H) == [derived] and found.find(LOCAL_USER) == []


def test_decorated_class_names_read_as_cpp_writes_them():
    assert rtti.class_name("Town") == "Town"
    assert rtti.class_name("Inner@Outer") == "Outer::Inner"
    # a template: its arguments (classes, built-in types, pointers, numbers), and digits naming an earlier name
    assert rtti.class_name("?$List@PAVTown@@H@game") == "game::List<Town *, int>"
    assert rtti.class_name("?$Board@VRanked@schema@@UInfo@2@$0A@@net") == "net::Board<schema::Ranked, schema::Info, 0>"
    assert rtti.class_name("?$Map@V?$Ptr@VShader@@@@I") == "Map<Ptr<Shader>, unsigned int>"
    # what it doesn't read (a function pointer argument) stays as the program has it
    assert rtti.class_name("?$Callback@P6AXH@Z@") == ".?AV?$Callback@P6AXH@Z@@@"


def test_a_constructors_store_through_a_register_is_found_not_a_locals():
    code = b"\xC7\x06" + _words(0x1234) + b"\xC7\x46\x08" + _words(0x5678) + b"\xC7\x45\xF8" + _words(0x9ABC) + b"\xC3"
    assert x86.immediate_stores(code, 0x401000) == {2: 0, 9: 8}


GHIDRA_DRAFT = """undefined4 _func_00401038_4(byte param_1)

{
  undefined4 in_ECX;

  _func_00401000();
  if ((param_1 & 1) != 0) {
    _func_00402000(in_ECX);
  }
  return in_ECX;
}
"""


def test_ghidras_draft_of_a_method_reads_as_class_method_with_this():
    text = x86.method_draft(GHIDRA_DRAFT, "func_00401038", "Derived", "func_00401038", "virtual function 1", "RTTI")
    assert "undefined4 Derived::func_00401038(byte param_1)" in text
    assert "in_ECX" not in text.split("*/", 1)[1] and "_func_00402000(this);" in text and "return this;" in text
    assert "virtual function 1 of class Derived" in text
    # a method Ghidra took for __fastcall: its first argument is `this`, its second (edx) unused and dropped
    fast = "int __fastcall _func_0040103C(int *param_1, int param_2, int param_3)\n{\n  return *param_1 + param_3;\n}\n"
    text = x86.method_draft(fast, "func_0040103C", "Outer::Inner", "Grow", "virtual function 0", "RTTI")
    assert "int Outer::Inner::Grow(int param_3)" in text and "return *this + param_3;" in text
    # no plain C++ name for a template's class, no signature: the draft stays as Ghidra wrote it
    assert x86.method_draft(fast, "func_0040103C", "List<int>", "Grow", "virtual function 0", "RTTI") is None
    assert x86.method_draft(fast, "func_00409999", "Derived", "Grow", "virtual function 0", "RTTI") is None


def _project(tmp_path):
    """A PC project as an import leaves it: the program, functions.json (a real name given to one function) and
    the manifest; registered as a fork in a fresh app."""
    root = tmp_path / "game"
    (root / "orig").mkdir(parents=True)
    (root / "orig" / "game.exe").write_bytes(_exe())
    (root / "config" / "game").mkdir(parents=True)
    units = {"text_00401000": _functions()}
    (root / "config" / "game" / "functions.json").write_text(json.dumps(
        {"exe": "orig/game.exe", "units": units, "names": {f"func_{DERIVED_H:08X}": "Grow"}}))
    (root / ".decomp.wtf.json").write_text(json.dumps({"platform": "pc", "version": "game",
                                                       "game": {"path": "orig/game.exe"}}))
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {"kind": "fork", "origin": "scratch", "platform": "pc", "version": "game", "ready": True}
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug, name, root, config) VALUES(?, ?, ?, ?)",
                   ("game", "game", str(root), json.dumps(cfg)))
    return net, root, cfg


def test_get_class_in_a_pc_project_names_classes_and_their_methods(tmp_path):
    net, root, cfg = _project(tmp_path)
    assert "get_class" in tools.tool_names(fork=True)  # PC projects are forks
    listing = net.get_class("game", "")
    assert listing.startswith("3 classes, found from the program's run-time type information")
    assert f"- Derived (0x{VT_DERIVED:08X}): 3 virtual functions, 4 fields seen, at least 0x12 bytes, base Base" in listing
    # by class name, by func_<address>, by the report's symbol, by a real name: the class, the function marked
    for what in ("Derived", f"func_{DERIVED_G:08X}", f"@func_{DERIVED_G:08X}@8"):
        text = net.get_class("game", what)
        assert text.startswith(f"class Derived: vtable at 0x{VT_DERIVED:08X}, 3 virtual functions")
    assert f"1 (+0x4): func_{DERIVED_G:08X}, overrides ← this function" in net.get_class("game", f"func_{DERIVED_G:08X}")
    text = net.get_class("game", "Grow")
    assert "2 (+0x8): Grow, new ← this function" in text and "base classes (every one, the direct base first): " \
        "Base, Outer::Inner" in text and f"stored by (constructors, destructors): func_{DERIVED_CTOR:08X}" in text
    assert "a second vtable at +0x8: Derived's vtable at +0x8" in text
    # its fields, from its methods' code, and its base's (Derived objects have them too)
    assert "  +0x0C: float, 1 function (Grow) ← this function" in text and "  +0x00: vtable pointer" in text
    assert f"  +0x04: u32, 1 function (func_{BASE_G:08X}) (also in Base)" in text
    # the brief's lines, and the draft as a method (by the name agents write it with)
    fp = ForkProject(root)
    fn = {"name": f"@func_{DERIVED_H:08X}@8", "meta": json.dumps({"symbol": f"@func_{DERIVED_H:08X}@8"})}
    assert net.forks._class_brief(fp, cfg, fn)[2] == "- slot 2 of Derived (3 virtual functions, base class Base); " \
                                                     "vtable pointer at +0x0; this function uses +0xC float"
    draft = GHIDRA_DRAFT.replace("_func_00401038_4", f"func_{DERIVED_H:08X}")
    assert "undefined4 Derived::Grow(byte param_1)" in net.forks._method_draft(fp, cfg, fn, draft)
    other = {"name": "@func_00409999@8", "meta": None}
    assert net.forks._class_brief(fp, cfg, other) == [] and net.forks._method_draft(fp, cfg, other, draft) == draft


def test_get_class_says_where_it_reads_classes_on_a_console_without_them(tmp_path):
    net, root, cfg = _project(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE slug='game'", (json.dumps({**cfg, "platform": "gc"}),))
    assert consoles.get("gc").classes(ForkProject(root)) is None
    with pytest.raises(NetworkError, match="and PC programs'"):
        net.get_class("game", "")
    with pytest.raises(NetworkError, match="guess_struct reads MIPS, x86 code for now, not this project's"):
        net.guess_struct("game", f"@func_{DERIVED_H:08X}@8", "this")


def test_guess_struct_in_a_pc_project_reads_this_and_the_stack(tmp_path):
    net, root, cfg = _project(tmp_path)
    assert "guess_struct" in tools.tool_names(fork=True)
    text = net.guess_struct("game", "Grow", "this")
    assert text.startswith("Likely layout of this of Grow: S_Grow_this, from the loads and stores of 1 function.") and "/* 0x0C */ f32 unk_0C; /* 1 function */" in text, text
    assert "No loads or stores through arg0" in net.guess_struct("game", "Grow", "arg0")
    with pytest.raises(NetworkError, match="say this, arg0 to arg3"):
        net.guess_struct("game", "Grow", "nowhere")
    fn = {"name": f"@func_{DERIVED_H:08X}@8", "meta": json.dumps({"symbol": f"@func_{DERIVED_H:08X}@8"})}
    assert net.forks._struct_brief(ForkProject(root), cfg, fn)[1:] == [
        '### Likely struct layouts (from loads and stores across the game; guess_struct("@func_0040103C@8", "arg0") '
        'has one as C)', "- this: S_Grow_this, +0xC f32 (1 function)"]
    # a rename reads the names again, not the code (what the functions do is most of the time a big game takes)
    facts = next(iter(rtti._FACTS.values()))
    config = root / "config" / "game" / "functions.json"
    config.write_text(config.read_text().replace('"Grow"', '"Bigger"'))
    assert "Likely layout of this of Bigger: S_Bigger_this" in net.guess_struct("game", "Bigger", "this")
    assert next(iter(rtti._FACTS.values())) is facts
