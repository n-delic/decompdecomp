"""How a function of a PC or Xbox program takes its arguments, read off the original's machine code: for games built
with link-time code generation (forks/ltcg.py), whose functions get conventions of the linker's choosing.

- The callee: the registers it reads before writing them on some path from its entry (its register parameters;
  pushes of EBX/ESI/EDI/EBP it pops again are saves, not reads), the stack slots above its return address it reads
  (stack parameters), and `ret N` (the bytes it pops).
- Its callers: at each call site, the instructions just before the call that set EAX, ECX, EDX, EBX, ESI or EDI,
  and the pushes, so a driver can call it the same way and a stand-in can be shaped the same way.
- A program-wide sign of LTCG (`detect`): call sites that load a register the callee reads before writing and
  doesn't save, other than ECX (`this`) and EDX (__fastcall's second argument).

Everything comes from the project's own copy of the program and config/<version>/functions.json (each function's
extent and relocations, as scratch_pe found them).
A register a function only passes on to a direct callee counts as read (the callee's own reads, followed through
call chains up to 40 deep). shortcut: calls through pointers pass nothing on, and frames addressed through EBP
show no stack arguments (`ret N` still says how many bytes it pops).
"""

from __future__ import annotations

import json
import re
import struct
import threading
from dataclasses import dataclass, field
from pathlib import Path

import capstone
from capstone import x86

from ..consoles.pc.scratch import DIR32, REL32, read_pe

ARGS = ("eax", "ecx", "edx", "ebx", "esi", "edi")
GPR = (*ARGS, "ebp")
SAVED = {"ebx", "esi", "edi", "ebp"}
XMM = tuple(f"xmm{i}" for i in range(8))
TRACKED = set(GPR) | set(XMM)
SCRATCH = {"eax", "ecx", "edx", *XMM}  # what a call may change, as far as the caller can count on
_PARENT = {r: r for r in (*GPR, "esp", *XMM)}
for _full, _subs in {"eax": "ax al ah", "ecx": "cx cl ch", "edx": "dx dl dh", "ebx": "bx bl bh", "esi": "si sil",
                     "edi": "di dil", "ebp": "bp bpl", "esp": "sp spl"}.items():
    for _s in _subs.split():
        _PARENT[_s] = _full

_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_md.detail = True


@dataclass
class Convention:
    reads: list[str] = field(default_factory=list)  # registers read before written (parameters)
    stack: list[int] = field(default_factory=list)  # [esp+N] at entry it reads (N >= 4: stack parameters)
    ret: int | None = None  # bytes `ret N` pops; None: no ret (never returns, or only tail calls)
    saves: list[str] = field(default_factory=list)  # callee-saved registers it pushes and pops
    xmm: list[str] = field(default_factory=list)  # XMM registers read before written (float parameters)
    clobbers: list[str] = field(default_factory=list)  # registers it may leave changed (its callees' too)

    def describe(self) -> str:
        regs = ", ".join(self.reads) or "none"
        out = [f"reads before writing: {regs}" + (f" (and {', '.join(self.xmm)})" if self.xmm else "")]
        if self.stack:
            out.append("stack arguments: " + ", ".join(f"[esp+{n}]" for n in self.stack[:8]) +
                       (" ..." if len(self.stack) > 8 else ""))
        out.append("no ret (never returns, or ends in a tail call)" if self.ret is None else
                   f"ret {self.ret}" if self.ret else "ret (the caller pops)")
        if self.saves:
            out.append(f"saves {', '.join(self.saves)}")
        return "; ".join(out)

    def register_params(self) -> list[str]:
        """Registers that aren't a plain __cdecl/__stdcall/__thiscall/__fastcall's: the sign of a register
        convention of LTCG's own."""
        return [r for r in self.reads if r not in ("ecx", "edx")]


