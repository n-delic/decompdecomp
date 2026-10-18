"""Which compiler built each part of a game (MIPS: PS2, N64), from evidence the judge stands behind.

Big games mix compilers: GTA San Andreas is EE-GCC 2.95 with CodeWarrior and GCC 2.9x libraries linked in. The import
guesses one compiler for the whole game (rom/ps2.py: guess_compiler), and agents found the exceptions by hand, one
function at a time: of the ranges that gave, 43 were one tiny function each, and wrong.

Measured on GTA San Andreas, the compilers' habits barely tell them apart: CodeWarrior's code there moves registers,
saves $ra and frees the stack as GCC's does, and GCC 2.95, 2.96 and 991111 look alike. What the code does say:
- -G0 code never reads through $gp (it can't: that's what -G0 means);
- some habits never show in one compiler's matched code (no branch-likely in 149 large CodeWarrior functions, 9-24%
  of GCC's).
So habits only rule compilers out (contradictions), and what rules a compiler in is the judge: a function's C that
matches the game when built with another compiler, and not with today's (engine.survey_compilers tries agents'
attempts with each compiler the project uses). From those confirmations and the matched functions, a Viterbi pass
over the functions in address order (the linker lays out one object file after another, and an object file is one
compiler) spreads each compiler over its neighbours, with a cost for every change of compiler. A run another
compiler than today's built, with confirmations in it and big enough to count, is proposed as a range through the
same suggestion as an agent's, so it's checked against every matched function before it applies.
"""

from __future__ import annotations

import functools
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field

JR_RA = 0x03E00008
MIN_INSNS = 6  # shorter functions say too little about their compiler
NEVER_FROM = 40  # matched functions of a compiler before a habit they never show rules it out
NEVER_ELSEWHERE = 0.05  # ... when other compilers' matched code shows it this often
SWITCH = 8.0  # the cost of a change of compiler between neighbours (in log-likelihood)
STAY = 0.5  # the compiler the game builds an address with, today: a slight preference
CONTRADICTED = 12.0  # a habit the compiler never shows
FIXED = 50.0  # a matched or confirmed function's compiler is known
QUIRKS_KEPT = 100_000  # functions whose habits are remembered (the owner's four games have about 30,000)
_quirks_seen: dict[tuple[bytes, bool], Counter] = {}


def _quirks_of(code: bytes, big_endian: bool) -> Counter:
    """quirks(), remembered by the code itself: a function's code almost never changes, and decoding every function
    again was most of each rebuild (GTA San Andreas: 590 of 930 ms, after every match). Shared: read it only."""
    key = (code, big_endian)
    found = _quirks_seen.get(key)
    if found is None:
        if len(_quirks_seen) >= QUIRKS_KEPT:
            _quirks_seen.clear()
        found = _quirks_seen[key] = quirks(code, big_endian)
    return found


