import copy
from types import SimpleNamespace

import pytest

from decompnet.app import repair_promotion as gate


def envelope(group,old=8,new=4):
    def report(exact):
        return dict(trial_budget=8,seconds_budget=30,cases=[dict(case=group,source_sha256=group,
            baseline_score=10,best_score=0,candidate_compiles=exact,first_exact_trial=exact,elapsed_seconds=1)])
    return dict(profile='profile',family='value_trace_bundle',rules='rules',groups={group:group},
                control=report(old),treatment=report(new))


def test_gate_requires_exact_compile_win_in_training_and_independent_heldout():
    assert gate.evaluate(envelope('train'),envelope('test'))['promote']
    assert not gate.evaluate(envelope('train'),envelope('test',new=8))['promote']
    with pytest.raises(ValueError,match='disjoint'):gate.evaluate(envelope('same'),envelope('same'))
    wrong=envelope('test');wrong['profile']='other'
    with pytest.raises(ValueError,match='same family'):gate.evaluate(envelope('train'),wrong)


def test_score_only_time_noise_losses_and_invalid_results_cannot_promote():
    training=envelope('train');heldout=envelope('test')
    for e in (training,heldout):
        e['control']['cases'][0].update(first_exact_trial=None,best_score=10)
        e['treatment']['cases'][0].update(first_exact_trial=None,best_score=1)
    assert not gate.evaluate(training,heldout)['promote']
    bad=envelope('test');bad['treatment']['cases'][0]['best_score']=float('nan')
    with pytest.raises(ValueError,match='finite'):gate.evaluate(envelope('train'),bad)
    bad=envelope('test');bad['groups']={}
    with pytest.raises(ValueError,match='target group'):gate.evaluate(envelope('train'),bad)


def test_failed_recheck_revokes_existing_priority_without_source_changes(tmp_path):
    from decompnet.store import Store
    net=SimpleNamespace(store=Store(tmp_path/'test.db'))
    training,heldout=envelope('train'),envelope('test')
    assert gate.install(net,training,heldout)['promote']
    assert net.store.one('SELECT family FROM repair_promotions')['family']=='value_trace_bundle'
    rejected=copy.deepcopy(heldout);rejected['treatment']=rejected['control']
    assert not gate.install(net,training,rejected)['promote']
    assert not net.store.one('SELECT family FROM repair_promotions')


def test_experimental_boosts_are_capped_until_current_rules_are_approved(tmp_path,monkeypatch):
    from decompnet.store import Store
    net=SimpleNamespace(store=Store(tmp_path/'test.db'))
    assert gate.cap(net,'profile',dict(value_trace_bundle=4,established=3))==dict(value_trace_bundle=1,established=3)


def test_priority_approval_expires_when_repair_implementation_changes(tmp_path,monkeypatch):
    import hashlib

    from decompnet.app import iterative_repairs
    from decompnet.store import Store
    net=SimpleNamespace(store=Store(tmp_path/'test.db'))
    monkeypatch.setattr(iterative_repairs,'repair_rules',lambda:[('rule','one')])
    digest=hashlib.sha256(repr(iterative_repairs.repair_rules()).encode()).hexdigest()
    training,heldout=envelope('train'),envelope('test')
    training['rules']=heldout['rules']=digest
    gate.install(net,training,heldout)
    assert gate.cap(net,'profile',dict(value_trace_bundle=4))['value_trace_bundle']==4
    monkeypatch.setattr(iterative_repairs,'repair_rules',lambda:[('rule','two')])
    assert gate.cap(net,'profile',dict(value_trace_bundle=4))['value_trace_bundle']==1
