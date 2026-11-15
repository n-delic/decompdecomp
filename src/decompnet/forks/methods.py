"""Structs and classes of a PC or Xbox program (Visual C++), recovered from its machine code: which functions are
member functions, of which object, and the fields of the object they touch.

Agents wrote `Accum::Add` (an Xbox test program built with link-time code generation) as a free function taking an
`Accum *`, because nothing said it was a member function, and guessed its fields. The code says most of it:
- a member function gets `this` in a register (ECX under __thiscall; with LTCG whichever register the linker chose,
  forks/convention.py finds the registers a function reads before writing) and uses it as the base of loads and
  stores at fixed offsets: those are the fields (offset, width, float or integer from the instruction);
- a register that walks (`add esi, 4`) is an array being iterated, not an object, and a function that uses two
  registers at the same offsets is a function of two objects (`Vec3Dot(a, b)`): neither is taken for a method;
- functions belong to one object type when they share evidence: a constructor or destructor stores the address of a
  table of functions into this+0 (a vtable: the class's virtual functions, in slot order), a function sits in that
  table, callers pass them the same global object (`mov ecx, offset g` right before the call), or one passes its own
  `this` on to the other;
- a virtual call through this is `mov eax, [this]; call [eax+N]`: slot N/4.

`class` is used only with that kind of evidence (a vtable stored by a constructor, a base class's vtable stored
first); everything else is a `struct` with member functions, as plain data with methods is written in C++. None of it
is something the judge trusts: it is where agents start, and every match is still decided byte for byte.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import capstone
from capstone import x86

from .convention import _PARENT, ARGS, SCRATCH, Program, _rw, _setup, instructions

FLOAT = {"fld", "fst", "fstp", "fadd", "fsub", "fsubr", "fmul", "fdiv", "fdivr", "fcom", "fcomp", "fild", "fist",
         "fistp", "fadd", "fsubp", "faddp", "fmulp", "fdivp", "fdivrp", "fsubrp", "movss", "addss", "subss", "mulss",
         "divss", "comiss", "ucomiss", "cvtss2sd", "cvtss2si", "cvttss2si", "minss", "maxss", "sqrtss", "rcpss",
         "rsqrtss", "movsd", "addsd", "subsd", "mulsd", "divsd", "comisd", "ucomisd", "cvtsd2ss"}
_INTEGER_FPU = {"fild", "fist", "fistp"}  # x87 instructions whose memory operand is an integer
_MAX_FIELD = 0x10000  # an offset further than this from a register is an address, not a field
_MAX_SLOTS = 400
_PREFER = ("ecx", "esi", "edi", "ebx", "eax", "edx")  # __thiscall's register first, then LTCG's usual choices


@dataclass
class Field:
    offset: int
    size: int
    kind: str = ""  # "float" (x87 or SSE), "" (an integer, or a width only)
    pointer: bool = False  # loaded and then used as an address
    users: set[int] = field(default_factory=set)

    def ctype(self) -> str:
        if self.pointer and self.size == 4:
            return "void *"
        if self.kind == "float":
            return "f64" if self.size == 8 else "f32"
        return {1: "u8", 2: "u16", 4: "s32", 8: "u64"}.get(self.size, f"u8[{self.size}]" if self.size else "u8")


@dataclass
class _Scan:
    """What one function does with each register it takes, in one pass in address order (branches aren't followed:
    a register's meaning holds until it is overwritten)."""
    accesses: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)  # register -> (offset, size, kind)
    writes: dict[str, set[int]] = field(default_factory=dict)  # register -> offsets stored through it
    pointers: dict[str, set[int]] = field(default_factory=dict)  # register -> offsets loaded, then used as addresses
    walks: set[str] = field(default_factory=set)  # registers stepped (add/sub/inc/lea r, [r+N]): an array
    vcalls: dict[str, set[int]] = field(default_factory=dict)  # register -> vtable offsets called through this
    stores: list[tuple[int, tuple, int]] = field(default_factory=list)  # (address stored, into what, at offset)
    calls: list[tuple[int, dict, int]] = field(default_factory=list)  # (callee, {register: what it holds}, where)


