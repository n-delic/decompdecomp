"""CodeWarrior's DWARF 1 (.debug and .line) gives a game's source files, prototypes, locals and types, as STABS do
(consoles/ps2/dwarf1.py): its PS2 and PSP compilers write it with -g.

The reader is tested on small entries built here, laid out as CodeWarrior lays them out (children after their
parent, a padding entry after the last, AT_sibling past them); the import's use of it on a hand-made ELF from
tests/test_ps2_debug.py's helpers; and, when CodeWarrior for PS2 is installed, on what it really writes.
"""

import struct
import subprocess

import pytest

from decompnet import toolchains
from decompnet.app.engine.importing import _HEADER_NAMES
from decompnet.consoles.ps2 import debuginfo, dwarf1
from decompnet.consoles.ps2.cheader import Header

from .test_ps2_debug import (
    SHF_ALLOC,
    SHF_EXECINSTR,
    SHT_MIPS_DEBUG,
    SHT_PROGBITS,
    SHT_STRTAB,
    SHT_SYMTAB,
    STB_GLOBAL,
    STB_LOCAL,
    STT_FUNC,
    STT_OBJECT,
    _elf,
    _symbols,
)

FT_CHAR, FT_SHORT, FT_INT, FT_UNSIGNED, FT_FLOAT = 0x01, 0x04, 0x07, 0x09, 0x0E
AT_LANGUAGE, AT_PRODUCER, TAG_LEXICAL_BLOCK = 0x0136, 0x0258, 0x0B
REG_S0, REG_V0, REG_SP = 16, 2, 29


class Ref:
    """Another entry's offset, by its label, after these modifiers (an AT_mod_u_d_type when there are some)."""

    def __init__(self, label: str, *modifiers: int):
        self.label, self.modifiers = label, bytes(modifiers)


class Die:
    """An entry to lay out: its tag, its attributes as (name, value), its children."""

    def __init__(self, tag: int, *attributes, label: str = "", children: tuple = ()):
        self.tag, self.attributes, self.label, self.children = tag, list(attributes), label, list(children)
        self.sibling = 0


def _debug(dies: list[Die], order: str = "<") -> bytes:
    """A .debug section of these entries: each `length, tag, AT_sibling, attributes`, its children after it and a
    4-byte padding entry after them, AT_sibling past it all."""
    offsets: dict[str, int] = {}

    def value(name: int, v) -> bytes:
        form = name & dwarf1.FORM_MASK
        if isinstance(v, Ref):
            v = v.modifiers + struct.pack(order + "I", offsets.get(v.label, 0)) if v.modifiers else offsets.get(v.label, 0)
        elif isinstance(v, list):  # a block with references in it
            v = b"".join(part if isinstance(part, bytes) else struct.pack(order + "I", offsets.get(part.label, 0))
                         for part in v)
        if form in (dwarf1.FORM_ADDR, dwarf1.FORM_REF, dwarf1.FORM_DATA4):
            return struct.pack(order + "I", v)
        if form == dwarf1.FORM_DATA2:
            return struct.pack(order + "H", v)
        if form == dwarf1.FORM_BLOCK2:
            return struct.pack(order + "H", len(v)) + v
        if form == dwarf1.FORM_BLOCK4:
            return struct.pack(order + "I", len(v)) + v
        if form == dwarf1.FORM_STRING:
            return v.encode() + b"\0"
        return v  # a form the reader doesn't know: these bytes as they are

    def body(die: Die) -> bytes:
        return b"".join(struct.pack(order + "H", name) + value(name, v) for name, v in die.attributes)

    def place(chain: list[Die], at: int) -> int:
        for die in chain:
            if die.label:
                offsets[die.label] = at
            at += 12 + len(body(die))
            if die.children:
                at = place(die.children, at) + 4
            die.sibling = at
        return at

    def write(chain: list[Die], out: bytearray) -> None:
        for die in chain:
            data = body(die)
            out += struct.pack(order + "IHHI", 12 + len(data), die.tag, dwarf1.AT_SIBLING, die.sibling) + data
            if die.children:
                write(die.children, out)
                out += struct.pack(order + "I", 4)

    place(dies, 0)
    out = bytearray()
    write(dies, out)
    return bytes(out)


