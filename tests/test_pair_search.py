"""The bounded pair search: two repairs that are each worse alone and better together (app/pair_search.py)."""
import pytest

from decompnet.app import deep_permuter, generation_calls, judge_pool, pair_search
from tests.test_deep_parallel import Log, setup

BASE = """\
void f(void)
{
    int a;
    int b;
    a = 1;
    b = 2;
    g(a);
    h(b);
    k();
}
"""
EDIT_A = BASE.replace('    a = 1;\n', '    a = 1 + 0;\n')          # touches line 5
EDIT_B = BASE.replace('    h(b);\n', '    h(b + 0);\n')             # touches line 8
EDIT_C = BASE.replace('    a = 1;\n', '    a = (1);\n')              # the same line as A
BOTH = BASE.replace('    a = 1;\n', '    a = 1 + 0;\n').replace('    h(b);\n', '    h(b + 0);\n')


def score(text):
    """A made-up judge: the match needs both edits; either one alone moves registers the wrong way."""
    a, b = '1 + 0' in text, 'b + 0' in text
    return 0 if a and b else 60 if a else 90 if b else 40


def deep_cfg(net, proj):
    return dict(type(net).cfg(net, proj))


def toy_rows(text):
    """The judge's rows of the toy: one row per needed edit, and a third that always matches."""
    return [(' ' if '1 + 0' in text else '|', 'addiu a', 'addiu b'), (' ' if 'b + 0' in text else '|', 'sw a', 'sw b'),
            (' ', 'jr', 'jr')]


def test_merge_applies_both_edits_when_they_touch_different_lines():
    assert pair_search.merge(BASE, EDIT_A, EDIT_B) == BOTH
    assert pair_search.merge(BASE, EDIT_B, EDIT_A) == BOTH


def test_merge_refuses_edits_on_the_same_lines_and_unchanged_children():
    assert pair_search.merge(BASE, EDIT_A, EDIT_C) is None
    assert pair_search.merge(BASE, EDIT_A, BASE) is None
    both_insert = BASE.replace('    k();\n', '    k();\n    m();\n')
    other_insert = BASE.replace('    k();\n', '    k();\n    n();\n')
    assert pair_search.merge(BASE, both_insert, other_insert) is None
    inserted = BASE.replace('    a = 1;\n', '    a = 1;\n    x();\n')
    assert pair_search.merge(BASE, inserted, EDIT_B) == BASE.replace('    a = 1;\n', '    a = 1;\n    x();\n').replace('h(b);', 'h(b + 0);')


def test_select_keeps_the_best_few_per_family_including_non_improving_ones():
    singles = [(50, 'x', 'x1'), (51, 'x', 'x2'), (52, 'x', 'x3'), (53, 'x', 'x4'), (70, 'y', 'y1'), (200, 'z', 'z1')]
    kept = pair_search.select(singles, top=5, per_family=3)
    assert [child for _, _, child in kept] == ['x1', 'x2', 'x3', 'y1', 'z1']
    assert [child for _, _, child in pair_search.select(singles, top=2)] == ['x1', 'x2']


def test_two_edits_each_worse_alone_are_found_together():
    """Each edit alone scores worse than the base (40); together they match. A search that keeps only improvements
    never takes either first step; the pair search composes them from the singles it judged."""
    assert score(BASE) == 40 and score(EDIT_A) == 60 and score(EDIT_B) == 90 and score(BOTH) == 0
    judged = []

    def judge_many(sources, check=lambda: None):
        judged.extend(sources)
        return [score(text) for text in sources]

    singles = [(score(EDIT_A), 'one_zero', EDIT_A), (score(EDIT_B), 'add_zero', EDIT_B), (score(EDIT_C), 'paren', EDIT_C)]
    best, candidate, label, count = pair_search.search(judge_many, BASE, singles, 40)
    assert (best, candidate, count) == (0, BOTH, 2)   # the A+B pair and the paren+B one
    assert label == 'one_zero+add_zero' and BOTH in judged and len(judged) == 2