@dataclass
class Vtable:
    addr: int
    slots: list[int]


@dataclass
class Group:
    """One object type: a struct with member functions, or a class (when there is a vtable)."""
    name: str
    methods: dict[int, str] = field(default_factory=dict)  # function -> the register `this` arrives in
    vtables: list[Vtable] = field(default_factory=list)  # its own first; more for multiple bases
    ctors: set[int] = field(default_factory=set)  # functions that store its vtable into `this`
    objects: set[int] = field(default_factory=set)  # global objects its functions are called on
    parent: str | None = None  # a base class, from a constructor storing two vtables
    fields: dict[int, Field] = field(default_factory=dict)
    vcalls: set[int] = field(default_factory=set)  # vtable offsets its functions call through this
    why: list[str] = field(default_factory=list)

    @property
    def keyword(self) -> str:
        return "class" if self.vtables else "struct"

    def size_seen(self) -> int:
        return max([f.offset + max(f.size, 1) for f in self.fields.values()] + [4 if self.vtables else 0])


@dataclass
class Objects:
    groups: list[Group] = field(default_factory=list)
    of: dict[int, Group] = field(default_factory=dict)  # function -> its group
    this: dict[int, str] = field(default_factory=dict)  # function -> the register `this` arrives in
    ambiguous: dict[int, list[str]] = field(default_factory=dict)  # function -> registers used alike (two objects)

    def find(self, addr: int) -> Group | None:
        if addr in self.of:
            return self.of[addr]
        return next((g for g in self.groups if any(v.addr == addr for v in g.vtables) or addr in g.objects), None)


def _base(ins, op) -> str | None:
    return _PARENT.get(ins.reg_name(op.mem.base)) if op.mem.base else None


