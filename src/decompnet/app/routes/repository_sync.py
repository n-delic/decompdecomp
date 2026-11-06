"""Repository sync and the explicit opt-in for automatic local match commits."""
import threading

from starlette.responses import JSONResponse

from ...network import NetworkError
from .. import repository_sync


def register(app):
    lock=threading.Lock()

    @app.mcp.custom_route('/api/repository-auto-commit', methods=['POST'])
    async def auto_commit(request):
        if bad := app.guard(request, write=True):
            return bad
        try:
            from .. import match_commits
            data = await app.body(request)
            result = await app.call(match_commits.configure, app.net, str(data.get('slug', '')),
                                    data.get('enabled'), str(data.get('folder', '')).strip())
            return JSONResponse(result)
        except (NetworkError, ValueError, KeyError, OSError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repository-sync', methods=['GET','POST'])
    async def sync(request):
        if bad:=app.guard(request,write=request.method=='POST'):
            return bad
        try:
            if request.method=='GET':
                return JSONResponse(await app.call(repository_sync.status,app.net,str(request.query_params.get('slug',''))))
            data=await app.body(request)
            slug=str(data.get('slug',''));folder=str(data.get('folder','')).strip()
            commit_push = data.get('commit_push', False)
            if not isinstance(commit_push, bool):raise NetworkError('Choose whether to commit and push with a boolean value.')
            app.net.project_row(slug)
            if not folder:raise NetworkError('Choose the root folder of your Git checkout.')
            # Reserve before starting a job; a second click must not launch another build.
            if not lock.acquire(blocking=False):raise NetworkError('A repository sync is already running.')
            def work(log):
                try:
                    if commit_push:return repository_sync.run(app.net,slug,folder,log,commit_push=True)
                    return repository_sync.run(app.net,slug,folder,log)
                finally:lock.release()
            try:
                job=app.jobs.start('repository-sync',work,cancel_message='Repository sync stopped. Any published files or local commit remain in the checkout; check Git status before retrying.')
            except BaseException:
                lock.release();raise
            return JSONResponse({'job':job})
        except (NetworkError,ValueError,KeyError,OSError) as error:
            return app.fail(error)
