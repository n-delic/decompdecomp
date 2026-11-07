"""Compiler inputs and profile publication use independent project files."""
import copy
import json
from types import SimpleNamespace

import pytest

from decompnet.app import function_profile_suggestions
from decompnet.app.engine.compilers import CompilerMixin
from decompnet.network import NetworkError
from tests import test_project_storage as base

scoped = base.scoped


def setup(net, stores):
    for name, value in vars(CompilerMixin).items():
        if callable(value):setattr(net, name, value.__get__(net))
    net.cfg = lambda proj: json.loads(proj['config'])
    for pid in (1, 2):
        with stores[pid].tx() as db:
            db.execute('UPDATE projects SET config=?', (json.dumps({'compiler':{'toolchain':'test','flags':['-O1']}}),))
            db.execute("UPDATE functions SET vram=4096,size=4,best_source='old source',best_score=50")
    net._source_of = lambda fn: fn['best_source']


@pytest.mark.parametrize('race', [False, True])
def test_compiler_config_publishes_scores_only_in_owner_and_rejects_races(scoped, race):
    net, stores = scoped
    setup(net, stores)
    proj = net._project('first')
    cfg = copy.deepcopy(net.cfg(proj));cfg['compiler']['flags'] = ['-O2']
    def judge(*args):
        if race:
            with stores[1].tx() as db:db.execute("UPDATE functions SET best_source='concurrent edit'")
        return False, SimpleNamespace(score=10), ''
    net._judge_with = judge
    if race:
        with pytest.raises(NetworkError, match='changed during'):
            net._apply_compiler_config(proj, cfg)
        assert net.cfg(net._project('first'))['compiler']['flags'] == ['-O1']
        assert stores[1].one('SELECT best_score FROM functions')['best_score'] == 50
    else:
        net._apply_compiler_config(proj, cfg)
        assert net.cfg(net._project('first'))['compiler']['flags'] == ['-O2']
        assert stores[1].one('SELECT best_score FROM functions')['best_score'] == 10
    assert net.cfg(net._project('second'))['compiler']['flags'] == ['-O1']
    assert stores[2].one('SELECT best_score FROM functions')['best_score'] == 50
    assert not net.store.one('SELECT 1 FROM functions')


def test_compiler_inputs_history_usage_and_suggestions_read_owner(scoped):
    net, stores = scoped
    setup(net, stores)
    proj = net._project('first');fn = net._function('same_name', 'first')
    shared = net._comparison_inputs(proj, [])
    assert shared['contexts'] == [['', 1, 'int first;']]
    assert net.compiler_profile_usage(proj)[0]['functions'] == 1
    digest = net._comparison_digest(proj, fn, 'old source', [], shared)
    with stores[1].tx() as db:
        db.execute('INSERT INTO compiler_tests VALUES(?,?,?,?)', (10, digest, 123, json.dumps({'ranked':[]})))
    assert net.compiler_test_result(proj, fn)['completed_at'] == 123
    assert net.compiler_test_result(net._project('second'), net._function('same_name', 'second')) is None
    proposal = {'toolchain':'test','function_match':{'function':'same_name','vram':4096,'size':4}}
    assert function_profile_suggestions.actionable(net, proj, proposal)
    with stores[1].tx() as db:db.execute("UPDATE functions SET status='matched'")
    assert not function_profile_suggestions.actionable(net, proj, proposal)
    assert function_profile_suggestions.actionable(net, net._project('second'), proposal)


def test_completed_comparison_is_saved_only_in_owner(scoped):
    from decompnet import arch
    net, stores = scoped
    setup(net, stores)
    net._rom_only = lambda *args: None
    net.is_rom = lambda proj: True
    net.console = lambda proj: SimpleNamespace(features={arch.COMPILER_MAP})
    net._cmaps = {}
    net._comparison_candidates = lambda *args: [('test', ('-O1',))]
    net._judge_with = lambda *args: (False, SimpleNamespace(percent=90), '')
    result = net._survey_compilers('first', all_installed=True)
    assert result['tried'] == 1
    assert stores[1].one('SELECT function_id FROM compiler_tests')['function_id'] == 10
    assert not stores[2].one('SELECT 1 FROM compiler_tests')
    assert not net.store.one('SELECT 1 FROM compiler_tests')