def _line(base: int, rows: list[tuple[int, int]], order: str = "<") -> bytes:
    """One compile unit's .line: (line, address from `base`) rows, then the line-0 end."""
    rows = rows + [(0, rows[-1][1] + 4)]
    entries = b"".join(struct.pack(order + "IHI", line, 0xFFFF, delta) for line, delta in rows)
    return struct.pack(order + "II", 8 + len(entries), base) + entries


def _loc(*ops: tuple[int, int], order: str = "<") -> bytes:
    return b"".join(bytes([op]) + (struct.pack(order + "i", operand) if op in dwarf1.OPERAND_OPS else b"")
                    for op, operand in ops)


def _type(t) -> tuple:
    """A type attribute: a fundamental type's number, or a Ref."""
    if isinstance(t, Ref):
        return (dwarf1.AT_MOD_U_D_TYPE if t.modifiers else dwarf1.AT_USER_DEF_TYPE, t)
    return (dwarf1.AT_FUND_TYPE, t)


def _program(order: str = "<") -> list[Die]:
    """A C file as CodeWarrior describes it (written for this test):

        typedef struct { short x, y; } Point;
        enum Kind { KIND_NONE, KIND_PLAYER = 3 };
        struct Actor { Point pos; enum Kind kind; unsigned flags : 3, alive : 1; struct Actor *next;
                       int (*think)(); int grid[2][2]; const char *name; };
        union Value { int i; float f; };
        struct Actor g_actors[4];
        static int s_ticks;
        int actor_tick(struct Actor *a, int dt, float rate) { int i; { static int calls; float scale; ... } }
        static void reset(void) { ... }
    """
    def u32(v: int) -> bytes:
        return struct.pack(order + "I", v)

    def u16(v: int) -> bytes:
        return struct.pack(order + "H", v)

    def member(name: str, offset: int, t, bits: tuple[int, int] | None = None) -> Die:
        extra = [(dwarf1.AT_BIT_OFFSET, bits[0]), (dwarf1.AT_BIT_SIZE, bits[1])] if bits else []
        return Die(dwarf1.TAG_MEMBER, (dwarf1.AT_NAME, name), _type(t),
                   (dwarf1.AT_LOCATION, _loc((dwarf1.OP_CONST, offset), (dwarf1.OP_ADD, 0), order=order)), *extra)

    def variable(tag: int, name: str, t, *ops) -> Die:
        return Die(tag, (dwarf1.AT_NAME, name), _type(t), (dwarf1.AT_LOCATION, _loc(*ops, order=order)))

    def dimension(high: int) -> bytes:  # FMT_FT_C_C: an int index, constant bounds
        return b"\0" + u16(FT_INT) + u32(0) + u32(high)

    types = [
        Die(dwarf1.TAG_STRUCT, (dwarf1.AT_NAME, "@anon0"), (dwarf1.AT_BYTE_SIZE, 4), label="anon",
            children=(member("x", 0, FT_SHORT), member("y", 2, FT_SHORT))),
        Die(dwarf1.TAG_TYPEDEF, (dwarf1.AT_NAME, "Point"), _type(Ref("anon")), label="Point"),
        Die(dwarf1.TAG_ENUM, (dwarf1.AT_NAME, "Kind"), (dwarf1.AT_BYTE_SIZE, 4),
            (dwarf1.AT_ELEMENT_LIST, u32(0) + b"KIND_NONE\0" + u32(3) + b"KIND_PLAYER\0"), label="Kind"),
        Die(dwarf1.TAG_FUNCTION_TYPE, (dwarf1.AT_FUND_TYPE, FT_INT), label="thinker"),
        Die(dwarf1.TAG_ARRAY, (dwarf1.AT_SUBSCR_DATA, dimension(1) + dimension(1) + bytes([dwarf1.FMT_ET])
                                + u16(dwarf1.AT_FUND_TYPE) + u16(FT_INT)), label="grid"),
        Die(dwarf1.TAG_STRUCT, (dwarf1.AT_NAME, "Actor"), (dwarf1.AT_BYTE_SIZE, 0x28), label="Actor", children=(
            member("pos", 0, Ref("Point")), member("kind", 4, Ref("Kind")),
            member("flags", 8, FT_UNSIGNED, bits=(0, 3)), member("alive", 8, FT_UNSIGNED, bits=(3, 1)),
            member("next", 0xC, Ref("Actor", dwarf1.MOD_POINTER)),
            member("think", 0x10, Ref("thinker", dwarf1.MOD_POINTER)), member("grid", 0x14, Ref("grid")),
            Die(dwarf1.TAG_MEMBER, (dwarf1.AT_NAME, "name"),
                (dwarf1.AT_MOD_FUND_TYPE, bytes([dwarf1.MOD_POINTER, dwarf1.MOD_CONST]) + u16(FT_CHAR)),
                (dwarf1.AT_LOCATION, _loc((dwarf1.OP_CONST, 0x24), (dwarf1.OP_ADD, 0), order=order))))),
        Die(dwarf1.TAG_UNION, (dwarf1.AT_NAME, "Value"), (dwarf1.AT_BYTE_SIZE, 4),
            children=(member("i", 0, FT_INT), member("f", 0, FT_FLOAT))),
        Die(dwarf1.TAG_ARRAY, (dwarf1.AT_SUBSCR_DATA, [dimension(3) + bytes([dwarf1.FMT_ET])
                                + u16(dwarf1.AT_USER_DEF_TYPE), Ref("Actor")]), label="actors"),
    ]
    tick = Die(dwarf1.TAG_GLOBAL_FUNCTION, (dwarf1.AT_NAME, "actor_tick"), (dwarf1.AT_FUND_TYPE, FT_INT),
               (dwarf1.AT_LOW_PC, 0x100000), (dwarf1.AT_HIGH_PC, 0x100040),
               (0x2013, _loc((dwarf1.OP_REG, REG_SP), order=order)),  # CodeWarrior's own: where it saves $ra
               children=(
                   variable(dwarf1.TAG_PARAMETER, "a", Ref("Actor", dwarf1.MOD_POINTER), (dwarf1.OP_REG, REG_S0)),
                   variable(dwarf1.TAG_PARAMETER, "dt", FT_INT, (dwarf1.OP_REG, REG_V0)),  # kept nowhere
                   variable(dwarf1.TAG_PARAMETER, "rate", FT_FLOAT, (dwarf1.OP_MW_FPREG, 20)),
                   variable(dwarf1.TAG_LOCAL_VARIABLE, "i", FT_INT, (dwarf1.OP_BASEREG, REG_SP),
                            (dwarf1.OP_CONST, 0x10), (dwarf1.OP_ADD, 0)),
                   Die(TAG_LEXICAL_BLOCK, (dwarf1.AT_LOW_PC, 0x100010), (dwarf1.AT_HIGH_PC, 0x100020), children=(
                       variable(dwarf1.TAG_LOCAL_VARIABLE, "calls", FT_INT, (dwarf1.OP_ADDR, 0x100208)),
                       variable(dwarf1.TAG_LOCAL_VARIABLE, "scale", FT_FLOAT, (dwarf1.OP_MW_FPREG, 21)))),
               ))
    reset = Die(dwarf1.TAG_FUNCTION, (dwarf1.AT_NAME, "reset"), (dwarf1.AT_LOW_PC, 0x100040),
                (dwarf1.AT_HIGH_PC, 0x100048))
    variables = [variable(dwarf1.TAG_GLOBAL_VARIABLE, "g_actors", Ref("actors"), (dwarf1.OP_ADDR, 0x100100)),
                 variable(dwarf1.TAG_LOCAL_VARIABLE, "s_ticks", FT_INT, (dwarf1.OP_ADDR, 0x100200))]
    return [Die(dwarf1.TAG_COMPILE_UNIT, (dwarf1.AT_NAME, "D:\\dev\\game\\src\\actor.c"), (AT_LANGUAGE, 2),
                (AT_PRODUCER, "MW MIPS C Compiler"), (dwarf1.AT_STMT_LIST, 0), (dwarf1.AT_LOW_PC, 0x100000),
                (dwarf1.AT_HIGH_PC, 0x100048), children=(*types, tick, reset, *variables))]


