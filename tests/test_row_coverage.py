"""Row-level coverage of a child's repair, and the search that combines children whose fixed rows complement each
other (app/row_coverage.py, app/pair_search.py)."""
from decompnet.app import pair_search, row_coverage

OK, BAD = ' ', '|'


def rows(*markers, extra_after=None):
    """Judge rows: one target instruction per marker; `extra_after` {k: n}: n candidate-only rows after target k."""
    out = []
    for k, marker in enumerate(markers):
        out.append((marker, f'insn{k}', f'insn{k}'))
        out += [('>', '', 'extra')] * (extra_after or {}).get(k, 0)
    return out


def test_the_keys_name_target_rows_and_extra_ones_by_their_gap():
    found = row_coverage.bad(rows(OK, BAD, OK, BAD, extra_after={1: 2}))
    assert found == {('t', 1), ('t', 3), ('g', 1, 0), ('g', 1, 1)}
    assert row_coverage.targets(rows(OK, BAD, extra_after={0: 3})) == 2


def test_a_child_fixes_the_rows_that_differed_and_breaks_those_that_matched():
    parent = row_coverage.bad(rows(BAD, BAD, OK, OK))
    fixed, broken = row_coverage.coverage(parent, rows(OK, BAD, BAD, OK))
    assert fixed == {('t', 0)} and broken == {('t', 2)}
    # a removed extra instruction is a fix, a new one a break
    parent = row_coverage.bad(rows(OK, OK, extra_after={0: 2}))
    fixed, broken = row_coverage.coverage(parent, rows(OK, OK, extra_after={0: 1, 1: 1}))
    assert fixed == {('g', 0, 1)} and broken == {('g', 1, 0)}


def test_the_prediction_counts_what_a_combination_leaves():
    parent = frozenset({('t', 0), ('t', 1), ('t', 2)})
    a = (frozenset({('t', 0)}), frozenset({('t', 5)}))      # fixes row 0, breaks row 5
    b = (frozenset({('t', 1), ('t', 5)}), frozenset())     # fixes rows 1 and 5
    c = (frozenset({('t', 2)}), frozenset({('t', 6)}))
    assert row_coverage.predicted_bad(parent, [a]) == 2.5 and row_coverage.predicted_bad(parent, [b]) == 2
    assert row_coverage.predicted_bad(parent, [a, b]) == 1       # b fixes what a broke
    assert row_coverage.predicted_bad(parent, [a, b, c]) == .5
    assert row_coverage.contributes([a, b]) and not row_coverage.contributes([b, (frozenset({('t', 1)}), frozenset())])
    found = row_coverage.groups(parent, [a, b, c])
    assert found[0] == (.5, (0, 1, 2)) and {group for _, group in found} == {(0, 1), (0, 2), (1, 2), (0, 1, 2)}


def test_rank_children_keeps_those_that_fix_rows_even_when_the_total_is_worse():
    items = [(900, 'x', 'bad total', (frozenset({('t', 1)}), frozenset({('t', 8), ('t', 9)}))),
             (50, 'y', 'fixes nothing', (frozenset(), frozenset())),
             (40, 'z', 'clean', (frozenset({('t', 2)}), frozenset()))]
    assert [item[2] for item in row_coverage.rank_children(items, 5)] == ['clean', 'bad total']


# A three-edit toy: the match needs A, B and C; each alone fixes its row and breaks another, so every total is worse.
BASE = 'void f(void)\n{\n    a();\n    b();\n    c();\n    d();\n    e();\n}\n'
A = BASE.replace('    a();\n', '    a(1);\n')
B = BASE.replace('    c();\n', '    c(2);\n')
C = BASE.replace('    e();\n', '    e(3);\n')


def toy_rows(text):
    has = ('a(1)' in text, 'c(2)' in text, 'e(3)' in text)
    # rows 0..2 are what each edit fixes; A alone breaks rows 3 and 4, B alone rows 4 and 5 (C's edit puts them right)
    marks = [OK if h else BAD for h in has] + [BAD if (has[0] and not has[1]) else OK,
                                               BAD if (has[0] and not has[1]) or (has[1] and not has[2]) else OK,
                                               BAD if (has[1] and not has[2]) else OK, OK]
    return rows(*marks)


def toy_score(text):
    return sum(1 for m, _, _ in toy_rows(text) if m != OK) * 100


def test_edits_that_are_each_worse_alone_are_found_by_the_rows_they_fix():
    assert (toy_score(BASE), toy_score(A), toy_score(B), toy_score(C)) == (300, 400, 400, 200)
    base_bad = row_coverage.bad(toy_rows(BASE))
    kids = [('fam_c', C)] + [(f'noise{i}', BASE.replace('d();', f'd({i});')) for i in range(40)] + [('fam_a', A), ('fam_b', B)]
    singles = [(toy_score(text), family, text) for family, text in kids]
    cover = {text: row_coverage.coverage(base_bad, toy_rows(text)) for _, text in kids}
    judged = []

    def judge_many(sources, check=lambda: None):
        judged.extend(sources)
        return [toy_score(text) for text in sources]
    exact = BASE.replace('a();', 'a(1);').replace('c();', 'c(2);').replace('e();', 'e(3);')
    # ranked by score alone, A and B (worse than 40 neutral children) never make the list and nothing reaches 0
    assert pair_search.search(judge_many, BASE, singles, 300)[0] > 0
    found = pair_search.search(judge_many, BASE, singles, 300, cover=cover, parent_bad=base_bad)
    assert found[0] == 0 and found[1] == exact and sorted(found[2].split('+')) == ['fam_a', 'fam_b', 'fam_c']
    assert len(judged) <= 2 * pair_search.PAIR_BUDGET


def test_without_rows_the_search_is_the_score_ranked_one():
    singles = [(300, 'fam_a', A), (300, 'fam_b', B)]
    pairs = pair_search.compose(BASE, singles)
    assert [label for label, _ in pairs] == ['fam_a+fam_b']
