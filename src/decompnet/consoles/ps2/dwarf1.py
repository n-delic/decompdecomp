"""DWARF 1: how Metrowerks CodeWarrior describes a program's types, functions and variables in its debug information.
Its MIPS compiler (CodeWarrior for PS2, and for PSP: "MW MIPS C Compiler") writes it with -g, in a .debug section
and a .line section; the format is Unix International's ("DWARF Debugging Information Format", 1.1.0, 1993; the
numbers are those of binutils' include/elf/dwarf.h).

.debug is a list of entries, each a 4-byte length (counting itself), a 2-byte tag saying what it is (a compile unit,
a function, a struct...), then attributes: a 2-byte name whose low 4 bits say how its value is written (the form),
then the value. An entry shorter than 8 bytes is padding. Entries nest by AT_sibling, which points past an entry's
children: a compile unit holds its functions and types, a function its parameters and locals, a struct its members.
Types refer to each other by the offset of their entry; a type attribute is a fundamental type (FT_int), or another
entry, with modifiers in front (pointer to, const).

What comes out is what stabs.py makes of STABS, so the shared header (cheader.py) and the briefs work unchanged: per
compile unit its named types, its functions with their parameters and locals, and its global and static variables.
CodeWarrior's own attributes (from 0x2000: where each saved register is) and tags are passed over by their form, an
entry this can't read is skipped by its length, and a type it doesn't know is "unknown" (written as its bytes).

.line gives each compile unit's line numbers: a 4-byte length, the unit's first address, then entries of a line
(4 bytes), a column (2) and an address from the first (4); line 0 ends the list. It gives each function the line it
starts on.

The addresses are what the program's bytes hold: a relocatable program (a PSP PRX, an object) has its relocations
applied first, by its console.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .stabs import BITS_PER_BYTE, POINTER_BYTES, Field, Function, SourceInfo, Type, Variable, register

# tags
TAG_ARRAY, TAG_CLASS, TAG_ENUM, TAG_PARAMETER, TAG_GLOBAL_FUNCTION, TAG_GLOBAL_VARIABLE = 0x01, 0x02, 0x04, 0x05, 0x06, 0x07
TAG_LOCAL_VARIABLE, TAG_MEMBER, TAG_COMPILE_UNIT, TAG_STRUCT, TAG_FUNCTION = 0x0C, 0x0D, 0x11, 0x13, 0x14
TAG_FUNCTION_TYPE, TAG_TYPEDEF, TAG_UNION, TAG_INHERITANCE = 0x15, 0x16, 0x17, 0x1C
# forms: the low 4 bits of an attribute's name
FORM_ADDR, FORM_REF, FORM_BLOCK2, FORM_BLOCK4, FORM_DATA2, FORM_DATA4, FORM_DATA8, FORM_STRING = range(1, 9)
FORM_MASK = 0xF
# attributes (their form included)
AT_SIBLING, AT_LOCATION, AT_NAME, AT_FUND_TYPE, AT_MOD_FUND_TYPE = 0x0012, 0x0023, 0x0038, 0x0055, 0x0063
AT_USER_DEF_TYPE, AT_MOD_U_D_TYPE, AT_SUBSCR_DATA, AT_BYTE_SIZE = 0x0072, 0x0083, 0x00A3, 0x00B6
AT_BIT_OFFSET, AT_BIT_SIZE, AT_ELEMENT_LIST, AT_STMT_LIST, AT_LOW_PC, AT_HIGH_PC = 0x00C5, 0x00D6, 0x00F4, 0x0106, 0x0111, 0x0121
TYPE_ATTRIBUTES = (AT_FUND_TYPE, AT_MOD_FUND_TYPE, AT_USER_DEF_TYPE, AT_MOD_U_D_TYPE)
# location operations: the four with a 4-byte operand, then those without; CodeWarrior's 0x80 (where GNU has its
# OP_MULT) is a floating-point register, with a 4-byte operand too
OP_REG, OP_BASEREG, OP_ADDR, OP_CONST, OP_DEREF2, OP_DEREF4, OP_ADD = range(1, 8)
OP_MW_FPREG = 0x80
OPERAND_OPS = (OP_REG, OP_BASEREG, OP_ADDR, OP_CONST, OP_MW_FPREG)
# registers CodeWarrior's MIPS compiler names for a variable it kept in none: 2 ($v0, where no parameter arrives: a
# PSP game with debug symbols has all three parameters of one function there) and 0xFFFF
NOT_KEPT = (2, 0xFFFF)
MOD_POINTER, MOD_REFERENCE, MOD_CONST, MOD_VOLATILE = 1, 2, 3, 4
# AT_subscr_data: a format byte per dimension (bit 2: index type an entry, not fundamental; bit 1: low bound a
# location, not a constant; bit 0: the high bound), then FMT_ET and the element type as an attribute
FMT_USER_TYPE, FMT_LOW_LOCATION, FMT_HIGH_LOCATION, FMT_ET = 4, 2, 1, 8
NULL_ENTRY_BYTES = 8  # an entry shorter than this is padding
LINE_HEADER_BYTES, LINE_ENTRY_BYTES = 8, 10
SPAN_BYTES = 4
ANONYMOUS = "@"  # CodeWarrior's names for what has none: @anon7, @enum
# fundamental types: kind, the name GCC gives it (what cheader.C_BASE_NAMES spells), bytes, signed
FUNDAMENTAL = {
    0x01: ("base", "char", 1, True), 0x02: ("base", "signed char", 1, True), 0x03: ("base", "unsigned char", 1, False),
    0x04: ("base", "short int", 2, True), 0x05: ("base", "short int", 2, True),
    0x06: ("base", "short unsigned int", 2, False), 0x07: ("base", "int", 4, True), 0x08: ("base", "int", 4, True),
    0x09: ("base", "unsigned int", 4, False), 0x0A: ("base", "long int", 4, True), 0x0B: ("base", "long int", 4, True),
    0x0C: ("base", "long unsigned int", 4, False), 0x0E: ("float", "float", 4, True),
    0x0F: ("float", "double", 8, True), 0x10: ("float", "long double", 8, True), 0x14: ("void", "void", 0, True),
    0x15: ("base", "bool", 1, False), 0x8008: ("base", "long long int", 8, True),
    0x8108: ("base", "long long int", 8, True), 0x8208: ("base", "long long unsigned int", 8, False),
}
TYPE_TAGS = (TAG_STRUCT, TAG_CLASS, TAG_UNION, TAG_ENUM, TAG_TYPEDEF)
FT_POINTER, FT_VOID = 0x0D, 0x14  # FT_pointer is void *
FT_SIZE_MASK = 0xFF  # GNU's fundamental types from 0x8000 say their size in their low byte


@dataclass(eq=False)
class Entry:
    offset: int
    tag: int
    attributes: dict[int, object]
    children: list[Entry] = field(default_factory=list)

    @property
    def name(self) -> str:
        name = self.attributes.get(AT_NAME, "")
        return "" if not isinstance(name, str) or name.startswith(ANONYMOUS) else name


def entries(data: bytes, little: bool = True) -> list[Entry]:
    """The top-level entries of .debug (compile units), each with the entries it holds."""
    order = "<" if little else ">"
    top: list[Entry] = []
    open_: list[tuple[int, Entry]] = []  # entries whose children come next, with where their children end
    at = 0
    while at + NULL_ENTRY_BYTES <= len(data):
        length, = struct.unpack_from(order + "I", data, at)
        if length < NULL_ENTRY_BYTES:
            at += max(length, SPAN_BYTES)
            continue
        end = min(at + length, len(data))
        entry = Entry(at, struct.unpack_from(order + "H", data, at + SPAN_BYTES)[0], _attributes(data, at + 6, end, order))
        while open_ and open_[-1][0] <= at:
            open_.pop()
        (open_[-1][1].children if open_ else top).append(entry)
        sibling = entry.attributes.get(AT_SIBLING)
        if isinstance(sibling, int) and sibling > end and (not open_ or sibling <= open_[-1][0]):
            open_.append((sibling, entry))
        at = end
    return top


def _attributes(data: bytes, at: int, end: int, order: str) -> dict[int, object]:
    """An entry's attributes; one of a form this doesn't know ends them (the entry's length still says where the
    next entry is)."""
    found: dict[int, object] = {}
    fixed = {FORM_ADDR: "I", FORM_REF: "I", FORM_DATA2: "H", FORM_DATA4: "I", FORM_DATA8: "Q"}
    while at + 2 <= end:
        name, = struct.unpack_from(order + "H", data, at)
        at += 2
        form = name & FORM_MASK
        if form in fixed:
            size = struct.calcsize(fixed[form])
            if at + size > end:
                break
            value, = struct.unpack_from(order + fixed[form], data, at)
            at += size
        elif form in (FORM_BLOCK2, FORM_BLOCK4):
            span = 2 if form == FORM_BLOCK2 else 4
            if at + span > end:
                break
            size, = struct.unpack_from(order + ("H" if span == 2 else "I"), data, at)
            value, at = data[at + span:at + span + size], at + span + size
            if at > end:
                break
        elif form == FORM_STRING:
            stop = data.find(b"\0", at, end)
            if stop < 0:
                break
            value, at = data[at:stop].decode("latin-1"), stop + 1
        else:
            break
        found.setdefault(name, value)
    return found


def line_table(data: bytes, offset: int, little: bool = True) -> list[tuple[int, int]]:
    """One compile unit's (address, line) pairs from .line, from its AT_stmt_list offset."""
    order = "<" if little else ">"
    if offset + LINE_HEADER_BYTES > len(data):
        return []
    length, base = struct.unpack_from(order + "II", data, offset)
    end = min(offset + length, len(data))
    out = []
    for at in range(offset + LINE_HEADER_BYTES, end - LINE_ENTRY_BYTES + 1, LINE_ENTRY_BYTES):
        line, _column, delta = struct.unpack_from(order + "IHI", data, at)
        if line == 0:
            break
        out.append((base + delta, line))
    return out


