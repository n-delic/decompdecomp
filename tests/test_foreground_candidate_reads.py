"""Read the installed candidate/target during a ready recheck, without inventing a verdict."""
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError

import pytest

from decompnet.app import agent_evidence, local_experiments, saves, tools
from decompnet.app.progress import _Recheck
from tests.test_progress import project as project_fixture

project=project_fixture


@pytest.mark.parametrize('transport',['direct','mcp'])
@pytest.mark.parametrize('tool,args',[('get_candidate',{}),('get_assembly',{}),('get_context',{}),
    ('get_context',{'function':''}),
    ('get_function',{}),('get_function',{'section':'brief'}),
    ('get_function',{'section':'candidate'}),('get_function',{'section':'history'})])
def test_current_reads_finish_during_saved_compile_using_real_handlers(project,monkeypatch,transport,tool,args):
    net,folder,judged=project
    with net.store.tx() as db:
        # Briefs need the compiler facts a real imported project always has.
        # An empty toolchain keeps this synthetic judge independent of SDK installs.
        db.execute("UPDATE projects SET config=json_set(config,'$.compiler',json(?))",
                   (json.dumps({'toolchain':'','flags':[]}),))
        db.execute("UPDATE functions SET relocs='[]',meta='{}',rom_offset=(id-1)*4")
        db.execute("UPDATE functions SET best_source='int f(void) { return 3; }',best_score=50,asm='nop' WHERE name='f'")
        db.execute("UPDATE context_versions SET header='typedef int committed_context;' WHERE project_id=1")
    net.progress.sync('game',force=True)
    agent,_=net.local_agent('reader')
    waiting,compilation=threading.Event(),Future()
    def checks(*args):
        waiting.set()
        yield 'f',{'matched_by':'helper'},'src','good imported C','f',('compile',compilation)
    monkeypatch.setattr(saves,'_saved_checks',checks)
    monkeypatch.setattr(net,'_rom',lambda *a:(bytes(16),[]))
    monkeypatch.setattr(net,'compiler_map',lambda *a,**k:None)  # no binary in this synthetic project
    monkeypatch.setattr(agent_evidence,'baseline',lambda *a,**k:None)
    monkeypatch.setattr(local_experiments,'summary',lambda *a,**k:'No synthetic trials available.')
    monkeypatch.setattr(net,'_compile',lambda *a,**k:pytest.fail('read must not invent a compiler result'))
    (folder/'src').mkdir(exist_ok=True);(folder/'src/f.c').write_text('good imported C')
    def invoke():return tools.call(net,agent,'game',tool,{'function':'f',**args})
    if transport=='mcp':
        import anyio
        from mcp.server.mcpserver import Context
        monkeypatch.setattr(Context,'headers',property(lambda self:{}))
        server=tools.build_mcp(net,lambda ctx:(agent,'game'))
        async def request():return await server.call_tool(tool,{'function':'f',**args})
        def invoke():return str(anyio.run(request))
    with ThreadPoolExecutor(2) as pool:
        background=pool.submit(net.progress.sync,'game',force=True)
        try:
            assert waiting.wait(5)
            before=dict(net.store.one("SELECT status,best_source,best_score FROM functions WHERE name='f'"))
            reply=pool.submit(invoke).result(timeout=2)
            assert not background.done() and not judged
            assert dict(net.store.one("SELECT status,best_source,best_score FROM functions WHERE name='f'"))==before
            assert before['status']=='open' and before['best_score']==50
            if tool=='get_candidate':
                assert 'Candidate revision' in reply and 'return 3;' in reply
                assert 'historical/unverified' in reply
            elif tool=='get_function':
                if not args:assert 'f: open' in reply and 'saved historical score 50' in reply
                elif args['section'] in {'candidate','brief'}:assert 'return 3;' in reply
                else:assert 'No attempt history' in reply
            elif tool=='get_context':
                assert 'context.h for game' in reply and 'typedef int committed_context;' in reply
            else:assert 'nop' in reply
        finally:compilation.set_result(None)
        assert background.result(timeout=5)
    assert judged==[('f','good imported C')]
    assert not getattr(net.progress.local,'foreground',False)


