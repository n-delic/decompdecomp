"""Slow helpers must leave the shared HTTP event loop free to serve health requests."""
import asyncio
import threading

import anyio
import httpx
import pytest

from decompnet.app import tool_policy, updates
from decompnet.app.routes import pages
from decompnet.app.support import Settings
from decompnet.network import NetworkError
from tests.test_app_server import WINDOW, app  # noqa: F401


@pytest.mark.parametrize('case', ['room-matches','room-stop','room-manage','fork-check','repair-replay',
                                  'tool-metadata','agent-selection','settings','asset','update-status'])
def test_slow_route_helpers_do_not_block_health(app, monkeypatch, case):  # noqa: F811
    client, net, manager, *_ = app
    entered, release = threading.Event(), threading.Event()
    reads = case in {'room-matches','fork-check','repair-replay','tool-metadata'}

    def slow(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            pytest.fail('Blocking helper executed on HTTP event loop')
        assert net.progress.viewing == reads
        entered.set()
        assert release.wait(5), 'Health request could not release blocked worker'
        if case == 'asset':return b'/* test asset */'
        if case == 'agent-selection':return {}
        if case == 'settings':return None
        if case == 'update-status':return {'version':'test'}
        raise NetworkError('Synthetic slow helper finished')

    method, path, data = 'POST', '', {}
    if case == 'room-matches':
        monkeypatch.setattr(net,'project_row',slow)
        method,path='GET','/api/collab/matches?slug=slow'
    elif case == 'room-stop':
        # Stopping also reads reconciliation state after close. Supply its
        # metadata so the fake still tests worker dispatch through the full route.
        monkeypatch.setitem(net.rooms,'slow',type('Room',(),{
            'close':staticmethod(slow), 'store':net.store, 'proj':{'id':0}})())
        path,data='/api/collab/stop',{'slug':'slow'}
        # This action has no route error conversion: let the fake close complete normally.
        original=slow
        def close():
            try:original()
            except NetworkError:pass
        monkeypatch.setattr(net.rooms['slow'],'close',close)
    elif case == 'room-manage':
        monkeypatch.setitem(net.rooms,'slow',type('Room',(),{'remove':staticmethod(slow)})())
        path,data='/api/collab/manage',{'slug':'slow','action':'remove','name':'member'}
    elif case == 'fork-check':
        monkeypatch.setattr(net,'fork_slug',slow)
        path,data='/api/fork/check',{'slug':'slow'}
    elif case == 'repair-replay':
        monkeypatch.setattr(net.repair_feedback,'listing',slow)
        path,data='/api/repair-examples/replay',{'slug':'slow'}
    elif case == 'tool-metadata':
        monkeypatch.setattr(tool_policy,'ensure',lambda *a:None)
        monkeypatch.setattr(tool_policy,'disabled',slow)
        method,path='GET','/api/agent-tools'
    elif case == 'agent-selection':
        monkeypatch.setattr(manager,'set_selection',slow)
        path,data='/api/agents/selection',{'id':'slow'}
    elif case == 'settings':
        monkeypatch.setattr(Settings,'save',slow)
        path='/api/settings'
    elif case == 'asset':
        monkeypatch.setattr(pages,'static_file',slow)
        method,path='GET','/assets/test.css'
    else:
        monkeypatch.setattr(updates,'installed',slow)
        monkeypatch.setenv('DECOMP_UPDATES','off')
        method,path='GET','/api/update'

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                    base_url=str(client.base_url),headers=WINDOW) as http:
            async def request():
                result=await http.request(method,path,json=data,headers={'X-Decomp':'1'})
                assert result.status_code in (200,400)
            async with anyio.create_task_group() as group:
                group.start_soon(request)
                try:
                    assert await anyio.to_thread.run_sync(lambda:entered.wait(2)), 'Slow helper not reached'
                    with anyio.fail_after(1):
                        assert (await http.get('/health')).json()['ok']
                finally:
                    release.set()
    try:
        anyio.run(run)
    finally:
        net.rooms.pop('slow',None)


@pytest.mark.parametrize('route', ['import','join','fork'])
def test_upload_disk_write_does_not_block_health(app, monkeypatch, tmp_path, route):  # noqa: F811
    import builtins

    from decompnet.app.support import Jobs
    client, net, *_ = app
    entered, release = threading.Event(), threading.Event()
    original_open = builtins.open
    written = []

    class SlowFile:
        def __init__(self, file):self.file=file
        def __getattr__(self, name):return getattr(self.file,name)
        def write(self, data):
            try:asyncio.get_running_loop()
            except RuntimeError:pass
            else:pytest.fail('Upload wrote to disk on the HTTP event loop')
            entered.set()
            assert release.wait(5)
            written.append(data)
            return self.file.write(data)

    def open_file(path, mode='r', *args, **kwargs):
        file=original_open(path,mode,*args,**kwargs)
        return SlowFile(file) if mode=='wb' else file

    monkeypatch.setattr(builtins,'open',open_file)
    monkeypatch.setattr(Jobs,'start',lambda *a,**kw:'synthetic-upload-job')
    if route=='import':path='/api/import?confirmed=1&name=test.bin'
    elif route=='join':path='/api/collab/join?confirmed=1&filename=test.bin'
    else:
        with net.store.tx() as db:
            db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                       ('upload-fork','Upload test',str(tmp_path),'{"kind":"fork"}'))
        path='/api/fork/game?slug=upload-fork&confirmed=1&name=test.bin'

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                    base_url=str(client.base_url),headers=WINDOW) as http:
            async def upload():
                response=await http.post(path,content=b'ab',headers={'X-Decomp':'1'})
                assert response.status_code==200, response.text
                assert response.json()['job']=='synthetic-upload-job'
            async with anyio.create_task_group() as group:
                group.start_soon(upload)
                try:
                    assert await anyio.to_thread.run_sync(lambda:entered.wait(2))
                    with anyio.fail_after(1):assert (await http.get('/health')).json()['ok']
                finally:release.set()
    anyio.run(run)
    assert b''.join(written)==b'ab'
