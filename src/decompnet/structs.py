"""Struct layouts, guessed from the loads and stores a game's code makes through pointers.

On GTA San Andreas, functions that make virtual calls matched 7% of the time against 62% for the rest: agents
guessed the layouts of the structs they work on, and every wrong offset is a failed compile. The code already says
most of a layout: a function that loads a halfword at arg0+0x22 says arg0 points to something with a 16-bit field at
0x22, and every function that is handed the same pointer says more about the same struct.

How the guess is made:
- the processor's reader (arch/: `Arch.field_accesses`, MIPS today) goes through each function once and says what
  it does with pointers: the loads and stores through its arguments, through words loaded from them (a pointer
  field: the struct it points to is a type too), through globals and through what callees return; which pointer it
  passes as which argument of which callee, what it returns, and which pointers it stores into fields;
- here, those facts become types: one pointer handed to a callee is the callee's argument's type, a pointer stored
  in a field is that field's type, and two types that are one have the same pointer fields (unification, so a
  type's fields come from every function it reaches);
- but a callee's argument (or result) joins its callers' types only when that's likely the same struct: the callee
  itself uses it (or hands it to a callee that does: a function that only passes a `void *` on, a container's Add,
  would make one type of everything stored in it), it doesn't walk it (a loop moving the pointer: memset, strcpy),
  it has at most HUB_CALLERS callers (a base class's method, CVector's, operator new: called with everything), and
  no offset is read with one width in the callers' type and another in the callee's. Unifying along every call made
  one type of 3,735 of GTA San Andreas's functions, and 43% of the offsets it proposed from other functions were
  right (checked against the structs agents gave matched functions); with these rules, 95%;
- each field's confidence is how many functions access it with that width and kind, and how many disagree.

It is a guess agents start from: they put what they confirm into the shared header with edit_context, and the
judge decides every match byte for byte. Where the game's own debug information gives the real type (a PS2 game's
STABS), that is shown instead.
"""

from __future__ import annotations

import bisect
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

# What a reader's facts call the things pointers come from (`term`s are tuples starting with one of these):
ARGUMENT = "arg"  # (ARGUMENT, n): the function's nth argument
GLOBAL = "global"  # (GLOBAL, address): the datum starting at address (the struct is the global itself)
CALL = "call"  # (CALL, at, callee): what the call at instruction `at` returned
FIELD = "field"  # (FIELD, term, offset): the word at term+offset (a pointer field: the struct it points to)
RETURNED = -1  # the argument number of a call's result, in Facts.calls
RETURN = "ret"  # (RETURN, function): what a function returns (a type of its own, between it and its callers)
NAMED = (GLOBAL, ARGUMENT, RETURN)  # what a type is named after, and listed by, for agents

# A callee with more callers than this keeps its own type: on GTA San Andreas, offsets proposed from other functions
# were right 95% of the time at 4 (the largest type: 498 functions), 90% at 8 (1,170), 74% at 16 (1,623).
HUB_CALLERS = 4
POINTER_BYTES = 4  # the consoles decompdecomp reads have 32-bit pointers
QUADWORD_BYTES = 16  # the PS2's 128-bit loads and stores
MAX_OFFSET = 0x10000  # past this, a load "through a pointer" is something else (an address being built)
SHOWN_FIELDS = 8  # fields per argument in a brief's line
SHOWN_MEMBERS = 12  # functions named where a type flows

C_TYPES = {("f", 4): "f32", ("f", 8): "f64", ("s", 1): "s8", ("u", 1): "u8", ("s", 2): "s16", ("u", 2): "u16",
           ("s", 4): "s32", ("u", 4): "u32", ("s", 8): "s64", ("u", 8): "u64"}


@dataclass
class Facts:
    """What one function does with pointers, in terms of its own arguments, globals and calls (a reader's output)."""
    accesses: list[tuple[tuple, int, int, str]] = field(default_factory=list)  # (term, offset, bytes, kind)
    calls: list[tuple[int, int, tuple]] = field(default_factory=list)  # (callee, its argument or RETURNED, term)
    returns: list[tuple] = field(default_factory=list)  # terms the function returns
    stores: list[tuple[tuple, int, tuple]] = field(default_factory=list)  # (term, offset, the pointer stored there)
    walked: set[tuple] = field(default_factory=set)  # terms moved along inside a loop (an array or a buffer walked)