def _rw(ins) -> tuple[set[str], set[str]]:
    try:
        r, w = ins.regs_access()
    except capstone.CsError:
        return set(), set()
    rd = {_PARENT.get(ins.reg_name(x)) for x in r} - {None}
    wr = {_PARENT.get(ins.reg_name(x)) for x in w} - {None}
    ops = ins.operands
    if ins.mnemonic in ("xor", "sub", "sbb", "pxor", "xorps") and len(ops) == 2 and ops[0].type == x86.X86_OP_REG \
            and ops[1].type == x86.X86_OP_REG and ops[0].reg == ops[1].reg:
        rd.discard(_PARENT.get(ins.reg_name(ops[0].reg)))  # zeroing reads nothing
    if ins.mnemonic in ("or", "and") and len(ops) == 2 and ops[0].type == x86.X86_OP_REG and ops[0].size == 4 \
            and ops[1].type == x86.X86_OP_IMM and (ops[1].imm & 0xFFFFFFFF) == (0xFFFFFFFF if ins.mnemonic == "or" else 0):
        rd.discard(_PARENT.get(ins.reg_name(ops[0].reg)))  # `or r, -1` / `and r, 0` set a constant
    return rd, wr


def code_end(code: bytes, addr: int) -> int:
    """Where a function's instructions end: its first jump table or byte table (MSVC puts them after the code)."""
    end = addr + len(code)
    for ins in _md.disasm(code, addr):
        for op in ins.operands:
            if op.type == x86.X86_OP_MEM and ins.disp_size == 4:
                disp = op.mem.disp & 0xFFFFFFFF
                if ins.address < disp < end and ((op.mem.scale == 4 and ins.id == x86.X86_INS_JMP) or op.size == 1):
                    end = disp
    return end


def instructions(code: bytes, addr: int) -> list:
    stop = code_end(code, addr)
    return list(_md.disasm(code[:stop - addr], addr))