def scan(prog: Program, addr: int, regs: list[str], ltcg: bool = True) -> _Scan:
    """Follow the registers `regs` hold at entry (the function's register parameters) through its code. `ltcg`: a
    callee may keep EAX, ECX or EDX alive and change EBX, ESI or EDI (its convention says which); else every call
    follows the standard rules."""
    out = _Scan()
    try:
        ins_list = instructions(prog.code(addr), addr)
    except (capstone.CsError, KeyError):
        return out
    loops = [(i.operands[0].imm, i.address) for i in ins_list if x86.X86_GRP_JUMP in i.groups and i.operands
             and i.operands[0].type == x86.X86_OP_IMM and addr <= i.operands[0].imm <= i.address]

    def in_loop(at: int) -> bool:
        """A register stepped inside a loop walks an array (stepped once, it's a member's address being made)."""
        return any(lo <= at <= hi for lo, hi in loops)

    tag: dict[str, tuple] = {r: ("arg", r) for r in regs}
    starts = prog.functions
    delta: int | None = 0  # esp relative to its value at entry (locals are identified by it)
    frame = 0  # a new count after an aligned frame's `and esp, -16`
    jumped: dict[int, tuple[dict, int | None]] = {}  # jump target -> the registers' meanings when jumping there
    ended = False  # the instruction before was a ret or a jmp: nothing falls through
    for i in ins_list:
        m, ops = i.mnemonic, i.operands
        if ended and i.address in jumped:
            # code after an early return (`pop edi; ret 8` on an error path) is reached by a jump: what the
            # registers held there, not what the return's pops left
            tag, delta = dict(jumped[i.address][0]), jumped[i.address][1]
        ended = m in ("ret", "jmp")
        if x86.X86_GRP_JUMP in i.groups and ops and ops[0].type == x86.X86_OP_IMM and ops[0].imm > i.address:
            jumped.setdefault(ops[0].imm, (dict(tag), delta))
        mems = [op for op in ops if op.type == x86.X86_OP_MEM]
        is_float = m in FLOAT or any(op.type == x86.X86_OP_REG and i.reg_name(op.reg).startswith("xmm") for op in ops)
        for op in mems:
            b = _base(i, op)
            t = tag.get(b) if b else None
            disp = op.mem.disp
            if (m == "mov" and op is ops[0] and len(ops) == 2 and ops[1].type == x86.X86_OP_IMM and op.size == 4
                    and not op.mem.index and b not in (None, "esp", "ebp") and 0 <= disp < _MAX_FIELD):
                # an address stored into an object (a constructor's vtable store, inlined or not)
                out.stores.append((ops[1].imm & 0xFFFFFFFF, t or ("reg", b, i.address), disp))
            if t is None:
                continue
            if t[0] == "arg" and not op.mem.index and m != "lea" and 0 <= disp < _MAX_FIELD:
                kind = "float" if is_float and m not in _INTEGER_FPU else ""
                out.accesses.setdefault(t[1], []).append((disp, op.size, kind))
                if op.access & capstone.CS_AC_WRITE:
                    out.writes.setdefault(t[1], set()).add(disp)
            elif t[0] == "load":
                out.pointers.setdefault(t[1], set()).add(t[2])
                if t[2] == 0 and m in ("call", "jmp"):
                    out.vcalls.setdefault(t[1], set()).add(disp)
        target = ops[0].imm & 0xFFFFFFFF if m in ("call", "jmp") and ops and ops[0].type == x86.X86_OP_IMM else None
        if m == "call" and ops and ops[0].type == x86.X86_OP_REG and tag.get(i.reg_name(ops[0].reg), ("",))[0] == "vfn":
            t = tag[i.reg_name(ops[0].reg)]
            out.vcalls.setdefault(t[1], set()).add(t[2])
        if target is not None and target in starts and target != addr:
            out.calls.append((target, {r: tag[r] for r in ARGS if r in tag}, i.address))
        # what this instruction leaves in the registers it writes
        _, wr = _rw(i)
        new = None
        dst = ops[0] if ops and ops[0].type == x86.X86_OP_REG else None
        dname = i.reg_name(dst.reg) if dst is not None else None
        if dst is not None and dst.size == 4 and len(ops) == 2:
            src = ops[1]
            if m == "mov" and src.type == x86.X86_OP_REG:
                new = tag.get(i.reg_name(src.reg))
            elif m == "mov" and src.type == x86.X86_OP_IMM:
                v = src.imm & 0xFFFFFFFF
                if prog.pe.contains(v) and v not in starts:
                    new = ("obj", v)
            elif src.type == x86.X86_OP_MEM and not src.mem.index:
                b = _base(i, src)
                t = tag.get(b) if b else None
                if b == "esp" and m == "lea" and delta is not None:
                    new = ("stk", frame, delta + src.mem.disp)  # a local object
                elif t and t[0] in ("arg", "sub") and m == "lea":
                    new = t if src.mem.disp == 0 else ("sub", t[1], src.mem.disp + (t[2] if t[0] == "sub" else 0))
                    if b == dname and src.mem.disp and t[0] == "arg" and in_loop(i.address):
                        out.walks.add(t[1])
                elif t and t[0] == "arg" and m == "mov":
                    new = ("load", t[1], src.mem.disp)
                elif t and t[0] == "load" and t[2] == 0 and m == "mov":
                    new = ("vfn", t[1], src.mem.disp)
            elif m in ("add", "sub") and dname in tag and tag[dname][0] == "arg" and in_loop(i.address):
                out.walks.add(tag[dname][1])
        elif m in ("inc", "dec") and dname in tag and tag[dname][0] == "arg" and in_loop(i.address):
            out.walks.add(tag[dname][1])
        for r in wr:
            tag.pop(r, None)
        if new is not None and dname is not None:
            tag[dname] = new
        if delta is not None:  # (a shortcut: one pass in address order, as for the registers)
            if m == "push":
                delta -= 4
            elif m == "pop":
                delta += 4
            elif m in ("sub", "add") and dname == "esp" and len(ops) == 2 and ops[1].type == x86.X86_OP_IMM:
                delta += -ops[1].imm if m == "sub" else ops[1].imm
            elif m == "and" and dname == "esp":
                delta, frame = 0, frame + 1  # an aligned frame: locals are counted from the aligned esp
            elif "esp" in wr and m not in ("call", "ret"):
                delta = None
        if m == "call":
            known = prog._reads(target) if target is not None and ltcg else None
            for r in (known[1] if known is not None else SCRATCH):
                tag.pop(r, None)
            if target is not None and target in starts:
                tag["eax"] = ("val", i.address)  # what it returns: one object, as long as a register holds it
                if delta is not None:
                    try:
                        delta += prog.convention(target).ret or 0  # the callee pops its stack arguments
                    except (capstone.CsError, KeyError):
                        delta = None
    return out