def _sections(order: str = "<") -> tuple[bytes, bytes]:
    """The test program's .debug and .line."""
    return _debug(_program(order), order), _line(0x100000, [(12, 0), (13, 0x10), (20, 0x40)], order)


@pytest.mark.parametrize("order", ["<", ">"])
def test_dwarf1_gives_a_c_programs_types_with_their_offsets(order):
    """Either byte order: CodeWarrior's GameCube compiler writes the same, big-endian."""
    debug, line = _sections(order)
    source, = dwarf1.read(debug, line, little=order == "<")
    assert source.name == "D:\\dev\\game\\src\\actor.c" and source.skipped == 0
    text = Header([source], _HEADER_NAMES).text()
    assert "struct Point { /* 0x4 bytes */\n    short x; /* 0x0 */\n    short y; /* 0x2 */\n};" in text
    assert "typedef struct Point Point;" in text  # a typedef of an anonymous struct (CodeWarrior's @anon0)
    assert "enum Kind {\n    KIND_NONE = 0,\n    KIND_PLAYER = 3,\n};" in text
    assert ("struct Actor { /* 0x28 bytes */\n    Point pos; /* 0x0 */\n    enum Kind kind; /* 0x4 */\n"
            "    unsigned int flags : 3; /* 0x8 bit 0 */\n    unsigned int alive : 1; /* 0x8 bit 3 */\n"
            "    struct Actor *next; /* 0xC */\n    int (*think)(); /* 0x10 */\n    int grid[2][2]; /* 0x14 */\n"
            "    const char *name; /* 0x24 */\n};") in text
    assert "union Value { /* 0x4 bytes */\n    int i; /* 0x0 */\n    float f; /* 0x0 */\n};" in text
    assert "extern struct Actor g_actors[4];" in Header([source]).text([("g_actors", source.globals[0].type, True)])