def test_pairs_are_bounded_and_only_from_different_families():
    base = ''.join(f'    s{n}();\n' for n in range(40))
    singles = [(10 + n, f'fam{n % 6}', base.replace(f'    s{n}();\n', f'    s{n}(0);\n')) for n in range(40)]
    pairs = pair_search.compose(base, singles, top=30, per_family=3, budget=50)
    assert len(pairs) == 50
    assert all(label.split('+')[0] != label.split('+')[1] for label, _ in pairs)
    # the likeliest first: the lowest sum of the two singles' scores
    assert pairs[0][0] == 'fam0+fam1'
    assert pair_search.compose(base, singles, budget=0) == []


def test_clashing_edits_are_asked_again_for_the_first_children_only():
    asked = []

    def regenerate(source):
        asked.append(source)
        return [('paren', source.replace('    h(b);\n', '    h((b));\n')), ('one_zero', source)]

    singles = [(60, 'one_zero', EDIT_A), (50, 'paren', EDIT_C), (80, 'other', BASE.replace('    k();\n', '    k(1);\n'))]
    pairs = pair_search.compose(BASE, singles, regenerate=regenerate, regen_parents=1)
    assert asked == [EDIT_C]                       # the best child only
    assert ('paren+other', BASE.replace('    k();\n', '    k(1);\n').replace('    a = 1;\n', '    a = (1);\n')) in pairs
    assert any(label == 'paren+one_zero' for label, _ in pairs) is False   # a regenerated child of the same family is skipped


@pytest.mark.parametrize('rows_on', [False, True])
def test_the_deep_permuters_pair_pass_adopts_a_pair_the_ordinary_judge_confirms(monkeypatch, rows_on):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': '', 'best_source': BASE}
    search = deep_permuter.Search(net, 10, 40, Log())
    search.winner_diff = ([('r', 'target', 'candidate')], '')
    monkeypatch.setattr(deep_permuter, '_candidate_source', lambda fn, best: BASE)
    monkeypatch.setattr(net, '_function', lambda name, slug: fn)
    if rows_on:  # the row-level ranking (project setting pair_row_coverage) reaches the same pair
        monkeypatch.setattr(net, 'cfg', lambda proj: {**deep_cfg(net, proj), 'pair_row_coverage': True})
    monkeypatch.setattr(search, '_local_proposals', lambda source, proj, fn, rows: (
        [('one_zero', EDIT_A), ('add_zero', EDIT_B)] if source == BASE else [], []))
    pools = []

    class Pool:
        def __init__(self, slug, fn, toolchain, flags, size=4, home=None):
            pools.append(size)
            self.errors = []

        def judge_many(self, sources, check=lambda: None):
            return [score(text) for text in sources]

        def judge_rows(self, source):
            return score(source), toy_rows(source)

        def judge_rows_many(self, sources, check=lambda: None):
            return [self.judge_rows(text) for text in sources]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(judge_pool, 'JudgePool', Pool)
    scored = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: scored.append(child) or score(child))
    assert search.repair_pair_pass(AGENT, proj, fn, 40, None) == 0
    assert scored == [BOTH] and pools == [deep_permuter.Search.PAIR_WORKERS]
    assert deep_permuter.Search.PAIR_WORKERS <= 2
    assert BOTH in search.local_seen


def test_the_pair_pass_declines_without_evidence_or_for_a_fork(monkeypatch):
    net = setup(monkeypatch)
    search = deep_permuter.Search(net, 1, 40, Log())
    fn = net.function()
    assert search.repair_pair_pass(None, net.project_row('game'), fn, 40, 'fork') == 40
    search.winner_diff = ([], '')
    assert search.repair_pair_pass(None, net.project_row('game'), fn, 40, None) == 40
    assert pytest is not None
