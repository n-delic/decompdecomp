"""C++ classes, recovered from the game's own machine code (MIPS: PS2, N64, read here; x86: PC programs, from what
arch/x86_fields.py says each function does, `from_facts`), and what agents read about them (also for the classes a
PC program's run-time type information names: consoles/pc/rtti.py makes the same Classes, and adds the fields).

Agents matched 7% of a PS2 game's functions that make virtual calls against 62% of the rest: without the class
layouts, every `this->field` is a guess. The code holds most of what a layout is:
- a constructor stores its class's vtable address into the object (`lui/addiu` the address, `sw` it at this+0):
  that store names the class, and the run of function addresses at that address are its virtual functions;
- a constructor that stores a base class's vtable before its own (an inlined or called base constructor) and a
  destructor that stores it after its own name the base class, so the vtable shared with the most is the parent;
- every load and store through `this` in the class's virtual functions and constructors is a field: its offset,
  its size and its kind (lwc1: a float, lh: a signed short, a word later used as an address: a pointer).

None of this is a guess the judge has to trust: it's a layout agents start from, and every match is still decided
byte for byte. A C game's table of function pointers stored by an init function looks the same, and is shown as one.
"""

from __future__ import annotations

import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import structs

R_MIPS_LO16 = 6

# loads and stores: opcode -> (bytes, kind); kind "" is a store that doesn't say signed or unsigned
_LOADS = {0x20: (1, "s8"), 0x24: (1, "u8"), 0x21: (2, "s16"), 0x25: (2, "u16"), 0x23: (4, "s32"), 0x27: (4, "u32"),
          0x37: (8, "u64"), 0x1E: (16, "u128"), 0x31: (4, "f32"), 0x35: (8, "f64"), 0x36: (16, "vu128")}
_STORES = {0x28: (1, ""), 0x29: (2, ""), 0x2B: (4, ""), 0x3F: (8, ""), 0x1F: (16, ""), 0x39: (4, "f32"),
           0x3D: (8, "f64"), 0x3E: (16, "vu128")}
_GPR_LOADS = {0x20, 0x21, 0x23, 0x24, 0x25, 0x27, 0x37, 0x1E, 0x1A, 0x1B, 0x22, 0x26}
_CALLER_SAVED = set(range(1, 16)) | {24, 25, 31}
_THIS = 4  # $a0
_MAX_SLOTS = 400
# Where the virtual functions are, from the address a constructor stores: (the first one, bytes from one to the next).
# g++ 2.9x with -fvtable-thunks (an offset and the type-info function) and CodeWarrior (type info and an offset) store
# the vtable's start, two header words before them; g++ 3 and later (the Itanium ABI: PSP GCC, EE-GCC 3.2) store the
# address of the first one; SN's SNC (cfront's layout) stores the start, 8 bytes of zeros, then an 8-byte entry per
# function, the adjustment to `this` before its address. A vtable is read every way and the way that finds the most
# functions in a row is its compiler's: the others stop at a header word, or skip slots. (Not measured from virtual
# calls: C's tables of function pointers, as in RenderWare, are called the same way from offset 0.)
_LAYOUTS = ((8, 4), (0, 4), (12, 8))
WHERE = ("get_class reads MIPS games' code (PlayStation 2, PlayStation Portable, PlayStation, N64) and PC programs' "
         "(their run-time type information too), for now")  # where there are classes to find (said where there aren't)


@dataclass
class Field:
    offset: int
    size: int
    kind: str = ""
    pointer: bool = False
    users: set[int] = field(default_factory=set)

    def ctype(self) -> str:
        """The field's C type, from its width and how the code reads it."""
        if self.pointer:
            return "void *"
        if self.kind == "f32":
            return "float"
        if self.kind == "f64":
            return "double"
        names = {1: "8", 2: "16", 4: "32", 8: "64", 16: "128"}
        if self.kind in ("s8", "s16", "s32"):
            return f"s{names[self.size]}"
        return f"u{names.get(self.size, str(8 * self.size))}" if self.size in names else f"u8[{self.size}]"


