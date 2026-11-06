"""The functions dsd's analysis misses, found after `dsd init` and written into its config (symbols.txt, relocs.txt).

dsd finds a module's functions by parsing them one after another until a pool constant that points to data stops
it, and then by following calls. A function only a pointer reaches (a callback, an entry of a table of functions)
is never found: its bytes stay between the functions dsd knows, in no function at all. On a DS game that was more
than half the main program's code (about 1,200 functions, 455 KB of 560 KB of .text), and every source file with
such a gap compiled shorter than the original, so calls across it had the wrong distance and never matched.

ds-decomp's own answer is to add them to symbols.txt by hand. This does that for every gap of a module's .text:
- where functions start: words that point into a gap (bit 0: Thumb), function prologues (`stmdb sp!, {..., lr}`,
  Thumb `push {..., lr}`), the code right after a function it found, and calls from the functions it adds;
- where each ends: its code, up to an unconditional return or branch no branch of its own goes past, then its
  literal pool; when that can't be told (a computed jump, a Thumb switch's table), up to the next function;
- its relocations, as dsd writes them: calls (arm_call, arm_call_thumb, thumb_call, thumb_call_arm; arm_branch for
  an ARM tail call) and pool words pointing to a symbol (load). A load dsd's scan of the gap as data put on what is
  an instruction goes, and pointers elsewhere to a new ARM function get their load.

dsd also ends .text at the last function it found, so code past it reached only through pointers was .rodata to
it (about 45 KB on a DS game, called from .text 42 times). Where functions start right at .text's end and run on,
and code dsd knows calls into them, .text ends where they do (extend_text) and its gap is searched like the others.

Every source file keeps its range (delinks.txt): a project's work stays where it is, and the rebuilt ROM is the
same bytes, the delinked objects only say more about them. Code that .text gains gets source files of its own
(scratch.add_sources).
"""

from __future__ import annotations

import bisect
import re
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import capstone
from capstone import arm as cs_arm

from ...hostenv import NO_WINDOW, console_python

SECTION = re.compile(r"^\s+(\.\w+)\s+start:(0x[0-9a-fA-F]+)\s+end:(0x[0-9a-fA-F]+)\s+kind:(\w+)", re.M)
SYMBOL = re.compile(r"^(\S+) kind:(\w+)(?:\(([^)]*)\))? addr:(0x[0-9a-fA-F]+)")
RELOC = re.compile(r"^from:(0x[0-9a-fA-F]+) kind:(\S+) to:(0x[0-9a-fA-F]+)")
MAX_PASSES = 16  # calls from the functions it adds lead to more: until none
ARM_PROLOGUE, ARM_PROLOGUE_MASK = 0xE92D4000, 0xFFFF4000  # stmdb sp!, {..., lr}
THUMB_PROLOGUE, THUMB_PROLOGUE_MASK = 0xB500, 0xFF00  # push {..., lr}
RETURNS = {"bx", "pop", "ldm", "ldmia", "ldmfd", "ldmib", "ldmda", "ldmdb", "ldmea", "ldmed", "ldmfa"}
NOT_BRANCHES = {"bl", "blx", "bx", "bic", "bics", "bkpt"}
FIXED_MODULES = ("main", "itcm", "dtcm")  # the modules always loaded: relocations may point into them from any module
SECURE_AREA = (0x02000000, 0x02000800)  # the main program's first 2 KiB: random bytes, as dsd treats them
# `dsd delink` reads every function of the config again and refuses one with code it calls illegal in it
REFUSED = re.compile(r"function (\S+) could not be analyzed: (\w+)[^\n]*")
MAX_REFUSALS = 500


@dataclass
class Module:
    name: str  # "main", "itcm", "dtcm", "ov012"
    kind: str  # its relocations' `module:` ("main", "itcm", "dtcm", "overlay(12)")
    folder: Path  # its config folder (symbols.txt, relocs.txt, delinks.txt)
    base: int
    code: bytes
    text: list[tuple[int, int]]
    symbols: set[int] = field(default_factory=set)  # every symbol's address
    functions: dict[int, tuple[int, bool]] = field(default_factory=dict)  # address -> (size, thumb): dsd's
    relocs: dict[int, str] = field(default_factory=dict)  # from -> its line
    changed: bool = False  # its relocs changed
    symbols_changed: bool = False  # its symbols.txt changed (a function dsd refused was taken out)
    added: dict[int, tuple[int, bool, bool]] = field(default_factory=dict)  # address -> (size, thumb, sure): ours
    covered: bytearray = field(default_factory=bytearray)  # 1 for each byte in a function
    dsds: bytes = b""  # 1 for each byte in a function dsd found (covered, before this added any)
    symbol_lines: list[str] = field(default_factory=list)  # symbols.txt as dsd wrote it
    refusals: list[str] = field(default_factory=list)  # what dsd said of the functions it refused
    rodata: tuple[int, int] | None = None  # .rodata, when it starts where .text ends
    extended: tuple[int, int] | None = None  # (dsd's end of .text, its end now): code dsd took for .rodata
    dropped: set[int] = field(default_factory=set)  # dsd's data symbols in that code, taken out

    def cover(self, start: int, size: int, value: int = 1) -> None:
        self.covered[start - self.base:start - self.base + size] = bytes([value]) * size

    def in_text(self, addr: int) -> tuple[int, int] | None:
        if self.kind == "main" and SECURE_AREA[0] <= addr < SECURE_AREA[1]:
            return None
        return next(((a, b) for a, b in self.text if a <= addr < b), None)

    def gap_at(self, addr: int) -> tuple[int, int] | None:
        """The gap of .text (bytes in no function) holding `addr`: (start, end), or None."""
        text = self.in_text(addr)
        at = addr - self.base
        if text is None or self.covered[at]:
            return None
        lo, hi = text[0] - self.base, text[1] - self.base
        before = self.covered.rfind(1, lo, at)
        after = self.covered.find(1, at, hi)
        return self.base + (before + 1 if before >= 0 else lo), self.base + (after if after >= 0 else hi)

    def function_name(self, addr: int) -> str:
        return f"func_{addr:08x}" if self.kind in FIXED_MODULES else f"func_{self.name}_{addr:08x}"

    def thumb_of(self, addr: int) -> bool | None:
        """Whether the function at `addr` is Thumb code; None when no function starts there."""
        if addr in self.added:
            return self.added[addr][1]
        return self.functions[addr][1] if addr in self.functions else None


