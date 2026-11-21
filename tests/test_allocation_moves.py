"""Ranking statement moves by the allocation evidence of a register swap (allocation_moves)."""
from decompnet.app import allocation_moves, paired_moves

SOURCE = '''void f(float *m, float x, float z) {
    float sz = g(z);
    float cy = g(x);
    m[0] = cy;
    m[1] = 1.0f;
    m[2] = 2.0f;
    m[3] = sz;
    m[4] = cy * sz;
}'''
EVIDENCE = {'values': [
    {'pseudo': 80, 'allocation_rank': 3, 'aliases': ['f20', '52'], 'source_lines': [3, 4, 8]},  # cy won $f20
    {'pseudo': 79, 'allocation_rank': 7, 'aliases': ['f22', '54'], 'source_lines': [2, 7, 8]},  # sz got $f22
]}
ROWS = [('r', 'mov.s $f20,$f0', 'mov.s $f22,$f0')]


def test_a_swap_names_the_winning_and_losing_variables():
    assert allocation_moves.rivals(EVIDENCE, ROWS, SOURCE) == [('cy', 'sz')]
    assert allocation_moves.rivals(EVIDENCE, [], SOURCE) == []
    assert allocation_moves.rivals({'values': []}, ROWS, SOURCE) == []


def test_moves_that_shorten_the_loser_or_lengthen_the_winner_come_first():
    from decomprepair import paired_statement_moves as moves
    singles = moves.single_moves(SOURCE, 'f')
    ordered = allocation_moves.order(SOURCE, singles, [('cy', 'sz')])
    assert sorted(ordered) == sorted(singles)
    first = ordered[0]
    assert allocation_moves._span(first, 'sz') < allocation_moves._span(SOURCE, 'sz') or \
        allocation_moves._span(first, 'cy') > allocation_moves._span(SOURCE, 'cy')


def test_the_search_judges_the_ranked_moves_first_and_steps_at_once():
    from decomprepair import paired_statement_moves as moves
    singles = moves.single_moves(SOURCE, 'f')
    good = singles[-1]
    asked = []

    def judge(candidates, check=lambda: None):
        asked.extend(candidates)
        return [10 if c == good else 120 for c in candidates]
    score, winner, judged = paired_moves.search(judge, SOURCE, 'f', '', 100, rank=lambda s: [good] + [c for c in s if c != good])
    assert (score, winner) == (10, good)
    assert len([c for c in asked if c in singles]) <= paired_moves.RANK_CHUNK