@dataclass
class Class:
    vtable: int  # the address constructors store (virtual functions begin `header` bytes in, `stride` apart)
    header: int
    slots: list[int]  # the virtual functions, in slot order
    stride: int = 4
    vptr: int = 0  # where the object keeps its vtable pointer
    ctors: set[int] = field(default_factory=set)  # functions that store this vtable into `this`
    parent: int | None = None
    others: dict[int, int] = field(default_factory=dict)  # this+offset -> a second vtable stored there (multiple bases)
    fields: dict[int, Field] = field(default_factory=dict)
    bases: list[str] = field(default_factory=list)  # every base class by name, the direct one first, where RTTI says

    def size_seen(self) -> int:
        return max([f.offset + f.size for f in self.fields.values()] + [self.vptr + 4])


@dataclass
class Classes:
    by_vtable: dict[int, Class] = field(default_factory=dict)
    roles: dict[int, list[tuple[int, str]]] = field(default_factory=lambda: defaultdict(list))  # function -> (vtable, role)
    found_from: str = "the vtables constructors store"  # what the classes were read from, as agents are told
    reads_fields: bool = True  # whether the fields come from the code (rtti.recover's alone don't: read_code adds them)
    names: dict[int, str] = field(default_factory=dict)  # vtable -> its class's own name, where the program says (RTTI)
    labels: dict[int, str] = field(default_factory=dict)  # (project mode) address -> the name agents know it by
    symbols: dict[str, int] = field(default_factory=dict)  # (project mode) a function's names -> its address

    def find(self, addr: int) -> list[Class]:
        """The classes an address is: a vtable, or one of their virtual functions or constructors."""
        if addr in self.by_vtable:
            return [self.by_vtable[addr]]
        return [self.by_vtable[v] for v, _ in self.roles.get(addr, [])]


@dataclass
class _Scan:
    """What one function does with `this` ($a0 at entry) and with vtable addresses."""
    accesses: list[tuple[int, int, str, bool]] = field(default_factory=list)  # (offset, size, kind, used as a pointer)
    stores: list[tuple[int, int, bool]] = field(default_factory=list)  # (vtable, offset, into `this`)
    this_calls: list[int] = field(default_factory=list)  # functions called with `this` in $a0


def _dest(w: int) -> int | None:
    """The general register an instruction writes, if any."""
    op, rs, rt, rd, fn = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 63
    if op == 0:
        return None if fn in (8, 0x0C, 0x0D, 0x0F, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F) or fn >= 0x30 else rd
    if op == 0x1C:  # MMI (EE)
        return rd
    if 8 <= op <= 0x0F or op in (0x18, 0x19) or op in _GPR_LOADS:
        return rt
    if op in (0x11, 0x12) and rs in (0, 1, 2):  # mfc1 / dmfc1 / cfc1
        return rt
    return None