@dataclass
class FieldGuess:
    offset: int
    size: int
    kind: str  # "s", "u", "f" or "v" (a 128-bit vector), from the loads and stores that agree
    functions: set[int]  # the functions that access it so
    others: dict[str, set[int]]  # another reading's type -> the functions that read it so
    points_to: int | None = None  # the type a pointer field points to, when that type has fields

    def ctype(self) -> str:
        if self.points_to is not None:
            return "void *"
        if self.size == QUADWORD_BYTES:  # lqc2: a VU0 vector, four floats; lq: a copy of 16 bytes
            return "f32[4]" if self.kind in ("v", "f") else "u64[2]"
        return C_TYPES.get((self.kind, self.size), f"u8[{self.size}]")


@dataclass
class Layout:
    """A guessed struct: its fields by offset, every function that touches it, and what else has this type."""
    root: int
    fields: list[FieldGuess]
    functions: set[int]
    members: list[tuple]  # the named terms of this type: (ARGUMENT, f, n), (RETURN, f), (GLOBAL, a), (CALL, f, at, g)
    pointed_from: list[tuple[int, int]]  # (type, offset): pointer fields of other types that point here

    def size_seen(self) -> int:
        return max((f.offset + f.size for f in self.fields), default=0)


class _Types:
    """Union-find over types, where two types made one also have one type at each pointer field."""

    def __init__(self):
        self.parent: list[int] = []
        self.children: list[dict[int, int]] = []  # pointer fields: offset -> type (on a set's root)
        self.accesses: list[list[tuple[int, int, str, int]]] = []  # (offset, bytes, kind, function) on a root
        self.widths: list[dict[int, set[int]]] = []  # offset -> the widths it's accessed with (on a root)

    def new(self) -> int:
        self.parent.append(len(self.parent))
        self.children.append({})
        self.accesses.append([])
        self.widths.append({})
        return len(self.parent) - 1

    def access(self, type_id: int, offset: int, size: int, kind: str, function: int) -> None:
        root = self.find(type_id)
        self.accesses[root].append((offset, size, kind, function))
        self.widths[root].setdefault(offset, set()).add(size)

    def agree(self, one: int, other: int) -> bool:
        """Whether two types could be one: no offset that one reads with a width the other never uses there."""
        one, other = self.find(one), self.find(other)
        small, large = sorted((self.widths[one], self.widths[other]), key=len)
        return all(not (offset in large and sizes.isdisjoint(large[offset])) for offset, sizes in small.items())

    def find(self, type_id: int) -> int:
        """The root of a type's set: the type it is one with."""
        while self.parent[type_id] != type_id:
            self.parent[type_id] = self.parent[self.parent[type_id]]
            type_id = self.parent[type_id]
        return type_id

    def child(self, type_id: int, offset: int) -> int:
        """The type of the pointer field at `offset` of a type (made when it's new)."""
        root = self.find(type_id)
        field_type = self.children[root].get(offset)
        if field_type is None:
            field_type = self.children[root][offset] = self.new()
        return field_type

    def union(self, one: int, other: int) -> None:
        """Make two types one, and so their pointer fields at the same offsets too."""
        work = [(one, other)]
        while work:
            keep, merged = (self.find(type_id) for type_id in work.pop())
            if keep == merged:
                continue
            if (len(self.accesses[keep]) + len(self.children[keep])
                    < len(self.accesses[merged]) + len(self.children[merged])):
                keep, merged = merged, keep  # the bigger set stays the root: less to move
            self.parent[merged] = keep
            self.accesses[keep] += self.accesses[merged]
            self.accesses[merged] = []
            for offset, sizes in self.widths[merged].items():
                self.widths[keep].setdefault(offset, set()).update(sizes)
            self.widths[merged] = {}
            mine = self.children[keep]
            for offset, field_type in self.children[merged].items():
                if offset in mine:
                    work.append((mine[offset], field_type))
                else:
                    mine[offset] = field_type
            self.children[merged] = {}


