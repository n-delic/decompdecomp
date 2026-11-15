"""Repository sync and the explicit opt-in for automatic local match commits."""
import threading

from starlette.responses import JSONResponse

from ...network import NetworkError
from .. import repository_sync


def register(app):
    lock=threading.Lock()
    pushes = {}
    setups = {}
    forks = {}

    def active_setup(slug):
        job = app.jobs.jobs.get(setups.get(slug), {})
        return job.get("id") if job.get("state") == "running" else None

    def active_push(slug):
        with app.jobs._lock:
            job = app.jobs.jobs.get(pushes.get(slug), {})
            return {key:job[key] for key in ('id', 'state', 'progress', 'scope') if key in job} if job.get('state') == 'running' else None

    @app.mcp.custom_route('/api/repository-setup', methods=['GET', 'POST'])
    async def setup(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        try:
            repository_setup, = await app.imports('decompnet.app', ('repository_setup',))
            if request.method == 'GET':
                slug = str(request.query_params.get('slug', ''))
                def pending_setup():
                    with app.jobs._lock:
                        if job := active_setup(slug):return JSONResponse({'job':job})
                if (reserved := await app.control(pending_setup)) is not None:
                    return reserved
                return JSONResponse(await (app.fast_call or app.ui_call or app.call)(repository_setup.status, app.net, slug))
            data = await app.body(request)
            slug = str(data.get('slug', ''))
            def start_setup():
                with app.jobs._lock:
                    if job := active_setup(slug):return JSONResponse({'job':job})
                    job = app.jobs.start('repository-setup', lambda log: repository_setup.run(
                        app.net, slug, str(data.get('action', '')), str(data.get('origin', '')).strip(), log,
                        github_name=str(data.get('github_name', '')).strip(), visibility=str(data.get('visibility', 'private'))),
                        cancel_message='Repository setup stopped. A created GitHub repository, Git metadata or completed commit may remain; check the log before retrying.')
                    app.jobs.jobs[job]['scope'] = {'slug':slug}
                    setups[slug] = job
                return JSONResponse({'job': job})
            return await app.control(start_setup)
        except (NetworkError, ValueError, KeyError, OSError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repository-github-owners', methods=['GET'])
    async def github_owners(request):
        if bad := app.guard(request):return bad
        try:
            repository_github, repository_setup = await app.imports('decompnet.app', ('repository_github', 'repository_setup'))
            slug = str(request.query_params.get('slug', ''))
            path, _ = await app.call(repository_setup.folder, app.net, slug)
            return JSONResponse(await app.call(repository_github.owners, path))
        except (NetworkError, ValueError, KeyError, OSError) as error:
            return app.fail(error)

    def active_fork(slug):
        job = app.jobs.jobs.get(forks.get(slug), {})
        return job if job.get('state') == 'running' else None

    @app.mcp.custom_route('/api/repository-fork', methods=['GET', 'POST'])
    async def fork(request):
        if bad := app.guard(request, write=request.method == 'POST'):return bad
        try:
            repository_fork, = await app.imports('decompnet.app', ('repository_fork',))
            data = await app.body(request) if request.method == 'POST' else request.query_params
            slug, folder = str(data.get('slug', '')), str(data.get('folder', '')).strip()
            def reserve_fork():
                with app.jobs._lock:
                    active = active_fork(slug)
                    if active:
                        if active['scope']['folder'] != folder:raise NetworkError('Fork setup is already running for another checkout of this project.')
                        return JSONResponse({'job':active['id']})
                    if request.method == 'POST':
                        job = app.jobs.start('repository-fork', lambda log:repository_fork.run(app.net,slug,folder,
                            str(data.get('owner','')).strip(),str(data.get('origin_revision','')),log), project=slug,
                            cancel_message='Fork setup stopped. A GitHub fork or completed remote change may remain. Reopen setup to check; no commits were pushed.')
                        app.jobs.jobs[job]['scope'] = {'slug':slug,'folder':folder}
                        forks[slug] = job
                        return JSONResponse({'job':job})
            if (reserved := await app.control(reserve_fork)) is not None:
                return reserved
            return JSONResponse(await app.call(repository_fork.info,app.net,slug,folder))
        except (NetworkError,ValueError,KeyError,OSError) as error:return app.fail(error)

    @app.mcp.custom_route('/api/repository-score-commits', methods=['POST'])
    async def score_commits(request):
        if bad := app.guard(request, write=True):return bad
        try:
            score_commits, = await app.imports('decompnet.app', ('score_commits',))
            data = await app.body(request)
            return JSONResponse(await app.call(score_commits.configure, app.net, str(data.get('slug', '')),
                data.get('enabled'), data.get('threshold', 10), str(data.get('folder', '')).strip()))
        except (NetworkError, ValueError, KeyError, OSError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repository-auto-push', methods=['POST'])
    async def auto_push(request):
        if bad := app.guard(request, write=True):return bad
        try:
            auto_push, = await app.imports('decompnet.app', ('auto_push',))
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
            match_commits, = await app.imports('decompnet.app', ('match_commits',))
            data = await app.body(request)
            result = await app.call(match_commits.configure, app.net, str(data.get('slug', '')),
                                    data.get('enabled'), str(data.get('folder', '')).strip(), str(data.get('kind', 'match')))
            return JSONResponse(result)
        except (NetworkError, ValueError, KeyError, OSError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repository-push', methods=['GET', 'POST'])
    async def push(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        try:
            repository_push, = await app.imports('decompnet.app', ('repository_push',))
            if request.method == 'GET':
                def pending_fork():
                    with app.jobs._lock:
                        setup = active_fork(str(request.query_params.get('slug', '')))
                        if setup and setup['scope']['folder'] == str(request.query_params.get('folder', '')).strip():
                            return JSONResponse({'fork_job':setup['id']})
                if (reserved := await app.control(pending_fork)) is not None:
                    return reserved
                active = await app.control(lambda: active_push(str(request.query_params.get('slug', ''))))
                if active:
                    return JSONResponse({'job':active['id'], 'progress':active.get('progress', {}), **active['scope']})
                return JSONResponse(await app.call(repository_push.status, app.net,
                    str(request.query_params.get('slug', '')), str(request.query_params.get('folder', '')).strip()))
            data = await app.body(request)
            slug = str(data.get('slug', ''))
            folder = str(data.get('folder', '')).strip()
            # Reserve by project under the job lock; repeated clicks/windows attach
            # to the same queued push without blocking a sync's API reservation.
            def start_push():
                with app.jobs._lock:
                    active = active_push(slug)
                    if active:return JSONResponse({'job':active['id']})
                    job = app.jobs.start('repository-push', lambda log: repository_push.run(app.net, slug, folder, log),
                        cancel_message='Push stopped. Waiting requests are removed from the queue; check origin if pushing had already started.')
                    app.jobs.jobs[job]['scope'] = {'slug':slug, 'folder':folder}
                    pushes[slug] = job
                return JSONResponse({'job':job})
            return await app.control(start_push)
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
            await app.read(app.net.project_row, slug)
            if not folder:raise NetworkError('Choose the root folder of your Git checkout.')
            # Reserve before starting a job; a second click must not launch another build.
            if not lock.acquire(blocking=False):raise NetworkError('A repository sync is already running.')
            def work(log):
                try:
                    if commit_push:return repository_sync.run(app.net,slug,folder,log,commit_push=True)
                    return repository_sync.run(app.net,slug,folder,log)
                finally:lock.release()
            try:
                job=await app.control(lambda: app.jobs.start('repository-sync',work,cancel_message='Repository sync stopped. Any published files or local commit remain in the checkout; check Git status before retrying.'))
            except BaseException:
                lock.release();raise
            return JSONResponse({'job':job})
        except (NetworkError,ValueError,KeyError,OSError) as error:
            return app.fail(error)