def _this(s: _Scan, regs: list[str]) -> tuple[str | None, list[str]]:
    """The register that carries `this`, or None; and the registers used alike (a function of two objects)."""
    def weight(r: str) -> int:
        return len(s.accesses.get(r, ())) + len(s.vcalls.get(r, ()))
    used = [r for r in regs if weight(r) and r not in s.walks]
    if not used:
        return None, []
    # the object whose fields a function changes is its own (a function reading a const input more than it touches
    # `this`, storing only into `this`); not an output parameter's single word (GetTextExtent(..., float *w));
    # then the one it uses most
    best = max(used, key=lambda r: (len(s.writes.get(r, ())) > 1, weight(r), -_PREFER.index(r)))
    mine = {o for o, _, _ in s.accesses.get(best, ())}
    alike = [r for r in used if r != best and mine and
             len(mine & {o for o, _, _ in s.accesses.get(r, ())}) * 2 >= len(mine)]
    return best, alike


def _vtable(prog: Program, addr: int, others: set[int] = frozenset()) -> list[int]:
    """The functions at `addr`, in order, while they are functions and until another of the `others` tables
    begins: a vtable's slots (MSVC: from its start)."""
    data = prog.pe.read(addr, 4 * _MAX_SLOTS) or b""
    slots = []
    for k in range(0, len(data) - 3, 4):
        w = int.from_bytes(data[k:k + 4], "little")
        if w not in prog.functions or (k and addr + k in others):
            break
        slots.append(w)
    return slots


def _loads_ecx(prog: Program, caller: int, off: int) -> bool:
    """Whether the call site sets ECX for the callee: loads it right before the call and doesn't push it (a
    pushed ECX is a stack argument that went through a register)."""
    setup, regs = _setup(prog, caller, off)
    if "ecx" not in regs:
        return False
    k = max(n for n, i in enumerate(setup) if "ecx" in _rw(i)[1])
    return not any(i.mnemonic == "push" and i.op_str in ("ecx", "cx") for i in setup[k + 1:])


class _Union:
    def __init__(self):
        self.up: dict = {}
        self.vt: dict = {}  # root -> its vtable (two classes never merge)

    def root(self, x):
        self.up.setdefault(x, x)
        while self.up[x] != x:
            self.up[x] = self.up[self.up[x]]
            x = self.up[x]
        return x

    def join(self, a, b) -> bool:
        ra, rb = self.root(a), self.root(b)
        if ra == rb:
            return True
        va, vb = self.vt.get(ra), self.vt.get(rb)
        if va is not None and vb is not None and va != vb:
            return False
        self.up[rb] = ra
        if va is None and vb is not None:
            self.vt[ra] = vb
        return True


def recover(prog: Program, ltcg: bool, skip: set[int] = frozenset()) -> Objects:
    """Every function of the program that looks like a member function, grouped by the object type it belongs to.
    `ltcg`: the program was built with link-time code generation (`this` can arrive in any register the convention
    reads); else only ECX (__thiscall). `skip`: library code."""
    with prog.lock:  # once per program, not once per agent claiming at the same moment
        memo = prog.__dict__.setdefault("_objects", {})
        key = (ltcg, frozenset(skip))
        if key not in memo:
            memo[key] = _recover(prog, ltcg, skip)
        return memo[key]


