"""References planned from a byte-exact build's linker map when the project has no splat config (forks/maprefs.py)."""

import io
import json
import struct
from types import SimpleNamespace

import pytest
from elftools.elf.elffile import ELFFile

from decompnet.forks import maprefs
from decompnet.forks.project import ForkError

SHT_PROGBITS, SHT_SYMTAB, SHT_STRTAB, SHT_REL = 1, 2, 3, 9
SHF_ALLOC, SHF_EXEC = 2, 4
R_26, R_HI16, R_LO16 = 4, 5, 6
JAL_FOO = 0x0C000000
LUI_A0_BAR = 0x3C040000
ADDIU_A0_BAR = 0x24840000
NOP = 0


def be_elf(sections, *, kind=1, load=None):
    """A big-endian MIPS ELF32: sections as (name, type, flags, address, data, link, info, entsize), and for a linked
    file (kind=2) `load` = (vaddr, paddr, section name) giving the first section its ROM address."""
    names = b"\0" + b"".join(s[0].encode() + b"\0" for s in sections) + b".shstrtab\0"
    body = bytearray(52 + (32 if load else 0))
    headers, at = [bytes(40)], 1
    for name, type_, flags, address, data, link, info, entsize in list(sections) + [
            (".shstrtab", SHT_STRTAB, 0, 0, names, 0, 0, 0)]:
        headers.append(struct.pack(">10I", at, type_, flags, address, len(body), len(data), link, info, 4, entsize))
        if name == (load[2] if load else None):
            phdr = struct.pack(">8I", 1, len(body), load[0], load[1], len(data), len(data), 5, 4)
        body += data
        at += len(name) + 1
    if load:
        body[52:84] = phdr
    header = (b"\x7fELF" + bytes([1, 2, 1]) + bytes(9)
              + struct.pack(">HHIIIIIHHHHHH", kind, 8, 1, 0, 52 if load else 0, len(body), 0, 52,
                            32 if load else 0, 1 if load else 0, 40, len(headers), len(headers) - 1))
    return header + bytes(body[52:]) + b"".join(headers)


def symtab(entries):
    """(.symtab, .strtab) for (name, value, bind, type, section index); local entries must come first."""
    strings, table = bytearray(b"\0"), bytearray(16)
    for name, value, bind, type_, index in entries:
        table += struct.pack(">IIIBBH", len(strings), value, 0, bind << 4 | type_, 0, index)
        strings += name.encode() + b"\0"
    return bytes(table), bytes(strings)


def word(*values):
    return b"".join(struct.pack(">I", v) for v in values)


def built_object(text):
    """An object whose text calls foo and loads the address of bar (relocations against two undefined globals)."""
    table, strings = symtab([("foo", 0, 1, 0, 0), ("bar", 0, 1, 0, 0)])
    reloc = struct.pack(">II", 0, 1 << 8 | R_26) + struct.pack(">II", 4, 2 << 8 | R_HI16) \
        + struct.pack(">II", 8, 2 << 8 | R_LO16)
    return be_elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXEC, 0, text, 0, 0, 0),
                   (".rel.text", SHT_REL, 0, 0, reloc, 3, 1, 8),
                   (".symtab", SHT_SYMTAB, 0, 0, table, 4, 1, 16), (".strtab", SHT_STRTAB, 0, 0, strings, 0, 0, 0)])


FOO, BAR = 0x80000100, 0x80021234
OBJECT_TEXT = word(JAL_FOO, LUI_A0_BAR, ADDIU_A0_BAR, NOP)
LINKED_TEXT = word(JAL_FOO | (FOO & 0x0FFFFFFF) >> 2, LUI_A0_BAR | (BAR + 0x8000) >> 16, ADDIU_A0_BAR | BAR & 0xFFFF, NOP)
MAP = """Discarded input sections

 .reginfo       0x00000000       0x18 build/us/src/a.o

Linker script and memory map

.main           0x80000000       0x20 load address 0x00001000
 build/us/src/a.o(.text*)
 .text          0x80000000       0x10 build/us/src/a.o
 build/us/src/b.o(.text*)
 .text
                0x80000010       0x10 build/us/src/b.o
 .reginfo       0x00000000       0x18 build/us/src/b.o
"""


