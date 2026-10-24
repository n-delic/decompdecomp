"""PS2 games that ship with their debug information get it used (#3: Champions of Norrath ships its debug symbols):
the symbol table names functions and data with their exact sizes before splat splits, and the .mdebug section's
STABS give the source files (the project's units), each function's prototype and locals (its brief), and the game's
types and globals (the shared header).

The readers are tested on small hand-made inputs written for these tests; the whole import on the demo game built
with -g (projects/demo-ps2/build.py --debug), which needs EE-GCC 2.96 and binutils.
"""

import struct
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.app.engine.importing import _HEADER_NAMES
from decompnet.consoles.ps2 import debuginfo, mdebug, stabs, symtab
from decompnet.consoles.ps2.cheader import Header
from decompnet.elfobj import unmangle
from decompnet.forks import csource
from decompnet.rom import RomError, split

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "projects" / "demo-ps2"
GAME = ROOT / "projects" / "demo-n64" / "original"

# a hand-made ELF: header, the sections' contents one after another, then the section headers
ELF_HEADER_BYTES, SECTION_HEADER_BYTES, SYMBOL_BYTES = 52, 40, 16
SHT_PROGBITS, SHT_SYMTAB, SHT_STRTAB, SHT_NOBITS, SHT_MIPS_DEBUG = 1, 2, 3, 8, 0x70000005
SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 1, 2, 4
STB_LOCAL, STB_GLOBAL = 0, 1
STT_NOTYPE, STT_OBJECT, STT_FUNC = 0, 1, 2
SHN_ABS = 0xFFF1


def _elf(sections: list[tuple[str, int, int, int, bytes, int]]) -> bytes:
    """A little-endian MIPS ELF with these sections: (name, type, flags, address, contents, link)."""
    names = b"\0" + b"".join(name.encode() + b"\0" for name, *_ in sections) + b".shstrtab\0"
    body = bytearray(ELF_HEADER_BYTES)
    headers, name_at = [bytes(SECTION_HEADER_BYTES)], 1
    for name, kind, flags, address, data, link in sections + [(".shstrtab", SHT_STRTAB, 0, 0, names, 0)]:
        entry = SYMBOL_BYTES if kind == SHT_SYMTAB else 0
        headers.append(struct.pack("<10I", name_at, kind, flags, address, len(body), len(data), link, 1, 4, entry))
        body += data
        name_at += len(name) + 1
    header = (b"\x7fELF" + bytes([1, 1, 1]) + bytes(9)
              + struct.pack("<HHIIIIIHHHHHH", 2, 8, 1, 0x100000, 0, len(body), 0, ELF_HEADER_BYTES, 32, 0,
                            SECTION_HEADER_BYTES, len(headers), len(headers) - 1))
    return bytes(header) + bytes(body[ELF_HEADER_BYTES:]) + b"".join(headers)


def _symbols(entries: list[tuple[str, int, int, int, int, int]]) -> tuple[bytes, bytes]:
    """.symtab and .strtab for (name, value, size, bind, type, section index)."""
    strings, table = bytearray(b"\0"), bytearray(SYMBOL_BYTES)
    for name, value, size, bind, kind, index in entries:
        table += struct.pack("<IIIBBH", len(strings), value, size, bind << 4 | kind, 0, index)
        strings += name.encode() + b"\0"
    return bytes(table), bytes(strings)


def _mdebug(at: int, files: list[tuple[str, int, list[tuple[str, int, int, int, int]]]]) -> bytes:
    """An .mdebug section placed at file offset `at`: (file name, address, [(string, value, st, sc, index)])."""
    strings, symbols, descriptors = bytearray(), bytearray(), bytearray()
    for name, address, entries in files:
        local = bytearray(b"\0" + name.encode() + b"\0")
        first = len(symbols) // mdebug.LOCAL_SYMBOL.size
        for text, value, kind, storage, index in entries:
            symbols += mdebug.LOCAL_SYMBOL.pack(len(local), value, kind | storage << mdebug.CLASS_SHIFT
                                                | index << mdebug.INDEX_SHIFT)
            local += text.encode() + b"\0"
        descriptors += mdebug.FILE_DESCRIPTOR.pack(address, 1, len(strings), len(local), first, len(entries),
                                                   *[0] * 13)
        strings += local
    symbols_at = at + mdebug.HEADER.size
    strings_at = symbols_at + len(symbols)
    files_at = strings_at + len(strings)
    header = mdebug.HEADER.pack(mdebug.MAGIC, 0, 0, 0, 0, 0, 0, 0, 0, len(symbols) // mdebug.LOCAL_SYMBOL.size,
                                symbols_at, 0, 0, 0, 0, len(strings), strings_at, 0, 0, len(files), files_at, 0, 0, 0, 0)
    return header + bytes(symbols) + bytes(strings) + bytes(descriptors)


