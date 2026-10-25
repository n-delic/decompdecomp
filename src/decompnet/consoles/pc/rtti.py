"""C++ classes from a Visual C++ program's run-time type information (RTTI): each polymorphic class's own name, its
vtable and virtual functions, its base classes, and the functions that store its vtable (constructors, destructors).

A program built with /GR (a choice in Visual C++ 6.0 to 7.1, on by default from 2005) keeps, for every class with
virtual functions, what typeid and dynamic_cast read at run time:
- the word before each vtable points to the class's Complete Object Locator: a signature (0 on 32-bit x86), where
  this vtable's pointer sits in the object (0, or a second base's place), then the class's type descriptor and its
  class hierarchy descriptor;
- the type descriptor holds the class's decorated name, `.?AVTown@@` (`.?AU` for a struct);
- the hierarchy descriptor lists the class and every base class it has, the class itself first, then its bases
  depth first (the direct base next).
So these are the programmer's own names, not guesses. A program built without /GR has none of this: its classes
come from the vtables its constructors store (classes.from_facts, as on MIPS), named after their vtable's address
(Class_0064C80C) until its symbol gets a real name. Both get their fields from the code (read_code): what
arch/x86_fields.py reads each function doing through `this`, merged across a class's methods, its bases' inherited;
the same reading gives guess_struct its layouts. Reading a game of 25,000 functions takes about 10 seconds, once per
change to the program or functions.json.
"""

from __future__ import annotations

import json
import re
import struct
from array import array
from pathlib import Path

from ... import classes, structs
from ...arch import x86, x86_fields

# `.?AV` (a class) or `.?AU` (a struct), then the name, `Inner@Outer` for nested names, and `@@`
TYPE_NAME = re.compile(rb"\.\?A[VU]([^\0]{1,1024}?)@@\0")
TYPE_NAME_AT = 8  # in a type descriptor: the type_info vtable's address and a spare word come first
COL_OFFSET_AT, COL_TYPE_AT, COL_HIERARCHY_AT, COL_BYTES = 4, 12, 16, 20
HIERARCHY_COUNT_AT, HIERARCHY_BASES_AT = 8, 12
MAX_BASES = 256  # a broken hierarchy descriptor stops here
MAX_VPTR_OFFSET = 0x10000  # where a second base's vtable pointer can sit in an object
MAX_SLOTS = 1024
WORD = 4
_FOUND: dict[tuple, tuple[classes.Classes, structs.Structs]] = {}  # the last project's, by what it was read from
_FACTS: dict[tuple, dict[int, structs.Facts]] = {}  # what its functions do with pointers, by the program and them
STORED = "the vtables its constructors store"


# the one- and two-letter codes of built-in types in a decorated name
PRIMITIVES = {"C": "signed char", "D": "char", "E": "unsigned char", "F": "short", "G": "unsigned short", "H": "int",
              "I": "unsigned int", "J": "long", "K": "unsigned long", "M": "float", "N": "double", "O": "long double",
              "X": "void", "_N": "bool", "_J": "__int64", "_K": "unsigned __int64", "_W": "wchar_t"}
MEMORIZED = 10  # a decorated name refers back to its first ten names (and template arguments) by a digit