def project(tmp_path, rom_patch=None):
    root = tmp_path / "game"
    (root / "src").mkdir(parents=True)
    (root / "build/us/src").mkdir(parents=True)
    (root / "Makefile").write_text("VERSION ?= us\n")
    for name in "ab":
        (root / f"src/{name}.c").write_text("int x;\n")
        (root / f"build/us/src/{name}.o").write_bytes(built_object(OBJECT_TEXT))
    table, strings = symtab([("foo", FOO, 1, 2, 1), ("bar", BAR, 1, 1, 1)])
    (root / "build/us/game.elf").write_bytes(be_elf(
        [(".main", SHT_PROGBITS, SHF_ALLOC | SHF_EXEC, 0x80000000, LINKED_TEXT * 2, 0, 0, 0),
         (".symtab", SHT_SYMTAB, 0, 0, table, 3, 1, 16), (".strtab", SHT_STRTAB, 0, 0, strings, 0, 0, 0)],
        kind=2, load=(0x80000000, 0x1000, ".main")))
    (root / "build/us/game.map").write_text(MAP)
    rom = bytearray(0x2000)
    rom[0x1000:0x1020] = LINKED_TEXT * 2
    if rom_patch:
        rom_patch(rom)
    (root / "baserom.us.z64").write_bytes(rom)
    return SimpleNamespace(root=root, platform="n64", _rom_placed=root / "baserom.us.z64",
                           needs=lambda version=None: {"path": "baserom.us.z64"},
                           setup_commands=lambda version=None: [["make", "VERSION=us"]])


def test_map_reading_handles_both_line_shapes_and_skips_discarded_sections():
    sections = maprefs.parse_map(MAP)
    assert sections == {("build/us/src/a.o", ".text"): (0x80000000, 0x10),
                        ("build/us/src/b.o", ".text"): (0x80000010, 0x10)}


def test_a_matching_build_gets_references_proved_against_the_original(tmp_path):
    proj = project(tmp_path)
    assert maprefs.generate(proj, "us", log=lambda *_: None) is True
    config = json.loads((proj.root / "objdiff.json").read_text())
    assert config["custom_args"] == ["VERSION=us"] and config["build_base"] and not config["build_target"]
    assert [u["name"] for u in config["units"]] == ["src/a", "src/b"]
    unit = config["units"][0]
    assert unit["base_path"] == "build/us/src/a.o" and unit["metadata"] == {"source_path": "src/a.c"}
    assert (proj.root / unit["target_path"]).read_bytes() == (proj.root / unit["base_path"]).read_bytes()
    assert not config["coverage_notes"]


def test_an_object_that_differs_from_the_original_is_left_out_and_named(tmp_path):
    def patch(rom):
        rom[0x1010 + 4] ^= 1  # b.o's `lui` no longer matches

    proj = project(tmp_path, patch)
    maprefs.generate(proj, "us", log=lambda *_: None)
    config = json.loads((proj.root / "objdiff.json").read_text())
    assert [u["name"] for u in config["units"]] == ["src/a"]
    assert "src/b.c" in config["coverage_notes"][0]


def test_nothing_is_published_when_no_object_reproduces_the_original(tmp_path):
    proj = project(tmp_path, lambda rom: rom.__setitem__(slice(0x1000, 0x1020), bytes(0x20)))
    with pytest.raises(ForkError, match="can't be verified"):
        maprefs.generate(proj, "us", log=lambda *_: None)
    assert not (proj.root / "objdiff.json").exists()
    assert not (proj.root / ".decomp-wtf/objdiff-references").exists()


def test_an_existing_objdiff_json_is_never_replaced(tmp_path):
    proj = project(tmp_path)
    (proj.root / "objdiff.json").write_text("{}")
    assert maprefs.generate(proj, "us", log=lambda *_: None) is False
    assert (proj.root / "objdiff.json").read_text() == "{}"


def test_other_consoles_are_not_planned_from_a_map(tmp_path):
    proj = project(tmp_path)
    proj.platform = "ps2"
    assert maprefs.generate(proj, "us", log=lambda *_: None) is False


def test_data_the_cartridge_does_not_hold_is_reported_but_code_must_still_match(tmp_path):
    root = tmp_path
    table, strings = symtab([])
    obj = be_elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXEC, 0, word(NOP, NOP), 0, 0, 0),
                  (".data", SHT_PROGBITS, SHF_ALLOC | 1, 0, word(7), 0, 0, 0),
                  (".symtab", SHT_SYMTAB, 0, 0, table, 4, 1, 16), (".strtab", SHT_STRTAB, 0, 0, strings, 0, 0, 0)])
    (root / "o.o").write_bytes(obj)
    addresses = {("o", ".text"): (0x80000000, 8), ("o", ".data"): (0x80900000, 4)}
    ranges = [(0x80000000, 8, 0x100), (0x80900000, 4, 0x2000)]
    image = bytes(0x100) + bytes(8) + bytes(0x100)
    covered, external = maprefs.verify_object(root / "o.o", "o", addresses, {}, ranges, image)
    assert covered == [".text"] and external == [".data"]
    with pytest.raises(maprefs.Unverified, match="does not reproduce"):
        maprefs.verify_object(root / "o.o", "o", addresses, {}, ranges, bytes(0x100) + word(1, 1))


