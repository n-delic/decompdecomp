#!/usr/bin/env python3
"""The closest matched functions to one function, with the start of their matching C: read these before writing any.

Ranked together: callers (how the result is used), callees (their prototypes), the functions just before and after it
in its file (one author's habits: the same temporaries and loop shapes), functions that read the same globals, and
instruction look-alikes. With `--source CAND.c` it also judges the candidate and adds the matched functions whose
assembly holds the mismatched region's instruction shape (the same clamp, cursor write or helper idiom), showing their
C around that place. setup.py prints this right after a claim, for the saved best; the app's brief for helpers has the
same section (without the shape search).

    neighbours.py --slug SLUG --function FUNC [--source CAND.c] [--limit 6]

Read-only, no claim needed (the judge worker, like fast_judge.py). ROM projects only: project mode stores no
cross-references yet (ROADMAP.md).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fast_judge  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--source", help="a candidate: also rank by the shape of its mismatched region")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    request = {"op": "neighbours", "slug": a.slug, "name": a.function, "limit": a.limit}
    if a.source and Path(a.source).is_file():
        judge = fast_judge.Judge(a.slug, a.function, home=a.home)
        _, rows = judge.rows(Path(a.source).read_text())
        request["rows"] = [list(row) for row in rows]
    else:
        judge = fast_judge.Judge.__new__(fast_judge.Judge)  # no target lookup: only the worker is needed
        fast_judge.Judge.__init__(judge, a.slug, a.function, vram=0, target_hex="", toolchain="-", flags=[], home=a.home)
    try:
        print(judge.ask(request)["text"])
    finally:
        judge.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
