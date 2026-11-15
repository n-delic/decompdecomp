"""A queued release cannot hold the project lock needed by its in-flight compile."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app import tools
from tests.test_agent_workflow_efficiency import game


@pytest.mark.parametrize('transport',['api','mcp'])
def test_release_waits_for_candidate_before_holding_project_lock(tmp_path,monkeypatch,transport):
    net,agent,fn=game(tmp_path)
    net.claim_row(agent,'game','f')
    queued=threading.Event();compiling=threading.Event();compile_thread=[]
    class CandidateLock:
        def __init__(self):self.lock=threading.RLock()
        def acquire(self,*args,**kwargs):
            if compiling.is_set() and threading.get_ident()!=compile_thread[0]:queued.set()
            return self.lock.acquire(*args,**kwargs)
        def release(self):self.lock.release()
        def __enter__(self):self.acquire();return self
        def __exit__(self,*args):self.release()
    candidate=CandidateLock()
    net._attempt_locks[(agent.id,fn['id'])]=candidate
    monkeypatch.setattr(net,'release_function',lambda *args,**kwargs:'Released; findings saved.')
    def compile_result():
        compile_thread.append(threading.get_ident())
        with candidate:
            compiling.set()
            assert queued.wait(5)
            project=net.progress._project_lock('game')
            acquired=project.acquire(timeout=.5)
            if acquired:project.release()
            return acquired
    args={'function':'f','notes':'Tried the ordinary judge; retain these findings.','move_on':True}
    def call():return tools.call(net,agent,'game','release_function',args)
    if transport=='mcp':
        import anyio
        from mcp.server.mcpserver import Context
        monkeypatch.setattr(Context,'headers',property(lambda self:{}))
        server=tools.build_mcp(net,lambda ctx:(agent,'game'))
        async def request():return await server.call_tool('release_function',args)
        def call():return anyio.run(request)
    with ThreadPoolExecutor(2) as pool:
        compile_future=pool.submit(compile_result)
        assert compiling.wait(5)
        release_future=pool.submit(call)
        acquired=compile_future.result(timeout=6)
        assert 'findings saved' in str(release_future.result(timeout=6))
    assert acquired, 'Release held the project lock while waiting for this same compile.'
    assert not getattr(net.progress.local,'foreground',False)


def test_release_rejects_a_replaced_function_before_entering_its_handler(tmp_path,monkeypatch):
    from decompnet.network import NetworkError
    net,agent,fn=game(tmp_path)
    resolve=net._function;calls=[]
    def changed(*args):
        calls.append(1)
        return resolve(*args) if len(calls)==1 else {**dict(fn),'id':fn['id']+1}
    monkeypatch.setattr(net,'_function',changed)
    with pytest.raises(NetworkError,match='function changed'):
        with tools._tool_foreground(net,agent,'game','release_function',{'function':'f'}):
            raise AssertionError('Must not acquire a different candidate lock under the project lock')
    assert not getattr(net.progress.local,'foreground',False)
