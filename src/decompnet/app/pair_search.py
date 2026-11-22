"""The bounded pair search: two repairs that each score worse alone and better together.

A hand match often needed two or three edits at once: a constant sunk to its join and a counter's start moved, a guard
collapsed and two locals merged. Every one of them alone scores the same or worse, so a search that keeps only
improvements never takes the first step. `paired_moves` does this for two statement moves; this does it for any two
library repairs. Take the best few single children (non-improving ones included, a few per family so one noisy family
cannot fill the list), and compose each pair from different families by applying the second child's edit to the first
child's source where it still applies (a line-level three-way merge against the source both came from: the edits
must touch different lines). Where two edits touch the same lines, the second family's repair is asked again on the
first child (`regenerate`), for the first few children only. The pairs are judged by the caller (the exact judge, in
parallel and bounded) and an improvement is kept.

Ranking by total score alone misses the edit that fixes one row of the difference and breaks others. With the
judge's rows (`row_coverage`) a child that fixes at least one of the parent's differing rows is kept even when its
total is worse, and the pairs and triples are ranked by the rows they are predicted to leave: children whose fixed
rows complement each other come first, the rest follow by the sum of their scores.

This module only proposes and ranks; nothing here compiles. A pair is a guess: the caller's judge decides."""
from __future__ import annotations

import difflib

from . import row_coverage

TOP_K = 30            # single children (by score, improving or not) whose pairs are composed
PER_FAMILY = 3        # most of them from one family, so a long list of one family's variants cannot crowd the rest out
PAIR_BUDGET = 120     # pairs judged in one search
REGEN_PARENTS = 3     # children the second family's repair is asked again on, when the two edits touch the same lines
MAX_SINGLES = 150     # single children judged to make the list (the deep permuter's)
COVER_K = 30          # children kept for the rows they fix though their totals are worse
COVER_BUDGET = 80     # combinations (pairs and triples) ranked by predicted rows, within PAIR_BUDGET
TRIPLE_SHARE = 40     # most of those that are triples


# Project setting (`pair_row_coverage`): off. On a sample of 59 before-states (10 true pairs) and the three-edit
# functions the row ranking judged the same pairs to the same results (docs/learnings/finding/row-coverage-combinations.md).
ROW_SETTING = 'pair_row_coverage'


def row_coverage_enabled(cfg) -> bool:
    return bool((cfg or {}).get(ROW_SETTING, False))


