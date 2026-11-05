"""Compact queues retain the distinct release generation for every claimed function."""
import re

import pytest

from decompnet.app import agent_tokens, tools
from decompnet.network import NetworkError
from decompnet.release_receipts import current
from tests.test_claims import _game


def test_compact_queue_tokens_release_each_own_claim_without_hydrating(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[(f'f{i}',2,'open') for i in range(3)]);agent,_=net.local_agent('a')
    monkeypatch.setattr(agent_tokens,'brief',lambda *a,**kw:pytest.fail('Fast claims must not hydrate briefs'))
    monkeypatch.setattr(net,'_compile',lambda *a,**kw:pytest.fail('A receipt must not compile'))
    text=tools.call(net,agent,'game','claim_function',dict(count=3))
    queued=dict(re.findall(r'^- game/(\w+); claim_token=([0-9a-f]+)$',text,re.M))
    assert len(queued)==2
    assert len(set(queued.values()))==2
    for name,lease in queued.items():
        fn=net._function(name,'game')
        assert lease==current(net,fn,agent)
        net.release(agent,name,'Synthetic queued handoff','game',claim_token=lease)
        assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==1
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0


def test_queued_token_changes_after_reclaim_and_old_token_cannot_release_new_lease(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('first',2,'open'),('second',2,'open')]);agent,_=net.local_agent('a')
    monkeypatch.setattr(agent_tokens,'brief',lambda *a,**kw:pytest.fail('No hydration'))
    text=tools.call(net,agent,'game','claim_function',dict(count=2))
    name,old=re.search(r'^- game/(\w+); claim_token=([0-9a-f]+)$',text,re.M).groups()
    net.release(agent,name,'Earlier findings','game',claim_token=old)
    held=net.store.all('SELECT f.* FROM functions f JOIN claims c ON c.function_id=f.id WHERE c.agent_id=?',agent.id)
    for fn in held:
        net.release(agent,fn['name'],'Finish the original batch','game',claim_token=current(net,fn,agent))
    reply=tools.call(net,agent,'game','claim_function',dict(function=name))
    new=re.search(r'^claim_token: ([0-9a-f]+)$',reply,re.M)[1]
    assert new!=old
    with pytest.raises(NetworkError,match='stale'):
        net.release(agent,name,'Delayed older findings','game',claim_token=old)
    assert net.store.one('SELECT 1 FROM claims WHERE function_id=?',net._function(name,'game')['id'])
    net.release(agent,name,'Current findings','game',claim_token=new)


def test_missing_queued_receipt_token_requests_cheap_recovery_instead_of_guessing(monkeypatch):
    from types import SimpleNamespace
    net=SimpleNamespace(store=SimpleNamespace(all=lambda *a:[(1,1),(2,1)]))
    monkeypatch.setattr(agent_tokens,'brief',lambda net,text,*a:text)
    text='## CLAIMED: game/first\nclaim_token: aaaa\n## CLAIMED: game/second\nlarge source'
    result=agent_tokens.claim_packet(net,SimpleNamespace(id=1),'game',text)
    assert 'game/second; get_claims() recovers its current claim_token' in result
    assert 'game/second; claim_token=aaaa' not in result
    repeat=agent_tokens.claim_packet(net,SimpleNamespace(id=1),'game',text)
    assert 'get_claims recovers current function-specific claim tokens without loading C' in repeat
