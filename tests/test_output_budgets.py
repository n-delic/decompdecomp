import pytest

from decompnet import claim_stalls
from decompnet.app import agent_evidence, agent_tokens, tools
from decompnet.network import NetworkError
from decomprepair import load_pair_repairs
from tests.test_claims import _game


def test_pair_candidates_keep_both_loads_and_reject_calls():
    source='void f(){a=*(u16 *)(manager+26);b=(*(s32 *)((char *)(node)+4));flag=b!=0;if(a){send();}}'
    out=load_pair_repairs.variants(source,'f')
    assert len(out)==1 and 'volatile u16' in out[0] and 'volatile s32' in out[0]
    assert not load_pair_repairs.variants(source.replace('manager+26','get()+26'),'f')
    assert not load_pair_repairs.variants(source.replace('b=','send();b='),'f')


def test_unchanged_output_budget_is_independent_and_claim_scoped(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a');net.claim_row(agent,'game','f');fn=net._function('f','game')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=? WHERE id=?',('void f(){}',fn['id']))
    net.set_selection('game','a',{'stall_compiles':200,'unchanged_compiles':2})
    net._agent_outcomes={(agent.id,fn['id']):{'score':5,'rows':[('r','a','b')]}}
    assert '0/2' in agent_evidence.track(net,agent,'game',fn)
    assert '1/2' in agent_evidence.track(net,agent,'game',fn)
    assert 'Unchanged-output limit reached' in agent_evidence.track(net,agent,'game',fn)
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])
    assert claim_stalls.blocked(net,net._project('game'),agent)==[fn['id']]
    net.claim_row(agent,'game','f')
    assert '0/2' in agent_evidence.track(net,agent,'game',fn)
    assert net.selection('game','a')['stall_compiles']==200


def test_warning_repeat_is_compact_and_original_remains_available(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a');net.claim_row(agent,'game','f');fn=net._function('f','game')
    warning='compiler warning: argument type mismatch'
    assert agent_evidence.warning(net,agent,fn,warning)==warning
    assert 'Same compiler warnings' in agent_evidence.warning(net,agent,fn,warning)
    assert warning in agent_tokens.function_view(net,agent,'game','f','warnings')


def test_selection_modes_cannot_be_mixed_and_legacy_still_works(tmp_path):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):tools.NextFamilyTarget(mode='next',old='int x;')
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a')
    with pytest.raises(NetworkError,match='one selector'):
        agent_tokens.local_family(net,agent,'game','f','r','signedness',old='a',selector={'mode':'next'})
    assert net.validate_selection({'unchanged_compiles':0})['unchanged_compiles']==0
    with pytest.raises(NetworkError):net.validate_selection({'unchanged_compiles':-1})