def _recover(prog: Program, ltcg: bool, skip: set[int]) -> Objects:
    out = Objects()
    scans: dict[int, _Scan] = {}
    regs_of: dict[int, list[str]] = {}
    for a in sorted(prog.functions):
        if a in skip:
            continue
        try:
            reads = prog.convention(a).reads
        except (capstone.CsError, KeyError):
            continue
        regs = [r for r in reads if r in ARGS and (ltcg or r == "ecx")]
        regs_of[a] = regs
        scans[a] = scan(prog, a, regs, ltcg)
        this, alike = _this(scans[a], regs)
        if alike:
            out.ambiguous[a] = [this, *alike]
        elif this:
            out.this[a] = this

    def node(a: int, t: tuple) -> tuple:
        """What a register of function `a` holds, as one object: its own `this`, a global, or a value of its own
        (a local, what a call returned, a member at this+N)."""
        if t[0] == "arg" and out.this.get(a) == t[1]:
            return ("f", a)
        if t[0] == "obj":
            return ("obj", t[1])
        if t[0] in ("sub", "load") and out.this.get(a) == t[1]:
            return ("m" if t[0] == "sub" else "p", a, t[2])
        return ("v", a, t)

    # vtables: tables of functions stored at +0 of an object (by constructors and destructors, inlined or not); one
    # ends where the next begins (Visual C++ puts them side by side when there's no RTTI)
    cands = {vt for s in scans.values() for vt, _, off in s.stores if off == 0 and vt not in prog.functions
             and int.from_bytes(prog.pe.read(vt, 4) or b"\0\0\0\0", "little") in prog.functions}
    tables: dict[int, list[int]] = {}
    for vt in cands:
        tables[vt] = _vtable(prog, vt, cands)
    stored: dict[int, list[tuple[int, int]]] = {}  # function -> (vtable, offset), into its own `this`
    u = _Union()
    for vt in tables:
        u.vt[u.root(("vt", vt))] = vt
    parents: dict[int, Counter] = {}
    for a, s in scans.items():
        here: dict[tuple, list[int]] = {}
        for vt, t, off in s.stores:
            if vt in tables and off == 0:
                here.setdefault(node(a, t), []).append(vt)
            if vt in tables and t[0] == "arg" and t[1] in regs_of[a]:
                stored.setdefault(a, []).append((vt, off))
                out.this.setdefault(a, t[1])
                out.ambiguous.pop(a, None)
        for n, vts in here.items():
            if n[0] == "v" and n[2][0] == "arg" and n[2][1] in regs_of[a]:
                n = ("f", a)  # a constructor whose `this` showed no other use
            own = max(reversed(vts), key=lambda v: len(tables[v]))  # the most derived: the longest table
            u.join(("vt", own), n)
            for vt in vts:
                if vt != own and len(tables[vt]) <= len(tables[own]):
                    parents.setdefault(own, Counter())[vt] += 1
    homes: dict[int, list[int]] = {}
    for vt, slots in tables.items():
        for f in slots:
            homes.setdefault(f, []).append(vt)
    for f, vts in homes.items():
        if f in skip:
            continue
        # a function in several tables (an inherited one, a folded destructor) goes with the base: the shortest
        home = min(vts, key=lambda v: (len(tables[v]), v))
        if u.join(("vt", home), ("f", f)):
            out.this.setdefault(f, "ecx")  # called through a vtable: always __thiscall

    # the same global object, and `this` passed on (to a method of the same object, or a base class's)
    if not ltcg:
        # a __thiscall function that never uses `this` still gets it: its callers load ECX right before the call
        # (a __cdecl or __stdcall call has no reason to)
        for c in prog.functions:
            if c in out.this or c in skip or c in out.ambiguous or "ecx" in prog.convention(c).reads:
                continue
            sites = prog.calls.get(c, [])
            loaded = sum(_loads_ecx(prog, caller, off) for caller, off in sites[:8])
            if sites and loaded * 2 > min(len(sites), 8):
                out.this[c] = "ecx"
    for _ in range(3):
        grew = False
        for a, s in scans.items():
            for callee, held, _at in s.calls:
                r = out.this.get(callee)
                if r is None or r not in held:
                    continue
                t = held[r]
                if t[0] == "arg" and a not in out.this and t[1] in regs_of[a] and a not in out.ambiguous:
                    out.this[a] = t[1]  # it only passes `this` on: a method too (a wrapper, an inlined part)
                    grew = True
                u.join(node(a, t), ("f", callee))
        # a member object (this+N) or one a pointer field points to (this->field) is of one type in every function
        # of a type
        seen: dict = {}
        for n in list(u.up):
            if n[0] in ("m", "p"):
                k = (n[0], u.root(("f", n[1])), n[2])
                if k in seen and u.root(seen[k]) != u.root(n):
                    grew |= u.join(seen[k], n)
                seen.setdefault(k, n)
        if not grew:
            break

    # the groups
    by_root: dict = {}
    for a, r in sorted(out.this.items()):
        root = u.root(("f", a))
        g = by_root.get(root)
        if g is None:
            vt = u.vt.get(root)
            g = by_root[root] = Group(f"Class_{vt:08X}" if vt is not None else f"Struct_{a:08X}")
            if vt is not None:
                g.vtables.append(Vtable(vt, tables[vt]))
            out.groups.append(g)
        g.methods[a] = r
        out.of[a] = g
    for node in list(u.up):
        if node[0] == "obj" and u.root(node) in by_root:
            by_root[u.root(node)].objects.add(node[1])
    names = {vt: g.name for g in out.groups for vt in (t.addr for t in g.vtables)}
    for own, cnt in parents.items():
        g = next((x for x in out.groups if x.vtables and x.vtables[0].addr == own), None)
        best = max(cnt, key=lambda v: (len(tables[v]), cnt[v]))
        if g is not None:
            g.parent = names.get(best, f"Class_{best:08X}")
    for g in out.groups:
        for a, r in g.methods.items():
            s = scans.get(a)
            for vt, off in stored.get(a, ()):
                if off == 0 and g.vtables and vt == g.vtables[0].addr:
                    g.ctors.add(a)
            if s is None:
                continue
            g.vcalls |= s.vcalls.get(r, set())
            ptrs = s.pointers.get(r, set())
            for off, size, kind in s.accesses.get(r, ()):
                if g.vtables and off == 0:
                    continue  # the vtable pointer
                fl = g.fields.get(off)
                if fl is None:
                    fl = g.fields[off] = Field(off, size, kind)
                elif size > fl.size:
                    fl.size = size
                if kind and not fl.kind:
                    fl.kind = kind
                fl.pointer |= off in ptrs
                fl.users.add(a)
        g.why = _why(g)
    out.groups.sort(key=lambda g: (-len(g.methods), g.name))
    return out


