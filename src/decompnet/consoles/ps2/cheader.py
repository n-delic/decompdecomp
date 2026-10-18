"""The game's own types written as C, for the shared header every function compiles with: what its debug information
(stabs.py) says about each struct, union, enum and typedef, with every field at the offset the game has it, and its
global variables declared with their types. These are what agents guess worst without it (on GTA San
Andreas, functions with virtual calls matched 7% of the time against 62% for the rest, because agents guessed the
layouts).

The same header is compiled as C and, for a function written as a C++ method, as C++, so what's written is plain C
that both accept: a C++ class becomes a struct of its data members (a base class is a member at its offset, the vtable
pointer a pointer), methods are left out. Each type is written once, though every source file that includes a header
repeats its types; two different types with one name get the second one a new name (Thing_2). Fields keep the
game's offsets: where natural alignment would put a field earlier, padding goes in front of it, and every field
says its offset in a comment, so agents can check.
"""

from __future__ import annotations

import re

from .stabs import BITS_PER_BYTE, POINTER_BYTES, Function, SourceInfo, Type, Variable

C_BASE_NAMES = {"int": "int", "char": "char", "long int": "long", "unsigned int": "unsigned int",
                "long unsigned int": "unsigned long", "long long int": "long long",
                "long long unsigned int": "unsigned long long", "short int": "short",
                "short unsigned int": "unsigned short", "signed char": "signed char", "unsigned char": "unsigned char",
                "float": "float", "double": "double", "long double": "long double", "void": "void"}
SIZED_INTEGERS = {1: "char", 2: "short", 4: "int", 8: "long long", 16: "int __attribute__((mode(TI)))"}
SIZED_FLOATS = {4: "float", 8: "double"}
MAX_ALIGNMENT = 16  # the EE's 128-bit registers: its widest types are 16-byte aligned
TAGGED = ("struct", "union", "enum")
ANONYMOUS_ENUM = "anonymous_enum"  # the tag of an `enum { A, B };` of constants, which C lets go without one
# names C or C++ reserve: a field or type called one of these gets a _ after it
KEYWORDS = set("""auto break case char const continue default do double else enum extern float for goto if inline int
long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while asm
bool catch class const_cast delete dynamic_cast explicit false friend mutable namespace new operator private protected
public reinterpret_cast static_cast template this throw true try typeid typename using virtual wchar_t and and_eq bitand
bitor compl not not_eq or or_eq xor xor_eq""".split())
_NOT_IDENTIFIER = re.compile(r"\W")
_ENUMERATOR = re.compile(r"\s+(\w+) = ")
_DEPTH = 64  # how far a type is followed before it's taken for a loop in broken debug information


def identifier(name: str) -> str:
    """A name as a C identifier: other characters become _, and a reserved word gets a _ after it."""
    name = _NOT_IDENTIFIER.sub("_", name)
    if name[:1].isdigit():
        name = f"_{name}"
    return f"{name}_" if name in KEYWORDS else name


def _compiler_name(name: str) -> bool:
    """Names the compiler gives its own types (__builtin_va_list, __vtbl_ptr_type, bool, "complex int"): never
    declared, since C has them under other names or not at all."""
    return not name or name.startswith("__") or " " in name or name in ("bool", "wchar_t")


