"""Move-on guidance must carry the lease token needed after a same-worker reclaim."""
import ast

import pytest

from decompnet.network import NetworkError
from decompnet.release_receipts import instruction
from tests.test_agent_addresses_and_retries import game


def arguments(text):
    call=ast.parse(text,mode='eval').body
    assert isinstance(call,ast.Call) and call.func.id=='release_function'
    return {kw.arg:ast.literal_eval(kw.value) for kw in call.keywords}


def test_release_guidance_handles_reclaim_and_cannot_release_a_later_lease(tmp_path):
    net,agent,fn=game(tmp_path)
    net.claim_row(agent,'game','f')
    first=arguments(instruction(net,fn['id'],agent.id))
    net.release_function(agent,project='game',move_on=True,**first)
    net.claim_row(agent,'game','f')
    second=arguments(instruction(net,fn['id'],agent.id))
    assert second['claim_token']!=first['claim_token']
    with pytest.raises(NetworkError,match='stale'):
        net.release_function(agent,project='game',move_on=True,**dict(first,notes='Older delayed findings'))
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fn['id'])['agent_id']==agent.id
    # The exact suggested arguments work on the first release of the new lease.
    net.release_function(agent,project='game',move_on=True,**second)
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])
    assert net._function('f','game')['abandons']==2


def test_move_on_note_contains_executable_current_release_instruction(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    monkeypatch.setattr(net,'stall_status',lambda *a,**kw:dict(compiles=4,no_better=3,after=3))
    action=instruction(net,fn['id'],agent.id)
    note=net.stall_note(fn['id'],agent.id)
    assert action in note
    assert 'first release and on retries' in note
    monkeypatch.undo()
    net.release_function(agent,project='game',move_on=True,**arguments(action))
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])


def test_guidance_never_supplies_another_workers_token_or_releases_anything(tmp_path):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    other,_=net.local_agent('other')
    assert 'Do not release' in instruction(net,fn['id'],other.id)
    assert 'claim_token=' not in instruction(net,fn['id'],other.id)
    assert 'claim_token=' not in instruction(net,99999,agent.id)
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fn['id'])['agent_id']==agent.id
    assert net._function('f','game')['abandons']==0
