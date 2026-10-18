"""STABS: how GCC describes a program's types, functions and variables in its debug information. EE-GCC and SN
Systems' ProDG write it inside the .mdebug section (mdebug.py) of a PS2 program built with -g.

Each entry is a string like `Entity:T30=s28type:23,0,16;...;`: a name, a letter saying what it is (T a struct tag,
t a typedef, F a function, G a global, p a parameter...), then a type. Types are numbered within each source file;
`30=s28...` defines type 30 as a 28-byte struct, a later `*30` is a pointer to it. The format is GNU's ("The
STABS debug format"); this reads what GCC 2.9x and 3.x write for C and C++, and skips an entry it can't read
rather than guess.

What comes out, per source file: its named types (Type), its functions with their parameters and locals
(Function), and its global and static variables (Variable). cheader.py writes the types as C.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# stab codes (the "type" field of a stab; mdebug.py finds them in the ECOFF symbols)
N_GSYM, N_FUN, N_STSYM, N_LCSYM, N_RSYM, N_LSYM, N_PSYM = 0x20, 0x24, 0x26, 0x28, 0x40, 0x80, 0xA0

POINTER_BYTES = 4
ENUM_BYTES = 4
BITS_PER_BYTE = 8
INTEGER_BYTES = (1, 2, 4, 8, 16)
UNSIGNED_INT_SPAN = (1 << 32) - 1
# GCC's register numbers in STABS: 0-31 the general registers, from 38 the floating-point ones (FP_DBX_FIRST)
GPR_COUNT, FPR_FIRST, FPR_COUNT = 32, 38, 32
GPR_NAMES = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
             "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]
# Sun's negative type numbers, which GCC uses for a few C++ types (bool is -16): kind and size in bytes
BUILTINS = {-11: ("void", 0), -12: ("float", 4), -13: ("float", 8), -14: ("float", 8), -16: ("base", 4),
            -20: ("base", 1), -21: ("base", 1), -22: ("base", 2), -30: ("base", 4), -31: ("base", 8), -32: ("base", 8)}

_NAME = re.compile(r"((?:[^:]|::)*):")
_INTEGER = re.compile(r"-?\d+")
_SIMPLE_MEMBER = re.compile(r"([^:;/]+):(\d+),(\d+),(\d+);")


class StabsError(ValueError):
    """An entry this reader can't follow; it's skipped."""


@dataclass(eq=False)
class Field:
    name: str
    type: Type
    bit_offset: int
    bit_size: int  # 0 when the entry doesn't say (GCC 2's vtable pointer)


@dataclass(eq=False)
class Type:
    """One type number of one source file. kind: base, float, void, pointer, array, struct, union, enum, function,
    alias (another number for the same type), const, volatile, xref (a struct, union or enum known by name only),
    method (C++), or unknown (referred to, never defined)."""
    kind: str = "unknown"
    name: str = ""  # base types: the compiler's name ("long unsigned int"); xref: the name referred to
    tag: str = ""  # struct, union, enum: its tag
    typedef: str = ""  # the typedef name this number was given
    size: int = 0  # bytes, 0 when not known
    target: Type | None = None  # what a pointer, array, alias, const, volatile or function (its return) refers to
    count: int = 0  # arrays: elements
    signed: bool = True
    xref: str = ""  # xref: struct, union or enum
    fields: list[Field] = field(default_factory=list)
    enumerators: list[tuple[str, int]] = field(default_factory=list)
    bases: list[tuple[int, Type]] = field(default_factory=list)  # C++: (bit offset, base class)


@dataclass
class Variable:
    name: str
    type: Type
    where: str = ""  # "$s0", "stack +0x10", or a global's section
    address: int | None = None


@dataclass
class Function:
    name: str
    address: int
    returns: Type
    is_static: bool
    size: int = 0
    params: list[Variable] = field(default_factory=list)
    locals: list[Variable] = field(default_factory=list)


@dataclass
class SourceInfo:
    """What the STABS of one source file say."""
    name: str
    types: list[Type] = field(default_factory=list)  # every type a name was given to, in the order GCC wrote them
    functions: list[Function] = field(default_factory=list)
    globals: list[Variable] = field(default_factory=list)  # G: defined in this file, address from the symbol table
    statics: list[Variable] = field(default_factory=list)  # S: file statics, with their address
    skipped: int = 0  # entries this reader couldn't follow


def register(number: int) -> str:
    """The name of a register as GCC numbers them in STABS."""
    if 0 <= number < GPR_COUNT:
        return f"${GPR_NAMES[number]}"
    if FPR_FIRST <= number < FPR_FIRST + FPR_COUNT:
        return f"$f{number - FPR_FIRST}"
    return f"register {number}"


def _frame(offset: int) -> str:
    return f"the stack, frame offset {offset:+#x}"


class _Reader:
    """Reads one STABS string from `at`, defining type numbers in `table` as it goes."""

    def __init__(self, text: str, at: int, table: dict):
        self.text, self.at, self.table = text, at, table

    def peek(self, ahead: int = 0) -> str:
        return self.text[self.at + ahead] if self.at + ahead < len(self.text) else ""

    def take(self, char: str) -> None:
        if self.peek() != char:
            raise StabsError(f"expected {char!r} at {self.at} in {self.text[:80]!r}")
        self.at += 1

    def until(self, stop: str) -> str:
        end = self.text.find(stop, self.at)
        if end < 0:
            raise StabsError(f"no {stop!r} after {self.at}")
        part, self.at = self.text[self.at:end], end + 1
        return part

    def integer(self) -> int:
        found = _INTEGER.match(self.text, self.at)
        if found is None:
            raise StabsError(f"expected a number at {self.at}")
        self.at = found.end()
        return int(found.group())

    def bound(self) -> int:
        """A range's bound: GCC writes the wide ones in octal, with a leading 0."""
        found = _INTEGER.match(self.text, self.at)
        if found is None:
            raise StabsError(f"expected a bound at {self.at}")
        self.at = found.end()
        digits = found.group().lstrip("-")
        value = int(digits, 8) if len(digits) > 1 and digits[0] == "0" else int(digits)
        return -value if found.group().startswith("-") else value

    def number(self):
        if self.peek() == "(":  # (file, number), when a compiler numbers types per header
            self.take("(")
            file_number = self.integer()
            self.take(",")
            number = self.integer()
            self.take(")")
            return file_number, number
        return self.integer()

    def type(self) -> Type:
        if self.peek().isdigit() or self.peek() == "(":
            number = self.number()
            known = self.table.setdefault(number, Type())
            if self.peek() == "=":
                self.at += 1
                self._define(known, number)
            return known
        anonymous = Type()
        self._define(anonymous, None)
        return anonymous

    def _define(self, t: Type, number) -> None:
        """Read a type definition into `t` (filled in only once all of it is read: on an error it stays as it was)."""
        new = Type(name=t.name, tag=t.tag, typedef=t.typedef)
        size_bits = 0
        while self.peek() == "@" and self.peek(1).isalpha():  # attributes: @s64; is the size in bits
            self.at += 1
            attribute = self.until(";")
            if attribute.startswith("s") and attribute[1:].isdigit():
                size_bits = int(attribute[1:])
        kind = self.peek()
        if kind.isdigit() or kind == "(":
            start = self.at
            if self.number() == number and self.peek() != "=":
                new.kind = "void"  # defined as itself
            else:
                self.at = start
                new.kind, new.target = "alias", self.type()
        elif kind == "r":
            self.at += 1
            base = self.type()
            self.take(";")
            low = self.bound()
            self.take(";")
            high = self.bound()
            self.take(";")
            _range(new, base is self.table.get(number), low, high)
        elif kind in "*&":
            self.at += 1
            new.kind, new.target, new.size = "pointer", self.type(), POINTER_BYTES
        elif kind in "kB":
            self.at += 1
            new.kind, new.target = ("const" if kind == "k" else "volatile"), self.type()
        elif kind == "f":
            self.at += 1
            new.kind, new.target = "function", self.type()
        elif kind == "a":
            self.at += 1
            self.take("r")
            self.type()
            self.take(";")
            low = self.bound()
            self.take(";")
            high = self.bound()
            self.take(";")
            new.kind, new.count, new.target = "array", max(0, high - low + 1), self.type()
        elif kind in "su":
            self.at += 1
            new.kind, new.size = ("struct" if kind == "s" else "union"), self.integer()
            self._members(new)
        elif kind == "e":
            self.at += 1
            new.kind, new.size = "enum", ENUM_BYTES
            while self.peek() != ";":
                name = self.until(":")
                new.enumerators.append((name, self.integer()))
                self.take(",")
            self.take(";")
        elif kind == "x":
            self.at += 1
            new.xref = {"s": "struct", "u": "union", "e": "enum"}.get(self.peek(), "")
            if not new.xref:
                raise StabsError(f"cross-reference to {self.peek()!r}")
            self.at += 1
            new.kind, new.name = "xref", self.until(":")
        elif kind == "#":  # a C++ method's type: ##return; or #class,return,arguments...;
            self.at += 1
            new.kind = "method"
            if self.peek() == "#":
                self.at += 1
                self.type()
            else:
                self.type()
                while self.peek() == ",":
                    self.at += 1
                    self.type()
            self.take(";")
        elif kind == "-":
            builtin = self.integer()
            self.take(";")
            new.kind, new.size = BUILTINS.get(builtin, ("base", 4))
        else:
            raise StabsError(f"type {kind!r}")
        if size_bits:
            new.size = size_bits // BITS_PER_BYTE
        t.__dict__.update(new.__dict__)

    def _members(self, t: Type) -> None:
        """A struct's or union's members: data members `name:type,bit offset,bit size;`, and in C++ its base
        classes (`!count,...`), static members (`name:type:mangled;`) and methods (`name::...`), of which only the
        data and the non-virtual bases matter for its layout."""
        if self.peek() == "!":
            self.at += 1
            count = self.integer()
            self.take(",")
            for _ in range(count):
                virtual = self.peek()
                self.at += 2  # virtual or not, then public/protected/private
                offset = self.integer()
                self.take(",")
                base = self.type()
                self.take(";")
                if virtual == "0":
                    t.bases.append((offset, base))
        while self.peek() not in (";", ""):
            simple = _SIMPLE_MEMBER.match(self.text, self.at)
            if simple:  # most members: a name, a type defined already, where it is (read at once, it's most of the time)
                name, number, offset, bits = simple.groups()
                t.fields.append(Field(name, self.table.setdefault(int(number), Type()), int(offset), int(bits)))
                self.at = simple.end()
                continue
            name = self.until(":")
            if self.peek() == ":":
                self.at += 1
                self._methods()
                continue
            if self.peek() == "/":  # C++ visibility
                self.at += 2
            member = self.type()
            if self.peek() == ":":  # a static member: nothing in the object
                self.at += 1
                self.until(";")
                continue
            self.take(",")
            offset = self.integer()
            bits = 0
            if self.peek() == ",":
                self.at += 1
                bits = self.integer()
            self.take(";")
            t.fields.append(Field(name, member, offset, bits))
        self.take(";")
        if self.peek() == "~":  # which class holds the vtable pointer
            self.at += 1
            if self.peek() == "%":
                self.at += 1
                self.type()
            self.take(";")

    def _methods(self) -> None:
        """One method name's overloads: `type:mangled;` then visibility, qualifiers and virtuality (`*index;class;`
        for a virtual one), until a `;`."""
        while True:
            self.type()
            self.take(":")
            self.until(";")
            self.at += 2  # visibility, then const/volatile
            virtuality = self.peek()
            self.at += 1
            if virtuality == "*":
                self.integer()
                self.take(";")
                if self.peek() != ";":
                    self.type()
                    self.take(";")
            if self.peek() == ";":
                self.at += 1
                return
            if not self.peek():
                raise StabsError("methods run past the end")