class Structs:
    """Every type found in a game's code, and the named things (arguments, returns, globals) that have each one."""

    def __init__(self, types: _Types, named: dict[tuple, int]):
        self._types = types
        self._named = named
        self._members: dict[int, list[tuple]] | None = None
        self._pointed: dict[int, list[tuple[int, int]]] | None = None

    def type_of(self, key: tuple, path: Iterable[int] = ()) -> int | None:
        """The type of a named thing: (ARGUMENT, function, n), (RETURN, function) or (GLOBAL, address); `path`
        follows pointer fields (arg0+0x14: the struct the pointer at 0x14 points to)."""
        type_id = self._named.get(key)
        for offset in path:
            if type_id is None:
                return None
            type_id = self._types.children[self._types.find(type_id)].get(offset)
        return None if type_id is None else self._types.find(type_id)

    def _index(self) -> None:
        if self._members is not None:
            return
        members: dict[int, list[tuple]] = defaultdict(list)
        for key, type_id in self._named.items():
            members[self._types.find(type_id)].append(key)
        pointed: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for type_id, children in enumerate(self._types.children):
            if children and self._types.find(type_id) == type_id:
                for offset, field_type in children.items():
                    pointed[self._types.find(field_type)].append((type_id, offset))
        self._members, self._pointed = members, pointed

    def layout(self, type_id: int) -> Layout:
        """What the code says about type `type_id`: a field for each offset its functions access."""
        self._index()
        types = self._types
        readings: dict[int, dict[tuple[int, str], set[int]]] = defaultdict(lambda: defaultdict(set))
        signs: dict[int, list[str]] = defaultdict(list)
        functions = set()
        for offset, size, kind, function in types.accesses[type_id]:
            letter = kind[:1] if kind else ""
            group = "f" if letter == "f" else "v" if letter == "v" else "i"
            readings[offset][(size, group)].add(function)
            if letter in ("s", "u"):
                signs[offset].append(letter)
            functions.add(function)
        fields = []
        for offset in sorted(readings):
            by = readings[offset]
            pointee = types.children[type_id].get(offset)
            pointer = pointee is not None and bool(types.accesses[types.find(pointee)])
            for (size, group) in [k for k in by if k[1] == "f"]:  # an int read of a float field is a copy of it
                if (size, "i") in by and not (pointer and size == POINTER_BYTES):
                    by[(size, "f")] |= by.pop((size, "i"))
            (size, group), users = max(by.items(), key=lambda item: (len(item[1]), item[0][1] != "i", item[0][0]))
            if group == "i":
                said = signs[offset]
                kind = "u" if said and said.count("u") > said.count("s") else "s"
            else:
                kind = group
            others: dict[str, set[int]] = {}
            for (other_size, other_group), who in by.items():
                if (other_size, other_group) != (size, group) and (who - users):
                    other = FieldGuess(offset, other_size, "s" if other_group == "i" else other_group, set(), {})
                    others[other.ctype()] = who - users
            guess = FieldGuess(offset, size, kind, users, others)
            if pointer and group == "i" and size == POINTER_BYTES:
                guess.points_to = types.find(pointee)
            fields.append(guess)
        return Layout(type_id, fields, functions, sorted(self._members.get(type_id, []), key=_member_order),
                      self._pointed.get(type_id, []))

    def name(self, type_id: int, label: Callable[[int], str]) -> str:
        """A name for the type that every function sharing it shows the same: after a global that is one, else the
        first function (by address) that takes it as an argument or returns it."""
        self._index()
        for kind, *rest in sorted(self._members.get(type_id, []), key=_member_order):
            if kind == GLOBAL:
                return f"S_{label(rest[0])}"
            if kind == ARGUMENT:
                return f"S_{label(rest[0])}_arg{rest[1]}"
            if kind == RETURN:
                return f"S_{label(rest[0])}_ret"
        for parent, offset in self._pointed.get(type_id, []):  # else after a pointer field to it: S_func_0012A0_arg0_14
            if any(m[0] in NAMED for m in self._members.get(parent, [])):
                return f"{self.name(parent, label)}_{offset:X}"
        return f"S_type{type_id}"


