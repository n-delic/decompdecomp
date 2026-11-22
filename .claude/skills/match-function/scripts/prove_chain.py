#!/usr/bin/env python3
"""Does a chain of library repairs reach the match from here? Apply the best child of chosen families, step by step.

After a manual match, step 5 of the skill asks whether the permuter could have found the steps. This answers it for the
families you name, without the Deep Permuter: at each step it asks the app's own proposal code
(`draft_repairs.variants`, the path drafts and the permuter's beam use) for every child of the current source, keeps
those from the chosen families, scores them one by one with the standalone judge (fast_judge.py) and keeps the best
child that beats the current score. It stops at 0, when no child improves, or after --steps. Bounded: --children caps
the children scored per step (default 80, about 8 seconds of judging), one judge worker, nothing parallel.

With --combine K (default 0: off), a step where no single child improves tries combinations before stopping: children
of that step merged two at a time, then three at a time, each merge only when the children's edits touch different
parts of the source (`merge`). Several learnings needed edits that are each worse alone and exact together
(walked-table-index: three; call-carrier-pointer-split, collapsed-guard-lifetime-merge: two). Children are ranked by
the rows of the target's difference they fix (the judge's rows against the parent's: `row_coverage`), not by total
score alone: a child that fixes at least one differing row is kept though its total is worse, and combinations of
children that each fix rows of their own come first, fewest predicted differing rows first. The K best by score are
combined as before after those. At most --combine-limit merges are judged, only on a stalled step. `--no-rows` ranks
by score alone (the older search; docs/learnings/finding/row-coverage-combinations.md has the comparison).

    prove_chain.py --slug SLUG --function FUNC [SOURCE.c] [--families a,b,c | all] [--steps 8] [--children 80]
                   [--combine 10] [--out BEST.c] [--list]

SOURCE.c defaults to the app's saved best (read through the app, no claim). `--list` prints which families produce
children for the source and how many, then exits (a family that produces none cannot be in the chain). Each step prints
`step N  family  score  (children scored)`; the chain ends with the final score, and --out keeps the final source.
It scores with the app's judge, so a chain that reaches 0 is a candidate for compile_and_diff, not yet a recorded match.
Run it with the python of a setup.py session (it imports decompnet): `$UV_PROJECT_ENVIRONMENT/bin/python prove_chain.py`.
"""
import argparse
import difflib
import itertools
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fast_judge  # noqa: E402

from decompnet.app import row_coverage  # noqa: E402

DEFAULT_STEPS = 8
DEFAULT_CHILDREN = 80
DEFAULT_COMBINE = 0  # off: on three three-edit learnings neither the score nor the row ranking found one (docs/learnings/finding/draft-start-candidates.md, docs/learnings/experiment/row-coverage-combinations.md)
DEFAULT_COMBINE_LIMIT = 200
TOKEN = re.compile(r"[A-Za-z_]\w*|0[xX][0-9a-fA-F]+|\d+|\s+|.", re.S)


def _edits(base, child):
    """The child as token edits of `base`: [(start, end, replacement tokens)] over base's tokens."""
    left, right = TOKEN.findall(base), TOKEN.findall(child)
    ops = difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes()
    return left, [(a, b, right[c:d]) for tag, a, b, c, d in ops if tag != "equal"]


def merge(base, children):
    """One source with every child's edits of `base`, or None when two of them touch the same tokens (or insert at
    the same place): then they are not independent edits and their merge would be a guess."""
    tokens, edits = None, []
    for child in children:
        tokens, own = _edits(base, child)
        if not own:
            return None
        edits += own
    edits.sort(key=lambda e: (e[0], e[1]))
    for (a1, b1, _), (a2, b2, _) in zip(edits, edits[1:]):
        if a2 < b1 or (a1 == a2 and (a1 == b1 or a2 == b2)):
            return None
    out, at = [], 0
    for a, b, replacement in edits:
        out += tokens[at:a] + replacement
        at = b
    return "".join(out + tokens[at:])


