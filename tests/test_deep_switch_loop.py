import time
from types import SimpleNamespace

import pytest

from decompnet import scoring, source_lines
from decompnet.app import deep_switch_loop
from decompnet.app.permute import Permuter
from decompnet.repair_feedback import current_trial, trial
from tests.test_deep_permuter import Log, setup
from tests.test_permute import AGENT


def rows(*different):
    target = [
        'beqz $a0, .L10', 'nop', 'addiu $v1, $v1, 1', 'nop',
        'sll $v0, $a0, 16', 'jr $ra', 'nop',
    ]
    return [('!' if index in different else ' ', op, 'nop' if index in different else op)
            for index,op in enumerate(target)]


def test_disjoint_measured_blocks_earn_one_pair_from_the_same_parent(monkeypatch):
    miss_rows = {'first':rows(4), 'second':rows(2)}
    baseline = rows(2,4)
    measured = []
    pairs = []

    def judge(agent, proj, fn, source, best, fork):
        measured.append(source)
        if source == 'paired':
            assert current_trial()['lineage'] == ['first_family', 'second_family']
            return 0
        return 105

    def combine(parent, first, second, family, name, header):
        pairs.append((parent, first, second))
        return 'paired'

    monkeypatch.setattr(deep_switch_loop.switch_loop_repairs, 'variants',
                        lambda *a, **k: [('first_family','first'), ('second_family','second')])
    monkeypatch.setattr(deep_switch_loop.switch_loop_repairs, 'combine', combine)
    monkeypatch.setattr(deep_switch_loop.repair_ranking, 'evidence', lambda *args: miss_rows[measured[-1]])
    search = SimpleNamespace(_score=judge, _trial_reused=False, log=Log(), local_seen=set(), net=object(),
                             efficiency=SimpleNamespace(generated=lambda *args: None))
    settings = {'toolchain':'gcc-2.7.2', 'flags':['-O2','-g3']}
    args = (search, AGENT, {'arch':'mips','slug':'game'}, {'name':'f','id':1}, 'root', baseline, '', 100, settings)
    assert deep_switch_loop.run(*args, 3, time.monotonic()+10) == (0, 'paired', 3)
    assert measured == ['first','second','paired']
    assert pairs == [('root','first','second')]
    assert deep_switch_loop.run(*args, 0, time.monotonic()+10) == (100,'root',0)


@pytest.mark.parametrize('verdict', [False, True])
def test_explicit_qualifier_hypothesis_only_saves_a_linked_match(monkeypatch, verdict):
    net = setup(monkeypatch)
    net._compile = lambda *args: SimpleNamespace(ok=True,obj=object())
    net._evaluate = lambda *args: (object(), verdict, [])
    net._target_obj = lambda *args: SimpleNamespace(cpu='mips')
    net.keep_diff = lambda *args: None
    monkeypatch.setattr(scoring, 'diff', lambda *args: scoring.DiffResult(1,1000,[],False))
    monkeypatch.setattr(scoring, 'disassemble', lambda *args: [])
    monkeypatch.setattr(source_lines, 'mismatches', lambda *args, **kwargs: [])
    recorded = []
    net._record = lambda *args, **kwargs: recorded.append(kwargs['matched'])
    fn = net.function()
    with trial({'family':deep_switch_loop.switch_loop_repairs.EXACT, 'exact_only':True}):
        score = Permuter(net)._score(AGENT,net.project_row('game'),fn,'int f(void) { return 1; }',40,None)
    assert score == (0 if verdict else None)
    assert recorded == ([True] if verdict else [])
