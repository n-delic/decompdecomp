import asyncio

from decompnet import ui_diagnostics as diagnostics


def test_diagnostics_bounded_and_fast_requests_ignored():
    with diagnostics._lock:diagnostics._samples.clear()
    diagnostics.record('request',.1,'/api/project')
    assert diagnostics.snapshot()==[]
    for _ in range(120):diagnostics.record('request',.6,'/api/project')
    assert len(diagnostics.snapshot())==100
    assert diagnostics.snapshot()[-1]['seconds']==.6


def test_diagnostics_records_registered_endpoint_not_secret_url(monkeypatch):
    samples=[]
    ticks=iter([0.,1.])
    monkeypatch.setattr(diagnostics,'_clock',lambda:next(ticks))
    monkeypatch.setattr(diagnostics,'record',lambda *args:samples.append(args))
    async def endpoint():pass
    async def app(scope,receive,send):scope['endpoint']=endpoint
    scope={'type':'http','path':'/api/private/secret','query_string':b'invite=secret'}
    asyncio.run(diagnostics.Diagnostics(app)(scope,None,None))
    assert samples[0][2]=='endpoint'
    assert 'secret' not in str(samples)


def test_diagnostics_monitor_cancelled_when_lifespan_finishes(monkeypatch):
    cancelled=[]
    async def watch():
        try:await asyncio.Event().wait()
        finally:cancelled.append(True)
    async def app(*args):await asyncio.sleep(0)
    monkeypatch.setattr(diagnostics,'watch_loop',watch)
    asyncio.run(diagnostics.Diagnostics(app)({'type':'lifespan'},None,None))
    assert cancelled==[True]


def test_diagnostics_uses_starlette_route_template(monkeypatch):
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Route
    from starlette.testclient import TestClient
    samples=[]
    ticks=iter([0.,1.])
    monkeypatch.setattr(diagnostics,'_clock',lambda:next(ticks))
    monkeypatch.setattr(diagnostics,'record',lambda *args:samples.append(args))
    async def endpoint(request):return Response('ok')
    app=Starlette(routes=[Route('/api/item/{item}',endpoint)])
    with TestClient(diagnostics.Diagnostics(app)) as client:
        assert client.get('/api/item/private-token?invite=secret').status_code==200
    assert samples[0][2]=='/api/item/{item}'
    assert 'private-token' not in str(samples) and 'secret' not in str(samples)
