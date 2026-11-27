"""prove_chain: a helper runs the bounded greedy chain of repair families that hand sessions run with the skill's
prove_chain.py (a whole-path audit matched 11 of 42 stalled functions with the library alone; helpers could try only
one family per compile)."""
import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.app import prove_chain, tool_policy, tools
from decompnet.network import NetworkError


def test_registered_for_matching_helpers_in_rom_projects():
    assert 'prove_chain' in tools.HANDLERS and 'prove_chain' in tool_policy.MATCHING
    assert 'prove_chain' in tools.JUDGE_TOOLS
    assert 'prove_chain' in tools.tool_names(False) and 'prove_chain' not in tools.tool_names(True)
    assert 'prove_chain' in {t['function']['name'] for t in api_tool_schemas()}


def _scores(table):
    return lambda sources: [table.get(s) for s in sources]


def test_the_chain_takes_the_best_child_of_the_named_families_until_it_matches():
    children = {'a': [('x', 'b'), ('y', 'c')], 'b': [('x', 'd')], 'c': [], 'd': []}
    final, best, taken, _ = prove_chain.chain('a', lambda s: children[s], _scores({'a': 30, 'b': 20, 'c': 25, 'd': 0}))
    assert (final, best) == ('d', 0) and [t[:2] for t in taken] == [('x', 20), ('x', 0)]
    # a family not named is never taken
    final, best, taken, notes = prove_chain.chain('a', lambda s: children[s],
                                                  _scores({'a': 30, 'b': 20, 'c': 25, 'd': 0}), families={'y'})
    assert (final, best) == ('c', 25) and 'no child' in notes[-1]


def test_the_chain_is_bounded():
    many = [('x', f's{i}') for i in range(10)]
    scores = {'a': 30} | {f's{i}': 30 - i for i in range(10)}
    _, best, taken, notes = prove_chain.chain('a', lambda s: many if s == 'a' else [], _scores(scores), children=3)
    assert best == 28 and 'left out' in notes[0]
    _, best, taken, _ = prove_chain.chain('a', lambda s: [('x', s + '+')], lambda ss: [100 - len(s) for s in ss], steps=2)
    assert len(taken) == 2

    def late():
        raise TimeoutError
    _, best, taken, notes = prove_chain.chain('a', lambda s: many, _scores(scores), check=late)
    assert best == 30 and not taken and 'out of time' in notes[0]


def test_a_batch_with_no_improving_child_is_not_a_stall_while_children_are_left_out():
    # the only improving child is number 8 of 10: under children=3 the first batch (s0..s2) stalls, the chain judges on
    many = [('x', f's{i}') for i in range(10)]
    scores = {'a': 30} | {f's{i}': 31 for i in range(10)} | {'s7': 20}
    judged = []

    def score(sources):
        judged.extend(sources)
        return [scores.get(s) for s in sources]
    final, best, taken, notes = prove_chain.chain('a', lambda s: many if s == 'a' else [], score, children=3)
    assert (final, best) == ('s7', 20) and taken[0][0] == 'x'
    assert judged == ['a'] + [f's{i}' for i in range(9)]  # stops after the batch holding the improving child
    assert any('left out' in note for note in notes)
    # nothing improves anywhere: every child is judged once, then the chain ends
    judged.clear()
    flat = {'a': 30} | {f's{i}': 31 for i in range(10)}
    _, best, taken, notes = prove_chain.chain('a', lambda s: many if s == 'a' else [],
                                              lambda ss: judged.extend(ss) or [flat.get(s) for s in ss], children=3)
    assert best == 30 and not taken and len(judged) == 11 and 'no child of 10' in notes[-1]


class _Net:
    def __init__(self, best):
        self.best = best

    def _function(self, function, slug):
        return {'best_score': self.best}


def test_only_a_final_source_beating_the_saved_best_is_recorded(monkeypatch):
    recorded = []
    monkeypatch.setattr(tools, '_compile', lambda net, agent, slug, function, source: recorded.append(source) or 'ok')
    out = prove_chain.report(_Net(12), None, 's', 'f', 'src', 12, [('x', 12, 3, 3)], [], [])
    assert not recorded and 'nothing saved' in out
    out = prove_chain.report(_Net(12), None, 's', 'f', 'best', 4, [('x', 4, 3, 3)], [], [])
    assert recorded == ['best'] and 'Final 4' in out


def test_project_mode_says_what_to_use_instead():
    class Fork:
        def _function(self, function, slug):
            return {}

        def fork_slug(self, slug):
            return True
    with pytest.raises(NetworkError, match='try_local_repairs'):
        prove_chain.run(Fork(), None, 's', 'f', 'r')
    assert prove_chain._families('all') is None and prove_chain._families(['a', ' b']) == {'a', 'b'}