def hunks(base: str, child: str):
    """[(first line, end line, replacement lines)] of `child` against `base`: the edits as line ranges of `base`."""
    old, new = base.splitlines(True), child.splitlines(True)
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [(i1, i2, new[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != 'equal']


def _clash(x, y):
    (x1, x2, _), (y1, y2, _) = x, y
    if x1 == x2 and y1 == y2:
        return x1 == y1                       # two insertions at one place: which goes first is a guess
    if x1 == x2:
        return y1 < x1 < y2
    if y1 == y2:
        return x1 < y1 < x2
    return x1 < y2 and y1 < x2


def merge(base: str, first: str, second: str) -> str | None:
    """`base` with both children's edits, or None when one of them changes nothing or two edits touch the same lines."""
    one, two = hunks(base, first), hunks(base, second)
    if not one or not two or any(_clash(x, y) for x in one for y in two):
        return None
    lines = base.splitlines(True)
    for start, end, new in sorted(one + two, key=lambda hunk: (hunk[0], hunk[1]), reverse=True):
        lines[start:end] = new
    return ''.join(lines)


def select(singles, top=TOP_K, per_family=PER_FAMILY):
    """The best `top` of `singles` ([(score, family, child)]), at most `per_family` of each family, best first."""
    taken, kept = {}, []
    for score, family, child in sorted(singles, key=lambda item: item[0]):
        if taken.get(family, 0) < per_family:
            taken[family] = taken.get(family, 0) + 1
            kept.append((score, family, child))
    return kept[:top]


def compose(base, singles, *, top=TOP_K, per_family=PER_FAMILY, budget=PAIR_BUDGET, regenerate=None,
            regen_parents=REGEN_PARENTS, skip=(), cover=None, parent_bad=None, cover_k=COVER_K,
            cover_budget=COVER_BUDGET, triples=TRIPLE_SHARE):
    """[(label, source)]: the pairs of the best singles from different families, the likeliest first, at most `budget`.

    Likeliest means the lowest sum of the two singles' scores. `regenerate(source)` -> [(family, child)] is asked about
    the first `regen_parents` children for a second repair whose edit clashes with theirs.

    `cover` ({child: (fixed, broken)}, row_coverage.coverage against the parent) and `parent_bad` (the parent's
    differing row keys) add the row-level search: the `cover_k` children that fix rows join the list whatever their
    scores, and up to `cover_budget` pairs and triples whose predicted differing rows are fewest come first (at most
    `triples` of them triples: a triple is a merge of a merged pair and a third child)."""
    chosen = select(singles, top, per_family)
    extra = []
    if cover and parent_bad is not None:
        taken = {child for _, _, child in chosen}
        items = [(score, family, child, cover[child]) for score, family, child in singles
                 if child in cover and child not in taken]
        extra = [(score, family, child) for score, family, child, _ in row_coverage.rank_children(items, cover_k, per_family)]
    seen = set(skip) | {base} | {child for _, _, child in chosen} | {child for _, _, child in extra}
    out, pairs = [], []
    if cover and parent_bad is not None:
        pool = [item for item in chosen + extra if item[2] in cover]
        ranked = row_coverage.groups(parent_bad, [cover[item[2]] for item in pool], limit=cover_budget * 6)
        triple_count = 0
        for _, group in ranked:
            if len(pairs) >= cover_budget:
                break
            members = [pool[i] for i in group]
            if len({m[1] for m in members}) < len(members):
                continue
            if len(members) == 3 and triple_count >= triples:
                continue
            merged = members[0][2]
            for member in members[1:]:
                merged = merge(base, merged, member[2]) if merged is not None else None
            if merged is not None and merged not in seen:
                seen.add(merged)
                triple_count += len(members) == 3
                pairs.append(('+'.join(m[1] for m in members), merged))
    for at, (score_a, family_a, child_a) in enumerate(chosen):
        for score_b, family_b, child_b in chosen[at + 1:]:
            if family_a == family_b:
                continue
            merged = merge(base, child_a, child_b)
            if merged is not None and merged not in seen:
                seen.add(merged)
                out.append((score_a + score_b, f'{family_a}+{family_b}', merged))
    out.sort(key=lambda item: item[0])
    pairs += [(label, text) for _, label, text in out]
    if regenerate is not None:
        wanted = {family for _, family, _ in chosen}
        for score_a, family_a, child_a in chosen[:regen_parents]:
            for family_b, grandchild in regenerate(child_a):
                if family_b != family_a and family_b in wanted and grandchild not in seen:
                    seen.add(grandchild)
                    pairs.append((f'{family_a}+{family_b}', grandchild))
    return pairs[:budget]


def search(judge_many, base, singles, best, *, regenerate=None, check=lambda: None, log=lambda text: None,
           top=TOP_K, per_family=PER_FAMILY, budget=PAIR_BUDGET, skip=(), cover=None, parent_bad=None):
    """(score, candidate, label, judged) of the best pair that scores below `best`; candidate None when none does.

    `singles` are [(score, family, child)] already judged (a score of None, a child that did not compile, is left out);
    `judge_many(sources, check)` returns the pairs' exact scores."""
    singles = [item for item in singles if item[0] is not None]
    pairs = compose(base, singles, top=top, per_family=per_family, budget=budget, regenerate=regenerate, skip=skip,
                    cover=cover, parent_bad=parent_bad)
    if not pairs:
        log('Pair search: no two of the best single children can be composed.')
        return best, None, None, 0
    scores = judge_many([text for _, text in pairs], check)
    winner = None
    for (label, text), score in zip(pairs, scores):
        if score is not None and score < best:
            best, winner = score, (label, text)
    if winner is None:
        log(f'Pair search: {len(pairs)} pairs of {min(top, len(singles))} single children judged, none better.')
        return best, None, None, len(pairs)
    log(f'Pair search: {winner[0]} improves the score to {best} ({len(pairs)} pairs judged).')
    return best, winner[1], winner[0], len(pairs)
