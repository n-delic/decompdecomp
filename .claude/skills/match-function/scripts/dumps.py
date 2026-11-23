#!/usr/bin/env python3
"""One command for the compiler's own reading of a candidate: explain_regs.py's register causes, then the two
schedulers' orders and greg's conflict and preference lines (passes.py), for the same source.

The skill's routine at about 20 minutes with register or order rows left (SKILL.md, step 3b): read the dumps before
another spelling. Agents that kept respelling a block whose registers were decided by a call argument or a value live
across it stalled; the dumps named the cause in one run.

    dumps.py CAND.c --slug SLUG --function FUNC [--sched-verbose]

Diagnostic only, no claim needed (the judge worker, like fast_judge.py). GCC-family compilers only.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claimlost  # noqa: E402
import explain_regs  # noqa: E402
import fast_judge  # noqa: E402
import preprocess  # noqa: E402

GREG_LINES = (";; ", "Register ")


def report(judge, source, sched_verbose=False):
    """The text: the score, the register explanation (with causes), greg's lines and both schedulers' orders."""
    result, text, reply = explain_regs.explain(judge, source)
    out = [f"score {result}" if result is not None else "score: did not compile", "", "## registers (explain_regs)", text]
    greg = [line for line in (reply.get("greg") or "").splitlines() if line.startswith(GREG_LINES)]
    if greg:
        out += ["", "## greg: conflicts and preferences", *greg]
    request = {"op": "passes", "slug": judge.slug, "name": judge.name, "source": source, "passes": ["sched", "sched2"]}
    if sched_verbose:
        request["sched_verbose"] = True
    out += ["", "## scheduling (passes.py sched, sched2)", judge.ask(request).get("text") or ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--sched-verbose", action="store_true", help="also the first scheduler's launch-priority notes")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    claimlost.warn()
    judge = fast_judge.Judge(a.slug, a.function, home=a.home)
    try:
        print(report(judge, preprocess.strip_comments(Path(a.source).read_text()), a.sched_verbose))
    finally:
        judge.close()


if __name__ == "__main__":
    main()
