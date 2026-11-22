#!/usr/bin/env python3
"""Does a chain of library repairs reach the match from here? Apply the best child of chosen families, step by step.

After a manual match, step 5 of the skill asks whether the permuter could have found the steps. This answers it for the
families you name, without the Deep Permuter: at each step it asks the app's own proposal code
(`draft_repairs.variants`, the path drafts and the permuter's beam use) for every child of the current source, keeps
those from the chosen families, scores them one by one with the standalone judge (fast_judge.py) and keeps the best
child that beats the current score. It stops at 0, when no child improves, or after --steps. Bounded: --children caps
the children scored per step (default 80, about 8 seconds of judging), one judge worker, nothing parallel.

    prove_chain.py --slug SLUG --function FUNC [SOURCE.c] [--families a,b,c | all] [--steps 8] [--children 80]
                   [--out BEST.c] [--list]

SOURCE.c defaults to the app's saved best (read through the app, no claim). `--list` prints which families produce
children for the source and how many, then exits (a family that produces none cannot be in the chain). Each step prints
`step N  family  score  (children scored)`; the chain ends with the final score, and --out keeps the final source.
It scores with the app's judge, so a chain that reaches 0 is a candidate for compile_and_diff, not yet a recorded match.
Run it with the python of a setup.py session (it imports decompnet): `$UV_PROJECT_ENVIRONMENT/bin/python prove_chain.py`.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fast_judge  # noqa: E402

DEFAULT_STEPS = 8
DEFAULT_CHILDREN = 80


def prove(source, generate, score, families, steps=DEFAULT_STEPS, children=DEFAULT_CHILDREN, log=print):
    """The greedy chain. `generate(source) -> [(family, child)]`, `score(source) -> int | None` (None: didn't
    compile, never an improvement). Returns (final source, final score, [(family, score)])."""
    current, best = source, score(source)
    if best is None:
        raise SystemExit("the starting source doesn't compile with the judge: fix that first (is its name the function's?)")
    chain = []
    log(f"start  score {best}")
    for step in range(1, steps + 1):
        if best == 0:
            break
        options, seen = [], {current}
        for family, child in generate(current):
            if (families is None or family in families) and child not in seen:
                seen.add(child)
                options.append((family, child))
        cut = len(options) - children
        if cut > 0:
            log(f"step {step}  note: {cut} of {len(options)} children cut off by --children {children}; "
                f"raise it (or name --families) if the right child may be among them")
        scored = []
        for family, child in options[:children]:
            result = score(child)
            if result is not None:
                scored.append((result, family, child))
        if not scored or min(row[0] for row in scored) >= best:
            log(f"step {step}  no child of {len(options)} improves on {best} (scored {len(scored)}): stopped")
            break
        result, family, child = min(scored, key=lambda row: row[0])
        current, best = child, result
        chain.append((family, result))
        log(f"step {step}  {family}  {result}  ({len(scored)} of {len(options)} children scored)")
    log("matched: score 0" if best == 0 else f"final score {best}")
    return current, best, chain


def current_name(source, name, vram):
    """A source saved before a rename defines `func_<VRAM>`, and the families look the function up by `name`: they
    propose nothing (13 of 34 stalls in the whole-path audit). Rename it, as the app's canonical_source does."""
    from decompnet.app.function_names import rename_identifiers
    from decompnet.forks.csource import find
    old = f"func_{vram:08X}" if isinstance(vram, int) else None
    if old and old != name and find(source, name) is None and find(source, old) is not None:
        return rename_identifiers(source, {old: name})
    return source


def generator(judge, name):
    from decompnet.app import draft_repairs
    vram = (getattr(judge, "request", None) or {}).get("function", {}).get("vram")

    def generate(source):
        source = current_name(source, name, vram)
        labels = {}
        texts = draft_repairs.variants(source, name, judge.arch, judge.asm, judge.compiler["flags"], labels,
                                       limit=None, header=judge.header, tables=getattr(judge, "tables", ()),
                                       callees=getattr(judge, "callees", ""),
                                       entry_scope=getattr(judge, "entry_scope", None))
        return [(labels.get(text, "unlabelled"), text) for text in texts]
    return generate


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="C file to start from (default: the app's saved best)")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--families", default="all", help="comma separated family names, or all")
    ap.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    ap.add_argument("--children", type=int, default=DEFAULT_CHILDREN)
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    if a.source:
        source = Path(a.source).read_text()
    else:
        import judge
        source = judge.App(a.home).function(a.slug, a.function).get("source") or ""
        if not source:
            sys.exit("the app has no saved C for that function: give SOURCE.c")
    judge = fast_judge.Judge(a.slug, a.function, home=a.home)
    try:
        generate = generator(judge, a.function)
        if a.list:
            counts = {}
            for family, _ in generate(source):
                counts[family] = counts.get(family, 0) + 1
            for family, n in sorted(counts.items(), key=lambda row: (-row[1], row[0])):
                print(f"{n:5}  {family}")
            return
        families = None if a.families == "all" else {f.strip() for f in a.families.split(",") if f.strip()}
        final, _, _ = prove(source, generate, judge.score, families, a.steps, a.children)
        if a.out:
            Path(a.out).write_text(final)
            print(f"final source -> {a.out}")
    finally:
        judge.close()


if __name__ == "__main__":
    main()