def analyse(code: bytes, addr: int, callee=None) -> Convention:
    """The convention of the function whose bytes (its extent) are `code`, at `addr`. `callee(address)`: (the
    registers a direct callee reads before writing, the ones it may change) or None, so a register only passed on to
    it counts as read here too (LTCG keeps an argument where the callee wants it: Accum::Add(this=ESI, p=EDX, k=EAX)
    passes EAX on), and one it leaves alone stays an argument across the call (Accum::Add reads EDX after it)."""
    ins = instructions(code, addr)
    if not ins:
        return Convention()
    at = {i.address: k for k, i in enumerate(ins)}
    pops = {i.op_str for i in ins if i.mnemonic == "pop"}
    succ: list[list[int]] = []
    for k, i in enumerate(ins):
        ops, m, s = i.operands, i.mnemonic, []
        imm = bool(ops) and ops[0].type == x86.X86_OP_IMM
        if m == "jmp" and ops and ops[0].type == x86.X86_OP_MEM and ops[0].mem.scale == 4 and not ops[0].mem.base:
            table = (ops[0].mem.disp & 0xFFFFFFFF) - addr  # a switch: its cases are inside the function
            for n in range(256):
                if not 0 <= table + 4 * n <= len(code) - 4:
                    break
                t = struct.unpack_from("<I", code, table + 4 * n)[0]
                if t not in at:
                    break
                s.append(at[t])
        elif m in ("ret", "retf") or (m == "jmp" and not imm):
            pass
        elif imm and x86.X86_GRP_JUMP in i.groups:
            t = ops[0].imm & 0xFFFFFFFF
            if t in at:
                s.append(at[t])
            if m != "jmp" and k + 1 < len(ins):
                s.append(k + 1)
        elif k + 1 < len(ins):
            s.append(k + 1)
        succ.append(s)

    defined: list[frozenset | None] = [None] * len(ins)
    delta: list[int | None] = [None] * len(ins)
    defined[0], delta[0] = frozenset(), 0
    reads, xmm, stack, saves, written = set(), set(), set(), set(), set()
    pushes: list[int] = [0] * len(ins)
    work = [0]
    while work:
        k = work.pop()
        i, d, dl = ins[k], set(defined[k]), delta[k]
        rd, wr = _rw(i)
        m, ops = i.mnemonic, i.operands
        prologue = all(x.mnemonic == "push" for x in ins[:k])
        if m == "push" and ops[0].type == x86.X86_OP_REG and i.op_str not in d:
            if i.op_str in SAVED and (i.op_str in pops or prologue):
                rd = set()
                saves.add(i.op_str)
            elif prologue and i.op_str in ("eax", "ecx", "edx"):
                rd = set()  # `push ecx` first thing: room for a local, as often as a spilled argument
        if m == "lea" and len(ops) == 2 and ops[1].type == x86.X86_OP_MEM and ops[1].mem.base == ops[0].reg \
                and not ops[1].mem.index and ops[1].mem.disp == 0:
            rd = set()  # lea ecx, [ecx]: alignment padding
        known = None  # a direct callee's (reads, clobbers)
        if callee is not None and m in ("call", "jmp") and ops and ops[0].type == x86.X86_OP_IMM \
                and (ops[0].imm & 0xFFFFFFFF) not in at and (ops[0].imm & 0xFFFFFFFF) != addr:
            known = callee(ops[0].imm & 0xFFFFFFFF)
            if known is not None:
                rd = rd | known[0]  # passed on to the callee
        for r in rd - d:
            if r in GPR:
                reads.add(r)
            elif r in XMM:
                xmm.add(r)
        for op in ops:
            if op.type == x86.X86_OP_MEM and op.mem.base and i.reg_name(op.mem.base) == "esp" and dl is not None \
                    and not op.mem.index and op.mem.disp + dl >= 4 and (op.access & capstone.CS_AC_READ
                                                                         or m in ("lea", "push")):
                stack.add(op.mem.disp + dl)
        d |= wr
        written |= wr
        ndl, npush = dl, pushes[k]
        if m == "call":
            # what the callee changes (LTCG callers count on the rest: an argument can sit in EDX across a call)
            gone = known[1] if known is not None else SCRATCH
            d |= gone
            written |= gone
        if dl is not None:
            if m == "push":  # (the prologue's saves aren't arguments for the next call)
                ndl, npush = dl - 4, npush + (0 if prologue else 1)
            elif m == "pop":
                ndl = dl + 4
            elif m in ("sub", "add") and len(ops) == 2 and ops[0].type == x86.X86_OP_REG \
                    and i.reg_name(ops[0].reg) == "esp" and ops[1].type == x86.X86_OP_IMM:
                ndl = dl + (-ops[1].imm if m == "sub" else ops[1].imm)
            elif "esp" in wr and m not in ("call", "ret", "push", "pop"):
                ndl = None  # esp set some other way (an aligned frame): stop counting
            elif m == "call":
                nxt = ins[k + 1] if k + 1 < len(ins) else None
                caller_pops = nxt is not None and nxt.mnemonic == "add" and nxt.op_str.startswith("esp,")
                ndl, npush = (dl if caller_pops else dl + 4 * npush), 0
        for s in succ[k]:
            new = frozenset(d)
            if defined[s] is None:
                defined[s], delta[s], pushes[s] = new, ndl, npush
                work.append(s)
            elif not defined[s] <= new:
                defined[s] = defined[s] & new
                work.append(s)
    rets = [i for i in ins if i.mnemonic == "ret"]
    ret = None if not rets else (rets[0].operands[0].imm if rets[0].operands else 0)
    clobbers = sorted((written & TRACKED) - saves, key=lambda r: (r in XMM, r))
    return Convention(sorted(reads, key=GPR.index), sorted(stack), ret, sorted(saves, key=GPR.index),
                      sorted(xmm, key=XMM.index), clobbers)


# ------------------------------------------------------------------------------------------- the project's side


