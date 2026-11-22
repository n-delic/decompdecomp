#!/usr/bin/env python3
"""A large function's diff, region by region: which stretches of the target already match and which still differ.

A 2,000-instruction function's diff is thousands of rows; a frame size that differs marks every stack row, and a fix
near the top shifts everything below it. Split at the target's basic blocks (a block starts at offset 0, at every
branch target, and after a branch and its delay slot) and merged into regions of at least `--size` target
instructions, the same rows say where to work: the first region that still differs, and which regions are done.

    regions.py FILE.c --slug S --function F [--size 40] [--show N]

Each line: the region's number, its target offsets, its instruction count, the differing rows by kind (the judge's
markers: r regalloc, s stack, i operand, o reordered, | different, < missing, > extra) and an estimated score: the
judge's penalty per row kind (scoring.py: regalloc 5, stack 1, operand 10, reorder 60, missing/extra 100, different
200). The estimate ranks regions; only the judge's total is the score. `--show N` prints region N's rows in full.
Rows come from the judge worker (`raw_rows`), the same rows `compile_and_diff` renders.
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REGION_SIZE = 40
WIDTH = {"mips": 4, "ppc": 4}  # fixed-width processors: a row's target offset is its index times this
PENALTY = {"r": 5, "s": 1, "i": 10, "o": 60, "<": 100, ">": 100, "|": 200}
# a branch's mnemonic (MIPS b*, j, jr; PowerPC b*): calls (jal, jalr, bl, bal) return and don't end a block
_BRANCH = re.compile(r"^(b[a-z.]*|j|jr)$")
_CALL = re.compile(r"^(jal|jalr|bal|bl|bgezal|bltzal|bctrl|blrl)$")
_TARGET = re.compile(r"(?:^|[ ,])(0x[0-9a-fA-F]+|\d+)\s*$")
DELAY = {"mips": 1}


def mnemonic(text):
    return text.split(None, 1)[0].lower() if text.strip() else ""


def block_starts(targets, arch="mips"):
    """Indexes (into the target's instructions) where a basic block starts."""
    width, delay = WIDTH.get(arch), DELAY.get(arch, 0)
    starts = {0}
    for index, text in enumerate(targets):
        op = mnemonic(text)
        if _CALL.match(op) or not _BRANCH.match(op) or op == "break":
            continue
        starts.add(index + 1 + delay)
        found = _TARGET.search(text.split(";", 1)[0])
        if width and found and op not in ("jr",):
            offset = int(found[1], 16) if found[1].startswith("0x") else int(found[1])
            if offset % width == 0:
                starts.add(offset // width)
    return sorted(s for s in starts if s < len(targets))


def regions(rows, arch="mips", size=REGION_SIZE):
    """The rows (marker, target, candidate) grouped into regions of whole basic blocks, at least `size` target
    instructions each (the last may be shorter). Extra rows (candidate only) belong to the region they sit in."""
    targets = [t for _, t, _ in rows if t]
    starts = block_starts(targets, arch)
    cuts, last = [], 0
    for start in starts[1:]:
        if start - last >= size:
            cuts.append(start)
            last = start
    bounds = [0] + cuts + [len(targets)]
    found = [{"index": n, "first": bounds[n], "end": bounds[n + 1], "rows": [], "kinds": Counter()}
             for n in range(len(bounds) - 1)]
    at, region = 0, 0
    for row in rows:
        while region + 1 < len(found) and at >= found[region]["end"] and row[1]:
            region += 1
        found[region]["rows"].append(row)
        if row[0] != " ":
            found[region]["kinds"][row[0]] += 1
        at += bool(row[1])
    width = WIDTH.get(arch)
    for item in found:
        item["insns"] = item["end"] - item["first"]
        item["differ"] = sum(item["kinds"].values())
        item["estimate"] = sum(PENALTY.get(kind, 10) * n for kind, n in item["kinds"].items())
        item["where"] = (f"0x{item['first'] * width:x}-0x{item['end'] * width:x}" if width
                         else f"insn {item['first']}-{item['end']}")
    return found


def table(found):
    lines = [f"{len(found)} regions, {sum(1 for r in found if not r['differ'])} already match"]
    first = next((r["index"] for r in found if r["differ"]), None)
    for r in found:
        kinds = " ".join(f"{k}{n}" for k, n in sorted(r["kinds"].items())) or "match"
        mark = "  <- first to fix" if r["index"] == first else ""
        lines.append(f"{r['index']:>3}  {r['where']:<15} {r['insns']:>4} insns  {r['differ']:>4} differ  "
                     f"~{r['estimate']:<6} {kinds}{mark}")
    return "\n".join(lines)


def show(region):
    return "\n".join(f" {m} {t:<44}| {c}" for m, t, c in region["rows"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--size", type=int, default=REGION_SIZE, help="smallest region, in target instructions")
    ap.add_argument("--show", type=int, metavar="N", help="print region N's rows in full")
    ap.add_argument("--vram", type=int)
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    from fast_judge import Judge
    judge = Judge(a.slug, a.function, vram=a.vram, home=a.home)
    try:
        reply = judge.ask(dict(judge.request, source=Path(a.source).read_text(), raw_rows=True))
    finally:
        judge.close()
    print("score", reply.get("score"))
    if reply.get("score") is None:
        print(reply.get("detail") or "")
        return 1
    found = regions(reply.get("rows") or [], judge.arch, a.size)
    print(table(found))
    if a.show is not None:
        print(show(found[a.show]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
