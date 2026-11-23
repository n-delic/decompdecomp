#!/usr/bin/env python3
"""Every call in a function's C checked against what the project knows of the callee, with the edit to make.

An m2c prototype is a guess from the registers live at a call, and a stray or missing argument moves code far from
the call: a display-list builder's last nine rows (55 -> 0) were one argument its callee's matched C never took, after
twelve spellings of the cursor block around it compiled identically. This prints, for the saved best (or CAND.c):

  - a call whose argument count differs from the callee's matched C, or a void callee whose result is used;
  - a header prototype that disagrees with the callee's matched C (parameters, or the return type's width/sign);
  - (MIPS) a call whose target site sets more argument registers than the C passes, reads $v0 the C ignores, or
    overwrites $v0 the C uses;
  - the function's own parameters against what its matched callers pass and what its target reads first.

    signatures.py --slug SLUG --function FUNC [CAND.c]

setup.py prints it right after a claim. Read-only, no claim needed (the judge worker, like fast_judge.py). ROM projects
only (the worker's). A lead, not a verdict: make the edit and judge it.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fast_judge  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="the candidate (default: the app's saved best)")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    judge = fast_judge.Judge.__new__(fast_judge.Judge)  # no target lookup: only the worker is needed
    fast_judge.Judge.__init__(judge, a.slug, a.function, vram=0, target_hex="", toolchain="-", flags=[], home=a.home)
    request = {"op": "signatures", "slug": a.slug, "name": a.function}
    if a.source:
        request["source"] = Path(a.source).read_text()
    try:
        reply = judge.ask(request)
    finally:
        judge.close()
    print(reply.get("text") or reply.get("error") or "")
    return 1 if reply.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