def test_dwarf1_gives_prototypes_locals_statics_and_lines():
    debug, line = _sections()
    source, = dwarf1.read(debug, line)
    header = Header([source], _HEADER_NAMES)
    tick, reset = source.functions
    assert (tick.address, tick.size, tick.is_static, tick.line) == (0x100000, 0x40, False, 12)
    assert (reset.address, reset.size, reset.is_static, reset.line) == (0x100040, 8, True, 20)
    assert header.prototype(tick, "actor_tick") == "int actor_tick(struct Actor *a, int dt, float rate)"
    assert header.prototype(reset, "reset") == "void reset(void)"
    # $v0 is where CodeWarrior's MIPS compiler puts a variable it kept nowhere: the brief says nothing of it
    assert [(p.name, p.where) for p in tick.params] == [("a", "$s0"), ("dt", ""), ("rate", "$f20")]
    assert [(header.variable(v), v.where, v.address) for v in tick.locals] == [
        ("int i", "the stack at $sp+0x10", None), ("int calls", "static", 0x100208), ("float scale", "$f21", None)]
    assert [(v.name, v.address) for v in source.globals] == [("g_actors", 0x100100)]
    assert [(v.name, v.address) for v in source.statics] == [("s_ticks", 0x100200)]
    assert dwarf1.line_table(line, 0) == [(0x100000, 12), (0x100010, 13), (0x100040, 20)]


