"""Why a candidate's saved registers differ from the target's: GCC 2.x's own allocation priorities, read back.

Register-only differences (the `r` rows of a diff) are the commonest last mile of a near match, and the old GCCs
decide them by one rule (global.c): the pseudo registers that live across calls are handed callee-saved registers
($s0, $s1, ...) in order of

    priority = floor_log2(uses) * uses / live_length

where `uses` counts every reference weighted by loop depth and `live_length` the instructions the pseudo is live
over. Each takes the lowest saved register that is free over its whole range, trying first the registers an earlier
pseudo already took and only then a new one (global.c find_reg: pass 0 never allocates a register for the first
time, pass 1 does), and skipping a register a lower-ranked pseudo that overlaps it prefers. Two pseudos joined by a
copy whose source dies there can share one allocation unit (their uses add up, the live length is the longer one),
which is why a copied temporary can outrank both of its parts. Nothing in the C source
names a register, so a search that only compares instructions learns "a register differs" and nothing about why.
Compiling the candidate once with `-dl -dg` makes the compiler print the numbers: a table of each pseudo's uses,
length, priority and final register, in the order it allocated them. This module reads that table and, for the
register swaps a diff shows, says which pseudo outranked which and what moves the order (a reference more or fewer
inside its range, a longer or shorter live range), so a helper or the permuter can aim at the cause.

Only the GCC 2.x family prints these dumps (gcc-2.7.2-kmc, gcc-2.8.1-pm, egcs); any other compiler, and any
compile that writes no dump, gives no table and no advice. Nothing here changes a verdict: the byte judge decides."""
import dataclasses
import math
import re
from pathlib import Path

# the GCC 2.x toolchains that take -dl -dg (the folder name of the driver)
GCC2 = re.compile(r"/gcc-(?:2\.|egcs)[^/]*/")
FLAGS = ("-dl", "-dg")
# MIPS names of the hard registers a dump numbers; other processors show the number
MIPS = {2: "$v0", 3: "$v1", 4: "$a0", 5: "$a1", 6: "$a2", 7: "$a3", **{8 + i: f"$t{i}" for i in range(8)},
        **{16 + i: f"$s{i}" for i in range(8)}, 24: "$t8", 25: "$t9", 30: "$fp", 31: "$ra"}
_USED = re.compile(r"^Register (\d+) used (\d+) times across (\d+) insns;(.*)$", re.M)
_ORDER = re.compile(r"^;; (\d+) regs to allocate:([\d ]*)$", re.M)
_DISPOSITION = re.compile(r"(\d+) in (\d+)")
_CONFLICT = re.compile(r"^;; (\d+) conflicts:([\d ]*)$", re.M)
_INSN = re.compile(r"\n(?=\((?:insn|jump_insn|call_insn) \d+ )")
SALIENT = 6  # the table in a brief: the pseudos around the swapped registers, never the whole function


@dataclasses.dataclass(frozen=True)
class Pseudo:
    number: int
    uses: int
    length: int
    hard: int | None  # the hard register it got, None when it is a caller-saved or unallocated one
    order: int | None  # its place in the global allocation order
    role: str
    conflicts: frozenset = frozenset()  # the pseudos live at the same time as this one

    @property
    def priority(self) -> float:
        return math.floor(math.log2(self.uses)) * self.uses / self.length if self.uses and self.length else 0.0


def enabled(cmd) -> bool:
    """Does this compile command run a GCC 2.x driver that prints the dumps?"""
    return bool(cmd) and bool(GCC2.search(str(cmd[0]).replace("\\", "/")))


def role(number: int, insns: list[str]) -> str:
    """What the pseudo looks like from its instructions: a loop counter (set to 0, then plus 1), an address held
    in a register, a user variable or a compiler temporary. A hint for the reader, never a name."""
    mine = [i for i in insns if re.search(rf"\(reg[^ ]* {number}\)", i)]
    text = "\n".join(mine)
    if re.search(rf"\(set \(reg[^ ]* {number}\)\s*\(const_int 0\)", text) and re.search(
            rf"\(plus:SI \(reg[^ ]* {number}\)\s*\(const_int 1\)", text):
        return "loop counter"
    symbol = re.search(rf'\(set \(reg[^ ]* {number}\)\s*\((?:const:SI \(plus:SI )?\(?symbol_ref:SI \("([^"]+)"\)', text)
    if symbol:
        return f"address of {symbol[1]}"
    return "user variable" if re.search(rf"\(reg/v[^ ]* {number}\)", text) else "temporary"


def parse(lreg: str, greg: str) -> list[Pseudo]:
    """The global allocation table, in allocation order, from the dumps of `-dl` (uses, lengths, instructions)
    and `-dg` (the order and the registers)."""
    order = _ORDER.search(greg)
    if not order:
        return []
    ranked = [int(n) for n in order[2].split()]
    disposition = greg.split("Register dispositions:", 1)[1].split("Hard regs used", 1)[0] if "Register dispositions:" in greg else ""
    hard = {int(a): int(b) for a, b in _DISPOSITION.findall(disposition)}
    stats = {int(m[1]): (int(m[2]), int(m[3])) for m in _USED.finditer(lreg)}
    insns = _INSN.split(lreg)
    conflicts = {int(m[1]): frozenset(int(x) for x in m[2].split()) for m in _CONFLICT.finditer(greg)}
    rows = []
    for place, number in enumerate(ranked):
        uses, length = stats.get(number, (0, 0))
        rows.append(Pseudo(number, uses, length, hard.get(number), place, role(number, insns), conflicts.get(number, frozenset())))
    return rows