@dataclass
class Program:
    """The project's original program and its functions (functions.json), indexed for call sites."""

    pe: object
    functions: dict[int, dict]  # address -> functions.json row (with "unit")
    by_name: dict[str, int]
    calls: dict[int, list[tuple[int, int]]]  # callee address -> [(caller address, offset of the rel32)]
    pointers: dict[int, list[tuple[int, int]]]  # function address -> [(function using it as an address, offset)]
    # its analyses one at a time (convention, methods.recover): agents claim in parallel threads and share it.
    # Re-entrant for the callees' analyses. The program's own: analysing one project never waits for another's
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def code(self, addr: int) -> bytes:
        fn = self.functions[addr]
        return self.pe.read(addr, fn["size"])

    def convention(self, addr: int) -> Convention:
        # One analysis at a time: the program is shared by every agent's claim (load caches it), and another thread
        # must never see the recursion guard below as a result. The lock is re-entrant for the callees' analyses.
        with self.lock:
            memo = self.__dict__.setdefault("_conventions", {})
            if addr not in memo:
                memo[addr] = None  # a guard while it's worked out (recursion): a callee in a cycle passes nothing on
                try:
                    memo[addr] = analyse(self.code(addr), addr, self._reads)
                except BaseException:
                    del memo[addr]  # not a cycle: leave nothing behind that reads as one
                    raise
            return memo[addr]

    def _reads(self, addr: int) -> tuple[set[str], set[str]] | None:
        if addr not in self.functions:
            return None
        memo = self.__dict__.setdefault("_conventions", {})
        depth = self.__dict__.get("_depth", 0)
        if (addr in memo and memo[addr] is None) or (addr not in memo and depth > 40):
            return None  # in a cycle, or a call chain too deep to follow
        self.__dict__["_depth"] = depth + 1
        try:
            c = self.convention(addr)
        finally:
            self.__dict__["_depth"] = depth
        return set(c.reads) | set(c.xmm), set(c.clobbers)

    def find(self, what: str) -> int | None:
        """A function by its symbol, func_<address> name or address."""
        if what in self.by_name:
            return self.by_name[what]
        m = re.search(r"(?:func_|0x)?([0-9A-Fa-f]{5,8})(?![0-9A-Fa-f])", what)
        if m and int(m.group(1), 16) in self.functions:
            return int(m.group(1), 16)
        return None


# Each project's program, by its functions.json: (what it was read from, the program), the most recently used last.
# One program was cached for every project: agents claiming in two Xbox games at once evicted each other's, and each
# claim read a 13 MB functions.json and analysed the whole game again (methods.recover's memo lives on the program),
# under one lock every claim and compile of both waited on: claims timed out at 5 minutes, compiles took 4.
CACHED_PROGRAMS = 4
_CACHE: dict[str, tuple[tuple, Program]] = {}
_CACHE_LOCK = threading.Lock()  # the dict itself, and _LOADING
_LOADING: dict[str, threading.Lock] = {}  # a project's functions.json -> held while its program is read


def load(root: Path, manifest: dict) -> Program:
    """The project's program and function index (cached while functions.json and the program are unchanged)."""
    fj = root / "config" / manifest["version"] / "functions.json"
    exe = root / manifest["game"]["path"]
    key = (fj.stat().st_mtime_ns, str(exe), exe.stat().st_mtime_ns)
    name = str(fj)
    with _CACHE_LOCK:
        loading = _LOADING.setdefault(name, threading.Lock())
    with loading:  # one read of a project at a time; other projects' go on meanwhile
        with _CACHE_LOCK:
            hit = _CACHE.pop(name, None)
            if hit is not None and hit[0] == key:
                _CACHE[name] = hit  # (most recently used: last)
                return hit[1]
        info = json.loads(fj.read_text(encoding="utf-8"))
        pe = read_pe(exe.read_bytes())  # (an Xbox game's too: orig/ holds its XBE as a Windows program)
        prog = index(pe, info)
        with _CACHE_LOCK:
            _CACHE[name] = (key, prog)
            while len(_CACHE) > CACHED_PROGRAMS:
                _CACHE.pop(next(iter(_CACHE)))  # the least recently used
        return prog


def index(pe, info: dict) -> Program:
    functions: dict[int, dict] = {}
    for unit, fns in info["units"].items():
        for fn in fns:
            functions[fn["addr"]] = {**fn, "unit": unit}
    by_name = {fn["name"]: a for a, fn in functions.items()}
    calls: dict[int, list[tuple[int, int]]] = {}
    pointers: dict[int, list[tuple[int, int]]] = {}
    for a, fn in functions.items():
        for off, rtype, name, addend in fn["relocs"]:
            target = by_name.get(name) if name else None
            if target is None or addend:
                continue
            if rtype == REL32:
                code = pe.read(a + off - 1, 1)
                if code == b"\xe8":  # a call, not a tail call's jmp
                    calls.setdefault(target, []).append((a, off))
            elif rtype == DIR32:
                pointers.setdefault(target, []).append((a, off))
    return Program(pe, functions, by_name, calls, pointers)


