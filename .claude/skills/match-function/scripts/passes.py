#!/usr/bin/env python3
"""What the compiler did with a candidate: allocation priorities, register preferences and scheduling order.

For a near-match whose rows are register choices or instruction order, read the compiler's own passes instead of
writing more variants. This compiles the candidate with the function's own compiler and flags (plus the dump flag), the
code behind the `compiler_passes` tool, and prints

  - the register allocation table in the order GCC allocated: refs, live insns, priority -> hard register
  - each pseudo's preferred register (an argument or return register it was copied from or to)
  - the instruction order the first (`sched`) and second (`sched2`) schedulers chose
  - where the compiler's own source is (toolchains.source_for), to read the pass that decides it

    passes.py CAND.c --slug SLUG --function FUNC [--passes greg,sched,sched2] [--sched-verbose]

Diagnostic only: nothing is recorded or judged, no claim is needed. GCC-family compilers only (not IDO or Metrowerks);
the candidate must define FUNC. Comments are blanked first (preprocess.py: the classic cpp reads an apostrophe in a `//`
comment as an unterminated constant), line numbers kept. To run cc1 yourself on the preprocessed C, use
`preprocess.py CAND.c --slug S --function F`, never the bare `cpp`, which aborts on every file. Uses the judge worker (fast_judge.py), so the same `decomp` and DECOMP_HOME rules apply.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fast_judge  # noqa: E402
import preprocess  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--passes", help="comma separated: any of greg, sched, sched2 (default: all three)")
    ap.add_argument("--sched-verbose", action="store_true",
                    help="also print the first scheduler's notes (launch priorities, the set-count rule)")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    judge = fast_judge.Judge(a.slug, a.function, home=a.home)
    try:
        request = {"op": "passes", "slug": a.slug, "name": a.function, "source": preprocess.strip_comments(Path(a.source).read_text())}
        if a.sched_verbose:
            request["sched_verbose"] = True
        if a.passes:
            request["passes"] = [p.strip() for p in a.passes.split(",") if p.strip()]
        print(judge.ask(request)["text"])
    finally:
        judge.close()


if __name__ == "__main__":
    main()
