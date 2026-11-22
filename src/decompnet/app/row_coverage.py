"""Which of the target's differing rows a child repair fixes, and which pairs of children complement each other.

A match that needs several edits at once (docs/learnings/finding/draft-start-candidates.md section 4: three edits,
each much worse alone) hides from a search that ranks children by total score: the edit that fixes one row of the
difference may break two others and rank far below the best ten. The judge's rows say more than the score does. Each
row is (marker, target instruction, candidate instruction); a marker other than a space is a difference. Rows are
keyed by the target's instruction (its ordinal among the rows that have one, which never moves between candidates)
and, for an instruction only the candidate has (marker '>'), by the gap it sits in and its place there.

For one child against its parent:
- `fixed`: keys that differ in the parent and match in the child;
- `broken`: keys that match in the parent and differ in the child.
A child that fixes at least one row is worth keeping even when its total is worse. Children that each fix rows
the others do not are a promising pair or triple (one may fix what the other breaks once they are merged, which no
single judgment shows), and the rows a merge is expected to leave differing, `predicted_bad`, rank them before any
is judged. The prediction is a guess (merged
edits interact: that is the reason to judge them); only the judge decides.

Pure functions over the judge's rows; nothing here compiles."""
from __future__ import annotations

from itertools import combinations


def bad(rows) -> frozenset:
    """The keys of the rows that differ: ('t', k) for the k-th target instruction, ('g', k, j) for the j-th extra
    candidate instruction after it."""
    out, ordinal, extra = set(), -1, 0
    for marker, target, _candidate in rows:
        if target:
            ordinal += 1
            extra = 0
            if marker != ' ':
                out.add(('t', ordinal))
        else:
            if marker != ' ':
                out.add(('g', ordinal, extra))
            extra += 1
    return frozenset(out)


def targets(rows) -> int:
    """How many target instructions there are."""
    return sum(1 for _, target, _ in rows if target)


def coverage(parent_bad, child_rows):
    """(fixed, broken) of a child against the parent's differing keys (`bad(parent rows)`).

    An extra instruction that is gone counts as fixed (the child has fewer in that gap); a gap that grew counts as
    broken. Target rows are compared by key."""
    child_bad = bad(child_rows)
    parent_t = {k for k in parent_bad if k[0] == 't'}
    child_t = {k for k in child_bad if k[0] == 't'}

    def gaps(keys):
        out = {}
        for _, k, j in (k for k in keys if k[0] == 'g'):
            out[k] = max(out.get(k, 0), j + 1)
        return out
    before, after = gaps(parent_bad), gaps(child_bad)
    fixed, broken = set(parent_t - child_t), set(child_t - parent_t)
    for gap, count in before.items():
        fixed |= {('g', gap, j) for j in range(after.get(gap, 0), count)}
    for gap, count in after.items():
        broken |= {('g', gap, j) for j in range(before.get(gap, 0), count)}
    return frozenset(fixed), frozenset(broken)


def predicted_bad(parent_bad, covers) -> float:
    """How many rows are expected to still differ if the children in `covers` ([(fixed, broken)]) applied together.

    The parent's differing rows that none fixes, plus what the merge keeps broken: a row two children both break and
    none fixes stays broken (1), a row one breaks and none fixes counts half. A row one child breaks is often put
    right by the other's edit (the reason the pair is worth judging), so the prediction is optimistic about it."""
    fixed = set().union(*(f for f, _ in covers)) if covers else set()
    counts = {}
    for _, broken in covers:
        for row in broken - fixed:
            counts[row] = counts.get(row, 0) + 1
    return len(set(parent_bad) - fixed) + sum(1.0 if n >= 2 else .5 for n in counts.values())


def worth_keeping(cover) -> bool:
    """A child that fixes at least one differing row of its parent."""
    return bool(cover[0])


def rank_children(items, limit, per_family=3):
    """The `limit` children that fix rows, best first: the most net rows fixed (fixed - broken), then the fewest
    broken, then the lowest score; at most `per_family` of one family. `items`: [(score, family, child, (fixed,
    broken))]."""
    taken, out = {}, []
    ordered = sorted((i for i in items if worth_keeping(i[3])),
                     key=lambda i: (-(len(i[3][0]) - len(i[3][1])), len(i[3][1]), i[0]))
    for item in ordered:
        if taken.get(item[1], 0) < per_family:
            taken[item[1]] = taken.get(item[1], 0) + 1
            out.append(item)
    return out[:limit]


def contributes(group) -> bool:
    """Whether every member of `group` ([(fixed, broken)]) fixes a row that no other member fixes: the members cover
    different rows, so a combination is not just one child with passengers."""
    for i, (fixed, _) in enumerate(group):
        others = set().union(*(f for j, (f, _) in enumerate(group) if j != i)) if len(group) > 1 else set()
        if not fixed - others:
            return False
    return True


def groups(parent_bad, items, sizes=(2, 3), limit=200):
    """[(predicted_bad, indexes)] of the combinations of `items` ([(fixed, broken)]) of those sizes in which every
    member fixes a row of its own, fewest predicted differing rows first (a triple before an equal pair: it fixes
    more), at most `limit`."""
    scored = []
    for size in sizes:
        for group in combinations(range(len(items)), size):
            covers = [items[i] for i in group]
            if contributes(covers):
                scored.append((predicted_bad(parent_bad, covers), -size, group))
    scored.sort(key=lambda s: (s[0], s[1]))
    return [(left, group) for left, _, group in scored[:limit]]