def call_site(prog: Program, caller: int, off: int, limit: int = 12) -> list[str]:
    """The instructions before the call at caller+off-1 that set an argument register or push: back to the previous
    call, branch or jump target, at most `limit` instructions, the last write to each register only."""
    return [f"{i.mnemonic} {i.op_str}".strip() for i in _setup(prog, caller, off, limit)[0]]


def _setup(prog: Program, caller: int, off: int, limit: int = 12) -> tuple[list, set[str]]:
    """(call_site's instructions, the argument registers they set)."""
    ins = instructions(prog.code(caller), caller)
    site = caller + off - 1
    k = next((n for n, i in enumerate(ins) if i.address == site), None)
    if k is None:
        return [], set()
    targets = {i.operands[0].imm & 0xFFFFFFFF for i in ins
               if x86.X86_GRP_JUMP in i.groups and i.operands and i.operands[0].type == x86.X86_OP_IMM}
    keep, seen = [], set()
    for n in range(k - 1, max(-1, k - 1 - limit), -1):
        i = ins[n]
        if i.mnemonic in ("call", "ret") or x86.X86_GRP_JUMP in i.groups:
            break
        _, wr = _rw(i)
        regs = {r for r in wr if r in ARGS} - seen
        if i.mnemonic == "push" or regs:
            keep.append(i)
            seen |= regs
        if i.address in targets:
            break
    return list(reversed(keep)), seen


def _where(prog: Program, addr: int, off: int) -> str:
    fn = prog.functions[addr]
    return f"{fn['name']}+0x{off - 1:X}"


def evidence(prog: Program, addr: int, sites: int = 3) -> list[str]:
    """Lines for a brief: the function's own convention, then a few of its call sites."""
    conv = prog.convention(addr)
    out = [conv.describe()]
    special = conv.register_params()
    if special:
        out.append(f"register parameters of LTCG's choosing: {', '.join(special)} (declare them as the call sites "
                   "below set them up; this, an argument or a value kept from the caller)")
    calls = prog.calls.get(addr, [])
    by_caller: dict[int, list[int]] = {}
    for c, off in calls:
        by_caller.setdefault(c, []).append(off)
    picked = [(c, offs[0]) for c, offs in by_caller.items()][:sites]
    if calls:
        out.append(f"called from {len(calls)} place{'s' if len(calls) != 1 else ''} in "
                   f"{len(by_caller)} function{'s' if len(by_caller) != 1 else ''}:")
    else:
        out.append("no direct calls in the original (called through a pointer or a vtable, or inlined everywhere)")
    for c, off in picked:
        setup = call_site(prog, c, off)
        out.append(f"  {_where(prog, c, off)} ({prog.functions[c]['unit']}): "
                   + ("; ".join(setup) if setup else "(nothing set up right before the call)") + " -> call")
    refs = prog.pointers.get(addr, [])
    if refs:
        out.append(f"its address is taken in {len(refs)} place{'s' if len(refs) != 1 else ''} (a callback or a "
                   f"table): {', '.join(_where(prog, c, off + 1) for c, off in refs[:3])}")
    return out


# ------------------------------------------------------------------------------------------- checking a harness link

# A function in a harness link's map: `0001:00000140  ?Begin@C@@SAXPAU1@@Z  10001140 f  00_unit.obj`
_MAP_FUNCTION = re.compile(r"^\s*000[1-9]:[0-9a-fA-F]{8}\s+(\S+)\s+([0-9a-fA-F]{8})\s+f\s+(?:\S+:)?(\S+)\s*$", re.M)


def interface(c: Convention) -> tuple:
    """How a function takes its arguments: registers and XMM registers read before written, whether it has stack
    arguments, the bytes `ret N` pops. Not which stack slots it reads (a body reading half an argument, or not using
    one, reads others), nor the registers it saves (they follow from its body)."""
    return tuple(c.reads), tuple(c.xmm), bool(c.stack), c.ret


