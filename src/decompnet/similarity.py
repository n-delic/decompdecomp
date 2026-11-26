"""Find functions that look alike: same instructions in a similar order.

Copy-pasted code, shared inlined helpers and template instantiations compile to look-alikes. A matched look-alike is the best starting point there is: its C often needs only
small changes. Comparison is on mnemonic sequences (operands differ between copies), pre-filtered by length
and instruction mix so that ranking a whole game stays fast.
"""

from __future__ import annotations

import math
from collections import Counter
from difflib import SequenceMatcher

MAX_OPS = 800  # compare at most this many instructions of very large functions


def mips_mnemonics(code: bytes, big_endian: bool = True, vram: int = 0, cpu: str = "") -> list[str]:
    """A MIPS function's instructions by name (operands differ between copies of the same code). `cpu`: the
    console's processor variant (Console.cpu_variant): read as plain MIPS, a PSP's vadd, vsub and vdiv would all be
    daddi, and unlike code would look alike."""
    import rabbitizer

    from .arch import mips

    category = mips.rabbitizer_category(cpu)
    order = "big" if big_endian else "little"
    out = []
    for i in range(0, len(code) - 3, 4):
        word = int.from_bytes(code[i:i + 4], order)
        out.append(rabbitizer.Instruction(word, vram + i, category).getOpcodeName())
    return out


def mnemonics(lines: list[str]) -> list[str]:
    """Mnemonics from formatted instructions ("lwz r3, 0x10(r31)" -> "lwz")."""
    return [l.split(None, 1)[0].lower() for l in lines if l.strip()]


def _cosine(a: Counter, b: Counter) -> float:
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def score(a: list[str], b: list[str]) -> float:
    """0..1, 1 = the same instruction sequence."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a[:MAX_OPS], b[:MAX_OPS], autojunk=False).ratio()


def rank(target: list[str], candidates: dict[str, list[str]], top: int = 5, floor: float = 0.6) -> list[tuple[str, float]]:
    """The `top` candidates most similar to `target`, best first, at least `floor` similar."""
    if len(target) < 4:  # tiny functions all look alike
        return []
    th = Counter(target)
    rough = []
    for name, ops in candidates.items():
        if not ops or not (0.6 <= len(ops) / len(target) <= 1 / 0.6):
            continue
        c = _cosine(th, Counter(ops))
        if c >= floor:
            rough.append((c, name))
    rough.sort(reverse=True)
    exact = [(name, score(target, candidates[name])) for _, name in rough[:150]]
    exact = [(n, s) for n, s in exact if s >= floor]
    exact.sort(key=lambda x: -x[1])
    return exact[:top]


# ------------------------------------------------------------------ a library-wide shortlist by content

SHINGLE = 4  # instructions per shingle: long enough that unrelated code rarely shares many
COMMON_SHINGLE = 4000  # a shingle in more functions than this (a nop run, a prologue) says nothing about likeness


def mips_shape(code: bytes, big_endian: bool = True) -> tuple[int, ...]:
    """A MIPS function's instructions as opcode classes, without operands: the same comparison as
    `mips_mnemonics` (an opcode, a SPECIAL function, a REGIMM or coprocessor sub-opcode), read straight from the
    words. Disassembling every matched function of every game took longer than a pass's budget; this reads 30,000
    of them in a second."""
    import struct
    n = min(len(code) // 4, MAX_OPS)
    out = []
    for w in struct.unpack(('>' if big_endian else '<') + 'I' * n, code[:n * 4]):
        op = w >> 26
        if w == 0:
            out.append(300)  # nop
        elif op == 0:
            out.append(64 + (w & 63))
        elif op == 1:
            out.append(128 + ((w >> 16) & 31))
        elif 16 <= op <= 19:
            out.append(160 + (op - 16) * 32 + ((w >> 21) & 31))
        else:
            out.append(op)
    return tuple(out)


def shingles(shape) -> set:
    return {hash(shape[i:i + SHINGLE]) for i in range(len(shape) - SHINGLE + 1)}


class ShingleIndex:
    """Every matched function of the library, findable by what its code looks like rather than by its size.

    The similar-code pass took the 400 donors nearest in byte size and ranked those: with tens of thousands of
    matched functions across a user's games, the donor most like an open function was usually not among them
    (in a sample of 6,000 open N64 functions, 5,500 projected to have a 90%-similar matched donor elsewhere, 15%
    of them ever tried). Shared shingles of four instructions find it whatever its size or id."""

    def __init__(self):
        self.shapes: dict = {}
        self.counts: dict = {}
        self.postings: dict = {}

    def add(self, key, shape) -> None:
        if key in self.shapes:
            return
        self.shapes[key] = shape
        found = shingles(shape)
        self.counts[key] = len(found)
        for g in found:
            self.postings.setdefault(g, []).append(key)

    def estimate(self, shape) -> float:
        """How alike the closest indexed function is by shared shingles alone (0..1): cheap enough to order a
        whole game by, never a verdict."""
        mine = shingles(shape)
        if not mine:
            return 0.0
        counts: dict = {}
        for g in mine:
            post = self.postings.get(g)
            if post and len(post) <= COMMON_SHINGLE:
                for key in post:
                    counts[key] = counts.get(key, 0) + 1
        return max((c / max(len(mine), self.counts[k]) for k, c in counts.items()), default=0.0)

    def query(self, shape, top: int = 48, *, ratio: float = .75, eligible=None) -> list:
        """The `top` keys sharing the most shingles with `shape`, best first, sized within `ratio` of it."""
        if len(shape) < SHINGLE:
            return []
        counts: dict = {}
        for g in shingles(shape):
            post = self.postings.get(g)
            if post and len(post) <= COMMON_SHINGLE:
                for key in post:
                    counts[key] = counts.get(key, 0) + 1
        n = len(shape)
        ranked = sorted(((c, k) for k, c in counts.items()
                         if (ratio <= 0 or ratio <= len(self.shapes[k]) / n <= 1 / ratio) and (eligible is None or eligible(k))),
                        key=lambda item: -item[0])
        return [k for _, k in ranked[:top]]

    def best(self, shape, candidates: int = 6, **kwargs) -> float:
        """How alike the closest indexed function is (0..1, `score`'s scale), from the shingle shortlist."""
        found = self.query(shape, candidates, **kwargs)
        return max((score(list(shape), list(self.shapes[k])) for k in found), default=0.0)