def name(register: int | None, arch: str = "mips") -> str:
    return "unallocated" if register is None else (MIPS.get(register, f"hard register {register}") if arch == "mips" else f"hard register {register}")


def swaps(rows) -> dict[tuple[str, str], int]:
    """How often each (candidate register, target register) pair differs in the diff's `r` rows."""
    found: dict[tuple[str, str], int] = {}
    for row in rows or []:
        if len(row) != 3 or row[0] != "r":
            continue
        target, candidate = re.findall(r"\$\w+", row[1]), re.findall(r"\$\w+", row[2])
        for want, got in zip(target, candidate):
            if want != got:
                found[(got, want)] = found.get((got, want), 0) + 1
    return found


def describe(p: Pseudo, arch: str = "mips") -> str:
    return (f"{p.role} (pseudo {p.number}: {p.uses} weighted uses over {p.length} instructions, priority "
            f"{p.priority:.2f}, allocated {p.order + 1}th) in {name(p.hard, arch)}")


def advice(table: list[Pseudo], rows, arch: str = "mips") -> list[str]:
    """One note per saved-register swap of the diff: the pseudo the candidate keeps in the register the target gives
    another, and the overlapping pseudo that holds the target's register, with their priorities. Only pseudos live
    at the same time compete for a register, so only those are compared."""
    if not table:
        return []
    number = {name: r for r, name in MIPS.items()}
    merged: dict[frozenset, int] = {}
    for (got, want), count in swaps(rows).items():
        if re.fullmatch(r"\$s[0-7]", got) and re.fullmatch(r"\$s[0-7]", want):  # (caller-saved ones follow insn order)
            merged[frozenset((got, want))] = merged.get(frozenset((got, want)), 0) + count
    notes = []
    for pair, count in sorted(merged.items(), key=lambda item: -item[1]):
        low, high = sorted(pair)
        lows = [p for p in table if p.hard == number[low]]
        highs = [p for p in table if p.hard == number[high]]
        rivals = [(a, b) for a in lows for b in highs if b.number in a.conflicts or a.number in b.conflicts]
        if not rivals:
            continue
        a, b = max(rivals, key=lambda pair: min(pair[0].priority, pair[1].priority))
        first, second = (a, b) if a.order < b.order else (b, a)
        notes.append(
            f"Register swap {low} <-> {high} ({count} instruction{'s' if count != 1 else ''}): the candidate has "
            f"{describe(a, arch)}, and, live at the same time, {describe(b, arch)}. GCC 2.x allocates saved registers "
            f"by floor_log2(uses)*uses/length and gives each the lowest free one it can reuse, so the {first.role} (priority "
            f"{first.priority:.2f}) was placed before the {second.role} (priority {second.priority:.2f}). To reverse "
            "them, lower the first one's priority (fewer or cheaper references inside its range, or a longer live "
            "range, for example the same variable also live across other code) or raise the second's (a reference "
            "more inside its range). This is a hypothesis from the compiler's own dump; only the byte judge accepts "
            "a match.")
    return notes


def table_text(table: list[Pseudo], arch: str = "mips", limit: int = 12) -> str:
    return "\n".join(f"{p.order + 1:>3}. pseudo {p.number} {p.role}: uses {p.uses}, length {p.length}, "
                     f"priority {p.priority:.3f}, {name(p.hard, arch)}" for p in table[:limit])


def dump(cfg, header: str, source: str) -> list[Pseudo] | None:
    """Compile `source` once with -dl -dg and read the allocation table, or None when this compiler has no such
    dump or the compile fails. The compile skips every object cache (a dump is not an object)."""
    from .. import compiler
    if not enabled(cfg.cmd):
        return None
    probe = dataclasses.replace(cfg, cmd=[cfg.cmd[0], *FLAGS, *cfg.cmd[1:]], identity=None)
    seen: dict[str, str] = {}

    def sink(work):
        for path in Path(work).rglob("*"):
            if path.suffix in (".lreg", ".greg") and path.is_file():
                seen[path.suffix] = path.read_text(errors="replace")
    with compiler.capture_artifacts(sink):
        result = compiler.compile_source(probe, header, source)
    if not result.ok or ".lreg" not in seen or ".greg" not in seen:
        return None
    return parse(seen[".lreg"], seen[".greg"]) or None


_CACHE: dict = {}


def hints(net, proj, fn, header: str, source: str, rows) -> list[str]:
    """Advice for a diff whose differences include saved-register swaps; empty for other compilers or other
    differences. Cached on the candidate, so a diff read twice compiles once."""
    if proj["arch"] != "mips" or not swaps(rows):
        return []
    key = (fn["id"], hash(source), hash(header))
    if key not in _CACHE:
        if len(_CACHE) > 64:
            _CACHE.pop(next(iter(_CACHE)))
        try:
            _cfg, invocation = net._invocation(proj, fn)
            _CACHE[key] = dump(invocation, header, source)
        except Exception:  # advisory only: a dump that can't be made is no diagnostic, never a failed diff
            _CACHE[key] = None
    table = _CACHE[key]
    return advice(table, rows, proj["arch"]) if table else []