def _member_order(member: tuple) -> tuple:
    kind, *rest = member
    return ({GLOBAL: 0, ARGUMENT: 1, RETURN: 2}.get(kind, 3), rest[0], rest[1] if len(rest) > 1 else 0)


def recover(functions: Iterable[tuple[int, bytes, list]], read_facts: Callable[..., Facts],
            datum: Callable[[int], int]) -> Structs:
    """Types from (address, code, relocations) of every function, read by the processor's reader:
    `read_facts(address, code, relocations, datum=...)`. `datum(address)`: where the global containing a data address
    starts (the symbol table's)."""
    funcs = sorted(functions, key=lambda function: function[0])
    starts = [addr for addr, _, _ in funcs]
    ends = [addr + len(code) for addr, code, _ in funcs]

    def data_start(addr: int) -> int | None:
        below = bisect.bisect_right(starts, addr) - 1
        return None if below >= 0 and addr < ends[below] else datum(addr)

    facts = {addr: read_facts(addr, code, relocs, datum=data_start) for addr, code, relocs in funcs}
    types = _Types()
    named: dict[tuple, int] = {}

    def node(term: tuple, function: int) -> int:
        kind = term[0]
        if kind == FIELD:
            return types.child(node(term[1], function), term[2])
        key = (ARGUMENT, function, term[1]) if kind == ARGUMENT else \
            (CALL, function, term[1], term[2]) if kind == CALL else term
        return named_node(key)

    def named_node(key: tuple) -> int:
        type_id = named.get(key)
        if type_id is None:
            type_id = named[key] = types.new()
        return type_id

    callers: dict[int, set[int]] = defaultdict(set)
    edges = []  # (caller, its term, the callee's side: (ARGUMENT, callee, n) or (RETURN, callee))
    walked = set()
    for function, said in facts.items():
        for term, offset, size, kind in said.accesses:
            if 0 <= offset < MAX_OFFSET:
                types.access(node(term, function), offset, size, kind, function)
        for term, offset, value in said.stores:
            if 0 <= offset < MAX_OFFSET:
                types.union(types.child(node(term, function), offset), node(value, function))
        for term in said.returns:
            types.union(named_node((RETURN, function)), node(term, function))
        for callee, argument, term in said.calls:
            if callee in facts and callee != function:
                callers[callee].add(function)
                edges.append((function, term,
                              (RETURN, callee) if argument == RETURNED else (ARGUMENT, callee, argument)))
        walked |= {(ARGUMENT, function, term[1]) for term in said.walked if term[0] == ARGUMENT}

    # A callee's side joins its callers' types once the callee itself uses it: repeated, because handing a pointer
    # to a callee that uses it is a use too (a function that only passes its argument on learns that from below).
    def used(key: tuple) -> bool:
        type_id = named.get(key)
        return type_id is not None and bool(types.accesses[types.find(type_id)])

    pending = [(caller, term, key) for caller, term, key in edges
               if key not in walked and len(callers[key[1]]) <= HUB_CALLERS]
    while True:
        waiting = []
        for function, term, key in pending:
            if used(key):
                mine = node(term, function)
                if types.agree(mine, named[key]):
                    types.union(mine, named[key])
            else:
                waiting.append((function, term, key))
        if len(waiting) == len(pending):
            break
        pending = waiting
    return Structs(types, named)


# --------------------------------------------------------------------------------------------- what agents read

def summary(layout: Layout, limit: int = SHOWN_FIELDS) -> str:
    """One line for a brief: "+0x04 s32 (12 functions), +0x10 f32 (3), ..."."""
    parts = []
    for number, guess in enumerate(layout.fields[:limit]):
        count = len(guess.functions)
        unit = (" functions" if count != 1 else " function") if number == 0 else ""
        disagree = f"; {sum(len(w) for w in guess.others.values())} disagree" if guess.others else ""
        kind = "pointer" if guess.points_to is not None else guess.ctype()
        parts.append(f"+0x{guess.offset:X} {kind} ({count}{unit}{disagree})")
    more = f", and {len(layout.fields) - limit} more" if len(layout.fields) > limit else ""
    return ", ".join(parts) + more