def _location(block: bytes, order: str) -> list[tuple[int, int]]:
    """A location's operations, (operation, operand)."""
    ops, at = [], 0
    while at < len(block):
        op = block[at]
        at += 1
        operand = 0
        if op in OPERAND_OPS:
            if at + 4 > len(block):
                break
            operand, = struct.unpack_from(order + ("i" if op == OP_CONST else "I"), block, at)
            at += 4
        ops.append((op, operand))
    return ops


class _Reader:
    """Turns one .debug's entries into stabs' shapes; types are shared by every compile unit (they refer to each
    other by offset, so one unit's struct can be another's)."""

    def __init__(self, top: list[Entry], little: bool):
        self.order, self.little = "<" if little else ">", little
        self.by_offset: dict[int, Entry] = {}
        stack = list(top)
        while stack:
            entry = stack.pop()
            self.by_offset[entry.offset] = entry
            stack += entry.children
        self.types: dict[int, Type] = {}
        self.fundamentals: dict[int, Type] = {}

    # ------------------------------------------------------------------ types

    def fundamental(self, code: int) -> Type:
        if code not in self.fundamentals:
            if code == FT_POINTER:
                t = Type(kind="pointer", target=self.fundamental(FT_VOID), size=POINTER_BYTES)
            elif code in FUNDAMENTAL:
                kind, name, size, signed = FUNDAMENTAL[code]
                t = Type(kind=kind, name=name, size=size, signed=signed)
            else:  # another vendor's: GNU's say their size in the low byte
                t = Type(kind="base", size=code & FT_SIZE_MASK if code >= 0x8000 else 4)
            self.fundamentals[code] = t
        return self.fundamentals[code]

    def type_of(self, entry: Entry) -> Type:
        """The type an entry's type attribute says (void when it has none: a function returning nothing)."""
        a = entry.attributes
        if isinstance(a.get(AT_FUND_TYPE), int):
            return self.fundamental(a[AT_FUND_TYPE])
        if isinstance(a.get(AT_USER_DEF_TYPE), int):
            return self.defined(a[AT_USER_DEF_TYPE])
        block = a.get(AT_MOD_FUND_TYPE)
        if isinstance(block, bytes) and len(block) >= 2:
            return self._modified(block[:-2], self.fundamental(struct.unpack(self.order + "H", block[-2:])[0]))
        block = a.get(AT_MOD_U_D_TYPE)
        if isinstance(block, bytes) and len(block) >= 4:
            return self._modified(block[:-4], self.defined(struct.unpack(self.order + "I", block[-4:])[0]))
        return self.fundamental(FT_VOID)

    def _modified(self, modifiers: bytes, t: Type) -> Type:
        """The first modifier applies last: pointer to, const, char is a pointer to const char."""
        for modifier in reversed(modifiers):
            if modifier in (MOD_POINTER, MOD_REFERENCE):
                t = Type(kind="pointer", target=t, size=POINTER_BYTES)
            elif modifier in (MOD_CONST, MOD_VOLATILE):
                t = Type(kind="const" if modifier == MOD_CONST else "volatile", target=t)
        return t

    def defined(self, offset: int) -> Type:
        """The type an entry defines, made once (a struct with a pointer to itself finds itself being made)."""
        if offset in self.types:
            return self.types[offset]
        t = self.types[offset] = Type()
        entry = self.by_offset.get(offset)
        if entry is not None:
            try:
                self._define(entry, t)
            except (struct.error, RecursionError):  # broken debug information: what it refers to stays unknown
                pass
        return t

    def _define(self, entry: Entry, t: Type) -> None:
        tag, size = entry.tag, entry.attributes.get(AT_BYTE_SIZE, 0)
        if tag in (TAG_STRUCT, TAG_CLASS, TAG_UNION):
            t.kind, t.tag, t.size = ("union" if tag == TAG_UNION else "struct"), entry.name, size
            for child in entry.children:
                if child.tag == TAG_MEMBER:
                    t.fields.append(self._member(child))
                elif child.tag == TAG_INHERITANCE:
                    t.bases.append((self._offset(child) * BITS_PER_BYTE, self.type_of(child)))
        elif tag == TAG_ENUM:
            t.kind, t.tag, t.size = "enum", entry.name, size or 4
            t.enumerators = self._enumerators(entry.attributes.get(AT_ELEMENT_LIST, b""))
        elif tag == TAG_TYPEDEF:
            t.kind, t.typedef, t.target = "alias", entry.name, self.type_of(entry)
        elif tag == TAG_FUNCTION_TYPE:
            t.kind, t.target = "function", self.type_of(entry)
        elif tag == TAG_ARRAY:
            self._array(entry.attributes.get(AT_SUBSCR_DATA, b""), t)

    def _offset(self, entry: Entry) -> int:
        """A member's offset: its location is `OP_CONST offset, OP_ADD` (added to the struct's address)."""
        ops = _location(entry.attributes.get(AT_LOCATION, b""), self.order)
        return next((operand for op, operand in ops if op == OP_CONST), 0)

    def _member(self, entry: Entry) -> Field:
        """A member as stabs has it: offset and size in bits (0 when it isn't a bitfield). CodeWarrior counts a
        bitfield's AT_bit_offset in the order it gives out bits, from the low bit on a little-endian processor (as
        GCC's STABS do), where the spec counts from the most significant: a PSP game with debug symbols numbers a
        struct's 1-bit flags 0, 1, 2... in the order they're declared."""
        bit_offset = self._offset(entry) * BITS_PER_BYTE
        bit_size = entry.attributes.get(AT_BIT_SIZE, 0)
        if bit_size:
            bit_offset += entry.attributes.get(AT_BIT_OFFSET, 0)
        return Field(entry.name, self.type_of(entry), bit_offset, bit_size)

    def _enumerators(self, block: bytes) -> list[tuple[str, int]]:
        """AT_element_list: each a 4-byte value then its name."""
        out, at = [], 0
        while at + 4 < len(block):
            value, = struct.unpack_from(self.order + "i", block, at)
            stop = block.find(b"\0", at + 4)
            if stop < 0:
                break
            out.append((block[at + 4:stop].decode("latin-1"), value))
            at = stop + 1
        return out

    def _array(self, block: bytes, t: Type) -> None:
        """AT_subscr_data: each dimension's index type and bounds, then the element type."""
        counts, at, element = [], 0, None
        while at < len(block):
            fmt = block[at]
            at += 1
            if fmt == FMT_ET:
                name, = struct.unpack_from(self.order + "H", block, at)
                found = _attributes(block, at, len(block), self.order)
                element = self.type_of(Entry(0, 0, found)) if name in TYPE_ATTRIBUTES else None
                break
            at += 4 if fmt & FMT_USER_TYPE else 2
            bounds = []
            for location in (fmt & FMT_LOW_LOCATION, fmt & FMT_HIGH_LOCATION):
                if location:  # a bound worked out at run time
                    size, = struct.unpack_from(self.order + "H", block, at)
                    at += 2 + size
                    bounds.append(None)
                else:
                    bounds.append(struct.unpack_from(self.order + "i", block, at)[0])
                    at += 4
            low, high = bounds
            counts.append(max(0, high - low + 1) if low is not None and high is not None else 0)
        if element is None or not counts:
            return
        for count in reversed(counts[1:]):
            element = Type(kind="array", count=count, target=element)
        t.kind, t.count, t.target = "array", counts[0], element

    # ------------------------------------------------------------------ variables and functions

    def where(self, entry: Entry) -> tuple[str, int | None]:
        """Where a parameter or variable lives ("" when the debug information doesn't say), and a static's
        address."""
        ops = _location(entry.attributes.get(AT_LOCATION, b""), self.order)
        kinds = [op for op, _ in ops]
        if ops and all(op in (OP_REG, OP_MW_FPREG) for op in kinds):  # two registers: a 64-bit value
            if any(op == OP_REG and number in NOT_KEPT for op, number in ops):
                return "", None
            return " and ".join(register(n) if op == OP_REG else f"$f{n}" for op, n in ops), None
        if kinds == [OP_ADDR]:
            return "static", ops[0][1]
        if kinds in ([OP_BASEREG], [OP_BASEREG, OP_CONST, OP_ADD]):
            return f"the stack at {register(ops[0][1])}{ops[-2][1] if len(ops) > 1 else 0:+#x}", None
        return "", None

    def variable(self, entry: Entry) -> Variable:
        where, address = self.where(entry)
        return Variable(entry.name, self.type_of(entry), where, address)

    def function(self, entry: Entry, lines: list[tuple[int, int]]) -> Function:
        low, high = entry.attributes[AT_LOW_PC], entry.attributes.get(AT_HIGH_PC, 0)
        function = Function(entry.attributes.get(AT_NAME, ""), low, self.type_of(entry),
                            is_static=entry.tag == TAG_FUNCTION, size=max(0, high - low))
        function.line = next((line for address, line in lines if low <= address < max(high, low + 1)), 0)
        stack = list(reversed(entry.children))
        while stack:  # the locals of its blocks too
            child = stack.pop()
            if child.tag == TAG_PARAMETER and child.name:
                function.params.append(self.variable(child))
            elif child.tag == TAG_LOCAL_VARIABLE and child.name:
                function.locals.append(self.variable(child))
            elif child.tag not in (TAG_PARAMETER, TAG_LOCAL_VARIABLE) and child.tag not in TYPE_TAGS:
                stack += reversed(child.children)
        return function

    def unit(self, entry: Entry, lines: bytes) -> SourceInfo:
        info = SourceInfo(entry.attributes.get(AT_NAME, "") or f"unit_{entry.offset:X}")
        stmt = entry.attributes.get(AT_STMT_LIST)
        table = line_table(lines, stmt, self.little) if isinstance(stmt, int) and lines else []
        stack = [(child, False) for child in reversed(entry.children)]
        while stack:
            child, in_function = stack.pop()
            try:
                if child.tag in TYPE_TAGS:
                    info.types.append(self.defined(child.offset))
                elif child.tag in (TAG_GLOBAL_FUNCTION, TAG_FUNCTION) and isinstance(child.attributes.get(AT_LOW_PC), int):
                    info.functions.append(self.function(child, table))
                elif child.tag in (TAG_GLOBAL_VARIABLE, TAG_LOCAL_VARIABLE) and child.name and not in_function:
                    (info.globals if child.tag == TAG_GLOBAL_VARIABLE else info.statics).append(self.variable(child))
            except (struct.error, KeyError, RecursionError):
                info.skipped += 1
            if child.tag not in TYPE_TAGS:  # the types a function or a block in it defines
                stack += [(c, True) for c in reversed(child.children)]
        return info


def read(debug: bytes, line: bytes = b"", little: bool = True) -> list[SourceInfo]:
    """What a program's .debug (and .line) say, one SourceInfo per compile unit, named by its source path."""
    top = entries(debug, little)
    reader = _Reader(top, little)
    return [reader.unit(entry, line) for entry in top if entry.tag == TAG_COMPILE_UNIT]