def _align_up(value: int, alignment: int) -> int:
    return -(-value // alignment) * alignment


class Header:
    """The types of every source file, merged and named. `reserved`: names the header already defines (BASE_HEADER's
    s32, u8, ..., and its macros): the game's typedefs of those names aren't written, and uses of them are written
    as what they stand for, so a game whose u32 is 64 bits still gets its own layout."""

    def __init__(self, sources: list[SourceInfo], reserved: set[str] = frozenset()):
        self.reserved = set(reserved)
        self.tags: dict[int, str] = {}  # id(type) -> its tag in the header
        self.typedefs: dict[int, str] = {}  # id(type) -> its typedef name in the header
        self.entities: dict[tuple[str, str], Type] = {}  # ("struct" | "union" | "enum" | "typedef", name) -> type
        self.renamed = 0
        self._forward: set[tuple[str, str]] = set()
        self._aligns: dict[int, int] = {}
        self._signatures: dict[tuple[int, str], str] = {}
        for source in sources:  # `typedef struct { ... } Foo;` comes as an anonymous struct and a Foo of it
            for t in source.types:
                target = t.target
                if t.kind == "alias" and t.typedef and target is not None and target.kind in TAGGED and not target.tag:
                    target.tag = t.typedef
        for source in sources:
            for t in source.types:
                if t.kind == "enum" and not t.tag:  # by its typedef's name, so its enumerators are checked like others'
                    t.tag = t.typedef or ANONYMOUS_ENUM
                if t.kind in TAGGED and t.tag and id(t) not in self.tags:
                    self.tags[id(t)] = self._claim(t.kind, t.tag, t)
                if self._typedef_name(t) and id(t) not in self.typedefs:
                    self.typedefs[id(t)] = self._claim("typedef", self._typedef_name(t), t)

    # ------------------------------------------------------------------ naming

    def _ident(self, name: str) -> str:
        """A field's, parameter's or enumerator's name, kept clear of the header's own macros and typedefs."""
        name = identifier(name)
        return f"{name}_" if name in self.reserved else name

    def _typedef_name(self, t: Type) -> str:
        """The typedef name a type is known by, or "" (none, or one that's left out)."""
        name = t.typedef or (t.name if t.kind == "base" and t.name not in C_BASE_NAMES else "")
        if not name or _compiler_name(name) or identifier(name) in self.reserved:
            return ""
        return name

    def _claim(self, space: str, name: str, t: Type) -> str:
        """The name a type of one source file gets: that of an identical type already there, else its own, else a
        new one (Thing_2) when a different type has it."""
        base = identifier(name)
        candidate, copy = base, 2
        while (space, candidate) in self.entities:
            if self._signature(self.entities[(space, candidate)], space) == self._signature(t, space):
                return candidate
            candidate, copy = f"{base}_{copy}", copy + 1
        self.entities[(space, candidate)] = t
        self.renamed += candidate != base
        return candidate

    def _signature(self, t: Type, space: str) -> str:
        """What makes two definitions the same type, whatever their names. Kept: a game's every source file repeats
        the types of the headers it includes, and each is compared with the one already there."""
        key = (id(t), space)
        if key not in self._signatures:
            if space == "typedef":
                self._signatures[key] = self._typedef_body(t, set(), "")
            else:
                self._signatures[key] = self._shape(t, 0)
        return self._signatures[key]

    def _shape(self, t: Type, depth: int) -> str:
        """A type as a short string: what it's made of (at `depth` 0), with the types it refers to by name."""
        known = (self._known_as(t) or (f"{t.kind} {self._tag(t)}" if t.kind in TAGGED and t.tag else "")) if depth else ""
        if known or depth > _DEPTH:
            return known
        if t.kind == "xref":  # a struct known by name here is the same as one defined here
            return f"{t.xref} {identifier(t.name)}"
        if t.kind in ("struct", "union"):
            bases = ",".join(self._shape(base, depth + 1) for _, base in t.bases)
            fields = ";".join(f"{f.name}:{f.bit_offset}:{f.bit_size}:{self._shape(f.type, depth + 1)}" for f in t.fields)
            return f"{t.kind}{t.size}{{{bases}|{fields}}}"
        if t.kind == "enum":
            return repr(t.enumerators)
        if t.kind in ("base", "float", "void"):
            return f"{t.kind}:{t.name}:{t.size}:{t.signed}"
        return f"{t.kind}{t.count}({self._shape(t.target, depth + 1) if t.target is not None else ''})"

    def _known_as(self, t: Type) -> str:
        """The typedef name a reference to `t` is written with, or "" (written by what it is)."""
        if not self._typedef_name(t):
            return ""
        return self.typedefs.get(id(t), identifier(self._typedef_name(t)))

    def _tag(self, t: Type) -> str:
        return self.tags.get(id(t), identifier(t.tag)) if t.tag else ""

    # ------------------------------------------------------------------ sizes and alignment

    def _resolve(self, t: Type) -> Type:
        """What a type stands for: through aliases, qualifiers and cross-references to the definition."""
        for _ in range(_DEPTH):
            if t.kind in ("alias", "const", "volatile") and t.target is not None:
                t = t.target
            elif t.kind == "xref" and (t.xref, identifier(t.name)) in self.entities:
                t = self.entities[(t.xref, identifier(t.name))]
            else:
                return t
        return Type()

    def size(self, t: Type, depth: int = 0) -> int:
        t = self._resolve(t)
        if t.kind == "array" and t.target is not None and depth < _DEPTH:
            return t.count * self.size(t.target, depth + 1)
        return t.size

    def align(self, t: Type, depth: int = 0) -> int:
        t = self._resolve(t)
        if id(t) in self._aligns:
            return self._aligns[id(t)]
        if t.kind == "array" and t.target is not None and depth < _DEPTH:
            value = self.align(t.target, depth + 1)
        elif t.kind in ("struct", "union") and depth < _DEPTH:
            value = max([self.align(f.type, depth + 1) for f in t.fields]
                        + [self.align(base, depth + 1) for _, base in t.bases] + [1])
        elif t.kind == "pointer":
            value = POINTER_BYTES
        else:
            value = max(1, min(t.size, MAX_ALIGNMENT))
        self._aligns[id(t)] = value
        return value

    def complete(self, t: Type, depth: int = 0) -> bool:
        """Whether a value of this type can be declared (the compiler knows its size)."""
        t = self._resolve(t)
        if t.kind in ("base", "float", "pointer", "enum"):
            return True
        if t.kind == "array":
            return t.count > 0 and t.target is not None and depth < _DEPTH and self.complete(t.target, depth + 1)
        return t.kind in ("struct", "union")

    # ------------------------------------------------------------------ writing C

    def declaration(self, t: Type, name: str = "", deps: set | None = None, by_value: bool = True,
                    own: bool = False) -> str:
        """`t` declared as `name` in C: `struct Entity *next`, `s32 (*think)()`, `char name[16]`. `deps` collects the
        definitions this needs before it. `own`: `t` itself is being defined by a typedef (its own name isn't used)."""
        deps = set() if deps is None else deps
        inner, qualifiers, spec = name, [], "void"
        for step in range(_DEPTH):
            known = "" if own and step == 0 else self._known_as(t)
            if known:
                spec = known
                deps.add(("typedef", known))
                if by_value:
                    self._value_deps(t, deps)
                break
            if t.kind == "pointer" and t.target is not None:
                inner, t, by_value = f"*{inner}", t.target, False
                if not self._known_as(t) and t.kind in ("array", "function"):
                    inner = f"({inner})"
                if not self._known_as(t) and t.kind in ("struct", "union") and not t.tag:
                    break  # a pointer to a struct with no name: void *
                continue
            if t.kind == "array" and t.target is not None:
                inner, t = f"{inner}[{t.count}]", t.target
                continue
            if t.kind == "function" and t.target is not None:
                inner, t, by_value = f"{inner}()", t.target, False
                continue
            if t.kind in ("const", "volatile") and t.target is not None:
                if t.target.kind == "pointer" and not self._known_as(t.target):
                    inner = f"{t.kind} {inner}"
                else:
                    qualifiers.append(t.kind)
                t = t.target
                continue
            if t.kind == "alias" and t.target is not None:
                t = t.target
                continue
            spec = self._specifier(t, deps, by_value)
            break
        return " ".join([*dict.fromkeys(qualifiers), spec]) + (f" {inner}" if inner else "")

    def _value_deps(self, t: Type, deps: set) -> None:
        """The definitions a value of `t` needs: the struct, union or enum it's made of."""
        for _ in range(_DEPTH):
            t = self._resolve(t)
            if t.kind == "array" and t.target is not None:
                t = t.target
                continue
            if t.kind in TAGGED and t.tag:
                deps.add((t.kind, self._tag(t)))
            return

    def _specifier(self, t: Type, deps: set, by_value: bool) -> str:
        if t.kind == "base":
            spelled = C_BASE_NAMES.get(t.name)
            if spelled:
                return spelled
            if t.size == 1:
                return "signed char" if t.signed else "unsigned char"
            integer = SIZED_INTEGERS.get(t.size, "int")
            return integer if t.signed else f"unsigned {integer}"
        if t.kind == "float":
            return C_BASE_NAMES.get(t.name) or SIZED_FLOATS.get(t.size, "double")
        if t.kind in TAGGED:
            tag = self._tag(t)
            if not tag:
                return self._inline(t, deps)
            if by_value or t.kind == "enum":  # C++ refuses an enum used before it's defined
                deps.add((t.kind, tag))
            self._forward.add((t.kind, tag))
            return f"{t.kind} {tag}"
        if t.kind == "xref":
            name = identifier(t.name)
            if by_value or t.xref == "enum":
                deps.add((t.xref, name))
            self._forward.add((t.xref, name))
            return f"{t.xref} {name}"
        return "void"

    def _inline(self, t: Type, deps: set) -> str:
        if t.kind == "enum":
            return "enum { " + ", ".join(f"{self._ident(n)} = {v}" for n, v in t.enumerators) + " }"
        members = " ".join(line.split(" /*")[0] for line in self._members(t, deps))
        return f"{t.kind} {{ {members} }}"

    def _members(self, t: Type, deps: set) -> list[str]:
        """A struct's or union's member lines, with padding where the game's offsets need it."""
        lines, cursor_bits, used = [], 0, set()
        members = [(offset, f"base_{self._tag(self._resolve(base)) or 'class'}", base, 0) for offset, base in t.bases]
        members += [(f.bit_offset, self._ident(f.name) if f.name else "", f.type, f.bit_size) for f in t.fields]
        if t.kind == "struct":
            members.sort(key=lambda member: member[0])
        for bit_offset, name, member, bit_size in members:
            offset = bit_offset // BITS_PER_BYTE
            name = name or f"unnamed_{offset:X}"
            if name in used:
                name = f"{name}_{offset:X}"
            used.add(name)
            width = self.size(member)
            bitfield = bit_size and bit_size != width * BITS_PER_BYTE and self._resolve(member).kind in ("base", "enum")
            if t.kind == "struct" and not bitfield:
                start = _align_up(cursor_bits, BITS_PER_BYTE) // BITS_PER_BYTE
                if _align_up(start, self.align(member)) < offset:
                    lines.append(f"unsigned char pad_{start:X}[0x{offset - start:X}];")
            where = f"0x{offset:X}" + (f" bit {bit_offset % BITS_PER_BYTE}" if bitfield else "")
            if bitfield:
                lines.append(f"{self.declaration(member, name, deps)} : {bit_size}; /* {where} */")
            elif self.complete(member):
                lines.append(f"{self.declaration(member, name, deps)}; /* {where} */")
            else:  # a type the debug information doesn't define: its bytes
                count = max((bit_size or width * BITS_PER_BYTE) // BITS_PER_BYTE, 1)
                lines.append(f"unsigned char {name}[0x{count:X}]; /* {where} */")
            cursor_bits = max(cursor_bits, bit_offset + (bit_size or width * BITS_PER_BYTE))
        end = _align_up(cursor_bits, BITS_PER_BYTE) // BITS_PER_BYTE
        if t.kind == "union":
            if max([self.size(member) for _, _, member, _ in members] + [0]) < t.size:
                lines.append(f"unsigned char size[0x{t.size:X}];")
        elif end < t.size and _align_up(end, self.align(t)) < t.size:
            lines.append(f"unsigned char pad_{end:X}[0x{t.size - end:X}];")
        return lines

    def _typedef_body(self, t: Type, deps: set, name: str) -> str:
        if t.kind in TAGGED and t.tag:  # tag and typedef in one (C++ classes)
            if t.kind == "enum":
                deps.add(("enum", self._tag(t)))
            return f"{t.kind} {self._tag(t)} {name}".rstrip()
        target = t.target if t.kind == "alias" and t.target is not None else t
        return self.declaration(target, name, deps, by_value=False, own=target is t)

    def _definition(self, t: Type, space: str, deps: set) -> str:
        """The C that defines one entity: a struct, union or enum by its tag, or a typedef."""
        if space == "typedef":
            return f"typedef {self._typedef_body(t, deps, self.typedefs[id(t)])};"
        if t.kind == "enum":
            return f"enum {self._tag(t)} {{\n" + "".join(f"    {self._ident(n)} = {v},\n" for n, v in t.enumerators) + "};"
        lines = self._members(t, deps)
        if not lines and t.size:
            lines = [f"unsigned char data[0x{t.size:X}];"]
        body = "".join(f"    {line}\n" for line in lines)
        return f"{t.kind} {self._tag(t)} {{ /* 0x{t.size:X} bytes */\n{body}}};"

    def text(self, variables: list[tuple[str, Type, bool]] = ()) -> str:
        """Every type, in an order the compiler accepts (what each needs comes first), then `variables`: (name, type,
        extern) for the game's global variables."""
        out, done = [], set()
        ordinary = {name for space, name in self.entities if space == "typedef"} | self.reserved

        def emit(key) -> None:
            if key in done or key not in self.entities:
                return
            done.add(key)
            deps: set = set()
            text = self._definition(self.entities[key], key[0], deps)
            for dep in sorted(deps):
                emit(dep)
            if key[0] == "enum":  # an enumerator is an ordinary name: one that's taken already is left out
                lines = []
                for line in text.splitlines():
                    found = _ENUMERATOR.match(line)
                    if found and found.group(1) in ordinary:
                        line = f"    /* {line.strip()} (the name is taken) */"
                    elif found:
                        ordinary.add(found.group(1))
                    lines.append(line)
                text = "\n".join(lines)
            out.append(text)

        for key in list(self.entities):
            emit(key)
        declared = []
        for name, t, extern in variables:
            if name in ordinary or not self.complete(t):
                continue
            ordinary.add(name)
            declared.append(f"{'extern ' if extern else ''}{self.declaration(t, name)};")
        forward = [f"{space} {name};" for space, name in sorted(self._forward) if space != "enum"]
        return "\n".join(forward + [""] + out + ([""] + declared if declared else [])) + "\n"

    def prototype(self, function: Function, name: str) -> str:
        params = ", ".join(self.declaration(p.type, self._ident(p.name), by_value=False) for p in function.params)
        return self.declaration(function.returns, f"{name}({params or 'void'})", by_value=False)

    def variable(self, variable: Variable) -> str:
        return self.declaration(variable.type, self._ident(variable.name), by_value=False)
