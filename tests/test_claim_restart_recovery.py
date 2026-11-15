"""Synthetic lease recovery: no running app, provider, compiler or user project access."""
from types import SimpleNamespace

import pytest

from decompnet.app import tools
from decompnet.app.agents import Manager, Session
from decompnet.network import NetworkError
from tests.test_claims import _game


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_host_grant_reconciles_stale_local_claim_and_survives_restart(tmp_path, monkeypatch, mode):
    net,pid=_game(tmp_path,[('old',10,'open'),('next',20,'open'),('other',30,'open')])
    agent,_=net.local_agent('worker');other,_=net.local_agent('other-worker')
    net.claim_row(agent,'game','old');net.claim_row(other,'game','other')
    granted=[]
    link=SimpleNamespace(claim=lambda a,f,s:granted.append(f) or 'next',sync=lambda:None)
    net.links['game']=link
    if mode=='fork':
        monkeypatch.setattr(net,'fork_slug',lambda slug:'game')
        monkeypatch.setattr(net.forks,'brief',lambda fn,*args,**kwargs:'CLAIMED '+fn['name'])
    else:
        monkeypatch.setattr(net,'_brief',lambda fn,*args,**kwargs:'CLAIMED '+fn['name'])
    assert 'CLAIMED next' in net.claim(agent,'game')
    assert 'CLAIMED next' in net.claim(agent,'game')  # a subsequent provider session renews it
    assert granted==[None,None]
    assert [r['name'] for r in net.store.all('SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=?',agent.id)]==['next']
    assert net.store.one('SELECT 1 FROM claims WHERE agent_id=?',other.id)
    assert net.store.one("SELECT COUNT(*) n FROM events WHERE kind='room_claim_resynced'")['n']==1


def test_failed_host_request_does_not_discard_local_claim(tmp_path):
    net,_=_game(tmp_path,[('old',10,'open'),('next',20,'open')]);agent,_=net.local_agent('worker')
    net.claim_row(agent,'game','old')
    def fail(*args):raise NetworkError('host offline')
    net.links['game']=SimpleNamespace(claim=fail)
    with pytest.raises(NetworkError,match='host offline'):net.claim(agent,'game')
    assert net.store.one('SELECT function_id FROM claims WHERE agent_id=?',agent.id)['function_id']==net._function('old','game')['id']


def test_failed_local_validation_rolls_back_reconciliation(tmp_path):
    net,_=_game(tmp_path,[('old',10,'open'),('next',20,'open')]);agent,_=net.local_agent('worker');other,_=net.local_agent('other')
    net.claim_row(agent,'game','old');net.claim_row(other,'game','next')
    with pytest.raises(NetworkError):net._room_claim_row(agent,'game','next','easiest')
    assert net.store.one('SELECT function_id FROM claims WHERE agent_id=?',agent.id)['function_id']==net._function('old','game')['id']
    assert net.store.one('SELECT 1 FROM claims WHERE agent_id=?',other.id)


def test_existing_batch_claim_can_be_renewed_but_unowned_switch_still_fails(tmp_path,monkeypatch):
    net,pid=_game(tmp_path,[('first',10,'open'),('second',20,'open'),('third',30,'open')]);agent,_=net.local_agent('worker')
    monkeypatch.setattr(net,'_brief',lambda fn,*a,**kw:'CLAIMED '+fn['name'])
    net.claim_many(agent,'game',2)
    assert net.claim_row(agent,'game','second')['name']=='second'
    with pytest.raises(NetworkError,match='already hold'):net.claim_row(agent,'game','third')


def test_new_provider_session_receives_a_fresh_claim_brief(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('held',10,'open')]);agent,_=net.local_agent('worker')
    manager=Manager(net,tmp_path,'http://localhost/mcp')
    monkeypatch.setattr(tools,'_project_info',lambda *a,**kw:'Project facts')
    net._claim_packets={(agent.id,'game'):('cached',()),(999,'game'):('other',())}
    def claim(*args,**kwargs):
        assert (agent.id,'game') not in net._claim_packets
        assert (999,'game') in net._claim_packets
        return 'Full resumed brief and candidate C'
    monkeypatch.setattr(tools,'_claim',claim)
    session=Session('s','api','model','game',agent.name,{})
    manager.prepare_session(session,agent)
    assert 'Full resumed brief' in session.startup_context