def _stab(code: int, text: str, value: int = 0) -> tuple[str, int, int, int, int]:
    return text, value, mdebug.ST_NIL, mdebug.SC_INFO, mdebug.STAB_MARK + code


# A C program's STABS as GCC writes them (written for this test): base types, a typedef of a reserved name (u8),
# an enum, structs with a bitfield, a pointer to itself, a function pointer, a 2D array and a field the game keeps
# further out than natural alignment would, a function with parameters and locals, a global and a static.
C_STABS = [
    (stabs.N_LSYM, "int:t1=r1;-2147483648;2147483647;"), (stabs.N_LSYM, "char:t2=r2;0;127;"),
    (stabs.N_LSYM, "unsigned char:t3=@s8;r3;0;255;"), (stabs.N_LSYM, "float:t4=r1;4;0;"),
    (stabs.N_LSYM, "void:t5=5"), (stabs.N_LSYM, "u8:t6=3"),
    (stabs.N_LSYM, "Kind:T7=eKIND_NONE:0,KIND_PLAYER:3,;"),
    (stabs.N_LSYM, "Vec:T8=s12x:4,0,32;y:4,32,32;z:4,64,32;;"),
    (stabs.N_LSYM, "Actor:T9=s52pos:8,0,96;kind:7,96,32;flags:6,128,3;alive:6,131,1;next:10=*9,160,32;"
                   "think:11=*12=f1,192,32;grid:13=ar1;0;1;14=ar1;0;1;1,224,128;later:1,384,32;;"),
    (stabs.N_LSYM, "Actor:t15=9"),
    (stabs.N_FUN, "actor_tick:F1", 0x100000), (stabs.N_RSYM, "a:P16=*15", 4), (stabs.N_PSYM, "dt:p1", 0x10),
    (stabs.N_RSYM, "i:r1", 16), (stabs.N_LSYM, "rate:4", -8), (stabs.N_FUN, "", 0x40),
    (stabs.N_GSYM, "g_actors:G17=ar1;0;3;15"), (stabs.N_LCSYM, "s_ticks:S1", 0x100200),
]


def _source(name: str, entries) -> stabs.SourceInfo:
    """A source file's STABS from (code, string) or (code, string, value)."""
    return stabs.read_file(name, [(entry[0], entry[1], entry[2] if len(entry) > 2 else 0) for entry in entries])


def test_stabs_give_a_c_programs_types_with_their_offsets():
    header = Header([_source("actor.c", C_STABS)], _HEADER_NAMES)
    text = header.text()
    assert "enum Kind {\n    KIND_NONE = 0,\n    KIND_PLAYER = 3,\n};" in text
    assert "struct Actor { /* 0x34 bytes */\n    struct Vec pos; /* 0x0 */\n    enum Kind kind; /* 0xC */\n" in text
    assert "    unsigned char flags : 3; /* 0x10 bit 0 */\n    unsigned char alive : 1; /* 0x10 bit 3 */\n" in text
    assert "    struct Actor *next; /* 0x14 */\n    int (*think)(); /* 0x18 */\n    int grid[2][2]; /* 0x1C */\n" in text
    assert "    unsigned char pad_2C[0x4];\n    int later; /* 0x30 */\n};" in text  # where the game has it
    assert "typedef struct Actor Actor;" in text and "typedef unsigned char u8" not in text  # u8: BASE_HEADER's
    assert text.index("struct Vec {") < text.index("struct Actor {") < text.index("typedef struct Actor Actor;")