def quirks(code: bytes, big_endian: bool) -> Counter:
    """The habits a function's code shows, and how often."""
    words = struct.unpack((">" if big_endian else "<") + f"{len(code) // 4}I", code[:len(code) // 4 * 4])
    out: Counter = Counter()
    for k, w in enumerate(words):
        op, rs, rt, fn = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 63
        nxt = words[k + 1] if k + 1 < len(words) else None
        imm = w & 0xFFFF
        if op in (0x14, 0x15, 0x16, 0x17) or (op == 1 and rt in (2, 3, 0x12, 0x13)):
            out["branch-likely"] += 1
        if w == JR_RA and nxt is not None:
            if nxt == 0:
                out["nop after return"] += 1
            elif nxt >> 26 == 9 and (nxt >> 21) & 31 == 29 and (nxt >> 16) & 31 == 29:
                out["stack freed in the return's delay slot"] += 1
            else:
                out["work in the return's delay slot"] += 1
        if op == 3 and nxt == 0:
            out["nop after a call"] += 1
        if op == 0 and fn in (0x21, 0x25, 0x2D) and (rs == 0) != (rt == 0) and (w >> 11) & 31:
            out[{0x21: "addu move", 0x25: "or move", 0x2D: "daddu move"}[fn]] += 1
        if rs == 29 and rt == 31 and op in (0x1F, 0x3F, 0x2B):
            out[{0x1F: "$ra saved with sq", 0x3F: "$ra saved with sd", 0x2B: "$ra saved with sw"}[op]] += 1
        if rs == 28 and (op in (0x09, 0x19) or 0x20 <= op <= 0x3F):
            out["$gp-relative"] += 1
        if op == 9 and rs == 29 and rt == 29 and imm >= 0x8000:
            if k == 0:
                out["starts by moving the stack"] += 1
            out["16-byte frame" if (0x10000 - imm) % 16 == 0 else "8-byte frame"] += 1
        if op == 0x11 and rs == 4:
            out["mtc1"] += 1
        if op == 0x31 and rs == 28:
            out["float constant through $gp"] += 1
        if op == 0 and fn == 0x0D and (w >> 16) & 0x3FF == 7:
            out["break 7 after a division"] += 1
        if op == 0 and fn == 0x34:
            out["teq trap"] += 1
        if op == 0x1C:
            out["MMI instruction"] += 1
    return out


Label = tuple[str, tuple[str, ...]]  # (toolchain, flags)
KINDS_KEPT = 1024  # compiler labels whose kind is remembered: a game uses a handful, asked for every function


@functools.lru_cache(maxsize=KINDS_KEPT)
def kind(label: Label) -> tuple[str, str]:
    """What a compiler's code can be told by: the toolchain and its small-data setting (-G8 code reads through $gp,
    -G0 code never does). Other flags (-lang c++, -O3 or -O3,p) hardly change the code's habits, so labels that differ
    only in them are one kind, which their matches teach together."""
    tc, flags = label
    small = next((f for f in flags if f.startswith("-G")), "")
    if "-sdatathreshold" in flags[:-1]:
        small = "-sdatathreshold " + flags[flags.index("-sdatathreshold") + 1]
    return tc, small


@dataclass
class Run:
    start: int
    end: int  # the end of its last function
    label: Label  # the compiler its code looks like (the flags its matches used most)
    functions: int = 0
    matched: int = 0  # of them, matched with a compiler of this kind
    confirmed: int = 0  # of them, whose C the judge matched with it (and not with today's)
    assigned: Counter = field(default_factory=Counter)  # the compilers the game builds its functions with today

    def differs(self) -> bool:
        return kind(self.assigned.most_common(1)[0][0]) != kind(self.label)


@dataclass
class CompilerMap:
    of: dict[int, Label]  # function address -> the compiler its code looks like
    runs: list[Run]
    examples: Counter  # kind -> matched and confirmed functions
    contradicted: set[int] = field(default_factory=set)  # functions with a habit their compiler never shows
    never: dict[tuple, list[str]] = field(default_factory=dict)  # kind -> the habits that rule it out

    def proposals(self, proof_bytes: int, proof_functions: int) -> list[Run]:
        """Runs the judge says another compiler than today's built, big enough to count as evidence."""
        return [r for r in self.runs if r.differs() and r.confirmed
                and (r.end - r.start >= proof_bytes or r.functions >= proof_functions)]


def build(functions: list[tuple[int, int, bytes, Label, bool, Label | None]], big_endian: bool) -> CompilerMap:
    """The map from (address, size, code, the compiler the game builds it with today, matched, the compiler the
    judge confirmed for it or None) of every function."""
    funcs = sorted(functions)
    q = {f[0]: _quirks_of(f[2], big_endian) for f in funcs if len(f[2]) // 4 >= MIN_INSNS}
    known: dict[int, Label] = {f[0]: f[5] or f[3] for f in funcs if f[4] or f[5]}  # matched or confirmed
    kinds = sorted({kind(f[3]) for f in funcs} | {kind(lab) for lab in known.values()})
    examples = Counter(kind(lab) for a, lab in known.items() if a in q)
    labels: dict[tuple, Counter] = defaultdict(Counter)
    for f in funcs:
        labels[kind(f[3])][f[3]] += 1
    for lab in known.values():
        labels[kind(lab)][lab] += 100  # the flags it really matched with
    label_of = {k: labels[k].most_common(1)[0][0] for k in kinds}
    never = _never(q, known, examples, kinds)

    def emission(addr: int, today: Label, confirmed: Label | None, matched: bool) -> list[float]:
        if addr in known:
            mine = kind(known[addr])
            return [0.0 if k == mine else -FIXED for k in kinds]
        mine = kind(today)
        return [(STAY if k == mine else 0.0) - (CONTRADICTED if addr in q and never[k] & q[addr].keys() else 0.0)
                for k in kinds]

    # Viterbi: the likeliest compiler for every function, changing compiler only where the evidence pays for it
    n = len(kinds)
    score = [0.0] * n
    back: list[list[int]] = []
    for addr, size, _, today, matched, confirmed in funcs:
        e = emission(addr, today, confirmed, matched)
        best = max(range(n), key=lambda j: score[j])
        row, new = [], []
        for i in range(n):
            j = i if score[i] >= score[best] - SWITCH else best
            row.append(j)
            new.append(score[j] - (SWITCH if j != i else 0.0) + e[i])
        back.append(row)
        score = new
    i = max(range(n), key=lambda j: score[j])
    picked = [0] * len(funcs)
    for k in range(len(funcs) - 1, -1, -1):
        picked[k] = i
        i = back[k][i]
    # A run reaches as far as its evidence: past its first and last matched or confirmed function, nothing says its
    # compiler goes on (the switch cost only decides gaps between evidence), so those functions keep today's.
    start = 0
    while start < len(funcs):
        end = start
        while end + 1 < len(funcs) and picked[end + 1] == picked[start]:
            end += 1
        k = kinds[picked[start]]
        sure = [i for i in range(start, end + 1) if funcs[i][0] in known and kind(known[funcs[i][0]]) == k]
        for i in range(start, end + 1):
            if not sure or i < sure[0] or i > sure[-1]:
                picked[i] = kinds.index(kind(funcs[i][3]))
        start = end + 1
    of = {funcs[k][0]: label_of[kinds[picked[k]]] for k in range(len(funcs))}
    contradicted = {f[0] for f in funcs if f[0] in q and f[0] not in known and never[kind(f[3])] & q[f[0]].keys()}
    return CompilerMap(of, _runs(funcs, of), examples, contradicted, {k: sorted(v) for k, v in never.items() if v})


def _never(q: dict[int, Counter], known: dict[int, Label], examples: Counter, kinds: list) -> dict[tuple, set[str]]:
    """Per compiler kind, the habits that rule it out: $gp for small data off, and what its matched code never
    shows while the others' often does."""
    out: dict[tuple, set[str]] = defaultdict(set)
    shows: dict[tuple, Counter] = defaultdict(Counter)
    for a, lab in known.items():
        if a in q:
            shows[kind(lab)].update(set(q[a]))
    for k in kinds:
        if k[1] in ("-G0", "-sdatathreshold 0"):
            out[k].add("$gp-relative")
        if examples[k] < NEVER_FROM:
            continue
        others = sum(examples[o] for o in examples if o[0] != k[0])
        for habit in {h for o in shows for h in shows[o]}:
            elsewhere = sum(shows[o][habit] for o in shows if o[0] != k[0])
            if not shows[k][habit] and others and elsewhere / others >= NEVER_ELSEWHERE:
                out[k].add(habit)
    return out


def _runs(funcs, of: dict[int, Label]) -> list[Run]:
    runs: list[Run] = []
    for addr, size, _, today, matched, confirmed in funcs:
        label = of[addr]
        if not runs or kind(runs[-1].label) != kind(label):
            runs.append(Run(addr, addr + size, label))
        r = runs[-1]
        r.end, r.functions = addr + size, r.functions + 1
        r.matched += bool(matched) and kind(today) == kind(label)
        r.confirmed += confirmed is not None and kind(confirmed) == kind(label)
        r.assigned[today] += 1
    return runs