def same_interface(a: Convention, b: Convention) -> bool:
    """interface() alike, where a function with no `ret` (it ends in a tail jump, or never returns) counts as popping
    whatever the other does."""
    ia, ib = interface(a), interface(b)
    return ia[:3] == ib[:3] and (a.ret is None or b.ret is None or a.ret == b.ret)


def _brief_interface(c: Convention) -> str:
    regs = ", ".join([*c.reads, *c.xmm]) or "no registers"
    stack = f"stack arguments {', '.join(f'[esp+{n}]' for n in c.stack[:6])}" if c.stack else "no stack arguments"
    ret = "no ret" if c.ret is None else f"ret {c.ret}" if c.ret else "ret"
    return f"reads {regs}; {stack}; {ret}"


@dataclass
class LinkCheck:
    """A function's convention in a harness link against the original's, and its callees' (check_link)."""

    own: tuple[Convention, Convention]  # (the original's, the link's)
    callees: list[tuple[str, str, Convention, Convention]] = field(default_factory=list)  # (name, object, orig, ours)
    absent: list[str] = field(default_factory=list)  # callees of the original's that the link has no function for

    @property
    def own_differs(self) -> bool:
        return not same_interface(*self.own)

    @property
    def saves_differ(self) -> bool:
        return self.own[0].saves != self.own[1].saves

    def differing_callees(self) -> list[tuple[str, str, Convention, Convention]]:
        return [c for c in self.callees if not same_interface(c[2], c[3])]


def link_functions(map_text: str) -> dict[str, tuple[int, str]]:
    """A harness link's functions from its map: symbol -> (address, the object that defines it)."""
    return {m.group(1): (int(m.group(2), 16), m.group(3)) for m in _MAP_FUNCTION.finditer(map_text)}


def check_link(prog: Program, addr: int, dll, ours: int, size: int, functions: dict[str, tuple[int, str]]) -> LinkCheck:
    """The original function at `addr` against its build at `ours` (`size` bytes) in a harness link (`dll`: the
    linked DLL, read_pe's shape; `functions`: link_functions of its map), and each function the original calls
    against the link's function of the same name. Both sides are read alike, without following callees (the link's
    callees are other code at other addresses), so what differs is the convention the link gave, not the reading."""
    found = LinkCheck((analyse(prog.code(addr), addr), analyse(dll.read(ours, size), ours)))
    ends = sorted(a for a, _ in functions.values())
    by_identifier: dict[str, list[str]] = {}
    for symbol in functions:
        by_identifier.setdefault(_identifier(symbol), []).append(symbol)
    seen = set()
    for off, rtype, name, addend in prog.functions[addr]["relocs"]:
        target = prog.by_name.get(name) if name else None
        if target is None or target == addr or target in seen or rtype != REL32 or addend \
                or prog.pe.read(addr + off - 1, 1) != b"\xe8":
            continue
        seen.add(target)
        callee = prog.functions[target]["name"]
        symbol = callee if callee in functions else next(iter(by_identifier.get(_identifier(callee), [])[:1]), None)
        if symbol is None:
            found.absent.append(callee)
            continue
        at, obj = functions[symbol]
        end = next((e for e in ends if e > at), at + 0x1000)
        found.callees.append((callee, obj, analyse(prog.code(target), target),
                              analyse(dll.read(at, min(end - at, 0x4000)), at)))
    return found


def _identifier(symbol: str) -> str:
    """The name source code uses for a decorated one (unresolved.identifier's simple cases)."""
    if symbol.startswith("?"):
        return symbol[1:].split("@", 1)[0]
    return symbol.lstrip("_@").split("@", 1)[0]


