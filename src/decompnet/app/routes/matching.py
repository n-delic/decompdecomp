"""Project-scoped explicit CPU jobs; admission/status never wait for saved-project synchronization."""
from starlette.responses import JSONResponse

from ...network import NetworkError
from ..local_matching import Matching
from . import App


def register(app: App) -> None:
    matching = Matching(app.net,app.jobs)

    def route(path, methods, handler):
        @app.mcp.custom_route(path,methods=methods)
        async def request_handler(request):
            if bad := app.guard(request,write=request.method=='POST'):
                return bad
            data = await app.body(request) if request.method=='POST' else {}
            slug = data.get('slug') or request.query_params.get('slug','')
            try:
                return JSONResponse(await app.call(handler,slug,request,data))
            except (NetworkError,ValueError,OSError) as error:
                return app.fail(error)

    def deep(slug, request, data):
        if request.method=='GET':
            return matching.status(slug)['deep']
        action = data.get('action','start')
        if action=='start':
            result = matching.start(slug,'deep',data)
            return matching.control(slug)['deep'] | result
        if action=='stop':
            return matching.stop(slug,'deep')
        if action=='reset-metrics':
            return matching.reset(slug)['deep']
        raise NetworkError('Choose Start search, Stop or Reset metrics.')

    route('/api/local-matching',['GET'],lambda slug,r,data:matching.control(slug) if r.query_params.get('view')=='control' else matching.status(slug))
    route('/api/permuter/pass',['GET','POST'],deep)
    route('/api/cross-game/run',['POST'],lambda slug,r,data:matching.start(slug,'cross'))
    route('/api/cross-game',['GET'],lambda slug,r,data:{'results':matching.status(slug)['cross']})