class _Name:
    """Reads the part of Visual C++'s name decoration a type descriptor holds: nested names, templates and their
    arguments (classes, enums, built-in types, pointers, integers) and the digits that repeat an earlier name.
    Anything else raises ValueError, and the name stays decorated."""

    def __init__(self, text: str):
        self.text, self.at, self.names, self.args = text, 0, [], []

    def take(self, prefix: str) -> bool:
        if self.text.startswith(prefix, self.at):
            self.at += len(prefix)
            return True
        return False

    def word(self) -> str:
        end = self.text.index("@", self.at)
        word, self.at = self.text[self.at:end], end + 1
        if not word:
            raise ValueError("an empty name")
        self.remember(self.names, word)
        return word

    @staticmethod
    def remember(seen: list, name: str) -> None:
        if len(seen) < MEMORIZED and name not in seen:
            seen.append(name)

    def qualified(self) -> str:
        parts = []
        while not self.take("@"):
            parts.append(self.part())
        return "::".join(reversed(parts))

    def part(self) -> str:
        here = self.text[self.at]
        if here.isdigit():
            self.at += 1
            return self.names[int(here)]
        if self.take("?$"):  # a template: its name and arguments are remembered apart from the names around it
            outer, self.names, outer_args, self.args = self.names, [], self.args, []
            name = self.word()
            args = []
            while not self.take("@"):
                args.append(self.type())
            self.names, self.args = outer, outer_args
            full = f"{name}<{', '.join(args)}{' ' if args and args[-1].endswith('>') else ''}>"
            self.remember(self.names, full)
            return full
        if self.take("?A"):  # an anonymous namespace (`?A0x1b2c3d4e@`)
            self.text.index("@", self.at)
            self.at = self.text.index("@", self.at) + 1
            return "`anonymous namespace'"
        return self.word()

    def type(self) -> str:
        start = self.at
        here = self.text[self.at]
        if here.isdigit():
            self.at += 1
            return self.args[int(here)]
        if here in "VUT":
            self.at += 1
            found = self.qualified()
        elif self.take("W4"):
            found = self.qualified()
        elif self.take("$0"):
            found = str(self.number())
        elif self.take("PA") or self.take("PB"):
            const = self.text[self.at - 1] == "B"
            found = f"{'const ' if const else ''}{self.type()} *"
        elif (code := self.text[self.at:self.at + 2]) in PRIMITIVES or (code := here) in PRIMITIVES:
            self.at += len(code)
            return PRIMITIVES[code]
        else:
            raise ValueError(f"no reading for {self.text[self.at:]!r}")
        if self.at - start > 1:
            self.remember(self.args, found)
        return found

    def number(self) -> int:
        sign = -1 if self.take("?") else 1
        here = self.text[self.at]
        if here.isdigit():
            self.at += 1
            return sign * (int(here) + 1)
        end = self.text.index("@", self.at)
        digits, self.at = self.text[self.at:end], end + 1
        if not digits or any(not "A" <= c <= "P" for c in digits):
            raise ValueError("not a number")
        return sign * int("".join("0123456789ABCDEF"[ord(c) - ord("A")] for c in digits) or "0", 16)


def class_name(decorated: str) -> str:
    """A type descriptor's name (without `.?AV` and the last `@@`) as C++ writes it: `Town`, `Outer::Inner`,
    `List<Town *, int>`. A decoration this doesn't read stays as the program has it (`.?AV...@@`)."""
    reader = _Name(decorated + "@@")
    try:
        name = reader.qualified()
        if reader.at == len(reader.text) and name:
            return name
    except (ValueError, IndexError):
        pass
    return f".?AV{decorated}@@"