def scan(code: bytes, relocs: list, big_endian: bool, vtables: set[int] | None = None) -> _Scan:
    """One pass in address order (branches aren't followed: a register's meaning holds until it's overwritten)."""
    words = struct.unpack((">" if big_endian else "<") + f"{len(code) // 4}I", code[:len(code) // 4 * 4])
    addresses = {off // 4: addr for off, rtype, addr in relocs if rtype == R_MIPS_LO16}
    this = {_THIS}
    loaded: dict[int, tuple[int | None, int]] = {}  # register -> (this-offset or None, offset) it was loaded from
    holds: dict[int, int] = {}  # register -> vtable address it holds
    out = _Scan()
    clobber = False
    for k, w in enumerate(words):
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, struct.unpack("<h", struct.pack("<H", w & 0xFFFF))[0]
        if rs in loaded and (op in _LOADS or op in _STORES) and loaded[rs][0] is not None:
            out.accesses.append((loaded[rs][0], 4, "", True))  # a word of `this` used as an address: a pointer
        if op in _LOADS or op in _STORES:
            size, kind = _LOADS.get(op) or _STORES[op]
            if rs in this:
                out.accesses.append((imm, size, kind, False))
            if op in _STORES and size == 4 and rt in holds and (vtables is None or holds[rt] in vtables):
                out.stores.append((holds[rt], imm, rs in this))
        if op == 3 and _THIS in this:
            out.this_calls.append(((w & 0x3FFFFFF) << 2))  # the segment bits are added by the caller
        # what this instruction leaves in its destination (worked out before the old facts about it go)
        d = _dest(w)
        if d:
            moved = op == 0 and (w & 63) in (0x21, 0x25, 0x2D) and ((rs in this and rt == 0) or (rt in this and rs == 0))
            from_this = op in (0x23, 0x27) and rs in this
            for facts in (loaded, holds):
                facts.pop(d, None)
            this.discard(d)
            if moved:
                this.add(d)
            elif op in (0x23, 0x27):
                loaded[d] = (imm if from_this else None, imm)
            elif op in (0x09, 0x0D, 0x19) and k in addresses:
                holds[d] = addresses[k]
        if clobber:
            for r in _CALLER_SAVED:
                this.discard(r)
                loaded.pop(r, None)
                holds.pop(r, None)
            clobber = False
        if op == 3 or (op == 0 and (w & 63) == 9):
            clobber = True  # after the delay slot
    return out


def _slots(data: bytes, header: int, stride: int, code: Callable[[int], bool] | set[int], e: str,
           vt: int = 0, stored: set[int] | None = None) -> list[int]:
    """The functions in a row from `header` on, `stride` apart: the vtable read one compiler's way."""
    is_code = code if callable(code) else code.__contains__
    slots = []
    for at in range(header, min(len(data) - 3, header + stride * _MAX_SLOTS), stride):
        if stored and slots and (vt + at) in stored:
            break
        (w,) = struct.unpack_from(e + "I", data, at)
        if not is_code(w):
            break
        slots.append(w)
    return slots


def recover(functions: Iterable[tuple[int, bytes, list]], read: Callable[[int, int], bytes | None],
            big_endian: bool) -> Classes:
    """Classes from (address, code, relocations) of every function and a reader of the program's data."""
    funcs = list(functions)
    scans = {addr: scan(code, relocs, big_endian) for addr, code, relocs in funcs}
    out = from_scans(scans, read, big_endian)
    add_fields(out, scans)
    return out


def from_facts(facts: dict[int, structs.Facts], read: Callable[[int, int], bytes | None], this: tuple,
               header: int = 0, big_endian: bool = False, known: Classes | None = None,
               code: Callable[[int], bool] | None = None) -> Classes:
    """Classes from what a processor's reader for structs.py (Arch.field_accesses) says each function does with
    pointers, where `this` is the term of the object a method gets (x86: ecx at entry): the globals stored into
    objects (a constructor's vtable), the accesses through `this`, the calls handed `this`. `known`: the classes
    another source names already (a PC program's RTTI), which get the constructors found here and every class's
    fields; the vtables it doesn't know are added to it (a program's code built without RTTI). `code`: whether an
    address is code, for the vtables' slots (by default: a function's start)."""
    scans = {addr: _scan_of(f, this) for addr, f in facts.items()}
    found = from_scans(scans, read, big_endian, header, code)
    if known is not None:
        _merge(known, found)
        found = known
    add_fields(found, scans)
    return found


def _merge(known: Classes, found: Classes) -> None:
    """The classes of `found` into `known`: constructors of the ones it has, and the others whole (not a second
    base's vtable `known` names: that's one of its classes too)."""
    for vt, cls in found.by_vtable.items():
        own = known.by_vtable.get(vt)
        if own is not None:
            for c in sorted(cls.ctors - own.ctors):
                own.ctors.add(c)
                known.roles[c].append((vt, "constructor or destructor"))
        elif vt not in known.names:
            known.by_vtable[vt] = cls
            for f in set(cls.slots) | cls.ctors:
                known.roles[f] += [(v, role) for v, role in found.roles.get(f, []) if v == vt]
    for cls in known.by_vtable.values():
        if cls.parent is not None and cls.parent not in known.by_vtable:
            cls.parent = None


def _scan_of(facts: structs.Facts, this: tuple) -> _Scan:
    out = _Scan()
    for term, offset, size, kind in facts.accesses:
        if term == this:
            out.accesses.append((offset, size, kind, False))
        elif term[0] == structs.FIELD and term[1] == this:  # a word of `this` used as an address: a pointer
            out.accesses.append((term[2], 4, "", True))
    out.stores = [(value[1], offset, term == this) for term, offset, value in facts.stores if value[0] == structs.GLOBAL]
    out.this_calls = [callee for callee, n, term in facts.calls if term == this and n == this[1]]
    return out


def from_scans(scans: dict[int, _Scan], read: Callable[[int, int], bytes | None], big_endian: bool,
               layouts: Iterable[tuple[int, int]] | int = _LAYOUTS,
               code: Callable[[int], bool] | None = None) -> Classes:
    """Classes from each function's _Scan, without their fields yet (add_fields): a stored address whose words (from
    `header` bytes in) are functions is a vtable; the functions that store it into `this` are its constructors and
    destructors. A vtable's slots run while its words are code (`code`; by default a function's start), up to the
    next address a function stores: vtables built without type information (MSVC's without RTTI) sit side by side,
    and a function the program's analysis merged into the one before it (as a PC game's tiny virtual functions
    between nop padding were) is still a slot."""
    starts = set(scans)
    code = code or starts.__contains__
    stored_anywhere = {vt for s in scans.values() for vt, _, _ in s.stores}
    e = ">" if big_endian else "<"
    if isinstance(layouts, int):
        layouts = ((layouts, 4),)
    out = Classes()
    for addr, s in scans.items():
        for vt, _, _ in s.stores:
            if vt in out.by_vtable:
                continue
            data = read(vt, 12 + 8 * _MAX_SLOTS) or b""
            found = [(_slots(data, header, stride, code, e, vt, stored_anywhere), header, stride)
                     for header, stride in layouts]
            slots, header, stride = max(found, key=lambda f: len(f[0]))  # (of equals, the first in _LAYOUTS)
            if slots:
                out.by_vtable[vt] = Class(vt, header, slots, stride=stride)
    if not out.by_vtable:
        return out
    # A function that stores several vtables at one place in `this` constructs (or destroys) the most derived class:
    # the others are its bases' (inlined base constructors; destructors resetting the vtable on their way down).
    # Its own class is the one at the lowest offset; a vtable further in is a second base's (or a member object's).
    seg = {addr: addr & 0xF0000000 for addr in starts}
    def stored(addr: int) -> dict[int, list[int]]:
        at: dict[int, list[int]] = defaultdict(list)
        for vt, off, t in scans[addr].stores:
            if t and vt in out.by_vtable:
                at[off].append(vt)
        return at
    together: dict[int, Counter] = defaultdict(Counter)
    vptrs: dict[int, Counter] = defaultdict(Counter)
    # Of two vtables as long, the class is the one fewer functions store: a base's is stored by its own constructors
    # and every derived class's (a destructor stores its class's first and its base's last, a constructor the other
    # way round: the order can't say)
    storers = Counter(vt for addr in scans for vts in stored(addr).values() for vt in set(vts))
    for addr, s in scans.items():
        at = stored(addr)
        if not at:
            continue
        mine = {off: max(reversed(vts), key=lambda v: (len(out.by_vtable[v].slots), -storers[v]))
                for off, vts in at.items()}
        first = min(mine)
        cls = out.by_vtable[mine[first]]
        cls.ctors.add(addr)
        vptrs[cls.vtable][first] += 1
        for off, vt in mine.items():
            if off != first:
                cls.others[off - first] = vt
        for callee in s.this_calls:  # a base constructor or destructor it calls, with the same `this`
            callee |= seg.get(addr, 0)
            if callee in scans and callee != addr:
                for vt in stored(callee).get(first, ()):
                    at[first].append(vt)
        for vt in at[first]:
            if vt != cls.vtable:
                together[cls.vtable][vt] += 1
    for vt, offs in vptrs.items():
        out.by_vtable[vt].vptr = offs.most_common(1)[0][0]
    for vt, cls in out.by_vtable.items():
        bases = [b for b in together[vt] if len(out.by_vtable[b].slots) <= len(cls.slots)]
        if bases:  # the closest ancestor: the longest vtable; then the one with most of the same functions
            cls.parent = max(bases, key=lambda b: (len(out.by_vtable[b].slots),
                                                   sum(x == y for x, y in zip(out.by_vtable[b].slots, cls.slots))))
    for vt, cls in out.by_vtable.items():
        for i, f in enumerate(cls.slots):
            out.roles[f].append((vt, f"slot {i}"))
        for c in cls.ctors:
            out.roles[c].append((vt, "constructor or destructor"))
    return out


def add_fields(found: Classes, scans: dict[int, _Scan]) -> None:
    """Each class's fields, from its virtual functions and constructors (an inherited one's fields are the base's:
    the class has them too, at the same offsets), then its base classes' fields: a derived object has them all."""
    for cls in found.by_vtable.values():
        for f in set(cls.slots) | cls.ctors:
            for off, size, kind, ptr in scans[f].accesses if f in scans else ():
                if off < 0 or off == cls.vptr or off >= 0x10000:
                    continue
                fl = cls.fields.get(off)
                if fl is None:
                    fl = cls.fields[off] = Field(off, size, kind)
                elif size > fl.size and not ptr:
                    fl.size = size
                if kind and (not fl.kind or kind in ("f32", "f64")):
                    fl.kind = kind
                fl.pointer |= ptr and fl.size == 4
                fl.users.add(f)
    done: set[int] = set()

    def inherit(cls: Class, seen: frozenset) -> None:
        if cls.vtable in done:
            return
        parent = found.by_vtable.get(cls.parent) if cls.parent is not None else None
        if parent is not None and parent.vtable not in seen:
            inherit(parent, seen | {cls.vtable})
            for off, fl in parent.fields.items():
                if off not in cls.fields and off != cls.vptr:
                    cls.fields[off] = Field(fl.offset, fl.size, fl.kind, fl.pointer, set(fl.users))
        done.add(cls.vtable)

    for cls in found.by_vtable.values():
        inherit(cls, frozenset())


def listing(found: Classes, label: Callable[[int], str], shown: int = 60) -> str:
    """get_class() with nothing asked: every class, largest first."""
    if not found.by_vtable:
        return ("No C++ classes found: no function stores a vtable (a table of function addresses) into an object."
                if found.reads_fields else f"No C++ classes found in {found.found_from}.")
    rows = sorted(found.by_vtable.values(), key=lambda c: (-len(c.slots), c.vtable))
    fields = (lambda c: f", {len(c.fields)} fields seen, at least 0x{c.size_seen():X} bytes") if found.reads_fields \
        else (lambda c: "")
    return (f"{len(rows)} classes, found from {found.found_from} (largest first; get_class(vtable or function) has "
            f"one's fields and virtual functions):\n"
            + "\n".join(f"- {label(c.vtable)} (0x{c.vtable:08X}): {len(c.slots)} virtual functions{fields(c)}"
                        + (f", base {label(c.parent)}" if c.parent is not None else
                           f", base {c.bases[0]}" if c.bases else "") for c in rows[:shown]))


def brief_lines(found: Classes | None, addr: int, label: Callable[[int], str]) -> list[str]:
    """What get_function says about a virtual function, constructor or destructor: its class and the fields of
    `this` it uses, so agents write this->field at the right offsets."""
    roles = found.roles.get(addr) if found else None
    if not roles:
        return []
    lines = ["", f"### C++ class (found from {found.found_from}; get_class has the whole layout)"]
    for vt, role in roles[:3]:
        cls = found.by_vtable[vt]
        used = [f"+0x{o:X} {f.ctype()}" for o, f in sorted(cls.fields.items()) if addr in f.users]
        base = (f", base class {label(cls.parent)}" if cls.parent is not None else
                f", base class {cls.bases[0]}" if cls.bases else "")
        lines.append(f"- {role} of {label(vt)} ({len(cls.slots)} virtual functions{base}); vtable pointer at "
                     f"+0x{cls.vptr:X}" + (f"; this function uses {', '.join(used[:16])}" if used else ""))
    return lines


def owner(found: Classes | None, addr: int) -> tuple[Class | None, str]:
    """The class a function is a method of, and what it is there ("virtual function 3"), when that's sure: one class,
    or one class whose subclasses inherit it (a virtual function a subclass doesn't override is in its vtable too, at
    the same slot). (None, "") otherwise."""
    roles = found.roles.get(addr, []) if found else []
    owners = {vt for vt, _ in roles}
    owners = {vt for vt in owners if found.by_vtable[vt].parent not in owners}
    if len(owners) != 1:
        return None, ""
    cls = found.by_vtable[owners.pop()]
    role = next(r for vt, r in roles if vt == cls.vtable)
    return cls, (f"virtual function {role.removeprefix('slot ')}" if role.startswith("slot") else
                 "a constructor or destructor")


def describe(cls: Class, classes: Classes, label: Callable[[int], str], focus: int | None = None) -> str:
    """The layout as agents read it: fields by offset, then the virtual functions by slot."""
    name = label(cls.vtable)
    parent = classes.by_vtable.get(cls.parent) if cls.parent is not None else None
    lines = [f"class {name}: vtable at 0x{cls.vtable:08X}, {len(cls.slots)} virtual functions from +0x{cls.header:X}"
             + (f"; base class {label(parent.vtable)} ({len(parent.slots)} virtual functions)" if parent else "")]
    if cls.bases:
        lines.append("base classes (every one, the direct base first): " + ", ".join(cls.bases))
    if cls.ctors:
        lines.append("stored by (constructors, destructors): " + ", ".join(label(c) for c in sorted(cls.ctors)[:12]))
    for off, other in sorted(cls.others.items()):
        lines.append(f"a second vtable at +0x{off:X}: {label(other)} (a second base class)")
    if not classes.reads_fields:
        lines += ["", f"fields: +0x{cls.vptr:02X} is the vtable pointer; the others aren't read from this processor's "
                      "code yet: the offsets its functions use through `this` are its fields"]
    else:
        lines.append(f"size: at least 0x{cls.size_seen():X} bytes (the furthest field its functions touch)")
        lines += ["", "fields (offset: type, the functions that use it):", f"  +0x{cls.vptr:02X}: vtable pointer"]
    inherited = parent.fields if parent else {}
    for off in sorted(cls.fields):
        fl = cls.fields[off]
        users = sorted(fl.users)
        here = " ← this function" if focus in fl.users else ""
        base = f" (also in {label(parent.vtable)})" if off in inherited else ""
        lines.append(f"  +0x{off:02X}: {fl.ctype()}, {len(users)} function{'s' * (len(users) != 1)} "
                     f"({', '.join(label(u) for u in users[:4])}{', ...' if len(users) > 4 else ''}){base}{here}")
    lines += ["", "virtual functions (slot: offset in the vtable, function; a virtual call loads vtable+offset):"]
    pslots = parent.slots if parent else []
    for i, f in enumerate(cls.slots):
        how = ("inherited" if i < len(pslots) and pslots[i] == f else "overrides" if i < len(pslots) else "new")
        lines.append(f"  {i} (+0x{cls.header + cls.stride * i:X}): {label(f)}, {how}" + (" ← this function" if f == focus else ""))
    return "\n".join(lines)
