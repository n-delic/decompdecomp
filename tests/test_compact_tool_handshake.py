"""A real CLI handshake must not wait for saved-code verification before work can connect."""
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import anyio
import pytest
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from decompnet.app import tools
from decompnet.network import NetworkError
from tests.test_agent_workflow_efficiency import game


@pytest.mark.parametrize('transport', ['mcp', 'direct'])
def test_pending_compact_handshake_does_not_wait_for_saved_sync(tmp_path, monkeypatch, transport):
    net,agent,_ = game(tmp_path)
    net._pending_tool_handshakes = {(agent.id,'game')}
    net.set_instructions('game','Keep this instruction in the handshake')
    monkeypatch.setattr(Context,'headers',property(lambda self:{}))
    lock = threading.Lock()
    entered = threading.Event()
    def sync(*args,**kwargs):
        entered.set()
        with lock:pass
    monkeypatch.setattr(net.progress,'sync',sync)
    authenticated = []
    def agent_of(ctx):
        authenticated.append(True)
        return agent,'game'
    server = tools.build_mcp(net,agent_of)
    def call():
        if transport=='direct':return tools.call(net,agent,'game','project_info',{'detail':False})
        async def run():return str(await server.call_tool('project_info',{'detail':False}))
        return anyio.run(run)
    blocked = False
    lock.acquire()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(call)
        try:
            try:result = future.result(timeout=2)
            except TimeoutError:blocked = True
        finally:lock.release()
        if blocked:future.result(timeout=5)
    assert not blocked, 'The authenticated handshake waited for saved-file synchronization'
    assert 'Keep this instruction' in result and 'Compact saved metadata' in result
    assert not entered.is_set() and not net._pending_tool_handshakes
    assert bool(authenticated) == (transport=='mcp')
    assert not net.progress.viewing  # no view scope leaks into subsequent work
    # Ordinary compact reads and the first real work request still synchronize.
    tools.call(net,agent,'game','project_info',{'detail':False})
    assert entered.is_set()
    entered.clear()
    monkeypatch.setitem(tools.HANDLERS,'claim_function',lambda *a,**kw:'synthetic work')
    assert tools.call(net,agent,'game','claim_function',{}) == 'synthetic work'
    assert entered.is_set()


@pytest.mark.parametrize('failure', ['authentication', 'missing project'])
def test_failed_real_handshake_keeps_gate_and_project_checks(tmp_path,monkeypatch,failure):
    net,agent,_ = game(tmp_path)
    slug = 'game' if failure=='authentication' else 'removed'
    net._pending_tool_handshakes = {(agent.id,slug)}
    monkeypatch.setattr(Context,'headers',property(lambda self:{}))
    def agent_of(ctx):
        if failure=='authentication':raise NetworkError('Rejected test credential')
        return agent,slug
    server = tools.build_mcp(net,agent_of)
    async def run():return await server.call_tool('project_info',{'detail':False})
    with pytest.raises(ToolError,match='Rejected test credential' if failure=='authentication' else 'no project'):
        anyio.run(run)
    assert net._pending_tool_handshakes == {(agent.id,slug)}
    assert not net.progress.viewing


def test_detailed_pending_handshake_still_uses_normal_project_reads(tmp_path,monkeypatch):
    net,agent,_ = game(tmp_path)
    net._pending_tool_handshakes = {(agent.id,'game')}
    synchronized = []
    monkeypatch.setattr(net.progress,'sync',lambda *a,**kw:synchronized.append(True))
    monkeypatch.setattr(tools,'_project_info',lambda *a,**kw:'Detailed facts')
    monkeypatch.setattr(net,'overview',lambda *a:{})
    tools.call(net,agent,'game','project_info',{'detail':True})
    assert synchronized and not net._pending_tool_handshakes


@pytest.mark.parametrize('work', ['owned claim','focus','readable','boundary'])
def test_final_availability_check_retains_owned_and_targeted_work(tmp_path,monkeypatch,work):
    from tests.test_agent_audit_followups import setup
    net,manager,session,agent = setup(tmp_path,monkeypatch)
    if work=='owned claim':net.claim_row(agent,'game')
    else:
        other,_ = net.local_agent('busy-worker')
        net.claim_row(other,'game')
        session.options = {'focus_id':1} if work=='focus' else {'task':work}
    monkeypatch.setattr(manager,'_preflight_tools',lambda *a, **kw:None)
    monkeypatch.setattr(manager,'prepare_session',lambda *a,**kw:None)
    def command(*args):raise RuntimeError('Synthetic command reached')
    monkeypatch.setattr(manager,'_command',command)
    with pytest.raises(RuntimeError,match='Synthetic command reached'):
        manager._run_cli(session,net.local_agent(session.agent_name)[1],tmp_path)
    assert not session.rotated
