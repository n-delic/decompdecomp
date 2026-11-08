"""Held backend locks must delay only their request, never the event loop or health."""
import ast
import hashlib
import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio
import pytest

from decompnet.app import routes, support
from decompnet.app.web_login import WebLogin
from tests.test_app_server import app as app_fixture


@pytest.fixture
def app(tmp_path_factory):
    client,net,manager=app_fixture.__wrapped__(tmp_path_factory)
    try:yield client,net,manager
    finally:
        net.startup.close()
        net.autosave.close()
        net.deep_sweeps.shutdown()
        net.permuter.close()
        manager.request_shutdown()


def check_responsive(client,request,entered,release):
    # One TestClient portal is essential: separate portals would hide ASGI stalls.
    with client, ThreadPoolExecutor(2) as pool:
        blocked=pool.submit(request)
        try:
            assert entered.wait(3)
            health=pool.submit(client.get,'/health')
            assert health.result(timeout=1).status_code==200
            assert not blocked.done()
        finally:release.set()
        return blocked.result(timeout=5)


def test_job_start_contention_does_not_block_health(app,monkeypatch):
    client,net,manager=app
    entered=threading.Event();release=threading.Event()
    def start(*a,**kw):
        entered.set();assert release.wait(5)
        return 'synthetic-job'
    monkeypatch.setattr(support.Jobs,'start',start)
    response=check_responsive(client,lambda:client.post('/api/match-library',headers={'X-Decomp':'1'},json={}),entered,release)
    assert response.json()=={'job':'synthetic-job'}


def test_repository_job_lock_contention_does_not_block_health(app,monkeypatch):
    client,net,manager=app
    entered=threading.Event();release=threading.Event()
    class HeldLock:
        def __enter__(self):entered.set();assert release.wait(5)
        def __exit__(self,*a):pass
    # GET repository-setup waits for its job reservation before reading Git.
    jobs=[]
    # The module fixture's Jobs is captured by the registered route.
    for route in client.app.routes:
        endpoint=getattr(route,'endpoint',None)
        if endpoint and getattr(route,'path',None)=='/api/repository-setup':
            owner=inspect.getclosurevars(endpoint).nonlocals.get('app')
            assert isinstance(owner,routes.App)
            jobs.append(owner.jobs)
    assert jobs
    monkeypatch.setattr(jobs[0],'_lock',HeldLock())
    response=check_responsive(client,lambda:client.get('/api/repository-setup?slug=missing'),entered,release)
    assert response.status_code==400


def test_progress_activity_lock_does_not_block_health(app,monkeypatch):
    client,net,manager=app
    entered=threading.Event();release=threading.Event()
    def status(slug):entered.set();assert release.wait(5);return None
    monkeypatch.setattr(net.progress,'status',status)
    response=check_responsive(client,lambda:client.get('/api/project-loading?slug=game'),entered,release)
    assert response.json()=={'verification':None}


def test_control_requests_have_capacity_when_normal_workers_are_full():
    from decompnet.app.routes import App
    async def run():
        normal=anyio.CapacityLimiter(1);control=anyio.CapacityLimiter(1)
        entered=threading.Event();release=threading.Event();seen=[]
        async def call(fn,*args):return await anyio.to_thread.run_sync(fn,*args,limiter=normal)
        async def controls(fn,*args):return await anyio.to_thread.run_sync(fn,*args,limiter=control)
        owner=object.__new__(App);owner.call=call;owner.control_call=controls
        def blocked():entered.set();release.wait(5)
        async with anyio.create_task_group() as group:
            group.start_soon(call,blocked)
            try:
                while not entered.is_set():await anyio.sleep(.01)
                with anyio.fail_after(1):await owner.control(lambda:seen.append('cancel'))
            finally:release.set()
        assert seen==['cancel']
    anyio.run(run)


def test_cookie_authorization_does_not_wait_for_login_writer():
    from starlette.requests import Request
    login=WebLogin(6492);token='test-token'
    login.sessions[hashlib.sha256(token.encode()).hexdigest()]=time.monotonic()+60
    request=Request({'type':'http','headers':[(b'cookie',f'{login.cookie}={token}'.encode())]})
    with ThreadPoolExecutor(1) as pool:
        with login.lock:
            result=pool.submit(login.authorized,request)
            assert result.result(timeout=1)
    login.sessions[hashlib.sha256(token.encode()).hexdigest()]=0
    assert not login.authorized(request)


def test_async_routes_do_not_acquire_thread_locks_or_start_jobs_directly():
    root=Path(__file__).resolve().parents[1]/'src/decompnet/app'
    violations=[]
    for path in [*sorted((root/'routes').glob('*.py')),root/'web_github.py',root/'web_login.py']:
        tree=ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function,ast.AsyncFunctionDef):continue
            def visit(node,function=function,path=path):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.Lambda)) and node is not function:return
                if isinstance(node,ast.With):
                    for item in node.items:
                        name=ast.unparse(item.context_expr)
                        if 'lock' in name or '.tx(' in name:violations.append((path.name,node.lineno,name))
                if isinstance(node,ast.Call):
                    name=ast.unparse(node.func)
                    if name.endswith('.acquire') and not any(k.arg=='blocking' and isinstance(k.value,ast.Constant) and k.value.value is False for k in node.keywords) and not (node.args and isinstance(node.args[0],ast.Constant) and node.args[0].value is False):
                        violations.append((path.name,node.lineno,name))
                    if name in {'jobs.start','app.jobs.start','jobs.active','jobs.cancel','jobs.check_start',
                                'app.net.project_row','app.settings.save','settings.save','net.progress.status',
                                'bridge.allowed','bridge.auth','bridge.pair','bridge.revoke','bridge.consume',
                                'page_log.warning','jobs.logger.warning'}:
                        violations.append((path.name,node.lineno,name))
                for child in ast.iter_child_nodes(node):visit(child)
            visit(function)
    assert not violations