def _why(g: Group) -> list[str]:
    why = []
    if g.vtables:
        why.append(f"a vtable at 0x{g.vtables[0].addr:08X} with {len(g.vtables[0].slots)} virtual functions"
                   + (f", stored by {len(g.ctors)} constructor{'s' * (len(g.ctors) != 1)} or destructors"
                      if g.ctors else ""))
    if g.objects:
        why.append("called on the global object" + "s" * (len(g.objects) > 1) + " "
                   + ", ".join(f"D_{o:08X}" for o in sorted(g.objects)[:4]))
    if len(g.methods) > 1 and not g.objects:
        why.append("called on the same objects (one passes its `this` on, or a caller calls them on one local, "
                   "member or returned pointer; a base class's functions or a member at +0 join it the same way)")
    if not why:
        why.append("one function using a register parameter as the base of field accesses")
    return why


# ------------------------------------------------------------------------------------------- what agents read


def _c_name(symbol: str) -> str:
    """A function's name as source writes it: `_func_X@8` -> func_X, `?Update@C_vt_003A94FC@@UAEX...` -> Update
    (the `?` of a C++ name used to stay: a brief told an agent to write `Struct_0015F270::?Update`)."""
    return symbol.lstrip("?_@").split("@", 1)[0]


# MSVC's undecorated name of a member function: "public: virtual void __thiscall C_vt_003A94FC::Update(struct
# CCamOut *,float)"
_DEMANGLED_METHOD = re.compile(r"^(?:(?:public|protected|private): )?(?:(virtual|static) )?(.*?)\s*"
                               r"(?:__thiscall|__cdecl|__stdcall|__fastcall)\s+([\w:<>~]+)::(~?\w+)\((.*)\)"
                               r"(\s*const)?\s*$")