def load_modules(config: Path, extract: Path) -> list[Module]:
    """The modules of a dsd config (config/<version>/arm9/config.yaml) with their code (extract/<version>/)."""
    import yaml

    cfg = yaml.safe_load((config / "config.yaml").read_text(encoding="utf-8"))
    entries = [("main", "main", cfg["main_module"], extract / "arm9" / "arm9.bin")]
    for a in cfg.get("autoloads") or []:
        if (kind := str(a.get("kind", "")).lower()) in ("itcm", "dtcm"):
            entries.append((a["name"], kind, a, extract / "arm9" / f"{kind}.bin"))
    for ov in cfg.get("overlays") or []:
        entries.append((ov["name"], f"overlay({ov['id']})", ov, extract / "arm9_overlays" / f"{ov['name']}.bin"))
    out = []
    for name, kind, entry, binary in entries:
        folder = (config / entry["delinks"]).parent
        head = (folder / "delinks.txt").read_text(encoding="utf-8").split("\n\n")[0]
        sections = [(int(a, 16), int(b, 16), k) for _, a, b, k in SECTION.findall(head)]
        if not sections or not binary.is_file():
            continue
        code = binary.read_bytes()
        m = Module(name, kind, folder, min(a for a, _, _ in sections), code,
                   [(a, b) for a, b, k in sections if k == "code" and b > a], covered=bytearray(len(code)))
        m.rodata = next(((a, b) for a, b, k in sections if k == "rodata" and m.text and a == m.text[-1][1] < b), None)
        m.symbol_lines = (folder / "symbols.txt").read_text(encoding="utf-8").splitlines()
        for line in m.symbol_lines:
            if not (s := SYMBOL.match(line)):
                continue
            addr = int(s.group(4), 16)
            m.symbols.add(addr)
            if s.group(2) == "function":
                size = re.search(r"size=(0x[0-9a-fA-F]+)", s.group(3) or "")
                n = int(size.group(1), 16) if size else 0
                m.functions[addr] = (n, "thumb" in (s.group(3) or ""))
                if 0 <= addr - m.base < len(code):
                    m.cover(addr, n)
        for line in (folder / "relocs.txt").read_text(encoding="utf-8").splitlines():
            if (r := RELOC.match(line)):
                m.relocs[int(r.group(1), 16)] = line
        # data dsd put in .text (a DS program's BuildInfo) is no gap: up to the next symbol, as dsd sizes data(any)
        ordered = sorted(m.symbols)
        for addr, nxt in zip(ordered, ordered[1:] + [max(b for _, b in m.text) if m.text else 0]):
            if addr not in m.functions and m.in_text(addr):
                m.cover(addr, max(0, min(nxt, m.in_text(addr)[1]) - addr))
        m.dsds = bytes(m.covered)
        out.append(m)
    return out


_ARM = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
_THUMB = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
for _md in (_ARM, _THUMB):
    _md.detail = True


@dataclass
class Parsed:
    end: int  # where its code and pool end
    sure: bool  # told from its code (False: cut where the next function starts)
    illegal: bool = False  # it ran into code that can't be (data, padding) before a return: not a function
    calls: list[tuple[int, int, str]] = field(default_factory=list)  # (address, target, "bl"/"blx"/"b")
    pools: set[int] = field(default_factory=set)  # its pool words' addresses
    code: set[int] = field(default_factory=set)  # its instructions' addresses


def parse(m: Module, start: int, bound: int, thumb: bool) -> Parsed:
    """A function's extent, calls and literal pool, from its code (up to `bound`, the next function). Code dsd
    calls illegal (an opcode capstone can't read either, Thumb's 0000) ends it there: dsd refuses a function holding
    it, and it's padding or data after a function this can't see the end of."""
    out = Parsed(bound, False)
    furthest = last = start
    for ins in (_THUMB if thumb else _ARM).disasm(m.code[start - m.base:bound - m.base], start):
        here, end = ins.address, ins.address + ins.size
        if any(p <= here < p + 4 for p in out.pools):  # into its own pool: its code ended before, unseen
            out.end = max([here] + [p + 4 for p in out.pools if p + 4 <= bound])
            return out
        if thumb and ins.bytes == b"\0\0":
            out.illegal = True
            break
        last = end
        out.code.add(here)
        ops = ins.operands
        always = ins.cc in (cs_arm.ARM_CC_AL, cs_arm.ARM_CC_INVALID)
        mnem = ins.mnemonic.split(".")[0]
        if mnem.startswith("ldr"):
            for op in ops:  # ldr rX, [pc, #imm]: a word of its pool
                if op.type == cs_arm.ARM_OP_MEM and op.mem.base == cs_arm.ARM_REG_PC:
                    addr = ((here + (4 if thumb else 8)) & ~3) + op.mem.disp
                    if start <= addr < bound:
                        out.pools.add(addr)
        target = ops[0].imm if ops and ops[0].type == cs_arm.ARM_OP_IMM else None
        if mnem in ("bl", "blx") and target is not None:
            out.calls.append((here, target, mnem))
            continue
        writes_pc = bool(ops) and ops[0].type == cs_arm.ARM_OP_REG and ops[0].reg == cs_arm.ARM_REG_PC
        if mnem.startswith("b") and mnem not in NOT_BRANCHES and target is not None:
            if start <= target < bound and target > here:
                furthest = max(furthest, target)
            if not (always and mnem == "b"):
                continue
            if not start <= target < bound:
                out.calls.append((here, target, "b"))  # a tail call
        elif not (always and (mnem == "bx" or (mnem in RETURNS and any(
                o.type == cs_arm.ARM_OP_REG and o.reg == cs_arm.ARM_REG_PC for o in ops)) or (mnem == "mov" and writes_pc))):
            if writes_pc:  # add pc, ... / ldr pc, ...: a computed jump (a switch's table), not followed
                return out
            continue
        if end > furthest:  # an unconditional return or branch no branch of its own goes past: its code ends
            end = max([end] + [p + 4 for p in out.pools])
            if end <= bound:
                out.end, out.sure = end, True
            return out
    out.end, out.illegal = (last, True) if last < bound else (bound, out.illegal)  # stopped at code it can't read
    return out