def row_combinations(base, scored, cover, parent_bad, k=DEFAULT_COMBINE, limit=DEFAULT_COMBINE_LIMIT):
    """[(families joined by +, merged source)] of the children that fix rows of the parent's difference (`cover`:
    {child: (fixed, broken)}, row_coverage.coverage), in pairs and triples that each fix rows of their own,
    those predicted to leave the fewest rows first; at most `limit`. Children from one family are not combined
    (their edits are alternatives)."""
    items = [(score, family, child, cover[child]) for score, family, child in scored if child in cover]
    kept = row_coverage.rank_children(items, max(k, 1) * 3, per_family=3)
    out, seen = [], {base}
    for _, group in row_coverage.groups(parent_bad, [item[3] for item in kept], limit=limit * 6):
        members = [kept[i] for i in group]
        if len({m[1] for m in members}) < len(members):
            continue
        merged = members[0][2]
        for member in members[1:]:
            merged = merge(base, [merged, member[2]]) if merged is not None else None
        if merged is not None and merged not in seen:
            seen.add(merged)
            out.append(("+".join(m[1] for m in members), merged))
            if len(out) >= limit:
                break
    return out


def combinations(base, scored, k=DEFAULT_COMBINE, limit=DEFAULT_COMBINE_LIMIT):
    """[(families joined by +, merged source)] of the k best scored children (`scored`: [(score, family, child)]),
    pairs first, then triples whose three pairs all merged; at most `limit`."""
    top = sorted(scored, key=lambda row: row[0])[:k]
    out, pairs, seen = [], set(), set()
    for size in (2, 3):
        for group in itertools.combinations(range(len(top)), size):
            if len(out) >= limit:
                return out
            if size == 3 and not all(p in pairs for p in itertools.combinations(group, 2)):
                continue
            merged = merge(base, [top[i][2] for i in group])
            if merged is None or merged in seen or merged == base:
                continue
            if size == 2:
                pairs.add(group)
            seen.add(merged)
            out.append(("+".join(top[i][1] for i in group), merged))
    return out


def prove(source, generate, score, families, steps=DEFAULT_STEPS, children=DEFAULT_CHILDREN, log=print,
          combine=DEFAULT_COMBINE, combine_limit=DEFAULT_COMBINE_LIMIT, rows=None):
    """The greedy chain. `generate(source) -> [(family, child)]`, `score(source) -> int | None` (None: didn't
    compile, never an improvement). A stalled step tries `combine` best children merged in pairs and triples.
    `rows(source) -> (score, rows)` (Judge.rows) adds the row-level ranking of those combinations: children are
    judged with their rows, and the combinations of those that fix rows of the difference go first.
    Returns (final source, final score, [(family, score)])."""
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
        scored, cover = [], {}
        parent_bad = row_coverage.bad(rows(current)[1]) if rows and combine else None
        for family, child in options[:children]:
            if parent_bad:
                result, child_rows = rows(child)
                if result is not None and child_rows:
                    cover[child] = row_coverage.coverage(parent_bad, child_rows)
            else:
                result = score(child)
            if result is not None:
                scored.append((result, family, child))
        if (not scored or min(row[0] for row in scored) >= best) and combine and len(scored) > 1:
            merged = row_combinations(current, scored, cover, parent_bad, combine, combine_limit) if cover else []
            have = {child for _, child in merged}
            merged += [pair for pair in combinations(current, scored, combine, combine_limit) if pair[1] not in have]
            merged = merged[:combine_limit]
            tried = []
            for family, child in merged:
                result = score(child)
                if result is not None:
                    tried.append((result, family, child))
            log(f"step {step}  no single child improves; {len(tried)} of {len(merged)} merges of the best {combine} scored")
            scored += tried
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
    ap.add_argument("--combine", type=int, default=DEFAULT_COMBINE,
                    help="on a stalled step, merge the best K children in pairs and triples (0: off)")
    ap.add_argument("--combine-limit", type=int, default=DEFAULT_COMBINE_LIMIT)
    ap.add_argument("--no-rows", action="store_true", help="rank combinations by total score alone (the older search)")
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
        final, _, _ = prove(source, generate, judge.score, families, a.steps, a.children,
                            combine=a.combine, combine_limit=a.combine_limit,
                            rows=None if a.no_rows else judge.rows)
        if a.out:
            Path(a.out).write_text(final)
            print(f"final source -> {a.out}")
    finally:
        judge.close()


if __name__ == "__main__":
    main()