def test_stabs_give_prototypes_locals_and_globals():
    source = _source("actor.c", C_STABS)
    header = Header([source], _HEADER_NAMES)
    function, = source.functions
    assert (function.address, function.size, function.is_static) == (0x100000, 0x40, False)
    assert header.prototype(function, "actor_tick") == "int actor_tick(Actor *a, int dt)"
    assert [(p.name, p.where) for p in function.params] == [("a", "$a0"), ("dt", "the stack, frame offset +0x10")]
    assert [(header.variable(v), v.where) for v in function.locals] == [
        ("int i", "$s0"), ("float rate", "the stack, frame offset -0x8")]
    assert header.text([("g_actors", source.globals[0].type, True)]).endswith("extern Actor g_actors[4];\n")
    assert [(v.name, v.address) for v in source.statics] == [("s_ticks", 0x100200)]


# A C++ class hierarchy as GCC 2.9x writes it (written for this test): a vtable pointer field, methods with
# overloads and virtual ones, a base class, then the same as GCC 3 writes it (const, _vptr$ fields, mangled names)
CPP_STABS = [
    (stabs.N_LSYM, "int:t1=r1;-2147483648;2147483647;"), (stabs.N_LSYM, "void:t2=2"),
    (stabs.N_LSYM, "__vtbl_ptr_type:t3=s8__delta:4=@s16;r4;-32768;32767;,0,16;__index:4,16,16;__pfn:5=*2,32,32;;"),
    (stabs.N_LSYM, "Shape:Tt6=s8area:1,0,32;$vf6:7=*3,32;Shape::8=##9=*6;:__5Shape;2A.;"
                   "_$_5Shape::10=#6,2,9,1,2;:_$_5Shape;2A*1;6;;size::11=##1;:C5Shape;2B*2;6;;;~%6;"),
    (stabs.N_LSYM, "Square:Tt12=s12!1,020,6;side:/21,64,32;size::13=##1;:C6Square;2B*2;6;;count:1:_6Square$count;;~%6;"),
    (stabs.N_FUN, "size__C6Square:F1", 0x100000), (stabs.N_RSYM, "this:P14=*12", 4), (stabs.N_FUN, "", 0x10),
]
CPP3_STABS = [
    (stabs.N_LSYM, "int:t1=r1;-2147483648;2147483647;"), (stabs.N_LSYM, "bool:t2=@s8;-16;"),
    (stabs.N_LSYM, "__vtbl_ptr_type:t3=*4=f1"),
    (stabs.N_LSYM, "Light:Tt5=s12_vptr$Light:6=*3,0,32;on:2,32,8;level:1,64,32;__base_ctor::7=#5,8=k9=*5,9;"
                   ":_ZN5LightC2Ev;2A.;get::10=#5,1,11=k12=*13=k5,1;:_ZNK5Light3getEv;2B*2;5;;;~%5;"),
]


def test_stabs_give_cpp_classes_as_structs_of_their_data():
    text = Header([_source("shapes.cpp", CPP_STABS)], _HEADER_NAMES).text()
    assert "struct Shape { /* 0x8 bytes */\n    int area; /* 0x0 */\n    void *_vf6; /* 0x4 */\n};" in text
    assert "struct Square { /* 0xC bytes */\n    Shape base_Shape; /* 0x0 */\n    int side; /* 0x8 */\n};" in text
    assert "typedef struct Shape Shape;" in text and "__vtbl_ptr_type" not in text  # the compiler's own type
    source = _source("light.cpp", CPP3_STABS)
    text = Header([source], _HEADER_NAMES).text()
    assert "    int (**_vptr_Light)(); /* 0x0 */\n    signed char on; /* 0x4 */\n    int level; /* 0x8 */\n" in text
    assert source.skipped == 0