def _word_values(modules: list[Module]) -> list[tuple[Module, int, int]]:
    """(module, address, value) of every aligned word whose value could point into a module's .text."""
    lo = min((a for m in modules for a, _ in m.text), default=0)
    hi = max((b for m in modules for _, b in m.text), default=0)
    out = []
    for m in modules:
        for i, (v,) in enumerate(struct.iter_unpack("<I", m.code[:len(m.code) // 4 * 4])):
            if lo <= v & ~1 < hi:
                out.append((m, m.base + 4 * i, v))
    return out


_ENTRY_REGS = {cs_arm.ARM_REG_R0, cs_arm.ARM_REG_R1, cs_arm.ARM_REG_R2, cs_arm.ARM_REG_R3, cs_arm.ARM_REG_SP,
               cs_arm.ARM_REG_PC}  # what a function's first Thumb instruction may read: its arguments, sp, pc


def _starts_like_code(m: Module, addr: int, thumb: bool) -> bool:
    """dsd's own tests for where a function may start (its analysis/function_start.rs, simplified): ARM's first
    instruction runs always (condition AL); Thumb's reads only argument registers, sp or pc (data read as Thumb code
    reads others)."""
    at = addr - m.base
    if not 0 <= at <= len(m.code) - (2 if thumb else 4):
        return False  # not in this module's code: a pointer into another one (overlays share addresses)
    if not thumb:
        return addr % 4 == 0 and m.code[at + 3] & 0xF0 == 0xE0
    first = next(_THUMB.disasm(m.code[at:at + 4], addr), None)
    if addr % 2 or first is None or first.bytes == b"\0\0":
        return False
    for op in first.operands[1:]:
        if op.type == cs_arm.ARM_OP_MEM and op.mem.base not in _ENTRY_REGS:
            return False
        if op.type == cs_arm.ARM_OP_REG and first.mnemonic.split(".")[0] not in ("push", "pop", "bx", "blx") and \
                op.reg not in _ENTRY_REGS:
            return False
    return True


def extend_text(m: Module, log=print) -> int:
    """End .text where the code right after it ends, when dsd took that code for .rodata: functions follow one
    another from .text's end (an ARM one's end told from its code; one that can't be told runs to the next
    prologue), and a function in .text calls into them. Returns the bytes .text gained."""
    if m.rodata is None:
        return 0
    start, bound = m.text[-1][1], m.rodata[1]
    known = [(a, s, t) for a, (s, t) in m.functions.items()] + [(a, s, t) for a, (s, t, _) in m.added.items()]
    calls = {target & ~1 for a, s, t in known if m.in_text(a)
             for _, target, _ in parse(m, a, a + s, t).calls if start <= target & ~1 < bound}
    cursor = start
    while cursor < bound and _starts_like_code(m, cursor, False):
        p = parse(m, cursor, bound, False)
        if p.illegal or p.end <= cursor:
            break
        if p.sure:
            cursor = (p.end + 3) & ~3
            continue
        # shortcut: ARM only, as the code this was made for; Thumb code past .text stays .rodata
        nxt = next((a for a in range(cursor + 4, min(bound, cursor + 0x4000), 4) if int.from_bytes(
            m.code[a - m.base:a - m.base + 4], "little") & ARM_PROLOGUE_MASK == ARM_PROLOGUE), None)
        if nxt is None:
            break
        cursor = nxt
    words = struct.unpack_from(f"<{(cursor - start) // 4}I", m.code, start - m.base)
    if not any(start <= c < cursor for c in calls) or sum(w >> 28 == 0xE for w in words) < 0.6 * len(words):
        return 0  # not reached from code dsd knows, or not code enough: .rodata it stays
    m.extended = (start, cursor)
    m.text[-1] = (m.text[-1][0], cursor)
    m.rodata = (cursor, m.rodata[1])
    m.dropped = {a for a in m.symbols if start <= a < cursor and a not in m.functions}
    m.symbols -= m.dropped
    m.symbol_lines = [ln for ln in m.symbol_lines if not ((s := SYMBOL.match(ln)) and int(s.group(4), 16) in m.dropped)]
    m.cover(start, cursor - start, 0)
    dsds = bytearray(m.dsds)  # no function dsd found is there (what was, its data symbols, is gone)
    dsds[start - m.base:cursor - m.base] = bytes(cursor - start)
    m.dsds = bytes(dsds)
    log(f"{m.name}: {cursor - start:#x} bytes of code past the end dsd gave .text")
    return cursor - start


def find_functions(modules: list[Module], log=print) -> int:
    """Fill each module's `added` with the functions its .text gaps hold. Returns how many."""
    words = _word_values(modules)
    # calls from the functions known (dsd's, and those found before .text grew): inside .text dsd made each target a
    # function, but not past its end
    calls = [(src, target & ~1, thumb != (mnem == "blx")) for src in modules
             for a, (s, thumb) in [*src.functions.items(), *((a, (s, t)) for a, (s, t, _) in src.added.items())]
             if s and src.in_text(a) for _, target, mnem in parse(src, a, a + s, thumb).calls]
    for m in modules:
        if not m.text:
            continue
        candidates = {(v & ~1, bool(v & 1)) for src, _, v in words
                      if (src is m or src.kind in FIXED_MODULES or m.kind in FIXED_MODULES) and m.gap_at(v & ~1)}
        candidates |= {(target, thumb) for src, target, thumb in calls
                       if (src is m or m.kind in FIXED_MODULES) and m.gap_at(target)}
        for a, b in m.text:
            for addr in range(a, b - 1, 2):
                at = addr - m.base
                if m.covered[at] or not m.in_text(addr):
                    continue
                if addr % 4 == 0 and int.from_bytes(m.code[at:at + 4], "little") & ARM_PROLOGUE_MASK == ARM_PROLOGUE:
                    candidates.add((addr, False))
                elif int.from_bytes(m.code[at:at + 2], "little") & THUMB_PROLOGUE_MASK == THUMB_PROLOGUE and not (
                        addr % 4 == 0 and m.code[at + 3] & 0xF0 == 0xE0):  # (the low half of an ARM instruction)
                    candidates.add((addr, True))
        # ARM first: ARM code read 2 bytes off as Thumb holds push-like halfwords (0xB5xx), and a Thumb "function"
        # there took the space of the real ARM one (dsd refused all 41 such on a DS game). Thumb then gets what's left
        later: set[tuple[int, bool]] = set()
        for thumb_phase in (False, True):
            if thumb_phase:
                candidates |= later
            for _ in range(MAX_PASSES):
                todo = sorted(c for c in candidates if c[0] not in m.added and c[1] == thumb_phase
                              and _starts_like_code(m, *c) and (m.gap_at(c[0]) or _inside_unsure(m, *c)))
                later |= {c for c in candidates if c[1] and not thumb_phase}
                if not todo:
                    break
                candidates = set()
                starts = sorted({a for a, _ in todo})
                for addr, thumb in todo:
                    if (host := _inside_unsure(m, addr, thumb)) is not None:  # inside a function cut by guess
                        size, t, _ = m.added[host]
                        m.cover(host, size, 0)
                        m.added[host] = (addr - host, t, False)
                        m.cover(host, addr - host)
                    if addr in m.added or not (gap := m.gap_at(addr)):
                        continue
                    bound = min([gap[1]] + [s for s in starts if s > addr])
                    p = parse(m, addr, bound, thumb)
                    if p.illegal or p.end <= addr:
                        continue  # data that only looked like a function's start (dsd would refuse it too)
                    m.added[addr] = (p.end - addr, thumb, p.sure)
                    m.cover(addr, p.end - addr)
                    if p.sure and p.end < bound:  # code right after it: another function, unless it's padding
                        nxt = (p.end + 3) & ~3
                        if nxt < bound and any(m.code[nxt - m.base:nxt - m.base + 4]):
                            word = int.from_bytes(m.code[nxt - m.base:nxt - m.base + 4], "little")
                            candidates.add((nxt, word >> 28 != 0xE))
                    for _, target, mnem in p.calls:  # bl stays in its mode, blx switches: the callee's mode
                        candidates.add((target & ~1, thumb != (mnem == "blx")))
        _drop_inconsistent(m)
        if m.added:
            log(f"{m.name}: {len(m.added)} functions dsd's analysis missed")
    return sum(len(m.added) for m in modules)


def _drop_inconsistent(m: Module) -> None:
    """Take out the functions this added whose calls and branches compiled code can't make: into another function's
    middle, or to a function of the other mode without blx. They're bytes read as code (2-byte Thumb "functions" in
    a table branched into ARM code; dsd then marked that ARM code Thumb and the linker added veneers: the ROM grew)."""
    while True:
        known = sorted([(a, s, t) for a, (s, t) in m.functions.items()] + [(a, s, t) for a, (s, t, _) in m.added.items()])
        starts = [a for a, _, _ in known]
        bad = []
        for start, (size, thumb, _) in m.added.items():
            for _, target, mnem in parse(m, start, start + size, thumb).calls:
                i = bisect.bisect_right(starts, target & ~1) - 1
                if i < 0 or not known[i][0] <= target & ~1 < known[i][0] + known[i][1]:
                    continue  # nothing there: not this one's problem
                if target & ~1 != known[i][0] or known[i][2] != (thumb != (mnem == "blx")):
                    bad.append(start)
                    break
        if not bad:
            return
        for start in bad:
            m.cover(start, m.added.pop(start)[0], 0)


def _inside_unsure(m: Module, addr: int, thumb: bool | None = None) -> int | None:
    """The start of the function this adds whose end was a guess and which holds `addr` past its start: of the same
    mode, where `addr` can start one (a Thumb start inside ARM code split it at 2 bytes, and the linker took the
    rest of that ARM function for Thumb)."""
    return next((a for a, (s, t, sure) in m.added.items() if not sure and a < addr < a + s
                 and (thumb is None or (t == thumb and (thumb or addr % 4 == 0)))), None)


def _target(modules: list[Module], m: Module, addr: int) -> Module | None:
    """The module whose symbol is at `addr`, as a relocation from `m` resolves it: its own, then main, ITCM, DTCM."""
    for x in [m] + [x for x in modules if x.kind in FIXED_MODULES and x is not m]:
        if addr in x.symbols or addr in x.added:
            return x
    return None


def relocations(modules: list[Module]) -> int:
    """Relocs for the functions each module gained and the pointers to them. Returns how many changed."""
    changed = 0
    for x in modules:  # dsd wants a symbol at every target: a data symbol that was code and is no function
        for at, line in list(x.relocs.items()):
            r = RELOC.match(line)
            dest = next((m for m in modules if m.kind == line.rsplit("module:", 1)[-1]), None)
            if r and dest and (v := int(r.group(3), 16)) in dest.dropped and v not in dest.added:
                del x.relocs[at]
                x.changed = True
                changed += 1
    for m in modules:
        for start, (size, thumb, _) in m.added.items():
            p = parse(m, start, start + size, thumb)
            for at in p.code:  # dsd scanned these bytes as data: a load on an instruction is its mistake
                if " kind:load " in m.relocs.get(at, ""):
                    del m.relocs[at]
                    m.changed = True
                    changed += 1
            for at, target, mnem in p.calls:
                if (line := _call(modules, m, at, target, mnem, thumb)) and at not in m.relocs:
                    m.relocs[at] = line
                    m.changed = True
                    changed += 1
            for at in sorted(p.pools):
                v = int.from_bytes(m.code[at - m.base:at - m.base + 4], "little")
                # shortcut: a pointer to a Thumb function (bit 0 set) stays a plain word, as dsd leaves it; it
                # needs an addend dsd's config format hasn't shown us yet, when a game's code relies on one
                if at in m.relocs or v & 1 or (dest := _target(modules, m, v)) is None:
                    continue
                m.relocs[at] = f"from:{at:#010x} kind:load to:{v:#010x} module:{dest.kind}"
                m.changed = True
                changed += 1
    # calls into the new functions from the ones there were (code dsd took for .rodata, which it never related)
    for m in modules:
        for start, (size, thumb) in m.functions.items():
            if not m.in_text(start) or not size:
                continue
            for at, target, mnem in parse(m, start, start + size, thumb).calls:
                dest = _target(modules, m, target & ~1)
                if at not in m.relocs and dest and target & ~1 in dest.added and (
                        line := _call(modules, m, at, target, mnem, thumb)):
                    m.relocs[at] = line
                    m.changed = True
                    changed += 1
    # a new ARM function's address in data (a table of functions) or in a new function's code: a load of it. Not in
    # a function dsd knew: agents matched those with the address as a number (no symbol was there), and objdiff
    # takes a plain word in the original for either a number or a symbol, but not a relocation for a number
    new = {(x.kind, a) for x in modules for a, (_, t, _) in x.added.items() if not t}
    for src, at, v in _word_values(modules):
        if at in src.relocs or v & 3 or src.dsds[at - src.base]:
            continue
        dest = next((x for x in [src] + [x for x in modules if x.kind in FIXED_MODULES] if (x.kind, v) in new), None)
        if dest is None or (src.in_text(at) and src.covered[at - src.base] and not _in_a_pool(src, at)):
            continue
        src.relocs[at] = f"from:{at:#010x} kind:load to:{v:#010x} module:{dest.kind}"
        src.changed = True
        changed += 1
    return changed


def _call(modules: list[Module], m: Module, at: int, target: int, mnem: str, thumb: bool) -> str | None:
    """The relocation, as dsd writes it, of a call at `at` in `m` (code of `thumb`'s mode) to `target`; None when
    nothing a relocation can name is there."""
    dest = _target(modules, m, target & ~1)
    if dest is None or (dest_thumb := dest.thumb_of(target & ~1)) is None:
        return None
    if dest_thumb != (thumb != (mnem == "blx")):  # a call that can't reach code of that mode: one of
        return None  # the modes is a wrong guess, and the linker would add a veneer for it (the ROM grows)
    if mnem == "b":
        if thumb or dest_thumb:
            return None  # only an ARM tail call to ARM has a kind of its own
        kind = "arm_branch"
    elif thumb:
        kind = "thumb_call_arm" if mnem == "blx" else "thumb_call"
    else:
        kind = "arm_call_thumb" if mnem == "blx" else "arm_call"
    return f"from:{at:#010x} kind:{kind} to:{target & ~1:#010x} module:{dest.kind}"


def _in_a_pool(m: Module, addr: int) -> bool:
    """Whether `addr`, inside a function, is a word of its literal pool rather than an instruction."""
    known = {**{a: (s, t) for a, (s, t) in m.functions.items()}, **{a: (s, t) for a, (s, t, _) in m.added.items()}}
    start = max((a for a, (s, _) in known.items() if a <= addr < a + s), default=None)
    return start is not None and addr in parse(m, start, start + known[start][0], known[start][1]).pools


def write(modules: list[Module]) -> list[Path]:
    """symbols.txt and relocs.txt of each module that changed, by address as dsd keeps them."""
    out = []
    for m in modules:
        if m.extended:
            old, new = (f"{a:#010x}" for a in m.extended)
            delinks = (m.folder / "delinks.txt").read_text(encoding="utf-8")
            head, rest = delinks.split("\n\n", 1) if "\n\n" in delinks else (delinks, "")
            head = re.sub(rf"^(\s+\.text\s+start:\S+ end:){old}", rf"\g<1>{new}", head, count=1, flags=re.M)
            head = re.sub(rf"^(\s+\.rodata\s+start:){old}", rf"\g<1>{new}", head, count=1, flags=re.M)
            (m.folder / "delinks.txt").write_text(head + ("\n\n" + rest if rest else ""), encoding="utf-8")
            out.append(m.folder / "delinks.txt")
        if not m.added and not m.symbols_changed:
            if m.changed:
                (m.folder / "relocs.txt").write_text("\n".join(m.relocs[k] for k in sorted(m.relocs)) + "\n",
                                                     encoding="utf-8")
                out.append(m.folder / "relocs.txt")
            continue
        kept = list(m.symbol_lines) + [f"{m.function_name(a)} kind:function({'thumb' if t else 'arm'},size={s:#x}) addr:{a:#010x}"
                 for a, (s, t, _) in m.added.items()]
        kept.sort(key=lambda ln: int(s.group(4), 16) if (s := SYMBOL.match(ln)) else -1)
        (m.folder / "symbols.txt").write_text("\n".join(kept) + "\n", encoding="utf-8")
        (m.folder / "relocs.txt").write_text("\n".join(m.relocs[k] for k in sorted(m.relocs)) + "\n", encoding="utf-8")
        out += [m.folder / "symbols.txt", m.folder / "relocs.txt"]
    return out


def _function_named(modules: list[Module], name: str) -> tuple[Module, int, bool] | None:
    """The module, address and whether recover added it (else dsd did) of the function `name`. dsd may name its own
    with the game's name rather than function_name's."""
    for m in modules:
        for a in m.added:
            if m.function_name(a) == name:
                return m, a, True
    for m in modules:
        for line in m.symbol_lines:
            s = SYMBOL.match(line)
            if s and s.group(1) == name and s.group(2) == "function" and (a := int(s.group(4), 16)) in m.functions:
                return m, a, False
        for a in m.functions:
            if m.function_name(a) == name:
                return m, a, False
    return None


def check_with_dsd(root: Path, version: str, dsd: str, modules: list[Module], log=print) -> int:
    """Delink the project with dsd, which reads every function again (by its own reading of the code, whatever size
    it's given): one it refuses (code it calls illegal on its path) is dropped, until it takes them all. That holds
    for the functions recover added and for dsd's own (a call's target init made a function where none starts, #78).
    Returns how many it refused."""
    from ...forks.project import ForkError
    from ...limits import capped

    config = root / "config" / version / "arm9" / "config.yaml"
    command = [console_python(), dsd] if Path(dsd).suffix.lower() == ".py" else [dsd]
    for refused in range(MAX_REFUSALS + 1):
        res = subprocess.run(capped([*command, "delink", "--config-path", str(config)]), cwd=root, capture_output=True,
                             text=True, timeout=3600, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        if res.returncode == 0:
            if refused:
                log(f"dsd refused {refused} function{'s' if refused != 1 else ''} (its own or ones it missed, code it "
                    "calls illegal on their path): left out")
            return refused
        said = res.stdout + res.stderr
        hit = REFUSED.search(said)
        owner = _function_named(modules, hit.group(1)) if hit else None
        if not owner or refused == MAX_REFUSALS:
            raise ForkError(f"dsd couldn't delink the game with the functions its analysis missed:\n{said.strip()[-1500:]}")
        m, start, ours = owner
        m.refusals.append(hit.group(0)[:300])
        if ours:
            m.cover(start, m.added.pop(start)[0], 0)
        else:
            # #78: `dsd init --allow-unknown-function-calls` (scratch._init) makes a call's target a function even
            # where none starts (a Thumb `lsrs` in the middle of code); dsd delinks it as its own and refuses it here
            size = m.functions.pop(start)[0]
            m.symbols.discard(start)
            m.symbol_lines = [ln for ln in m.symbol_lines if not (
                (s := SYMBOL.match(ln)) and s.group(2) == "function" and int(s.group(4), 16) == start)]
            m.cover(start, size, 0)
        m.symbols_changed = True
        for x in modules:  # nothing may point to it now: dsd wants a symbol at every relocation's target
            for at in [at for at, line in x.relocs.items() if f" to:{start:#010x} module:{m.kind}" in line]:
                del x.relocs[at]
                x.changed = True
        write(modules)
    return MAX_REFUSALS


def _file_ranges(m: Module) -> list[tuple[int, int]]:
    """The .text range of each of the module's source files (delinks.txt, after its header)."""
    files = (m.folder / "delinks.txt").read_text(encoding="utf-8").split("\n\n", 1)[1:]
    return [(int(a, 16), int(b, 16)) for a, b in re.findall(r"^\s+\.text start:(0x[0-9a-fA-F]+) end:(0x[0-9a-fA-F]+)",
                                                             files[0] if files else "", re.M)]


def joined_pieces(root: Path, version: str) -> dict[str, list[str]]:
    """Each function holding pieces joined to it before (merge_pieces left their symbols as labels), with them."""
    out: dict[str, list[str]] = {}
    for folder in (root / "config" / version / "arm9").rglob("symbols.txt"):
        owner = None
        for line in folder.read_text(encoding="utf-8").splitlines():
            if not (s := SYMBOL.match(line)):
                continue
            if s.group(2) == "function":
                owner = s.group(1)
            elif s.group(2) == "label" and s.group(1).startswith("func_") and owner:
                out.setdefault(owner, []).append(s.group(1))
    return out


def _source_files(m: Module) -> tuple[Path, str, list[str], list[tuple[int, str, int, int]]]:
    """A module's delinks.txt: its path, its header, each source file's block, and (block, file, .text start, end)."""
    path = m.folder / "delinks.txt"
    head, _, files = path.read_text(encoding="utf-8").partition("\n\n")
    blocks = files.split("\n\n")
    spans = [(k, re.search(r"^(\S.*):$", b, re.M), re.search(r"^\s+\.text start:(0x[0-9a-fA-F]+) end:(0x[0-9a-fA-F]+)", b, re.M))
             for k, b in enumerate(blocks)]
    return path, head, blocks, [(k, f.group(1), int(t.group(1), 16), int(t.group(2), 16)) for k, f, t in spans if f and t]


def _defines(root: Path, source: str, name: str) -> bool:
    """Whether the C in `source` (relative to the project) defines function `name`."""
    p = root / source
    text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    return bool(re.search(rf"\b{re.escape(name)}\s*\([^;{{]*\)\s*\{{", text))


def _move_boundary(blocks: list[str], ka: int, kb: int, old: int, new: int) -> None:
    """The .text boundary between source files ka and kb (one after the other) moves from `old` to `new`."""
    blocks[ka] = re.sub(rf"(\.text start:\S+ end:){old:#010x}", rf"\g<1>{new:#010x}", blocks[ka], count=1)
    blocks[kb] = re.sub(rf"(\.text start:){old:#010x}", rf"\g<1>{new:#010x}", blocks[kb], count=1)


def move_boundaries(root: Path, version: str, keep: set[str] = frozenset(), log=print) -> list[str]:
    """Where a source file's last function branches into the middle of the next file's first one (arm.jumps: a switch
    whose cases ds-decomp put in the next file), that function moves to the file before, so merge_pieces can join
    it: on a DS game agents saw 592 bytes of a 3 KB function, its cases out of reach in the next file. Only when the
    function moving isn't matched (`keep`), no C in its file defines it, and its file keeps some code; the files'
    names stay. Returns the functions moved."""
    from ...arch import arm

    moved = []
    for m in load_modules(root / "config" / version / "arm9", root / "extract" / version):
        path, head, blocks, spans = _source_files(m)
        names = {int(s.group(4), 16): s.group(1) for ln in m.symbol_lines
                 if (s := SYMBOL.match(ln)) and s.group(2) == "function"}
        changed = False
        for (ka, _, a_lo, a_hi), (kb, b_file, b_lo, b_hi) in zip(spans, spans[1:]):
            last = max((a for a in m.functions if a_lo <= a < a_hi), default=None)
            if last is None or a_hi != b_lo or b_lo not in m.functions or names.get(b_lo) in keep:
                continue
            size, thumb = m.functions[last]
            g_size = m.functions[b_lo][0]
            code = m.code[last - m.base:last - m.base + size]
            if not any(b_lo < t < b_lo + g_size for t in arm.jumps(code, thumb, last)) or b_lo + g_size >= b_hi:
                continue
            if _defines(root, b_file, names.get(b_lo, "?")):
                continue  # agents' C for it is in that file
            _move_boundary(blocks, ka, kb, a_hi, b_lo + g_size)
            moved.append(names.get(b_lo, f"{b_lo:#x}"))
            changed = True
        if changed:
            path.write_text(head + "\n\n" + "\n\n".join(blocks), encoding="utf-8")
    if moved:
        log(f"{len(moved)} functions moved to the source file before theirs, as its last function branches into them")
    return moved


def join_functions(root: Path, version: str, first: str, last: str, keep: set[str] = frozenset(),
                   dsd: str | None = None) -> str:
    """Make one function of `first`, `last` and every symbol between them: what agents agreed the analysis cut. The
    first grows over the rest, which become labels; bytes no symbol claims between them go with it. Across two
    source files one after the other, the later file's part moves to the earlier one (when its C doesn't define
    what moves, and that file keeps some code). Never a matched function (`keep`), data, or code of another mode.
    With `dsd`, dsd delinks the game with it, or it's taken back. Returns what changed, as a sentence."""
    from ...forks.project import ForkError

    for m in load_modules(root / "config" / version / "arm9", root / "extract" / version):
        named = {s.group(1): (i, int(s.group(4), 16), s.group(2)) for i, ln in enumerate(m.symbol_lines)
                 if (s := SYMBOL.match(ln))}
        if first not in named and last not in named:
            continue
        if first not in named or last not in named:
            raise ForkError(f"{first} and {last} aren't in the same part of the game (module)")
        (i_a, a, kind_a), (i_b, b, kind_b) = sorted((named[first], named[last]), key=lambda x: x[1])
        if kind_a != "function" or kind_b != "function" or a == b:
            raise ForkError(f"{first} and {last} must be two functions")
        inside = [(n, at, k) for n, (_, at, k) in named.items() if a <= at <= b]
        if bad := [n for n, at, k in inside if k == "function" and n in keep]:
            raise ForkError(f"{bad[0]} is matched: joining would change it, so the join is refused")
        if data := [n for n, at, k in inside if k not in ("function", "label")]:
            raise ForkError(f"{data[0]} between them is data, not code: they aren't one function")
        if len({m.functions[at][1] for _, at, k in inside if k == "function"}) > 1:
            raise ForkError("they're not the same mode (ARM and Thumb): they aren't one function")
        end = b + m.functions[b][0]
        path, head, blocks, spans = _source_files(m)
        home = [(k, f, lo, hi) for k, f, lo, hi in spans if lo <= a < hi]
        there = [(k, f, lo, hi) for k, f, lo, hi in spans if lo < end <= hi]
        if not home or not there:
            raise ForkError("they aren't in the game's source files")
        (ka, _, _, a_hi), (kb, b_file, b_lo, b_hi) = home[0], there[0]
        if ka != kb:
            if a_hi != b_lo or end >= b_hi:
                raise ForkError("they're in source files that aren't one after the other, or the later file would be "
                                "left empty")
            if moved := [n for n, at, k in inside if k == "function" and at >= b_lo and _defines(root, b_file, n)]:
                raise ForkError(f"{b_file} has C for {moved[0]}: move it to the file before first")
            before = path.read_text(encoding="utf-8")
            _move_boundary(blocks, ka, kb, a_hi, end)
            path.write_text(head + "\n\n" + "\n\n".join(blocks), encoding="utf-8")
        else:
            before = None
        lines = list(m.symbol_lines)
        for n, at, k in inside:
            if k == "function" and at != a:
                lines[named[n][0]] = f"{n} kind:label({'thumb' if m.functions[at][1] else 'arm'}) addr:{at:#010x}"
        lines[i_a] = re.sub(r"size=0x[0-9a-fA-F]+", f"size={end - a:#x}", lines[i_a], count=1)
        symbols = m.folder / "symbols.txt"
        old_symbols = symbols.read_text(encoding="utf-8")
        symbols.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if dsd:
            command = [console_python(), dsd] if Path(dsd).suffix.lower() == ".py" else [dsd]
            from ...limits import capped

            res = subprocess.run(capped([*command, "delink", "--config-path", str(root / "config" / version / "arm9" /
                                                                                      "config.yaml")]),
                                 cwd=root, capture_output=True, text=True, timeout=3600, encoding="utf-8", errors="replace",
                                 creationflags=NO_WINDOW)
            if res.returncode != 0:
                symbols.write_text(old_symbols, encoding="utf-8")
                if before is not None:
                    path.write_text(before, encoding="utf-8")
                raise ForkError(f"dsd refused the joined function: {(res.stdout + res.stderr).strip()[-300:]}")
        took = [n for n, at, k in sorted(inside, key=lambda x: x[1]) if k == "function" and at != a]
        name = next(n for n, at, _ in inside if at == a)
        return f"{', '.join(took)} joined to {name}{' (moved from ' + b_file + ')' if ka != kb else ''}"
    raise ForkError(f"{first} isn't one of the game's functions")


def merge_pieces(root: Path, version: str, dsd: str | None = None, keep: set[str] = frozenset(),
                 log=print) -> dict[str, list[str]]:
    """Join the pieces of functions dsd's analysis cut off (arch/arm.piece: a lone epilogue, a register save running
    on into the next symbol, code that never returns, code using registers the code before it set) to the function
    they belong to: an epilogue and code continuing what came before to the function before it, the others to the
    function after, which they run on into; and a function branching into the middle of the symbols after it (a
    switch whose cases were cut off) takes them, up to where its branches land. Only within one source file, in one mode,
    and never a function of `keep` (matched ones: their C would no longer be the whole function). The symbol a piece
    had stays, as a label: calls and pointers to it still have their target. On a DS game 128 of 557 open functions
    were such pieces, each given up on by five to seven agents, and the functions they belong to could never match
    without them. With `dsd`, dsd delinks the game with them, and puts them all back as they were when it refuses
    one. Returns each function that grew, with the pieces it took (none when dsd refused)."""
    from ...arch import arm
    from ...limits import capped

    joined: dict[str, list[str]] = {}
    before = {}
    for m in load_modules(root / "config" / version / "arm9", root / "extract" / version):
        ranges = _file_ranges(m)
        lines = {int(s.group(4), 16): (i, s.group(1)) for i, ln in enumerate(m.symbol_lines)
                 if (s := SYMBOL.match(ln)) and s.group(2) == "function"}
        funcs = {a: [lines[a][1], size, thumb] for a, (size, thumb) in m.functions.items() if a in lines}
        labels: set[int] = set()
        verdicts: dict[tuple[int, int], str | None] = {}  # (address, size) -> arm.piece's: read once
        changed = True
        while changed:
            changed = False
            order = sorted(funcs)
            for k, at in enumerate(order):
                name, size, thumb = funcs[at]
                if (at, size) not in verdicts:
                    code = m.code[at - m.base:at - m.base + size] if 0 <= at - m.base < len(m.code) else b""
                    verdicts[at, size] = arm.piece(code, thumb) if code else None
                why = verdicts[at, size]
                if (not why or why.startswith("jumps through")) and name not in keep:
                    if ("jumps", at, size) not in verdicts:
                        verdicts["jumps", at, size] = arm.jumps(m.code[at - m.base:at - m.base + size], thumb, at)
                    # a branch into the middle of a later symbol, in its file: that symbol is part of this function
                    file_end = next((b for a, b in ranges if a <= at < b), at)
                    if any(at + size <= t < file_end and t not in funcs for t in verdicts["jumps", at, size]):
                        why = "branches into the code after it: the start of a function cut short"
                if not why or why.startswith("jumps through") or name in keep:
                    continue
                back = "the end of" in why or "the rest of" in why  # it continues the code before it
                into = order[k - 1] if back and k else order[k + 1] if not back and k + 1 < len(order) else None
                if into is None:
                    continue
                lo, hi = min(at, into), max(at, into)
                one_file = any(a <= lo and hi < b for a, b in ranges)
                # next to each other, or with bytes between them no symbol claims (ds-decomp's analysis left them
                # out: on a DS game 68 bytes of one function lay between its two pieces), which go with them
                gap = not any(lo + funcs[lo][1] <= s < hi for s in m.symbols)
                if (not gap or funcs[into][2] != thumb or funcs[into][0] in keep or not one_file):
                    continue
                took = funcs.pop(hi)  # the lower one grows over the other and what's between, which becomes a label
                funcs[lo][1] = hi + took[1] - lo
                joined[funcs[lo][0]] = [*joined.get(funcs[lo][0], []), took[0], *joined.pop(took[0], [])]
                labels.add(hi)
                changed = True
                break
        if not labels:
            continue
        out = list(m.symbol_lines)
        for at, (i, name) in lines.items():
            if at in labels:
                out[i] = f"{name} kind:label({'thumb' if m.functions[at][1] else 'arm'}) addr:{at:#010x}"
            elif at in funcs and funcs[at][1] != m.functions[at][0]:
                out[i] = re.sub(r"size=0x[0-9a-fA-F]+", f"size={funcs[at][1]:#x}", out[i], count=1)
        before[m.folder / "symbols.txt"] = (m.folder / "symbols.txt").read_text(encoding="utf-8")
        (m.folder / "symbols.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
        log(f"{m.name}: {len(labels)} pieces of functions joined to the function they belong to")
    if dsd and before:
        command = [console_python(), dsd] if Path(dsd).suffix.lower() == ".py" else [dsd]
        config = root / "config" / version / "arm9" / "config.yaml"
        res = subprocess.run(capped([*command, "delink", "--config-path", str(config)]), cwd=root, capture_output=True,
                             text=True, timeout=3600, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        if res.returncode != 0:
            for path, text in before.items():
                path.write_text(text, encoding="utf-8")
            log(f"dsd didn't take the joined functions, so they stay apart: {(res.stdout + res.stderr).strip()[-300:]}")
            return {}
    return joined


def recover(root: Path, version: str, dsd: str | None = None, log=print) -> dict:
    """Find and write the functions dsd's analysis missed in a project's config, and (with `dsd`) delink the game with
    them. Returns what it found."""
    modules = load_modules(root / "config" / version / "arm9", root / "extract" / version)
    found = find_functions(modules, log=log)
    if sum(extend_text(m, log=log) for m in modules):  # the calls into that code may come from functions just found
        found = find_functions(modules, log=log)
    grew = any(m.extended for m in modules)
    changed = relocations(modules) if found or grew else 0
    files = write(modules) if found or grew else []
    refused = check_with_dsd(root, version, dsd, modules, log=log) if dsd and found else 0
    return {"functions": sum(len(m.added) for m in modules), "relocations": changed, "refused": refused,
            "extended": {m.name: m.extended for m in modules if m.extended},
            "files": [p.relative_to(root).as_posix() for p in files]}
