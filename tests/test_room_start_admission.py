"""A shutdown racing Share reports the existing admission error instead of HTTP 500."""
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from decompnet.app.routes import rooms
from decompnet.app.support import Jobs


@pytest.mark.parametrize('state,text', [('closing','shutting down'),
                                       ('source_update_pending','source update is queued')])
def test_share_admission_failure_is_actionable_and_does_not_create_a_room(tmp_path,state,text):
    routes=[]
    class MCP:
        def custom_route(self,path,methods):
            def register(fn):
                routes.append(Route(path,fn,methods=methods))
                return fn
            return register
    jobs=Jobs(tmp_path/'test.log')
    setattr(jobs,state,True)
    net=SimpleNamespace(links={},rooms={},fork_slug=lambda slug:None,store=SimpleNamespace(all=lambda *a:[]))
    async def call(fn,*args):return fn(*args)
    async def body(request):return await request.json()
    app=SimpleNamespace(net=net,mcp=MCP(),home=tmp_path,manager=SimpleNamespace(),jobs=jobs,settings={},
                        guard=lambda *a,**k:None,body=body,call=call,control=call,read=call,
                        fail=lambda error:JSONResponse({'error':str(error)},status_code=400))
    rooms.register(app)
    with TestClient(Starlette(routes=routes)) as client:
        response=client.post('/api/collab/start',json={'slug':'synthetic','mode':'address'})
    assert response.status_code==400
    assert text in response.json()['error']
    assert not jobs.jobs and not net.rooms
