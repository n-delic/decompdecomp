"""Repository sync and the explicit opt-in for automatic local match commits."""
import threading

from starlette.responses import JSONResponse

from ...network import NetworkError
from .. import repository_sync


def register(app):
    lock=threading.Lock()
    pushes = {}

    def active_push(slug):
        with app.jobs._lock:
            job = app.jobs.jobs.get(pushes.get(slug), {})
            return {key:job[key] for key in ('id', 'state', 'progress', 'scope') if key in job} if job.get('state') == 'running' else None

    @app.mcp.custom_route('/api/repository-setup', methods=['GET', 'POST'])
    async def setup(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        try:
            from .. import repository_setup
            if request.method == 'GET':
                return JSONResponse(await app.call(repository_setup.status, app.net,
                    str(request.query_params.get('slug', ''))))
            data = await app.body(request)
            slug = str(data.get('slug', ''))
            job = app.jobs.start('repository-setup', lambda log: repository_setup.run(
                app.net, slug, str(data.get('action', '')), str(data.get('origin', '')).strip(), log),
                cancel_message='Repository setup stopped. Created Git metadata or a completed local commit remain; review the folder before retrying.')
            return JSONResponse({'job': job})
        except (NetworkError, ValueError, KeyError, OSError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repository-auto-push', methods=['POST'])
    async def auto_push(request):
        if bad := app.guard(request, write=True):return bad
        try:
            from .. import auto_push
            data = await app.body(request)
            return JSONResponse(await app.call(auto_push.configure, app.net, str(data.get('slug', '')),
                data.get('enabled'), data.get('threshold', 10), str(data.get('folder', '')).strip()))
        except (NetworkError, ValueError, KeyError, OSError) as error:
            return app.fail(error)

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

    @app.mcp.custom_route('/api/repository-push', methods=['GET', 'POST'])
    async def push(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        try:
            from .. import repository_push
            if request.method == 'GET':
                active = active_push(str(request.query_params.get('slug', '')))
                if active:
                    return JSONResponse({'job':active['id'], 'progress':active.get('progress', {}), **active['scope']})
                return JSONResponse(await app.call(repository_push.status, app.net,
                    str(request.query_params.get('slug', '')), str(request.query_params.get('folder', '')).strip()))
            data = await app.body(request)
            slug = str(data.get('slug', ''))
            folder = str(data.get('folder', '')).strip()
            # Reserve by project under the job lock; repeated clicks/windows attach
            # to the same queued push without blocking a sync's API reservation.
            with app.jobs._lock:
                active = active_push(slug)
                if active:return JSONResponse({'job':active['id']})
                job = app.jobs.start('repository-push', lambda log: repository_push.run(app.net, slug, folder, log),
                    cancel_message='Push stopped. Waiting requests are removed from the queue; check origin if pushing had already started.')
                app.jobs.jobs[job]['scope'] = {'slug':slug, 'folder':folder}
                pushes[slug] = job
            return JSONResponse({'job':job})
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
