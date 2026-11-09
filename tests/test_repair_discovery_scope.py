"""Proposal filtering shares input fingerprints only within one discovery pass."""
import hashlib
import json

from decompnet.app import agent_tokens, local_experiments, repair_generation
from tests.test_claims import _game


def setup(tmp_path):
    net,pid=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',
                   (json.dumps({'kind':'rom','compiler':{'toolchain':'synthetic','flags':[]}}),pid))
    return net,pid,agent,net._function('f','game')


def remember(net,fn,revision,family,source):
    scope=agent_tokens.target_scope(net,fn,revision)
    with net.store.tx() as db:
        db.execute('INSERT INTO agent_family_targets VALUES(?,?,?,?)',
                   (fn['id'],scope,family,hashlib.sha256(source.encode()).hexdigest()))


def test_filter_fingerprint_work_is_constant_and_new_inputs_reopen_trials(tmp_path,monkeypatch):
    net,pid,agent,fn=setup(tmp_path);source='int f(){return 200;}'
    proposals=[f'int f(){{return {i};}}' for i in range(100)]
    remember(net,fn,'rev','local:branch_orientation',proposals[0])
    original=agent_tokens.target_scope;calls=[]
    def scope(*args):
        calls.append(args[-1]);return original(*args)
    monkeypatch.setattr(agent_tokens,'target_scope',scope)
    monkeypatch.setattr(local_experiments,'_generated',lambda function,*a,**kw:
                        [('branch_orientation',p) for p in proposals] if function is local_experiments.pattern_repairs.variants else [])
    pools=local_experiments._available(net,agent,'game',fn,source,'rev',['branch_orientation'])
    assert set(pools['branch_orientation'])==set(proposals[1:])
    assert calls.count('rev')==1 and len(calls)==4  # filtering/ranking scopes stay independent of pool size
    calls.clear()
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',
                   (json.dumps({'kind':'rom','compiler':{'toolchain':'synthetic','flags':['-O1']}}),pid))
    pools=local_experiments._available(net,agent,'game',fn,source,'rev',['branch_orientation'])
    assert set(pools['branch_orientation'])==set(proposals)
    assert calls.count('rev')==1 and len(calls)==4


def test_generation_eligibility_reuses_scope_but_reads_fresh_trial_records(tmp_path,monkeypatch):
    net,_,agent,fn=setup(tmp_path);source='int f(){return 200;}'
    proposals=[f'int f(){{return {i};}}' for i in range(100)]
    remember(net,fn,'rev','local:branch_orientation',proposals[0])
    original=agent_tokens.target_scope;calls=[]
    def scope(*args):
        calls.append(args[-1]);return original(*args)
    monkeypatch.setattr(agent_tokens,'target_scope',scope)
    def generate(*args):
        state=repair_generation.ACTIVE.get()
        return {'branch_orientation':[p for p in proposals if state.accept('branch_orientation',p)]}
    monkeypatch.setattr(local_experiments,'_available',generate)
    pools=local_experiments.available(net,agent,'game',fn,source,'rev',['branch_orientation'])
    assert set(pools['branch_orientation'])==set(proposals[1:])
    assert len(calls)==3
    remember(net,fn,'rev','local:branch_orientation',proposals[1]);calls.clear()
    pools=local_experiments.available(net,agent,'game',fn,source,'rev',['branch_orientation'])
    assert set(pools['branch_orientation'])==set(proposals[2:])
    assert len(calls)==3
