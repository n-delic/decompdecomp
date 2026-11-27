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
                   [--combine 10] [--out BEST.c] [--list] [--flags '...' | --fp-flip]

`--flags` / `--fp-flip` judge and propose under another profile (the function's flags with the other `-mfp32`/`-mfp64`):
the families' children are built and scored under it, as the Deep Permuter's profile climb does.

SOURCE.c defaults to the app's saved best (read through the app, no claim). `--list` prints which families produce
children for the source and how many, then exits (a family that produces none cannot be in the chain). Each step prints
`step N  family  score  (children scored)`; the chain ends with the final score, and --out keeps the final source.
It scores with the app's judge, so a chain that reaches 0 is a candidate for compile_and_diff, not yet a recorded match.
Run it with the python of a setup.py session (it imports decompnet): `$UV_PROJECT_ENVIRONMENT/bin/python prove_chain.py`.
"""
import argparse
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claimlost  # noqa: E402
import fast_judge  # noqa: E402

from decompnet.app import row_coverage  # noqa: E402
from decomprepair import compose  # noqa: E402

DEFAULT_STEPS = 8
DEFAULT_CHILDREN = 80
DEFAULT_COMBINE = 0  # off: on three three-edit learnings neither the score nor the row ranking found one (docs/learnings/finding/draft-start-candidates.md, docs/learnings/experiment/row-coverage-combinations.md)
DEFAULT_COMBINE_LIMIT = 200
DEFAULT_LOOKAHEAD = 3  # stalled step: children of the 3 most promising non-improving children are judged too
DEFAULT_LOOKAHEAD_CHILDREN = 40


def merge(base, children):
    """One source with every child's edits of `base`, or None when two of them touch the same tokens (decomprepair
    compose.merge: the library's searches and this one share it)."""
    return compose.merge(base, children)


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


def _bad_count(rows):
    """The number of the judge's differing rows (Judge.rows), or None without rows."""
    return len(row_coverage.bad(rows)) if rows else None


def prove(source, generate, score, families, steps=DEFAULT_STEPS, children=DEFAULT_CHILDREN, log=print,
          combine=DEFAULT_COMBINE, combine_limit=DEFAULT_COMBINE_LIMIT, rows=None, lookahead=DEFAULT_LOOKAHEAD,
          lookahead_children=DEFAULT_LOOKAHEAD_CHILDREN, rank_signal=None):
    """The greedy chain. `generate(source) -> [(family, child)]`, `score(source) -> int | None` (None: didn't
    compile, never an improvement). A stalled step (no single child improves) searches combined steps when
    `combine` > 0:
      - the `combine` children that did not improve, ranked by how few of the judge's rows still differ
        (`rows(source) -> (score, rows)`, Judge.rows; by score without rows), merged in pairs and triples of
        different families (decomprepair compose.compositions), with the older row-coverage and score merges;
      - then, with `lookahead` > 0, the children of the best `lookahead` of them (two steps whose first is no
        better alone), at most `lookahead_children` each.
    A combined step is taken when it beats the score, or ties it with fewer differing rows (a plateau move, never
    to a source seen before). `rank_signal(parent, child) -> (penalty, note)` (register_explain.dead_end: a child that
    turns a block-local value global) demotes a child in that ranking; it never stops an improving child.
    Returns (final source, final score, [(family, score)])."""
    current, best = source, score(source)
    if best is None:
        raise SystemExit("the starting source doesn't compile with the judge: fix that first (is its name the function's?)")
    best_bad = _bad_count(rows(current)[1]) if rows and combine else None
    chain, visited = [], {current}
    log(f"start  score {best}" + (f", {best_bad} differing rows" if best_bad is not None else ""))
    for step in range(1, steps + 1):
        if best == 0:
            break
        options, seen = [], {current}
        for family, child in generate(current):
            if (families is None or family in families) and child not in seen:
                seen.add(child)
                options.append((family, child))
        scored, cover, bad = [], {}, {}
        parent_rows = rows(current)[1] if rows and combine else None
        parent_bad = row_coverage.bad(parent_rows) if parent_rows else None
        offset = 0
        # --children is the size of a batch: a batch with no improving child is not a stall while children are left
        # out (func_8005502C: 528 children, the improving one number 131, stalled at 215 under --children 40 because the
        # cap's order is one child per family, so a family's later children come after every family's earlier ones).
        while offset < len(options):
            for family, child in options[offset:offset + children]:
                if rows and combine:
                    result, child_rows = rows(child)
                    if result is not None and child_rows:
                        bad[child] = _bad_count(child_rows)
                        if parent_bad:
                            cover[child] = row_coverage.coverage(parent_bad, child_rows)
                else:
                    result = score(child)
                if result is not None:
                    scored.append((result, family, child))
            offset += children
            if scored and min(row[0] for row in scored) < best:
                break
            if offset < len(options):
                log(f"step {step}  note: no child of the first {offset} improves on {best}; judging the other "
                    f"{len(options) - offset} of {len(options)}")
        if offset < len(options):
            log(f"step {step}  note: {len(options) - offset} of {len(options)} children left out: a batch of "
                f"--children {children} already improved (name --families to narrow it)")
        taken = None
        if (not scored or min(row[0] for row in scored) >= best) and combine and scored:
            taken = _combined_step(step, current, best, best_bad, scored, cover, bad, parent_bad, generate, families,
                                   score, rows, combine, combine_limit, lookahead, lookahead_children, rank_signal,
                                   visited, log)
        if taken:
            result, family, child, child_bad = taken
            current, best, best_bad = child, result, child_bad
            visited.add(child)
            chain.append((family, result))
            log(f"step {step}  {family}  {result}  (combined step)")
            continue
        if not scored or min(row[0] for row in scored) >= best:
            log(f"step {step}  no child of {len(options)} improves on {best} (scored {len(scored)}): stopped")
            break
        result, family, child = min(scored, key=lambda row: row[0])
        current, best, best_bad = child, result, bad.get(child, best_bad)
        visited.add(child)
        chain.append((family, result))
        log(f"step {step}  {family}  {result}  ({len(scored)} of {len(options)} children scored)")
    log("matched: score 0" if best == 0 else f"final score {best}")
    return current, best, chain


def _judge(source, score, rows):
    """(score, differing rows or None)."""
    if rows:
        result, found = rows(source)
        return result, _bad_count(found)
    return score(source), None


def _better(result, count, best, best_bad):
    return result is not None and (result < best or (result == best and count is not None and best_bad is not None
                                                      and count < best_bad))


def _combined_step(step, current, best, best_bad, scored, cover, bad, parent_bad, generate, families, score, rows,
                   combine, combine_limit, lookahead, lookahead_children, rank_signal, visited, log):
    """A stalled step's search over combined steps (prove's docstring): (score, families, source, rows) or None."""
    notes = {}
    items = sorted(((bad.get(child, 10**6), result, family, child) for result, family, child in scored),
                   key=lambda item: (item[0], item[1]))
    if rank_signal:  # asked only of the few children that could be ranked in: it costs a compile each
        for index, (count, result, family, child) in enumerate(items[:max(combine, 1) * 3]):
            penalty, note = rank_signal(current, child)
            if penalty:
                notes[family] = note
                items[index] = (count + penalty * 10**3, result, family, child)
    ranked = compose.rank(items, combine)
    for family, note in list(notes.items())[:3]:
        log(f"step {step}  note: {family} demoted ({note})")
    merged = [(f, c) for f, c in compose.compositions(current, ranked, limit=combine_limit)]
    have = {c for _, c in merged}
    if cover:
        merged += [m for m in row_combinations(current, scored, cover, parent_bad, combine, combine_limit) if m[1] not in have]
        have = {c for _, c in merged}
    merged += [m for m in combinations(current, scored, combine, combine_limit) if m[1] not in have]
    merged = merged[:combine_limit]
    found = []
    for family, child in merged:
        if child in visited:
            continue
        result, count = _judge(child, score, rows)
        if _better(result, count, best, best_bad):
            found.append((result, count if count is not None else 0, family, child))
    log(f"step {step}  no single child improves; {len(merged)} merges of the best {len(ranked)} judged"
        + (f", {len(found)} better" if found else ""))
    if not found and lookahead:
        for _, _, family, child in ranked[:lookahead]:
            judged = 0
            for second, grandchild in generate(child):
                if judged >= lookahead_children:
                    break
                if (families is not None and second not in families) or grandchild in visited or grandchild == current:
                    continue
                judged += 1
                result, count = _judge(grandchild, score, rows)
                if _better(result, count, best, best_bad):
                    found.append((result, count if count is not None else 0, f"{family}>{second}", grandchild))
        log(f"step {step}  lookahead: children of the best {min(lookahead, len(ranked))} non-improving judged"
            + (f", {len(found)} better" if found else ", none better"))
    if not found:
        return None
    result, count, family, child = min(found, key=lambda row: (row[0], row[1]))
    return result, family, child, (count if rows else None)


def dead_end_signal(judge):
    """prove's `rank_signal`: register_explain.dead_end over the lreg dumps of parent and child (the judge worker's
    allocation op, one diagnostic compile per source, cached). A compiler with no dumps gives no signal."""
    from decompnet.app import register_explain
    cache = {}

    def lreg(source):
        if source not in cache:
            try:
                cache[source] = judge.ask({"op": "allocation", "slug": judge.slug, "name": judge.name,
                                           "source": source}).get("lreg") or ""
            except SystemExit:  # the worker's error (no GCC dumps): no signal
                cache[source] = ""
        return cache[source]

    def signal(parent, child):
        return register_explain.dead_end(lreg(parent), lreg(child))
    return signal


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
                                       entry_scope=getattr(judge, "entry_scope", None),
                                       words=getattr(judge, "words", None))
        found = [(labels.get(text, "unlabelled"), text) for text in texts]
        # The families that read the judge's differing rows (diff_gated_repairs: the Deep Permuter calls them with
        # the candidate's diff) are not in draft_repairs.variants; without them a chain never sees what the permuter's
        # diff-gated lane would offer (boolean_guard_operands' sink, measured_* repairs).
        from decompnet.app import diff_gated_repairs
        _, rows = judge.rows(source) if hasattr(judge, "rows") else (None, [])
        seen = {text for _, text in found}
        try:
            gated = diff_gated_repairs.proposals(
                source, name, judge.header, judge.arch, judge.compiler.get("toolchain", ""), rows, judge.asm,
                jump_tables=getattr(judge, "jump_tables", ()),  # score 1 with no rows: the table's data (linked_switch_labels)
                flags=tuple(judge.compiler["flags"]),
                big_endian=bool(getattr(judge, "big_endian", True)))
        except Exception as error:  # a family's bug must not end a chain
            print(f"note: diff-gated proposals failed ({error})", file=sys.stderr)
            gated = []
        found += [(family, text) for family, text in gated if text not in seen and not seen.add(text)]
        return found
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
    ap.add_argument("--lookahead", type=int, default=DEFAULT_LOOKAHEAD,
                    help="on a stalled step, also judge the children of the best N non-improving children (0: off)")
    ap.add_argument("--lookahead-children", type=int, default=DEFAULT_LOOKAHEAD_CHILDREN)
    ap.add_argument("--no-dead-end", action="store_true",
                    help="don't demote children that turn a block-local value global (one extra compile per ranked child)")
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--home")
    ap.add_argument("--flags", metavar="'-G0 -mips3 ... -mfp32'",
                    help="judge and propose under these compiler flags instead of the function's own (the toolchain "
                         "stays): the way to ask whether the families reach a match under another profile")
    ap.add_argument("--toolchain", help="judge and propose with this compiler instead of the function's own")
    ap.add_argument("--fp-flip", action="store_true",
                    help="the function's own flags with the other floating-point model: -mfp64 <-> -mfp32 "
                         "(fp_fingerprints.flipped, the profile the Deep Permuter's climb tries)")
    a = ap.parse_args(argv)
    claimlost.warn()  # a lost claim is said first, before minutes of scoring
    if a.source:
        source = Path(a.source).read_text()
    else:
        import judge
        source = judge.App(a.home).function(a.slug, a.function).get("source") or ""
        if not source:
            sys.exit("the app has no saved C for that function: give SOURCE.c")
    judge = fast_judge.Judge(a.slug, a.function, home=a.home)
    try:
        if a.toolchain:
            judge.compiler["toolchain"] = a.toolchain
        if a.flags or a.fp_flip or a.toolchain:
            if a.flags or a.fp_flip:
                from decompnet.app import fp_fingerprints
                judge.compiler["flags"] = (a.flags.split() if a.flags
                                           else fp_fingerprints.flipped(judge.compiler["flags"]))
            print(f"profile: {judge.compiler['toolchain']} {' '.join(judge.compiler['flags'])}")
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
                            rows=None if a.no_rows else judge.rows, lookahead=a.lookahead,
                            lookahead_children=a.lookahead_children,
                            rank_signal=None if a.no_dead_end or not a.combine else dead_end_signal(judge))
        if a.out:
            Path(a.out).write_text(final)
            print(f"final source -> {a.out}")
    finally:
        judge.close()


if __name__ == "__main__":
    main()