def test_dwarf1_entries_nest_by_sibling_past_padding_and_attributes_it_doesnt_know():
    """CodeWarrior's own attributes (0x2000 on) are passed over by their form; one of a form no DWARF 1 has ends
    its entry's attributes, and the entry's length still finds the next one; a vendor tag is kept, unread."""
    unknown_form = 0x2009  # form 9: none
    dies = [Die(dwarf1.TAG_COMPILE_UNIT, (dwarf1.AT_NAME, "a.c"), children=(
        Die(dwarf1.TAG_GLOBAL_VARIABLE, (dwarf1.AT_NAME, "first"), (0x2303, b"\1\2\3\4"), (dwarf1.AT_FUND_TYPE, FT_INT)),
        Die(dwarf1.TAG_GLOBAL_VARIABLE, (dwarf1.AT_NAME, "second"), (unknown_form, b"\xff\xff"), (dwarf1.AT_FUND_TYPE, FT_INT)),
        Die(0x8001, (dwarf1.AT_NAME, "vendor")),
        Die(dwarf1.TAG_GLOBAL_VARIABLE, (dwarf1.AT_NAME, "third"), (dwarf1.AT_FUND_TYPE, FT_CHAR)))),
        Die(dwarf1.TAG_COMPILE_UNIT, (dwarf1.AT_NAME, "b.c"))]
    debug = _debug(dies) + bytes(4)  # padding at the end too
    top = dwarf1.entries(debug)
    assert [e.attributes[dwarf1.AT_NAME] for e in top] == ["a.c", "b.c"]
    first, second, vendor, third = top[0].children
    assert first.attributes[dwarf1.AT_FUND_TYPE] == FT_INT and dwarf1.AT_FUND_TYPE not in second.attributes
    assert vendor.tag == 0x8001 and third.attributes[dwarf1.AT_FUND_TYPE] == FT_CHAR
    a, b = dwarf1.read(debug)
    assert [(v.name, v.type.name) for v in a.globals] == [("first", "int"), ("second", "void"), ("third", "char")]


@pytest.mark.parametrize("damage", ["truncated", "garbage", "loop"])
def test_broken_dwarf1_never_stops_a_read(damage):
    debug, line = _sections()
    if damage == "truncated":
        debug = debug[:len(debug) // 2 + 3]
    elif damage == "garbage":
        debug = bytes(range(256)) * 8
    else:  # a type that refers to itself
        debug = _debug([Die(dwarf1.TAG_COMPILE_UNIT, (dwarf1.AT_NAME, "a.c"), children=(
            Die(dwarf1.TAG_TYPEDEF, (dwarf1.AT_NAME, "Loop"), _type(Ref("loop")), label="loop"),
            Die(dwarf1.TAG_GLOBAL_VARIABLE, (dwarf1.AT_NAME, "x"), _type(Ref("loop")))))])
    sources = dwarf1.read(debug, line)
    Header(sources).text([(v.name, v.type, True) for s in sources for v in s.globals])


def _program_elf(debug: bytes, line: bytes) -> bytes:
    table, strings = _symbols([("actor_tick", 0x100000, 0x40, STB_GLOBAL, STT_FUNC, 1),
                               ("reset", 0x100040, 8, STB_LOCAL, STT_FUNC, 1),
                               ("g_actors", 0x100100, 0xA0, STB_GLOBAL, STT_OBJECT, 2)])
    return _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0x100000, bytes(0x100), 0),
                 (".data", SHT_PROGBITS, SHF_ALLOC, 0x100100, bytes(0x200), 0),
                 (".symtab", SHT_SYMTAB, 0, 0, table, 4), (".strtab", SHT_STRTAB, 0, 0, strings, 0),
                 (".debug", SHT_MIPS_DEBUG, 0, 0, debug, 0), (".line", SHT_MIPS_DEBUG, 0, 0, line, 0)])


def test_a_program_with_dwarf1_imports_with_its_units_prototypes_and_types():
    info = debuginfo.read(_program_elf(*_sections()), _HEADER_NAMES)
    assert info.summary() == "debug symbols: 2 functions named, 5 types, 1 source file"
    assert info.sources == ["actor.c"] and info.units == {0x100000: "actor.c", 0x100040: "actor.c"}
    assert "struct Actor { /* 0x28 bytes */" in info.header and "extern struct Actor g_actors[4];" in info.header
    tick = info.functions[0x100000]
    assert tick == {"source": "actor.c", "prototype": "int actor_tick(struct Actor *a, int dt, float rate)",
                    "params": ["a in $s0", "rate in $f20"], "line": 12,
                    "locals": ["int i (the stack at $sp+0x10)", "int calls (static)", "float scale ($f21)"]}
    assert debuginfo.brief_lines(tick)[2] == "source file actor.c, from line 12; its types are in context.h already"