def test_types_repeated_by_every_file_are_written_once_and_different_ones_get_their_own_name():
    data = [(stabs.N_LSYM, "int:t1=r1;-2147483648;2147483647;"), (stabs.N_LSYM, "Data:T2=s4a:1,0,32;;")]
    other = [(stabs.N_LSYM, "int:t1=r1;-2147483648;2147483647;"), (stabs.N_LSYM, "Data:T2=s8a:1,0,32;b:1,32,32;;"),
             (stabs.N_LSYM, "Data:t3=2"), (stabs.N_LSYM, "Mode:T4=eA:0,B:1,;"), (stabs.N_LSYM, "Phase:T5=eB:5,C:6,;"),
             (stabs.N_LSYM, "Item:Tt6=s4a:1,0,32;;")]
    data = data + [(stabs.N_LSYM, "Item:Tt9=s8a:1,0,32;b:1,32,32;;")]  # a C++ class (tag and typedef) of another size
    # a file that only knows Data by name (a pointer to it) has the same Link as the one that defines Data
    defines = data + [(stabs.N_LSYM, "Link:T3=s4to:4=*2,0,32;;")]
    knows = [(stabs.N_LSYM, "Link:T1=s4to:2=*3=xsData:,0,32;;")]
    header = Header([_source("one.c", defines), _source("two.c", data), _source("four.c", knows),
                     _source("three.c", other)])
    text = header.text()
    assert text.count("struct Link {") == 1 and "    struct Data *to; /* 0x0 */" in text
    assert text.count("struct Data {") == 1 and "struct Data_2 { /* 0x8 bytes */" in text
    assert "typedef struct Data_2 Data;" in text and "struct Item_2 { /* 0x4 bytes */" in text
    assert "typedef struct Item_2 Item_2;" in text and header.renamed == 3  # Data_2, Item_2 and its typedef
    assert "    /* B = 5, (the name is taken) */" in text and "    C = 6," in text  # one enumerator per name


def test_an_mdebug_section_gives_its_files_procedures_and_stabs():
    """GCC continues a long STABS string in the next symbol with a backslash at the end."""
    entries = [("@stabs", 0, mdebug.ST_NIL, mdebug.SC_INFO, mdebug.STAB_MARK),
               _stab(stabs.N_LSYM, "int:t1=r1;-2147483648;2147483647;"),
               _stab(stabs.N_LSYM, "Pair:T2=s8a:1,0,32;\\"), _stab(stabs.N_LSYM, "b:1,32,32;;"),
               ("pair_sum", 0x100000, mdebug.ST_PROC, mdebug.SC_TEXT, 1),
               _stab(stabs.N_FUN, "pair_sum:F1", 0x100000), _stab(stabs.N_FUN, "", 0x20)]
    section = _mdebug(ELF_HEADER_BYTES, [("src/pair.c", 0x100000, entries), ("src/crt0.s", 0x100020, [
        ("_start", 0x100020, mdebug.ST_PROC, mdebug.SC_TEXT, 1)])])
    data = _elf([(".mdebug", SHT_MIPS_DEBUG, 0, 0, section, 0)])
    files = mdebug.read(data, ELF_HEADER_BYTES, len(section))
    assert [(f.name, f.address, f.procedures()) for f in files] == [
        ("src/pair.c", 0x100000, [("pair_sum", 0x100000)]), ("src/crt0.s", 0x100020, [("_start", 0x100020)])]
    assert files[0].stabs()[1] == (stabs.N_LSYM, "Pair:T2=s8a:1,0,32;b:1,32,32;;", 0)
    info = debuginfo.read(data)
    assert info.sources == ["crt0.s", "pair.c"] and info.units == {0x100000: "pair.c", 0x100020: "crt0.s"}
    assert info.functions[0x100000]["prototype"] == "int pair_sum(void)"
    assert "struct Pair { /* 0x8 bytes */" in info.header
    assert info.summary() == "debug symbols: 0 functions named, 1 type, 2 source files"


def _program(symbols: list[tuple[str, int, int, int, int, int]]) -> bytes:
    """A program with .text (section 1), .data (2), .bss (3), and these symbols."""
    table, strings = _symbols(symbols)
    return _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0x100000, bytes(0x100), 0),
                 (".data", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, 0x100100, bytes(0x40), 0),
                 (".bss", SHT_NOBITS, SHF_ALLOC | SHF_WRITE, 0x100140, b"", 0),
                 (".symtab", SHT_SYMTAB, 0, 0, table, 5), (".strtab", SHT_STRTAB, 0, 0, strings, 0)])


