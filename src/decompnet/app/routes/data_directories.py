"""Directory history is local to this machine; switching requires the desktop window."""
from starlette.responses import JSONResponse

from ...network import NetworkError
from .. import data_directories, window


def register(app):
    controller = data_directories.Controller(app)

    @app.mcp.custom_route('/api/data-directories', methods=['GET','POST'])
    async def directories(request):
        if bad := app.guard(request,write=request.method=='POST'):return bad
        try:
            if request.method=='GET':result = await app.call(controller.status)
            else:
                data = await app.body(request)
                action = data.get('action')
                if action not in ('remember','switch'):raise NetworkError('Choose Remember directory or Switch directory.')
                if action=='switch' and window.token_of(request.headers.get('user-agent','')) != app.secret:
                    raise NetworkError('Switch project directories in the desktop app.')
                result = await app.call(controller.choose,str(data.get('path') or ''),action=='switch')
            return JSONResponse({**result,'switchable':not app.headless and window.token_of(request.headers.get('user-agent','')) == app.secret})
        except (NetworkError,OSError,ValueError) as error:return app.fail(error)
