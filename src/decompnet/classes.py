"""C++ classes, recovered from the game's own machine code (MIPS: PS2, N64).

Agents matched 7% of GTA San Andreas's functions that make virtual calls against 62% of the rest: without the class
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
# Where the virtual functions start in a vtable: after two header words for g++ with -fvtable-thunks (an offset and
# the type-info function) and for CodeWarrior. (Not measured from virtual calls: C's tables of function pointers, as
# in RenderWare, are called the same way from offset 0.)
_HEADER = 8


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
        names = {1: "8", 2: "16", 4: "32", 8: "64", 16: "128"}
        if self.kind in ("s8", "s16", "s32"):
            return f"s{names[self.size]}"
        return f"u{names.get(self.size, str(8 * self.size))}" if self.size in names else f"u8[{self.size}]"


@dataclass
class Class:
    vtable: int  # the address constructors store (the vtable's start; virtual functions begin `header` bytes in)
    header: int
    slots: list[int]  # the virtual functions, in slot order
    vptr: int = 0  # where the object keeps its vtable pointer
    ctors: set[int] = field(default_factory=set)  # functions that store this vtable into `this`
    parent: int | None = None
    others: dict[int, int] = field(default_factory=dict)  # this+offset -> a second vtable stored there (multiple bases)
    fields: dict[int, Field] = field(default_factory=dict)

    def size_seen(self) -> int:
        return max([f.offset + f.size for f in self.fields.values()] + [self.vptr + 4])


@dataclass
class Classes:
    by_vtable: dict[int, Class] = field(default_factory=dict)
    roles: dict[int, list[tuple[int, str]]] = field(default_factory=lambda: defaultdict(list))  # function -> (vtable, role)

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


def recover(functions: Iterable[tuple[int, bytes, list]], read: Callable[[int, int], bytes | None],
            big_endian: bool) -> Classes:
    """Classes from (address, code, relocations) of every function and a reader of the program's data."""
    funcs = list(functions)
    starts = {addr for addr, _, _ in funcs}
    scans = {addr: scan(code, relocs, big_endian) for addr, code, relocs in funcs}
    e = ">" if big_endian else "<"
    out = Classes()
    for addr, s in scans.items():
        for vt, _, _ in s.stores:
            if vt in out.by_vtable:
                continue
            data = read(vt + _HEADER, 4 * _MAX_SLOTS) or b""
            slots = []
            for (w,) in struct.iter_unpack(e + "I", data[:len(data) // 4 * 4]):
                if w not in starts:
                    break
                slots.append(w)
            if slots:
                out.by_vtable[vt] = Class(vt, _HEADER, slots)
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
    for addr, s in scans.items():
        at = stored(addr)
        if not at:
            continue
        mine = {off: max(reversed(vts), key=lambda v: len(out.by_vtable[v].slots)) for off, vts in at.items()}
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
    # fields, from the virtual functions and constructors (an inherited one's fields are the base's: the class has
    # them too, at the same offsets)
    for vt, cls in out.by_vtable.items():
        for i, f in enumerate(cls.slots):
            out.roles[f].append((vt, f"slot {i}"))
        for c in cls.ctors:
            out.roles[c].append((vt, "constructor or destructor"))
        for f in set(cls.slots) | cls.ctors:
            for off, size, kind, ptr in scans[f].accesses if f in scans else ():
                if off < 0 or off == cls.vptr or off >= 0x10000:
                    continue
                fl = cls.fields.get(off)
                if fl is None:
                    fl = cls.fields[off] = Field(off, size, kind)
                elif size > fl.size and not ptr:
                    fl.size = size
                if kind and (not fl.kind or kind == "f32"):
                    fl.kind = kind
                fl.pointer |= ptr and fl.size == 4
                fl.users.add(f)
    return out


def describe(cls: Class, classes: Classes, label: Callable[[int], str], focus: int | None = None) -> str:
    """The layout as agents read it: fields by offset, then the virtual functions by slot."""
    name = label(cls.vtable)
    parent = classes.by_vtable.get(cls.parent) if cls.parent is not None else None
    lines = [f"class {name}: vtable at 0x{cls.vtable:08X}, {len(cls.slots)} virtual functions from +0x{cls.header:X}"
             + (f"; base class {label(parent.vtable)} ({len(parent.slots)} virtual functions)" if parent else "")]
    if cls.ctors:
        lines.append("stored by (constructors, destructors): " + ", ".join(label(c) for c in sorted(cls.ctors)[:12]))
    for off, other in sorted(cls.others.items()):
        lines.append(f"a second vtable at +0x{off:X}: {label(other)} (a second base class)")
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
        lines.append(f"  {i} (+0x{cls.header + 4 * i:X}): {label(f)}, {how}" + (" ← this function" if f == focus else ""))
    return "\n".join(lines)