def link_lines(found: LinkCheck, name: str, where) -> list[str]:
    """For a compile result: what the check says to do. `where(object)`: the source file an object of the link was
    compiled from (or the object's name)."""
    orig, ours = found.own
    lines = []
    if found.own_differs:
        lines.append(f"- {name} itself: the original {_brief_interface(orig)}; your link gives it "
                     f"{_brief_interface(ours)}. Under LTCG the linker picks this from the calls it sees, so the "
                     "code around every use of an argument differs until it agrees: make the driver's call (or the "
                     "unit's own callers) load what the original's call sites load (the brief lists them), and "
                     "declare the parameters it reads.")
    for callee, obj, o, b in found.differing_callees()[:6]:
        lines.append(f"- {callee} (in {where(obj)}): the original {_brief_interface(o)}; the one in your link "
                     f"{_brief_interface(b)}. Your function's code around the call follows the callee's convention: "
                     "shape that definition like the original's (its parameters, what it reads and returns) first.")
    if lines:
        # agents read the first version of this, wrote what to fix into a note and released the function: the fix is
        # theirs to make, in this claim
        return ["", "### Conventions in your link that differ from the original's (fix these before the code)", *lines,
                "These files are part of this claim's work: change them now with edit_file and compile again. Don't "
                "release the function for them or leave them to a note: the next agent would find the same thing."]
    n = len(found.callees)
    saves = (f" (it saves {', '.join(ours.saves) or 'nothing'} where the original saves "
             f"{', '.join(orig.saves) or 'nothing'}: that follows from the code, how many values it keeps alive at "
             "once)") if found.saves_differ else ""
    return ["", f"Conventions: {name} takes its arguments as the original does"
                + (f", and so {'does' if n == 1 else 'do'} the {n} function{'s' if n != 1 else ''} it calls in your "
                   "link" if n else "")
                + f". What still differs comes from the code itself{saves} or the data it uses, not from the "
                  "calls: keep working on the function rather than the driver."]


# ------------------------------------------------------------------------------------------- detecting LTCG

SAMPLE = 1500  # functions looked at (evenly spread): enough for a verdict, quick on a big game
# Of them, the share with a confirmed register parameter of LTCG's own that makes an LTCG build. Visual C++ 7 gives
# a file's own static functions such conventions without LTCG too (a non-LTCG Xbox test program: 1 of 16, 6%), but
# only LTCG gives them to functions called across files (a real LTCG game: 48%).
THRESHOLD = 0.15
MIN_CONFIRMED = 4  # (an XDK sample with 6 functions of its own, built with LTCG: 4 of 14)


def detect(prog: Program, skip: set[int] = frozenset()) -> dict:
    """Whether the program looks built with link-time code generation, and why: functions that read EAX, EBX, ESI
    or EDI before writing them (and don't save them), called from sites that load that register right before the
    call. No __cdecl, __stdcall, __thiscall or __fastcall function takes an argument there: only the C runtime's
    assembly helpers (`_chkstk` takes EAX), a file's own static functions (Visual C++ 7 picks their convention
    too) and, everywhere, LTCG. `skip`: library code."""
    pool = sorted(a for a in prog.functions if a not in skip and prog.calls.get(a))
    step = max(1, len(pool) // SAMPLE)
    sample = pool[::step][:SAMPLE]
    confirmed, examples = 0, []
    for a in sample:
        try:
            regs = set(prog.convention(a).register_params())
        except (capstone.CsError, KeyError):
            continue
        if not regs:
            continue
        for caller, off in prog.calls[a][:4]:
            _, set_up = _setup(prog, caller, off)
            if regs & set_up:
                confirmed += 1
                if len(examples) < 3:
                    shown = "; ".join(call_site(prog, caller, off))
                    examples.append(f"{prog.functions[a]['name']} reads {', '.join(sorted(regs & set_up))} before "
                                    f"writing it, and {_where(prog, caller, off)} sets it up: {shown} -> call")
                break
    share = confirmed / len(sample) if sample else 0.0
    ltcg = confirmed >= MIN_CONFIRMED and share >= THRESHOLD
    why = (f"{confirmed} of {len(sample)} called functions looked at ({share:.1%}) take a register other than "
           f"ECX/EDX that their callers load right before calling them")
    return {"ltcg": ltcg, "confirmed": confirmed, "sampled": len(sample), "share": round(share, 4),
            "why": why + (": link-time code generation" if ltcg else ": not link-time code generation"),
            "examples": examples}


def detect_functions(pe, functions: list, skip: set[int] = frozenset()) -> dict:
    """detect() for functions just analysed (scratch_pe.Function), before the project is written."""
    info = {"units": {"all": [{"name": f.name, "addr": f.addr, "size": f.size, "relocs": [list(r) for r in f.relocs]}
                              for f in functions]}}
    return detect(index(pe, info), skip)