def test_the_symbol_table_names_functions_and_data_as_c_names():
    """Mangled C++ names stay as they are; GCC 2's special ones get _ for what C can't have; labels and the
    linker's markers aren't names; two statics with one name are told apart by their address, and a global keeps
    its name."""
    data = _program([
        ("init", 0x100000, 0x10, STB_LOCAL, STT_FUNC, 1), ("Update__4CPedi", 0x100010, 0x20, STB_GLOBAL, STT_FUNC, 1),
        ("_$_4CPed", 0x100030, 0x10, STB_GLOBAL, STT_FUNC, 1), ("init", 0x100040, 0x8, STB_GLOBAL, STT_FUNC, 1),
        ("init", 0x100048, 0x8, STB_LOCAL, STT_FUNC, 1), (".L12", 0x100004, 0, STB_LOCAL, STT_NOTYPE, 1),
        ("gcc2_compiled.", 0x100000, 0, STB_LOCAL, STT_NOTYPE, 1), ("memcpy", 0x100050, 0x8, STB_GLOBAL, STT_FUNC, 1),
        ("__memcpy", 0x100050, 0x8, STB_LOCAL, STT_FUNC, 1), ("_vt$4CPed", 0x100100, 0x10, STB_GLOBAL, STT_OBJECT, 2),
        ("count.0", 0x100110, 4, STB_LOCAL, STT_OBJECT, 2), ("heap", 0x100140, 0, STB_LOCAL, STT_NOTYPE, 3),
        ("_gp", 0x108130, 0, STB_GLOBAL, STT_NOTYPE, SHN_ABS)])
    named = symtab.read(ELFFile(BytesIO(data)))
    assert [(n.name, n.address, n.size, n.function) for n in named] == [
        ("init_00100000", 0x100000, 0x10, True), ("Update__4CPedi", 0x100010, 0x20, True),
        ("___4CPed", 0x100030, 0x10, True), ("init", 0x100040, 8, True), ("init_00100048", 0x100048, 8, True),
        ("memcpy", 0x100050, 8, True), ("_vt_4CPed", 0x100100, 0x10, False), ("count_0", 0x100110, 4, False),
        ("heap", 0x100140, 0, False)]
    assert symtab.symbol_addrs(named[:1] + named[-2:]) == ["init_00100000 = 0x00100000; // type:func size:0x10",
                                                         "count_0 = 0x00100110; // size:0x4", "heap = 0x00100140;"]
    info = debuginfo.read(data)
    assert info.summary() == "debug symbols: 6 functions named" and info.header == ""


def test_a_name_too_long_for_a_file_gets_a_short_file_name():
    """splat writes each function to a file named after it and refuses a name over 253 bytes: a PS2 game's mangled
    C++ name stopped it, and the import went on without any of the game's names. That name gets its address's
    file name; the others stay as they were."""
    long = "__Q24Game7Network" + "Zt9allocator1ZQ24Game7Network6Packet" * 8
    named = [symtab.Named("init", 0x100000, 0x10, True, ".text"), symtab.Named(long, 0x120000, 0x164, True, ".text"),
             symtab.Named("_vt_" + long, 0x130000, 0x20, False, ".data")]
    assert len(long.encode()) > symtab.SPLAT_FILENAME_BYTES
    assert symtab.symbol_addrs(named) == [
        "init = 0x00100000; // type:func size:0x10",
        f"{long} = 0x00120000; // type:func size:0x164 filename:func_00120000",
        f"_vt_{long} = 0x00130000; // size:0x20 filename:D_00130000"]


def test_a_program_without_debug_information_has_none():
    assert debuginfo.read(_elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0x100000, bytes(8), 0)])) is None


def test_splat_gets_the_names_its_own_lines_lack(tmp_path):
    """Two names for one address, or one name twice, stop splat: the program's names never repeat splat's."""
    path = tmp_path / "symbol_addrs.txt"
    path.write_text("// splat's\n_start = 0x00100000; // type:func\n")
    split._add_symbols(path, ["_start = 0x00100000; // type:func size:0x5C", "__start = 0x00100000; // type:func",
                              "main = 0x00100060; // type:func size:0x8", "g_score = 0x00100100; // size:0x4"])
    assert path.read_text().splitlines()[1:] == ["_start = 0x00100000; // type:func",
                                                 "main = 0x00100060; // type:func size:0x8", "g_score = 0x00100100; // size:0x4"]
    assert split.named_symbols(tmp_path) == {"_start": 0x100000, "main": 0x100060}  # functions only


