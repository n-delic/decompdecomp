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
    if not thumb:
        return addr % 4 == 0 and len(m.code) >= at + 4 and m.code[at + 3] & 0xF0 == 0xE0
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
        if not m.added:
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


def check_with_dsd(root: Path, version: str, dsd: str, modules: list[Module], log=print) -> int:
    """Delink the project with dsd, which reads every function again (by its own reading of the code, whatever size
    it's given): one it refuses (code it calls illegal on its path) is dropped, until it takes them all. Returns how
    many it refused."""
    from ...forks.project import ForkError
    from ...limits import capped

    config = root / "config" / version / "arm9" / "config.yaml"
    for refused in range(MAX_REFUSALS + 1):
        res = subprocess.run(capped([dsd, "delink", "--config-path", str(config)]), cwd=root, capture_output=True,
                             text=True, timeout=3600, encoding="utf-8", errors="replace")
        if res.returncode == 0:
            if refused:
                log(f"dsd refused {refused} of them (code it calls illegal on their path): left out")
            return refused
        said = res.stdout + res.stderr
        hit = REFUSED.search(said)
        owner = hit and next(((x, a) for x in modules for a in x.added if x.function_name(a) == hit.group(1)), None)
        if not owner or refused == MAX_REFUSALS:
            raise ForkError(f"dsd couldn't delink the game with the functions its analysis missed:\n{said.strip()[-1500:]}")
        m, start = owner
        m.refusals.append(hit.group(0)[:300])
        m.cover(start, m.added.pop(start)[0], 0)
        for x in modules:  # nothing may point to it now: dsd wants a symbol at every relocation's target
            for at in [at for at, line in x.relocs.items() if f" to:{start:#010x} module:{m.kind}" in line]:
                del x.relocs[at]
                x.changed = True
        write(modules)
    return MAX_REFUSALS


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