@pytest.mark.parametrize('tool,args',[('get_candidate',{}),('get_assembly',{}),('get_function',{}),('get_context',{}),
                                     ('get_function',{'section':'experiment'}),
                                     ('get_candidate',{'base':'saved_best'}),('get_candidate',{'base':'last_compiling'}),
                                     ('get_candidate',{'base':'reported_corrected'})])
def test_current_reads_wait_for_evidence_and_recovery_choices_wait_for_recheck(project,monkeypatch,tool,args):
    net,folder,_=project;worker=net.progress
    agent,_=net.local_agent('reader')
    recovery=bool(args)
    worker.rechecking['game']=_Recheck(folder,worker._signature(folder),ready=recovery)
    dispatched=[]
    monkeypatch.setattr(tools,'_dispatch',lambda *a:dispatched.append(1) or 'read ready')
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(tools.call,net,agent,'game',tool,{'function':'f',**args})
        try:
            with pytest.raises(TimeoutError):future.result(timeout=.15)
            assert not dispatched
        finally:
            with worker._project_lock('game'):worker.rechecking.pop('game',None)
        assert future.result(timeout=3)=='read ready'


@pytest.mark.parametrize('tool',['get_candidate','get_assembly','get_function','get_context'])
def test_ready_read_rejects_changed_saved_files_before_handler(project,monkeypatch,tool):
    net,folder,_=project;worker=net.progress
    agent,_=net.local_agent('reader')
    worker.rechecking['game']=_Recheck(folder,worker._signature(folder),ready=True)
    (folder/'src').mkdir(exist_ok=True);(folder/'src/f.c').write_text('changed after import')
    monkeypatch.setattr(tools,'_dispatch',lambda *a:pytest.fail('changed file reached read handler'))
    try:
        reply=tools.call(net,agent,'game',tool,{'function':'f'})
        assert 'Saved files changed during the recheck' in reply
    finally:worker.rechecking.pop('game',None)
    assert not getattr(worker.local,'foreground',False)


@pytest.mark.parametrize('tool',['get_candidate','get_assembly','get_function','get_context'])
def test_read_outside_recheck_still_imports_and_judges_changed_source(project,monkeypatch,tool):
    net,folder,judged=project
    with net.store.tx() as db:db.execute("UPDATE functions SET asm='nop' WHERE name='f'")
    agent,_=net.local_agent('reader')
    monkeypatch.setattr(agent_evidence,'baseline',lambda *a,**k:None)
    monkeypatch.setattr(local_experiments,'summary',lambda *a,**k:'No synthetic trials available.')
    (folder/'src').mkdir(exist_ok=True);(folder/'src/f.c').write_text('good external source')
    reply=tools.call(net,agent,'game',tool,{'function':'f'})
    assert not reply.startswith('error:')
    assert judged==[('f','good external source')]
    assert net._function('f','game')['status']=='matched'


def test_unready_recheck_wait_has_its_own_timing_until_evidence_is_installed(project):
    from decompnet import request_timing
    net,folder,_=project;worker=net.progress
    active=_Recheck(folder,worker._signature(folder),ready=False)
    worker.rechecking['game']=active
    metrics={};entered=threading.Event()
    def read():
        with request_timing.capture() as captured, worker.foreground():
            entered.set()
            try:return worker.sync('game',force=True)
            finally:metrics.update(captured)
    with ThreadPoolExecutor(1) as pool:
        pending=pool.submit(read)
        try:
            assert entered.wait(2)
            with pytest.raises(TimeoutError):pending.result(timeout=.15)
            with worker._project_lock('game'):
                active.ready=True
                threading.Condition(worker._project_lock('game')).notify_all()
            assert pending.result(timeout=2) is False
            assert metrics['progress_recheck_wait']>=.10
            assert metrics.get('progress_import',0)==0
        finally:
            with worker._project_lock('game'):worker.rechecking.pop('game',None)