def test_dwarf_2_is_recognised_and_said_to_be_unread():
    """Later GCCs write DWARF 2 (.debug_info): not read yet, and the import log says so."""
    table, strings = _symbols([("main", 0x100000, 8, STB_GLOBAL, STT_FUNC, 1)])
    data = _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0x100000, bytes(8), 0),
                 (".debug_info", SHT_PROGBITS, 0, 0, bytes(8), 0), (".symtab", SHT_SYMTAB, 0, 0, table, 4),
                 (".strtab", SHT_STRTAB, 0, 0, strings, 0)])
    assert debuginfo.read(data).summary() == "debug symbols: 1 function named, DWARF 2 or later (.debug_info) not read yet"


# ---------------------------------------------------------------------------------------- with the compiler

CW_SOURCE = """typedef struct { short x, y; } Point;
enum Kind { KIND_NONE, KIND_PLAYER = 3 };
struct Actor { Point pos; enum Kind kind; unsigned flags : 3, alive : 1; struct Actor *next; int grid[2][2]; };
struct Actor g_actors[4];
int actor_tick(struct Actor *a, int dt) { int i, sum = 0; for (i = 0; i < dt; i++) sum += a->grid[i & 1][1]; return sum; }
"""


def test_what_codewarrior_for_ps2_writes_is_read(tmp_path):
    """CodeWarrior for PS2's own DWARF 1, from -g, in a program linked as a game is: its unit, the prototype, and
    the types, with the bitfields where CodeWarrior puts them (it writes no typedefs: Point comes inline)."""
    needs = ("mwcps2-3.0.1", "wibo", "binutils-mips")
    if any(toolchains.installed(tid) is None for tid in needs):
        pytest.skip("needs " + ", ".join(needs))
    from decompnet.compiler import run_in
    from decompnet.project import compiler_config

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "actor.c").write_text(CW_SOURCE)
    cfg = compiler_config(tmp_path, {"compiler": {"toolchain": "mwcps2-3.0.1", "flags": ["-O2", "-g"]}})
    argv = [a.replace("{src}", "src/actor.c").replace("{obj}", "actor.o").replace("{include}", "src") for a in cfg.cmd]
    result = run_in(cfg, tmp_path, argv, inputs=["src"], outputs=["actor.o"])
    assert result.returncode == 0, result.stdout + result.stderr
    # an object keeps a .debug per function, joined only by the linker
    ld = str(toolchains.binary("binutils-mips").parent / "mips-ps2-decompals-ld")
    subprocess.run([ld, "-EL", "-e", "actor_tick", "-Ttext", "0x100000", "-o", "actor.elf", "actor.o"],
                   cwd=tmp_path, check=True, timeout=60)
    info = debuginfo.read((tmp_path / "actor.elf").read_bytes(), _HEADER_NAMES)
    assert info.sources == ["actor.c"] and info.units == {0x100000: "actor.c"}
    assert info.functions[0x100000]["prototype"] == "int actor_tick(struct Actor *a, int dt)"
    assert info.functions[0x100000]["line"] == 5
    assert ("struct Actor { /* 0x20 bytes */\n    struct { short x; short y; } pos; /* 0x0 */\n"
            "    enum Kind kind; /* 0x4 */\n    unsigned int flags : 3; /* 0x8 bit 0 */\n"
            "    unsigned int alive : 1; /* 0x8 bit 3 */\n    struct Actor *next; /* 0xC */\n"
            "    int grid[2][2]; /* 0x10 */\n};") in info.header
    assert "extern struct Actor g_actors[4];" in info.header