def c_text(layout: Layout, name: str, type_name: Callable[[int], str]) -> str:
    """The guessed struct as C, offsets in comments, the gaps as unk_ arrays; a guess that overlaps an earlier one
    (a word also read as a byte) is in the earlier one's comment. `type_name` names the types pointer fields point to."""
    lines = [f"typedef struct {name} {{"]
    at = 0
    for guess in layout.fields:
        if guess.offset < at:
            continue  # inside the previous guess: said in its comment
        if guess.offset > at:
            lines.append(f"    /* 0x{at:02X} */ u8 unk_{at:02X}[0x{guess.offset - at:X}];")
        ctype = f"struct {type_name(guess.points_to)} *" if guess.points_to is not None else guess.ctype()
        ctype, bracket, count = ctype.partition("[")  # an array: f32[4], or an odd width's bytes
        array = bracket + count
        notes = [f"{len(guess.functions)} function{'s' if len(guess.functions) != 1 else ''}"]
        notes += [f"{len(who)} read it as {other}" for other, who in sorted(guess.others.items())]
        notes += [f"also {inner.ctype()} at 0x{inner.offset:X}" for inner in layout.fields
                  if guess.offset < inner.offset < guess.offset + guess.size]
        space = "" if ctype.endswith("*") else " "
        lines.append(f"    /* 0x{guess.offset:02X} */ {ctype}{space}unk_{guess.offset:02X}{array}; /* {'; '.join(notes)} */")
        at = guess.offset + guess.size
    lines.append(f"}} {name}; /* at least 0x{at:X} bytes */")
    return "\n".join(lines)


FLOATS = re.compile(r"^(float|double|f32|f64)$")


def parameter_types(source: str, function: str) -> list[str]:
    """The parameters' types in `function`'s definition in C an agent wrote (matched code), what agents settled on:
    ["CPed *", "s32"]; [] when the definition isn't there."""
    m = re.search(rf"\b{re.escape(function)}\s*\(([^()]*)\)\s*\{{", source)
    if not m or m.group(1).strip() in ("", "void"):
        return []
    out = []
    for param in m.group(1).split(","):
        param = " ".join(re.sub(r"\b(const|volatile|register)\b", " ", param).split())
        typed = re.match(r"^(.*?[\s*&])\s*[A-Za-z_]\w*$", param)  # without the parameter's name
        out.append((typed.group(1) if typed else param).strip())
    return out


DEFINITION = re.compile(r"\b(typedef\s+)?(?:struct|union|class)\s*(\w*)[^;{}()]*\{")
BASIC_TYPES = re.compile(r"^(void|char|short|int|long|float|double|unsigned|signed|bool|[su](8|16|32|64)|f(32|64)|"
                         r"[su]32_bool|vu?[su]?(8|16|32))$")


def type_name(ctype: str) -> str | None:
    """The struct a pointer type points to ("const CPed *" -> "CPed"), None for a basic type or not a pointer."""
    if "*" not in ctype and "&" not in ctype:
        return None
    name = " ".join(re.sub(r"\b(struct|union|class|const|volatile)\b|[*&]", " ", ctype).split())
    return None if not name or " " in name or BASIC_TYPES.match(name) else name


def definition(header: str, name: str) -> str | None:
    """`name`'s struct, union or class definition in C text (a header), braces and all; None when it isn't there."""
    for m in DEFINITION.finditer(header):
        depth = 0
        for at in range(m.end() - 1, len(header)):
            depth += {"{": 1, "}": -1}.get(header[at], 0)
            if depth == 0:
                break
        else:
            return None
        end = header.find(";", at)
        if end >= 0 and (m.group(2) == name or (m.group(1) and re.search(rf"\b{re.escape(name)}\b", header[at:end]))):
            return header[m.start():end + 1]
    return None


def argument_type(source: str, function: str, n: int) -> str | None:
    """The type matched code gives argument register n (a0 + n), when no float comes before it: floats travel in
    registers of their own, and whether that moves the integer ones depends on the console's calling convention."""
    params = parameter_types(source, function)
    if n >= len(params) or any(FLOATS.match(p) for p in params[:n + 1]):
        return None
    return params[n]
