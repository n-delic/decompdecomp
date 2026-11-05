"""Startup packets use the ordinary claim coordinator, exactly once per session."""
from types import SimpleNamespace

import pytest

from decompnet.app import tools
from decompnet.app.agents import Manager, Session
from tests.test_claims import _game, _held


def test_prepare_claims_once_respects_selection_and_keeps_workers_separate(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('small',10,'open'),('large',500,'open')])
    monkeypatch.setattr(net,'sdk_functions',lambda proj:{})
    manager=Manager(net,tmp_path,'http://localhost/mcp')
    monkeypatch.setattr(tools,'_project_info',lambda *a,**kw:'Synthetic project facts\nNext: claim_function()')
    calls=[]
    def packet(net,agent,slug,function,strategy,count):
        row=net.claim_row(agent,slug,strategy=net.selection(slug,agent.name).get('strategy','easiest'))
        calls.append(agent.name)
        return 'CLAIMED '+row['name'] if row else 'Nothing left to claim'
    monkeypatch.setattr(tools,'_claim',packet)
    a,_=net.local_agent('worker-a');b,_=net.local_agent('worker-b')
    net.set_selection('game',a.name,{'strategy':'hardest'})
    sa=Session('a','api','A','game',a.name,{})
    sb=Session('b','codex','B','game',b.name,{})
    manager.prepare_session(sa,a);manager.prepare_session(sa,a)
    manager.prepare_session(sb,b)
    assert _held(net,a)=='large' and _held(net,b)=='small'
    assert calls==['worker-a','worker-b']
    assert 'Synthetic project facts' in sa.startup_context and 'CLAIMED large' in sa.startup_context
    assert 'Do not claim again' in sa.startup_context and 'Next: claim_function()' not in sa.startup_context
    manager.sessions[sa.id]=sa
    manager._session_begin(sa)
    assert sa.startup_context is None


def test_polishing_facts_have_no_function_claim_and_large_briefs_are_bounded(monkeypatch):
    manager=Manager.__new__(Manager);manager.net=SimpleNamespace()
    monkeypatch.setattr(tools,'_project_info',lambda *a,**kw:'Project facts')
    claims=[]
    monkeypatch.setattr(tools,'_claim',lambda *a,**kw:claims.append(True) or ('large brief\n'*2000))
    tidy=Session('a','codex','A','game','worker',{'task':'readable'})
    manager.prepare_session(tidy,SimpleNamespace(id=1))
    assert not claims and 'Project facts' in tidy.startup_context
    matching=Session('b','api','B','game','worker',{})
    manager.prepare_session(matching,SimpleNamespace(id=1))
    assert len(matching.startup_context)<6800 and 'abbreviated' in matching.startup_context


def test_startup_failed_claim_is_not_cached_as_success(monkeypatch):
    from decompnet.network import NetworkError
    manager=Manager.__new__(Manager);manager.net=SimpleNamespace()
    monkeypatch.setattr(tools,'_project_info',lambda *a,**kw:'Project facts')
    def fail(*a,**kw): raise NetworkError('room unavailable')
    monkeypatch.setattr(tools,'_claim',fail)
    session=Session('a','codex','A','game','worker',{})
    with pytest.raises(NetworkError,match='room unavailable'):
        manager.prepare_session(session,SimpleNamespace(id=1))
    assert session.startup_context is None