def test_every_relocation_kind_is_applied_with_the_linkers_formulas():
    obj = ELFFile(io.BytesIO(built_object(OBJECT_TEXT)))
    out = maprefs.relocate(obj, "k", {("k", ".text"): (0x80000000, 16)}, {"foo": FOO, "bar": BAR})
    assert out[".text"] == LINKED_TEXT


def test_the_projects_decompressed_copy_is_offered_as_an_original_image(tmp_path):
    from types import SimpleNamespace

    rom = tmp_path / "baseroms" / "v" / "baserom.z64"
    rom.parent.mkdir(parents=True)
    for name in ("baserom.z64", "baserom-decompressed.z64", "other.z64"):
        (rom.parent / name).write_bytes(b"\0")
    found = maprefs.decompressed_images(SimpleNamespace(root=tmp_path), rom)
    assert [p.name for p in found] == ["baserom-decompressed.z64"]


SEGMENT_MAP = """Linker script and memory map

.text           0x00000000       0x20
 build/us/src/a.o(.text*)
 .text          0x00000000       0x10 build/us/src/a.o
 build/us/src/b.o(.text*)
 .text          0x00000010       0x10 build/us/src/b.o
.data           0x00000020        0x0
"""
SEGMENT_FINAL_MAP = """Linker script and memory map

.main           0x80000000       0x20 load address 0x00001000
 build/us/segments/seg.plf(.text*)
 .text          0x80000000       0x20 build/us/segments/seg.plf
"""


def test_a_partial_link_segment_is_planned_from_its_own_map(tmp_path):
    """The Zelda decomps' final map lists `code.plf`, not the objects inside it: the segment's map has them."""
    proj = project(tmp_path)
    (proj.root / "build/us/game.map").write_text(SEGMENT_FINAL_MAP)
    (proj.root / "build/us/segments").mkdir()
    (proj.root / "build/us/segments/seg.map").write_text(SEGMENT_MAP)
    (proj.root / "build/us/segments/seg.plf").write_bytes(b"")
    maprefs.generate(proj, "us", log=lambda *_: None)
    config = json.loads((proj.root / "objdiff.json").read_text())
    assert [u["name"] for u in config["units"]] == ["src/a", "src/b"]  # (the segment itself is no unit)


def test_a_partial_map_gives_offsets_inside_each_output_section():
    text = SEGMENT_MAP.replace(".data           0x00000020        0x0", (
        ".data           0x00000020       0x10\n build/us/src/a.o(.data*)\n .data          0x00000028        0x8 "
        "build/us/src/a.o\n"))
    assert maprefs.parse_partial_map(text) == [
        ("build/us/src/a.o", ".text", ".text", 0, 0x10), ("build/us/src/b.o", ".text", ".text", 0x10, 0x10),
        ("build/us/src/a.o", ".data", ".data", 8, 8)]


def test_a_map_that_proves_only_a_trivial_share_of_the_code_is_not_published(tmp_path):
    proj = project(tmp_path)
    table, strings = symtab([("foo", FOO, 1, 2, 1), ("bar", BAR, 1, 1, 1)])
    (proj.root / "build/us/game.elf").write_bytes(be_elf(
        [(".main", SHT_PROGBITS, SHF_ALLOC | SHF_EXEC, 0x80000000, LINKED_TEXT * 2 + bytes(0x400), 0, 0, 0),
         (".symtab", SHT_SYMTAB, 0, 0, table, 3, 1, 16), (".strtab", SHT_STRTAB, 0, 0, strings, 0, 0, 0)],
        kind=2, load=(0x80000000, 0x1000, ".main")))
    with pytest.raises(ForkError, match="too little to measure"):
        maprefs.generate(proj, "us", log=lambda *_: None)
    assert not (proj.root / "objdiff.json").exists()


def test_a_splat_config_the_planner_cannot_use_falls_back_to_the_builds_own_map(tmp_path, monkeypatch):
    """A compressed-cartridge game's splat segments have no numeric start/vram: the matching build's map plans it."""
    from decompnet import consoles
    from decompnet.forks.project import ForkProject

    (tmp_path / "game.yaml").write_text("options:\n  platform: n64\nsegments:\n  - {name: main, type: code}\n")
    stub = SimpleNamespace(generate_objdiff=lambda *a, **k: (_ for _ in ()).throw(
        ForkError("automatic objdiff setup needs a numeric start and vram for each code segment")))
    monkeypatch.setattr(consoles, "get", lambda platform: stub)
    seen = []
    monkeypatch.setattr(maprefs, "generate", lambda proj, version, log=print: seen.append(version) or True)
    assert ForkProject(tmp_path).generate_objdiff("us", log=lambda *_: None) is True
    assert seen == ["us"]
    monkeypatch.setattr(maprefs, "generate", lambda proj, version, log=print: False)
    with pytest.raises(ForkError, match="numeric start"):
        ForkProject(tmp_path).generate_objdiff("us", log=lambda *_: None)