def test_repository_validation_does_not_block_health(app,monkeypatch,tmp_path):
    client,net,manager=app
    from decompnet.network import NetworkError
    entered=threading.Event();release=threading.Event()
    def project(slug):
        entered.set();assert release.wait(5)
        raise NetworkError('Synthetic unavailable project')
    monkeypatch.setattr(net,'project_row',project)
    response=check_responsive(client,lambda:client.post('/api/repository-sync',headers={'X-Decomp':'1'},
        json={'slug':'game','folder':str(tmp_path)}),entered,release)
    assert response.status_code==400 and 'Synthetic unavailable' in response.text


def test_snapshot_recursive_wait_fails_instead_of_deadlocking():
    from decompnet.app.view_snapshots import Snapshots
    cache=Snapshots()
    with pytest.raises(RuntimeError,match='own builder'):
        cache.get('recursive',lambda:cache.get('recursive',lambda:None))
    assert cache.get('recursive',lambda:'recovered')=='recovered'


def test_snapshot_wait_is_bounded_without_cancelling_builder(monkeypatch):
    from decompnet.app import view_snapshots
    cache=view_snapshots.Snapshots();entered=threading.Event();release=threading.Event()
    monkeypatch.setattr(view_snapshots,'SNAPSHOT_WAIT_SECONDS',.03)
    def build():entered.set();release.wait(3);return 'finished'
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(cache.get,'slow',build)
        try:
            assert entered.wait(1)
            with pytest.raises(TimeoutError,match='still being prepared'):cache.get('slow',lambda:pytest.fail('Duplicate builder'))
        finally:release.set()
        assert future.result(timeout=1)=='finished'
    assert cache.get('slow',lambda:pytest.fail('Lost completed snapshot'))=='finished'


def test_watchdog_captures_stall_without_frame_locals(tmp_path):
    from decompnet.hang_diagnostics import Watchdog
    report=tmp_path/'event-loop-stacks.log'
    private_token='do-not-copy-private-local-values'
    watchdog=Watchdog(report,threshold=.01,interval=.01)
    watchdog.last_beat=time.monotonic()-1
    watchdog.start()
    try:
        deadline=time.monotonic()+2
        while not report.exists() and time.monotonic()<deadline:time.sleep(.01)
        text=report.read_text()
        assert 'test_watchdog_captures_stall_without_frame_locals' in text
        assert private_token not in text and 'Event loop unresponsive' in text
        assert len(text)<512_001
    finally:watchdog.close()
    assert not watchdog.thread.is_alive()


def test_database_writer_contention_keeps_health_live_and_commits_once(app,monkeypatch):
    client,net,manager=app
    from decompnet.app import prompt_settings
    held=threading.Event();entered=threading.Event();release=threading.Event()
    def hold_writer():
        with net.store.tx():
            held.set();assert release.wait(5)
    holder=threading.Thread(target=hold_writer,daemon=True);holder.start()
    assert held.wait(2)
    original=prompt_settings.save_global
    def save(*args):entered.set();return original(*args)
    monkeypatch.setattr(prompt_settings,'save_global',save)
    try:
        response=check_responsive(client,lambda:client.post('/api/agent-prompts',headers={'X-Decomp':'1'},
            json={'overrides':{'instructions':'Saved after the writer finishes'}}),entered,release)
        assert response.status_code==200,response.text
    finally:
        release.set();holder.join(2)
    assert prompt_settings.global_overrides(net)['instructions']=='Saved after the writer finishes'
    assert net.store.one('PRAGMA integrity_check')[0]=='ok'


def test_diagnostic_logging_contention_does_not_block_event_loop(monkeypatch):
    import asyncio

    from decompnet import ui_diagnostics
    entered=threading.Event();release=threading.Event()
    def record(*args):entered.set();assert release.wait(3)
    monkeypatch.setattr(ui_diagnostics,'record',record)
    ticks=iter([0.,1.,2.,3.])
    monkeypatch.setattr(ui_diagnostics,'_clock',lambda:next(ticks))
    async def endpoint(*args):pass
    app=ui_diagnostics.Diagnostics(endpoint)
    async def run():
        blocked=asyncio.create_task(app({'type':'http','path':'/api/slow'},None,None))
        try:
            while not entered.is_set():await asyncio.sleep(.01)
            await asyncio.wait_for(app({'type':'http','path':'/health'},None,None),1)
            assert not blocked.done()
        finally:release.set()
        await blocked
    asyncio.run(run())