def known_signature(demangled: str | None) -> tuple[str, str] | None:
    """(class, declaration) of a member function whose symbol says its real signature: the brief gives that, not a
    guess from the registers (it suggested `int Struct_0015F270::?Update(int, int)` for what the symbol said was
    `virtual void C_vt_003A94FC::Update(CCamOut *, float)`)."""
    m = _DEMANGLED_METHOD.match((demangled or "").strip())
    if m is None:
        return None
    virt, ret, cls, name, args, const = m.groups()
    args = re.sub(r"\b(?:struct|class|enum|union) ", "", args)
    args = ", ".join(re.sub(r"\s*\*", " *", a.strip()) for a in args.split(",")) if args.strip() not in ("", "void") else ""
    ret = re.sub(r"\b(?:struct|class|enum|union) ", "", ret).strip()
    head = f"{virt + ' ' if virt else ''}{ret + ' ' if ret else ''}"
    return cls, f"{head}{cls}::{name}({args}){' const' if const else ''}"


def params(prog: Program, addr: int, this: str | None) -> str:
    """A declaration's parameters from the convention: LTCG's registers named after them, then the stack
    arguments (`ret N` pops N bytes of them)."""
    conv = prog.convention(addr)
    args = [f"int {r}" for r in conv.reads if r in ARGS and r != this and (r not in ("ecx", "edx") or this != "ecx")]
    slots = sorted({n for n in conv.stack if 4 <= n <= 64 and n % 4 == 0} | set(range(4, 4 + (conv.ret or 0), 4)))
    args += [f"int arg{n // 4}" for n in slots]
    return ", ".join(args) or "void"


def declaration(prog: Program, g: Group) -> str:
    """A C++ declaration to start from: the fields by offset (gaps padded), then the member functions."""
    head = f"{g.keyword} {g.name}" + (f" : public {g.parent}" if g.parent else "")
    lines = [head + " {"]
    if g.keyword == "class":
        lines.append("public:")
    pos = 4 if g.vtables and not g.parent else 0
    if g.vtables and not g.parent:
        lines.append("    // +0x00: the vtable pointer (the compiler adds it: declare the virtual functions)")
    for off in sorted(g.fields):
        fl = g.fields[off]
        if off < pos:
            lines.append(f"    // +0x{off:02X}: also read as {fl.ctype()} (a union, or a smaller part of the field above)")
            continue
        if off > pos:
            lines.append(f"    u8 pad_{pos:02X}[0x{off - pos:X}];")
        t = fl.ctype()
        name = f"field_{off:02X}"
        lines.append(f"    {t.split('[')[0]} {name}{'[' + t.split('[')[1] if '[' in t else ''};  // +0x{off:02X}")
        pos = off + max(fl.size, 1)
    vslots = {f: k for t in g.vtables[:1] for k, f in enumerate(t.slots)}
    for a in sorted(g.methods, key=lambda a: (a not in vslots, vslots.get(a, 0), a)):
        name = _c_name(prog.functions[a]["name"])
        virt = "virtual " if a in vslots else ""
        lines.append(f"    {virt}int {name}({params(prog, a, g.methods[a])});"
                     + (f"  // slot {vslots[a]}" if a in vslots else ""))
    lines.append("};")
    return "\n".join(lines)


