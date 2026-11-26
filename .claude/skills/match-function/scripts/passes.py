#!/usr/bin/env python3
"""What the compiler did with a candidate: allocation priorities, register preferences and scheduling order.

For a near-match whose rows are register choices or instruction order, read the compiler's own passes instead of
writing more variants. This compiles the candidate with the function's own compiler and flags (plus the dump flag), the
code behind the `compiler_passes` tool, and prints

  - the register allocation table in the order GCC allocated: refs, live insns, priority -> hard register
  - each pseudo's preferred register (an argument or return register it was copied from or to)
  - the instruction order the first (`sched`) and second (`sched2`) schedulers chose
  - where the compiler's own source is (toolchains.source_for), to read the pass that decides it

  - the loop optimizer's own notes (hoists, induction variables, givs combined or refused) from the `loop` dump

    passes.py CAND.c --slug SLUG --function FUNC [--passes greg,sched,sched2] [--sched-verbose] [--save DIR]

`--save DIR` also keeps every file the compile left, named FUNC.<kind> in DIR: `i` (the preprocessed C), `s` (the final
assembly), the pass dumps (`rtl jump cse loop cse2 flow combine sched lreg greg sched2 jump2 dbr`) and `report.txt` (what
this prints). That is the whole hand-rolled recipe (cppn64 and cc1n64.exe under wibo with -dl -dg -dS -dR -da) in one
command, for sn-n64-281 and the other SN profiles as for the GCC ones.

Diagnostic only: nothing is recorded or judged, no claim is needed. GCC-family compilers only, SN's included (not IDO or Metrowerks);
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


def save_files(folder, function, report, files):
    """Write the compile's files as FUNC.<kind> and the report as FUNC.report.txt; the line that names them."""
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for kind, text in {**files, "report.txt": report}.items():
        (folder / f"{function}.{kind}").write_text(text)
        names.append(f"{function}.{kind}")
    return f"\nSaved in {folder}: " + ", ".join(names)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--passes", help="comma separated: any of greg, sched, sched2 (default: all three)")
    ap.add_argument("--sched-verbose", action="store_true",
                    help="also print the first scheduler's notes (launch priorities, the set-count rule)")
    ap.add_argument("--save", metavar="DIR", help="also write the preprocessed C, the final assembly and each pass dump here")
    ap.add_argument("--home")
    ap.add_argument("--flags", metavar="'-mfp32 ...'", help="extra compiler flags added to the function's own, as fast_judge.py --flags takes them (default $DD_EXTRA_FLAGS): the dumps are of that compile")
    a = ap.parse_args(argv)
    judge = fast_judge.Judge(a.slug, a.function, home=a.home, extra_flags=a.flags.split() if a.flags is not None else None)
    try:
        request = {"op": "passes", "slug": a.slug, "name": a.function, "source": preprocess.strip_comments(Path(a.source).read_text()),
                   "flags": judge.extra_flags}
        if a.sched_verbose:
            request["sched_verbose"] = True
        if a.passes:
            request["passes"] = [p.strip() for p in a.passes.split(",") if p.strip()]
        if a.save:
            request["save"] = True
        reply = judge.ask(request)
        print(reply["text"])
        if a.save:
            print(save_files(Path(a.save), a.function, reply["text"], reply.get("files") or {}))
    finally:
        judge.close()


if __name__ == "__main__":
    main()
