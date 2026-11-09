"""Agent controls stay independent of transcripts, progress scans and idle projects."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from decompnet.app import database_cleanup
from tests.test_claims import _game
from tests.test_database_cleanup import data  # noqa: F401
from tests.test_server_lock_contention import app  # noqa: F401


@pytest.mark.parametrize('kind', ['rom', 'fork'])
def test_spend_does_not_wait_for_progress_sync(tmp_path, monkeypatch, kind):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?', (json.dumps({'kind':kind}),))
    monkeypatch.setattr(net.progress, 'sync', lambda *a, **kw: pytest.fail('Usage must not scan progress'))
    net.add_spend('game', .25, 100)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: net.add_spend('game', .5, 200), range(8)))
    with net.progress.view():
        assert net.spend('game') == {'usd': 4.25, 'tokens': 1700}


def test_stop_releases_only_its_project(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('worker')
    net.claim_row(agent, 'game', 'f')
    monkeypatch.setattr(net, 'project_stores', lambda: pytest.fail('Stop must not visit idle projects'))
    assert net.release_stopped([agent.id], 'user stopped', project='game') == ['f']
    assert not net.project_store('game').one('SELECT 1 FROM claims WHERE agent_id=?', agent.id)


def test_active_cleanup_does_not_repeat_eligibility_scan(data, monkeypatch):  # noqa: F811
    net, _, _ = data
    database_cleanup.save(net, {'removed':True, 'before':'2000-01-01', 'automatic':True})
    jobs = SimpleNamespace(_lock=threading.RLock(), jobs={'one':{'kind':'Database transcript cleanup', 'state':'running'}})
    retention = database_cleanup.Retention(net, jobs)
    monkeypatch.setattr(database_cleanup, 'eligible', lambda *a: pytest.fail('Active cleanup owns the scan'))
    retention.tick(0)
    retention.tick(301)


@pytest.mark.parametrize('payload,method,args', [
    ({'id':'worker'}, 'stop', ('worker',)),
    ({'slug':'game'}, 'stop_project', ('game',)),
    ({'id':'worker','remove':True}, 'remove', ('worker',)),
    ({'slug':'game','remove_paused':True}, 'remove_paused', ('game',)),
])
def test_stop_routes_use_reserved_workers(app, monkeypatch, payload, method, args):  # noqa: F811
    import inspect
    client, _, manager = app
    route = next(r for r in client.app.routes if getattr(r, 'path', '') == '/api/agents/stop')
    owner = inspect.getclosurevars(route.endpoint).nonlocals['app']
    calls = []
    async def control(fn, *values):
        calls.append(values)
        return fn(*values)
    monkeypatch.setattr(owner, 'control_call', control)
    monkeypatch.setattr(manager, method, lambda *values: 0)
    response = client.post('/api/agents/stop', headers={'X-Decomp':'1'}, json=payload)
    assert response.status_code == 200
    assert args in calls


def test_start_route_uses_reserved_workers(app, monkeypatch):  # noqa: F811
    import inspect
    client, _, _ = app
    route = next(r for r in client.app.routes if getattr(r, 'path', '') == '/api/agents/start')
    owner = inspect.getclosurevars(route.endpoint).nonlocals['app']
    calls = []
    async def control(fn, *values):
        calls.append(fn.__name__)
        return fn(*values)
    monkeypatch.setattr(owner, 'control_call', control)
    response = client.post('/api/agents/start', headers={'X-Decomp':'1'}, json={'kind':'missing', 'slug':'missing'})
    assert response.status_code == 400
    assert 'create_agents' in calls


def test_stop_answers_while_default_workers_are_saturated(app, monkeypatch):  # noqa: F811
    import inspect
    import json

    import anyio
    from starlette.requests import Request

    from decompnet.app import window

    client, _, manager = app
    route = next(r for r in client.app.routes if getattr(r, 'path', '') == '/api/agents/stop')
    stopped = []
    monkeypatch.setattr(manager, 'stop', lambda sid: stopped.append(sid))
    async def receive():
        return {'type':'http.request', 'body':json.dumps({'id':'worker'}).encode(), 'more_body':False}
    owner = inspect.getclosurevars(route.endpoint).nonlocals['app']
    headers = {'host':f'127.0.0.1:{owner.port}', 'x-decomp':'1',
               'user-agent':window.TOKEN_AGENT + owner.secret}
    request = Request({'type':'http', 'method':'POST', 'scheme':'http', 'path':'/api/agents/stop',
                       'query_string':b'', 'headers':[(k.encode(),v.encode()) for k,v in headers.items()]}, receive)
    async def run():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 1
        entered = threading.Event()
        release = threading.Event()
        def blocked():
            entered.set()
            release.wait(5)
        async with anyio.create_task_group() as group:
            group.start_soon(anyio.to_thread.run_sync, blocked)
            try:
                with anyio.fail_after(2):
                    while not entered.is_set():await anyio.sleep(.01)
                    response = await route.endpoint(request)
                assert response.status_code == 200
                assert stopped == ['worker']
            finally:
                release.set()
                limiter.total_tokens = previous
    anyio.run(run)