def test_apps_own_publish_during_recheck_does_not_reject_agents_but_an_outside_edit_still_does(project,monkeypatch):
    """The app saves every agent's progress all the time; with eight agents the manifest changed every second, and each
    save during a recheck failed every agent's next call ("Saved files changed during the recheck") for any function."""
    net,folder,_=project;worker=net.progress
    agent,_=net.local_agent('reader')
    worker.rechecking['game']=_Recheck(folder,worker._signature(folder),ready=True)
    monkeypatch.setattr(tools,'_dispatch',lambda *a:'read ready')
    try:
        saves._publish(folder,{'best/other.c':'int other;\n',saves.MANIFEST:'{"revision": 9}\n'})
        assert tools.call(net,agent,'game','get_candidate',{'function':'f'})=='read ready'
        (folder/'best/f.c').write_text('an editor wrote this')
        assert 'Saved files changed during the recheck' in tools.call(net,agent,'game','get_candidate',{'function':'f'})
        saves._publish(folder,{'best/other.c':'int other2;\n'})  # (after an outside edit, no save can hide it)
        assert 'Saved files changed during the recheck' in tools.call(net,agent,'game','get_candidate',{'function':'f'})
    finally:worker.rechecking.pop('game',None)


def test_a_call_disturbed_by_a_recheck_waits_for_it_and_runs_again(project,monkeypatch):
    """Agents are not told about saves and rechecks: the call waits for the project to settle and then runs."""
    net,folder,_=project;worker=net.progress
    agent,_=net.local_agent('reader')
    worker.rechecking['game']=_Recheck(folder,worker._signature(folder),ready=True)
    (folder/'src').mkdir(exist_ok=True);(folder/'src/f.c').write_text('changed during recheck')
    monkeypatch.setattr(tools,'PROGRESS_RETRY_SECONDS',30)
    monkeypatch.setattr(tools,'_dispatch',lambda *a:'read ready')
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(tools.call,net,agent,'game','get_candidate',{'function':'f'})
        with pytest.raises(TimeoutError):future.result(timeout=.3)  # (waiting, not an error)
        with worker._project_lock('game'):worker.rechecking.pop('game',None)
        assert future.result(timeout=5)=='read ready'


def test_a_call_still_disturbed_after_the_wait_reports_it(project,monkeypatch):
    net,folder,_=project;worker=net.progress
    agent,_=net.local_agent('reader')
    monkeypatch.setattr(tools,'PROGRESS_RETRY_SECONDS',0.2)
    worker.rechecking['game']=_Recheck(folder,worker._signature(folder),ready=True)
    (folder/'src').mkdir(exist_ok=True);(folder/'src/f.c').write_text('changed during recheck')
    monkeypatch.setattr(tools,'_dispatch',lambda *a:pytest.fail('stale context reached the handler'))
    try:
        assert 'Saved files changed during the recheck' in tools.call(net,agent,'game','get_candidate',{'function':'f'})
    finally:worker.rechecking.pop('game',None)


def test_a_call_the_app_kept_from_running_is_noted_so_its_helper_is_not_paused(project,monkeypatch):
    net,folder,_=project;worker=net.progress
    agent,_=net.local_agent('reader')
    monkeypatch.setattr(tools,'PROGRESS_RETRY_SECONDS',0)
    worker.rechecking['game']=_Recheck(folder,worker._signature(folder),ready=True)
    (folder/'src').mkdir(exist_ok=True);(folder/'src/f.c').write_text('changed during recheck')
    try:
        assert tools.call(net,agent,'game','get_candidate',{'function':'f'}).startswith('error: Saved files changed')
    finally:worker.rechecking.pop('game',None)
    assert net._progress_disturbed[agent.id]==1


def test_every_way_into_the_tools_retries_a_call_the_app_disturbed(project,monkeypatch):
    """The MCP server (what CLI agents use) does not go through tools._invoke: both share _undisturbed."""
    from decompnet.app.progress import ProgressMoved
    net,folder,_=project
    agent,_=net.local_agent('reader')
    monkeypatch.setattr(tools,'PROGRESS_RETRY_SECONDS',5)
    tries=[]
    def run():
        tries.append(1)
        if len(tries)<3:raise ProgressMoved('Saved files changed during the recheck.')
        return 'done'
    assert tools._undisturbed(net,agent,'game',run)=='done' and len(tries)==3
    import inspect
    assert '_undisturbed' in inspect.getsource(tools.build_mcp)