def _range(t: Type, of_itself: bool, low: int, high: int) -> None:
    """A range type: GCC's way of writing a base type. `r1;4;0;` (a positive low bound, 0 high, of another type) is
    a floating-point type of that many bytes; otherwise the bounds say the integer's size and sign."""
    if not of_itself and high == 0 and low > 0:
        t.kind, t.size = "float", low
        return
    span = high - low if high >= low else UNSIGNED_INT_SPAN  # r4;0;-1;: Sun's way of writing unsigned int
    t.kind, t.signed = "base", low < 0
    t.size = next((size for size in INTEGER_BYTES if span < 1 << (BITS_PER_BYTE * size)), INTEGER_BYTES[-1])


def _name_type(t: Type, name: str, tag: bool, typedef: bool) -> None:
    if not name.strip():  # GCC's name for an anonymous struct or enum is a space
        return
    if tag and t.kind in ("struct", "union", "enum") and not t.tag:
        t.tag = name
    if typedef:
        if t.kind in ("base", "float", "void"):
            t.name = t.name or name
        elif not t.typedef:
            t.typedef = name


def read_file(name: str, stabs: list[tuple[int, str, int]]) -> SourceInfo:
    """What one source file's STABS entries (code, string, value) say."""
    info = SourceInfo(name)
    table: dict = {}
    current: Function | None = None
    named: set[int] = set()
    for code, text, value in stabs:
        if code == N_FUN and not text:  # the end of a function: its size
            if current is not None:
                current.size = value
            current = None
            continue
        found = _NAME.match(text)
        if found is None or found.end() >= len(text):
            continue
        name, at = found.group(1), found.end()
        letter = text[at]
        try:
            if letter.isdigit() or letter == "(":  # a local variable on the stack
                if current is not None:
                    current.locals.append(Variable(name, _Reader(text, at, table).type(), _frame(value)))
                continue
            reader = _Reader(text, at + 1, table)
            if letter == "T":
                typedef = reader.peek() == "t"
                reader.at += typedef
                t = reader.type()
                _name_type(t, name, tag=True, typedef=typedef)
            elif letter == "t":
                t = reader.type()
                _name_type(t, name, tag=False, typedef=True)
            elif letter in "Ff" and code == N_FUN:
                current = Function(name, value, reader.type(), is_static=letter == "f")
                info.functions.append(current)
                continue
            elif letter in "pPRv" and current is not None:
                where = _frame(value) if letter in "pv" else register(value)
                variable = Variable(name, reader.type(), where)
                if all(p.name != name for p in current.params):  # GCC can say a parameter twice
                    current.params.append(variable)
                continue
            elif letter == "r" and current is not None:
                current.locals.append(Variable(name, reader.type(), register(value)))
                continue
            elif letter == "V" and current is not None:
                current.locals.append(Variable(name, reader.type(), "static", value))
                continue
            elif letter == "G":
                info.globals.append(Variable(name, reader.type()))
                continue
            elif letter == "S":
                info.statics.append(Variable(name, reader.type(), "static", value))
                continue
            else:
                continue
        except (ValueError, RecursionError):  # StabsError, a bound that isn't a number, a type nested past reason
            info.skipped += 1
            continue
        if id(t) not in named:
            named.add(id(t))
            info.types.append(t)
    return info
