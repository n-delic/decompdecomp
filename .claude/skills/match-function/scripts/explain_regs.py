#!/usr/bin/env python3
"""Why the candidate's registers differ from the target's: GCC's allocation read for each swapped register.

For a near-match whose rows are register swaps (`r` rows), this judges the candidate (its rows), compiles it once more
with the compiler's dumps (the function's own compiler and flags, `-da`: the lreg and greg passes), and for each pair of
registers the rows swap prints

  - the pseudo(s) in the candidate's register and in the target's: refs, live length, priority
    (floor_log2(refs) * refs / live: global.c's allocation order and local-alloc's), block-local (local-alloc) or
    global (global.c), deaths, calls crossed, copy preferences, and the C lines the compiler's line notes give them;
  - the rule that most likely decided it: the target's register live across the value (it can never get it), a
    conflicting pseudo allocated first, a lower-priority conflicting pseudo that prefers it (find_reg skips
    regs_someone_prefers in its first pass), the value's own copy preference, or local-alloc's block order;
  - what usually moves it;
  - `cause:` lines: the instruction behind each conflict and copy preference and its C line, and what kind of copy it
    is (call-argument copy: "a value computed from pseudo 80 into $a1 for the call to helper (insn 38), argument 2,
    C line 5"; return-value, call-result, incoming-argument and loop copies; an argument register set for a call
    while the value is live). A call-argument cause on the swapped register usually means the fix is that call (a
    stray or missing argument: signatures.py), not the spelling of the code around it.

    explain_regs.py CAND.c --slug SLUG --function FUNC [--dumps]

`--dumps` also prints the greg dump's conflict and preference lines. Diagnostic only: nothing is recorded, no claim is
needed (the judge worker, like fast_judge.py: the same `decomp` and DECOMP_HOME rules). GCC-family compilers only. A
lead, not a verdict: write the change it suggests and judge it. For scheduling rows, use passes.py.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claimlost  # noqa: E402
import fast_judge  # noqa: E402
import preprocess  # noqa: E402


def explain(judge, source):
    """(score, the report text, the worker's allocation reply)."""
    result, rows = judge.rows(source)
    reply = judge.ask({"op": "allocation", "slug": judge.slug, "name": judge.name, "source": source,
                       "rows": [list(row) for row in rows]})
    return result, reply.get("text") or "", reply


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--dumps", action="store_true", help="also print greg's conflict and preference lines")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    claimlost.warn()
    source = preprocess.strip_comments(Path(a.source).read_text())
    judge = fast_judge.Judge(a.slug, a.function, home=a.home)
    try:
        result, text, reply = explain(judge, source)
        print(f"score {result}" if result is not None else "score: did not compile")
        print(text)
        if a.dumps:
            print("\ngreg:")
            print("\n".join(line for line in (reply.get("greg") or "").splitlines()
                            if line.startswith((";; ", "Register "))))
    finally:
        judge.close()


if __name__ == "__main__":
    main()