def test_a_compiled_destructor_is_judged_by_the_games_name():
    """The game's _$_4CPed is ___4CPed in decompdecomp; an agent's C++ destructor compiles to _$_4CPed again."""
    table, strings = _symbols([("_$_4CPed", 0, 8, STB_GLOBAL, STT_FUNC, 1), ("_vt$4CPed", 0, 0, STB_GLOBAL, STT_NOTYPE, 0)])
    obj = _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0, bytes(8), 0),
                (".symtab", SHT_SYMTAB, 0, 0, table, 3), (".strtab", SHT_STRTAB, 0, 0, strings, 0)])
    renamed = ELFFile(BytesIO(unmangle(obj, lambda name: name in {"___4CPed", "_vt_4CPed"})))
    assert [s.name for s in renamed.get_section_by_name(".symtab").iter_symbols()][1:] == ["___4CPed", "_vt_4CPed"]


# ---------------------------------------------------------------------------------------- with the toolchain

needs_toolchain = pytest.mark.skipif(toolchains.installed("ee-gcc-2.96") is None
                                     or toolchains.installed("binutils-mips") is None,
                                     reason="needs ee-gcc-2.96 and binutils-mips to build the PS2 demo")
DEBUG_BUILD = DEMO / "build" / "ee-gcc-2.96-debug"


def _import_debug_game(work: Path):
    if not (DEBUG_BUILD / "SLUS_999.99").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), "--debug"], check=True)
    net = RomNetwork(work / "app.db", work)
    said = []
    slug = net.import_rom(DEBUG_BUILD / "SLUS_999.99", confirmed=True, toolchain="ee-gcc-2.96", log=said.append)
    return net, slug, said


@pytest.fixture(scope="module")
def debug_game(tmp_path_factory):
    return _import_debug_game(tmp_path_factory.mktemp("ps2-debug"))


@needs_toolchain
def test_names_splat_refuses_never_stop_an_import(tmp_path, monkeypatch):
    """The game's own names help the split; if splat won't take them, the game imports as a stripped one would,
    and keeps its units, prototypes and types."""
    real = split.split_rom

    def picky(*args, symbols=None, **kwargs):
        if symbols:
            raise RomError("splat could not analyse this ROM: a symbol it doesn't take")
        return real(*args, **kwargs)

    monkeypatch.setattr(split, "split_rom", picky)
    net, slug, said = _import_debug_game(tmp_path)
    assert "splat refused the game's own names: splitting without them" in said
    pid = net.project_row(slug)["id"]
    units = {r["unit"] for r in net.store.all("SELECT unit FROM functions WHERE project_id=?", pid)}
    assert "game/game.c" in units and "struct Entity {" in net.get_context(slug)


@needs_toolchain
def test_a_game_with_debug_symbols_imports_with_its_names_units_and_types(debug_game):
    net, slug, said = debug_game
    summary = next(line for line in said if line.startswith("debug symbols: "))
    assert "functions named" in summary and "types" in summary and "18 source files" in summary
    pid = net.project_row(slug)["id"]
    unit = {r["name"]: r["unit"] for r in net.store.all("SELECT name, unit FROM functions WHERE project_id=?", pid)}
    assert unit["clamp_s32"] == "game/game.c" and unit["adler32"] == "zlib/adler32.c" and unit["_start"] == "crt0.s"
    assert not any(name.startswith("func_") for name in unit)  # every function has its real name
    header = net.get_context(slug)
    assert "struct Entity { /* 0x1C bytes */" in header and "    int hp; /* 0x18 */" in header
    assert "extern Entity g_entities[32];" in header and "\nint g_score;" in header  # $gp-relative: no extern
    agent, _ = net.local_agent("debug-reader")
    brief = net.claim(agent, slug, "entity_spawn")
    assert "source file game/game.c" in brief and "prototype: Entity *entity_spawn(short type, int x, int y);" in brief
    net.release(agent, "entity_spawn", "", slug)