def describe(prog: Program, g: Group, focus: int | None = None) -> str:
    """A group as get_class shows it: why, the fields by offset, the member functions, a declaration."""
    lines = [f"{g.keyword} {g.name}: {len(g.methods)} member function{'s' * (len(g.methods) != 1)}; "
             + "; ".join(g.why)]
    if g.parent:
        lines.append(f"base class: {g.parent} (a constructor stores its vtable before this one's)")
    lines.append(f"size: at least 0x{g.size_seen():X} bytes (the furthest field its functions touch)")
    if g.fields:
        lines += ["", "fields (offset: type, the functions that use it):"]
        for off in sorted(g.fields):
            fl = g.fields[off]
            users = sorted(fl.users)
            names = ", ".join(_c_name(prog.functions[u]["name"]) for u in users[:4])
            lines.append(f"  +0x{off:02X}: {fl.ctype()}, {len(users)} function{'s' * (len(users) != 1)} ({names}"
                         f"{', ...' if len(users) > 4 else ''})" + (" ← this function" if focus in fl.users else ""))
    lines += ["", "member functions (this arrives in):"]
    for a in sorted(g.methods):
        role = " (constructor or destructor: stores the vtable)" if a in g.ctors else ""
        lines.append(f"  {_c_name(prog.functions[a]['name'])}: {g.methods[a]}{role}"
                     + (" ← this function" if a == focus else ""))
    for t in g.vtables[:2]:
        lines += ["", f"virtual functions (vtable 0x{t.addr:08X}; a virtual call loads [this], then calls [vtable+4*slot]):"]
        lines += [f"  {k} (+0x{4 * k:X}): {_c_name(prog.functions[f]['name'])}" + (" ← this function" if f == focus else "")
                  for k, f in enumerate(t.slots[:64])]
    if g.vcalls:
        lines.append("virtual calls through this: " + ", ".join(f"+0x{o:X} (slot {o // 4})" for o in sorted(g.vcalls)))
    lines += ["", "A declaration to start from (put it in a header with edit_file; the names are yours to give):",
              "```cpp", declaration(prog, g), "```"]
    return "\n".join(lines)


def brief(prog: Program, objs: Objects, addr: int, ltcg: bool, demangled: str | None = None) -> list[str]:
    """For get_function and claims: whether the function looks like a member function, of what, and how to write it.
    `demangled`: the function's undecorated name, when its symbol has one: then its real class and signature."""
    known = known_signature(demangled)
    if known is not None:
        cls, decl = known
        return ["", f"### A member function of {cls}",
                f"- its symbol gives its real signature: write it as `{decl}`, with {cls} declared in a header "
                f"(edit_file) if it isn't yet. The types are the original's; the parameters' names are yours."]
    if addr in objs.ambiguous:
        regs = objs.ambiguous[addr]
        return ["", "### Two objects alike", f"- it uses {' and '.join(regs)} as bases of the same fields: a function "
                f"of two objects of one type (a free function such as Dot(a, b), or a method taking another of its own "
                f"kind). The code alone doesn't say which; the call sites and the names in a header may."]
    g = objs.of.get(addr)
    if g is None:
        return []
    r = g.methods[addr]
    name = _c_name(prog.functions[addr]["name"])
    used = [f"+0x{o:X} {f.ctype()}" for o, f in sorted(g.fields.items()) if addr in f.users]
    how = ("`this` arrives in ecx (__thiscall: the callee pops its stack arguments)" if r == "ecx" else
           f"`this` arrives in {r}: the linker's choice under link-time code generation, so a member function still "
           f"matches" if ltcg else f"its pointer arrives in {r}")
    lines = ["", f"### Looks like a member function of {g.keyword} {g.name}",
             f"- {how}; evidence: {'; '.join(g.why)}"]
    if used:
        lines.append(f"- it uses this->{', this->'.join(used[:16])}")
    if addr in g.ctors:
        lines.append("- it stores the vtable into this: a constructor or a destructor")
    lines.append(f"- write it as a member function, `int {g.name}::{name}({params(prog, addr, r)})`, with the "
                 f"{g.keyword} declared in a header (edit_file). Name the {g.keyword} as you like; get_class({name}) "
                 f"has its fields, its other functions and a declaration to start from.")
    return lines