def _words(data: bytes) -> array:
    words = array("I")
    words.frombytes(data[:len(data) // WORD * WORD])
    return words


def recover(pe, labels: dict[int, str] | None = None, relocs: dict[int, list] | None = None) -> classes.Classes:
    """The classes a program's RTTI names (`pe`: scratch.PE). `labels`: address -> the name agents know a function
    by; `relocs`: address -> a function's relocations as functions.json keeps them, to find which functions store a
    vtable."""
    out = classes.Classes(found_from="the program's run-time type information", reads_fields=False)
    word = {}

    def at(addr: int) -> int | None:
        if addr not in word:
            raw = pe.read(addr, WORD)
            word[addr] = struct.unpack("<I", raw)[0] if len(raw) == WORD else None
        return word[addr]

    types: dict[int, str] = {}  # type descriptor -> the class's name
    for s in pe.sections:
        data = pe.data[s.offset:s.offset + s.size]
        for m in TYPE_NAME.finditer(data):
            td = s.va + m.start() - TYPE_NAME_AT
            if td % WORD == 0 and pe.contains(at(td) or 0) and at(td + WORD) == 0:
                types[td] = class_name(m.group(1).decode("latin-1"))
    if not types:
        return out
    # Complete Object Locators: a word pointing at a type descriptor, 12 bytes into a 20-byte record whose
    # signature is 0 and whose hierarchy descriptor is in the program
    locators: dict[int, tuple[int, int]] = {}  # locator -> (type descriptor, where its vtable pointer sits)
    for s in pe.sections:
        for k, value in enumerate(_words(pe.data[s.offset:s.offset + s.size])):
            if value in types:
                col = s.va + WORD * k - COL_TYPE_AT
                offset = at(col + COL_OFFSET_AT)
                if at(col) == 0 and offset is not None and offset < MAX_VPTR_OFFSET \
                        and pe.contains(at(col + COL_HIERARCHY_AT) or 0):
                    locators[col] = (value, offset)
    vtables: dict[int, int] = {}  # vtable -> its locator: the word before a vtable points at it
    for s in pe.sections:
        for k, value in enumerate(_words(pe.data[s.offset:s.offset + s.size])):
            if value in locators:
                vtables[s.va + WORD * (k + 1)] = value

    def slots(vt: int) -> list[int]:
        found = []
        while len(found) < MAX_SLOTS:
            target = at(vt + WORD * len(found))
            sec = pe.section_at(target) if target is not None else None
            if sec is None or not sec.code:  # the next vtable's locator, or data
                break
            found.append(target)
        return found

    primary: dict[int, int] = {}  # type descriptor -> its class's vtable (the one at offset 0)
    seconds: list[tuple[int, int, int]] = []  # (vtable, type descriptor, offset)
    for vt, col in sorted(vtables.items()):
        td, offset = locators[col]
        if offset == 0 and td not in primary:
            primary[td] = vt
            out.by_vtable[vt] = classes.Class(vt, 0, slots(vt))
            out.names[vt] = types[td]
        elif offset:
            seconds.append((vt, td, offset))
    for vt, cls in out.by_vtable.items():
        hierarchy = at(vtables[vt] + COL_HIERARCHY_AT)
        count = min(at(hierarchy + HIERARCHY_COUNT_AT) or 0, MAX_BASES)
        bases = at(hierarchy + HIERARCHY_BASES_AT) or 0
        for k in range(1, count):
            base_td = at(at(bases + WORD * k) or 0)
            if base_td in types:
                cls.bases.append(types[base_td])
                if k == 1 and base_td in primary:
                    cls.parent = primary[base_td]
    for vt, td, offset in seconds:  # a second base's vtable: the class's too, at that place in the object
        if td in primary:
            own = out.by_vtable[primary[td]]
            own.others[offset] = vt
            out.names[vt] = f"{types[td]}'s vtable at +0x{offset:X}"
            for k, f in enumerate(slots(vt)):
                out.roles[f].append((own.vtable, f"slot {k} of its vtable at +0x{offset:X}"))
    for vt, cls in out.by_vtable.items():
        for k, f in enumerate(cls.slots):
            out.roles[f].append((vt, f"slot {k}"))
    out.labels = {**(labels or {}), **out.names}
    _constructors(pe, out, relocs or {}, {**{vt: vt for vt in out.by_vtable},
                                          **{vt: primary[td] for vt, td, _ in seconds if td in primary}})
    return out


def _constructors(pe, out: classes.Classes, relocs: dict[int, list], owner: dict[int, int]) -> None:
    """The functions that store a class's vtable into an object (`mov dword ptr [esi], offset vtable`): its
    constructors and destructors (and code that builds one inline). Only functions whose relocations name a vtable
    are read, so a big game doesn't disassemble every function."""
    names = {f"_D_{vt:08X}": vt for vt in owner}
    for addr, rs in relocs.items():
        wanted = {off: names[name] for off, _, name, addend in rs if name in names and not addend}
        if not wanted:
            continue
        end = max(off for off, _, _, _ in rs) + WORD
        stores = x86.immediate_stores(pe.read(addr, end), addr)
        for off, vt in wanted.items():
            disp = stores.get(off)
            cls = out.by_vtable[owner[vt]]
            if disp is not None and disp == next((o for o, v in cls.others.items() if v == vt), 0):
                if addr not in cls.ctors:
                    cls.ctors.add(addr)
                    out.roles[addr].append((cls.vtable, "constructor or destructor"))


def read_code(pe, functions: list[dict], found: classes.Classes, real: dict[str, str] | None = None,
              facts: dict[int, structs.Facts] | None = None) -> structs.Structs:
    """What every function does with pointers (arch/x86_fields.py), read once for both: the struct layouts
    (guess_struct) and the classes' fields. `functions`: functions.json's entries; `found`: the classes RTTI names,
    which get their fields here, the constructors their code shows, and the classes of the program's code built
    without RTTI (from the vtables constructors store, named after the vtable's address until its symbol gets a real
    name in `real`, functions.json's "names"). `facts`: what the functions do, read before (kept here when empty):
    reading them is most of the time, and a rename doesn't change them."""
    pops = {fn["addr"]: n for fn in functions if (n := x86_fields.pops(fn["name"])) is not None}
    facts = {} if facts is None else facts
    known = dict(facts)

    def read(addr: int, code: bytes, relocs: list, datum) -> structs.Facts:
        if addr not in known:
            facts[addr] = x86_fields.read_facts(addr, code, relocs, datum=datum, pops=pops)
        return facts[addr]

    def data(addr: int) -> int | None:  # every address the code names is a datum of its own (D_<address>)
        section = pe.section_at(addr)
        return addr if section is not None and not section.code else None

    layouts = structs.recover(((fn["addr"], pe.read(fn["addr"], fn["size"]), []) for fn in functions), read, data)
    named = set(found.by_vtable)
    classes.from_facts(facts, lambda addr, n: pe.read(addr, n) or None, (structs.ARGUMENT, structs.THIS), known=found,
                       code=lambda addr: (section := pe.section_at(addr)) is not None and section.code)
    added = [vt for vt in found.by_vtable if vt not in named]
    for vt in added:
        found.names[vt] = found.labels[vt] = (real or {}).get(f"D_{vt:08X}", f"Class_{vt:08X}")
    found.found_from = (f"the program's run-time type information and {STORED}" if named and added else
                        STORED if added else found.found_from)
    found.reads_fields = True
    return layouts


def for_project(root: Path, manifest: dict) -> classes.Classes | None:
    """A PC project's classes, with its functions' names (functions.json: func_0041B470, or the real name
    rename_symbol gave it), read again when the program or functions.json changes. None without either. The game's
    DLLs (functions.json's "modules") have RTTI and code of their own, read the same way: at the addresses the project
    gives them, which no two programs share."""
    read = _read(root, manifest)
    return read[0] if read else None


def structs_for_project(root: Path, manifest: dict) -> structs.Structs | None:
    """A PC project's struct layouts (structs.py), with the names agents know its functions by, read with its
    classes."""
    read = _read(root, manifest)
    return read[1] if read else None


def _read(root: Path, manifest: dict) -> tuple[classes.Classes, structs.Structs] | None:
    from .scratch import Programs, programs

    try:
        config = root / "config" / manifest["version"] / "functions.json"
        exe = root / manifest["game"]["path"]
        key = (str(exe), exe.stat().st_mtime_ns, config.stat().st_mtime_ns)
    except (KeyError, OSError):
        return None
    if key not in _FOUND:
        info = json.loads(config.read_text(encoding="utf-8"))
        real = info.get("names") or {}
        functions = [fn for fns in info["units"].values() for fn in fns]
        labels = {fn["addr"]: real.get(f"func_{fn['addr']:08X}", f"func_{fn['addr']:08X}") for fn in functions}
        try:
            pes = programs(root, info)
        except OSError:  # a DLL of the game's not in the project (yet)
            return None
        relocs = {fn["addr"]: fn["relocs"] for fn in functions}
        found = None
        for pe in pes.values():  # each program's own RTTI
            one = recover(pe, labels, relocs)
            if found is None:
                found = one
                continue
            found.by_vtable.update(one.by_vtable)
            found.names.update(one.names)
            for addr, roles in one.roles.items():
                found.roles[addr].extend(roles)
        code = key[:2] + (hash(tuple((fn["addr"], fn["size"], fn["name"]) for fn in functions)),)
        if code not in _FACTS:  # (renames keep them: polishing renames a function at a time)
            _FACTS.clear()
            _FACTS[code] = {}
        # the code of every program at once: a DLL's class can derive from one of the .exe's
        layouts = read_code(Programs(pes.values()), functions, found, real, _FACTS[code])
        for fn in functions:  # every way a function is spelled: the report's symbol, func_<address>, its real name
            for name in (fn["name"], f"func_{fn['addr']:08X}", labels[fn["addr"]]):
                found.symbols[name] = fn["addr"]
        data = {int(key[2:], 16): name for key, name in real.items() if key.startswith("D_")}
        layouts.labels, layouts.symbols = {**data, **found.labels}, found.symbols
        _FOUND.clear()
        _FOUND[key] = (found, layouts)
    return _FOUND[key]