@needs_toolchain
def test_a_game_with_debug_symbols_matches_with_its_own_source(debug_game):
    """What the debug information doesn't hold (macros, and prototypes for calls) is all an agent adds: the game's
    own C then matches function by function, with no renames and no struct worked out by hand."""
    net, slug, _ = debug_game
    agent, _ = net.local_agent("debug-matcher")
    game_h = (GAME / "game.h").read_text()
    extra = "\n".join(line for line in game_h.splitlines() if line.startswith("#define") and "NULL" not in line
                      or line.endswith(");") and "(" in line and not line.startswith(("extern", "typedef")))
    assert net.edit_context(agent, slug, "", extra.replace("const ", ""), "the game's macros and prototypes").startswith("✅")
    source = (GAME / "game.c").read_text()
    for name in ["clamp_s32", "abs_s32", "player_take_damage", "rng_next", "count_alive_entities",
                 "entity_update_physics", "entity_default_hp", "entity_spawn", "add_score", "lerp", "checksum_save",
                 "entity_collides", "game_tick"]:
        res = net.attempt(agent, name, csource.get(source, name).replace("const ", ""), slug)
        assert res["matched"], (name, res["text"][:2000])


@needs_toolchain
def test_types_the_compiler_refuses_stay_out_of_the_shared_header(tmp_path):
    """A header that doesn't compile would fail every attempt: the types go to a file in the project instead."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    said = []
    cfg = {"compiler": {"toolchain": "ee-gcc-2.96", "flags": ["-O2", "-G0"], "timeout": 20}, "gp": 0}
    broken = debuginfo.DebugInfo(header="struct Broken { int x }\n")
    assert net._debug_types(broken, cfg, tmp_path, said.append) == ""
    assert (tmp_path / "debug_types.h").read_text() == broken.header and "debug_types.h" in said[0]
    fine = debuginfo.DebugInfo(header="struct Fine { int x; };\n")
    assert "struct Fine { int x; };" in net._debug_types(fine, cfg, tmp_path, said.append)


CPP_SOURCE = """class Shape { public: Shape(); virtual ~Shape(); virtual int area() const; int m_sides; };
Shape::Shape() : m_sides(4) {}
Shape::~Shape() {}
int Shape::area() const { return m_sides; }
namespace Game { struct Square : public Shape { int side; unsigned char flags : 3; int area() const; };
int Square::area() const { return side * side; } }
typedef struct { short x, y; } Point;
Point g_origin;
"""


@pytest.mark.parametrize("toolchain", ["ee-gcc-2.96", "ee-gcc-3.2"])
def test_a_cpp_programs_types_compile_as_the_header_in_c_and_cpp(toolchain, tmp_path):
    """The header compiles as C (a function written in C) and as C++ (a class's method), from the STABS each GCC
    really writes for classes, a namespace, bitfields and a typedef'd anonymous struct."""
    if toolchains.installed(toolchain) is None:
        pytest.skip(f"needs {toolchain}")
    from decompnet.app.engine.common import BASE_HEADER
    from decompnet.compiler import compile_source, run_in
    from decompnet.project import compiler_config

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "shapes.cpp").write_text(CPP_SOURCE)
    cfg = compiler_config(tmp_path, {"compiler": {"toolchain": toolchain, "flags": ["-O2", "-g"]}})
    argv = [a.replace("{src}", "src/shapes.cpp").replace("{obj}", "shapes.o").replace("{include}", "src") for a in cfg.cmd]
    assert run_in(cfg, tmp_path, argv, inputs=["src"], outputs=["shapes.o"]).returncode == 0
    data = (tmp_path / "shapes.o").read_bytes()
    info = debuginfo.read(data, _HEADER_NAMES)
    assert "struct Square {" in info.header
    assert "    int side; /* 0x8 */" in info.header and "typedef struct { short x; short y; } Point;" in info.header
    base = BASE_HEADER.format(title="test") + info.header
    for source, language in (("int check;", "c"), ("struct Probe { int f(); }; int Probe::f() { return 0; }", "c++")):
        result = compile_source(compiler_config(tmp_path, {"compiler": {"toolchain": toolchain, "flags": ["-O2"]}}),
                                base, source)
        assert result.ok and result.language == language, result.log
