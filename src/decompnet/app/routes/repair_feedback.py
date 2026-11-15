"""Review and locally export matching repairs, independently of agent activity/transcript routes."""
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

from ...network import NetworkError
from .. import repair_investigations


def register(app):
    feedback = app.net.repair_feedback

    @app.mcp.custom_route('/api/repair-examples/database', methods=['GET'])
    async def export_database(request):
        if bad := app.guard(request):return bad
        import os
        import tempfile
        from pathlib import Path
        def copy_database():
            descriptor,path=tempfile.mkstemp(prefix='repair-evidence-',suffix='.sqlite3')
            os.close(descriptor)
            try:
                feedback.export_database(path)
            except BaseException:
                Path(path).unlink(missing_ok=True)
                raise
            return path
        try:
            path=await app.call(copy_database)
            return FileResponse(path,media_type='application/vnd.sqlite3',filename='repair-evidence.sqlite3',headers={'Cache-Control':'no-store'},
                                background=BackgroundTask(Path(path).unlink,missing_ok=True))
        except (ValueError,OSError) as error:return app.fail(error)

    @app.mcp.custom_route('/api/repair-examples/analysis', methods=['GET','POST'])
    async def automatic_analysis(request):
        if bad := app.guard(request,write=request.method=='POST'):return bad
        repair_outcomes, = await app.imports('decompnet.app', ('repair_outcomes',))
        try:
            if request.method=='POST':
                data=await app.body(request)
                await app.call(repair_outcomes.configure,feedback,data['enabled'])
            await app.call(repair_outcomes.resume,feedback)
            return JSONResponse({'enabled':await app.call(repair_outcomes.enabled,feedback),
                                 'backlog':await app.call(repair_outcomes.backlog,feedback),
                                 'budget':'One background analysis at a time; four ablations, 20-second soft deadline plus in-flight capped compiler work. New captures wait in a saved queue; disabling analysis pauses it.'})
        except (NetworkError,ValueError,KeyError,TypeError) as error:return app.fail(error)

    @app.mcp.custom_route('/api/repair-examples/replay', methods=['POST'])
    async def replay_examples(request):
        if bad := app.guard(request, write=True):
            return bad
        repair_replay_jobs, = await app.imports('decompnet.app', ('repair_replay_jobs',))
        try:
            data=await app.body(request)
            slug='' if data.get('all') else str(data.get('slug') or '')
            if not slug and not data.get('all'):
                raise NetworkError('Choose a project or explicitly select all projects.')
            trials,seconds=data.get('trials',12),data.get('seconds',30)
            if any(isinstance(v,bool) or not isinstance(v,int) or not 1<=v<=maximum for v,maximum in ((trials,32),(seconds,120))):
                raise NetworkError('Choose 1–32 trials and 1–120 seconds per example.')
            minimize,diagnostics=data.get('minimize',False),data.get('diagnostics',False)
            combinations=data.get('combinations',False)
            if not isinstance(minimize,bool) or not isinstance(diagnostics,bool) or not isinstance(combinations,bool):
                raise NetworkError('Analysis switches must be booleans.')
            if minimize and combinations:
                raise NetworkError('Choose either edit minimization or combination benchmarking for this run.')
            category,status=str(data.get('category') or ''),str(data.get('status') or '')
            comparable=bool(data.get('comparable',True))
            def select_examples():
                if data.get('id') is not None:
                    case_id=data['id']
                    if isinstance(case_id,bool) or not isinstance(case_id,int):raise NetworkError('Choose an example number.')
                    feedback.detail(slug,case_id,include_source=False)
                    ids=[case_id]
                else:
                    ids, seen = [], set()
                    for row in feedback.listing(slug,category,status,comparable=comparable)['examples']:
                        key = (row['slug'],row['function'])
                        if key not in seen:
                            ids.append(row['id']); seen.add(key)
                        if len(ids) == 20:
                            break
                return ids
            ids = await app.read(select_examples)

            if not ids:raise NetworkError('No examples match these filters.')
            def start_replay():
                with app.jobs._lock:
                    if any(j['kind']=='repair-replay' and j['state']=='running' for j in app.jobs.jobs.values()):
                        raise NetworkError('A repair replay is already running. Stop it or wait before starting another.')
                    job=app.jobs.start('repair-replay',lambda log:repair_replay_jobs.run(feedback,slug,ids,trials,seconds,log,minimize=minimize,diagnostics=diagnostics,combinations=combinations),
                                       cancel_message='Repair replay stopped. No game progress or review states changed.')
                return JSONResponse({'job':job})
            return await app.control(start_replay)
        except (NetworkError,ValueError,KeyError,TypeError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repair-examples/quality', methods=['GET', 'POST'])
    async def quality(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        candidate_quality, = await app.imports('decompnet', ('candidate_quality',))
        try:
            q = request.query_params
            if request.method == 'POST':
                data = await app.body(request)
                if data.get('action') == 'adopt':
                    candidate_recovery, = await app.imports('decompnet', ('candidate_recovery',))
                    return JSONResponse(await app.call(candidate_recovery.adopt, app.net, str(data['slug']),
                                                       str(data['function']), str(data['source_hash']), data['attempt_id']))
                return JSONResponse(await app.call(candidate_quality.review, app.net, str(data['slug']),
                                                   str(data['function']), str(data['source_hash'])))
            slug = '' if q.get('all') == '1' else q.get('slug', '')
            if not slug and q.get('all') != '1':
                raise NetworkError('Choose a project or explicitly select all projects.')
            if q.get('function'):
                fn = await app.read(app.net._function, q['function'], slug)
                return JSONResponse({'evidence': await app.read(candidate_quality.view, app.net, fn)})
            candidate_rechecks, = await app.imports('decompnet.app', ('candidate_rechecks',))
            return JSONResponse({'reports': await app.read(candidate_rechecks.listing, app.net, slug)})
        except (NetworkError, ValueError, KeyError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repair-examples/recovery', methods=['GET', 'POST'])
    async def recovery(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        candidate_rechecks, = await app.imports('decompnet.app', ('candidate_rechecks',))
        try:
            if request.method == 'GET':
                q = request.query_params
                slug = str(q.get('slug') or '')
                if not slug:
                    raise NetworkError('Choose a project for candidate recovery.')
                return JSONResponse(await app.read(candidate_rechecks.evidence, app.net, slug, str(q['function']),
                                                   str(q['source_hash']), int(q['attempt_id'])))
            data = await app.body(request)
            slug = str(data.get('slug') or '')
            await app.read(app.net.project_row, slug)
            limit = data.get('limit', 20)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
                raise NetworkError('Choose 1–200 functions per recovery batch.')
            retry_failed = data.get('retry_failed', False)
            if not isinstance(retry_failed, bool):
                raise NetworkError('Retry failed measurements must be true or false.')
            def start_recovery():
                with app.jobs._lock:
                    if any(j['kind'] == 'candidate-recovery' and j['state'] == 'running' for j in app.jobs.jobs.values()):
                        raise NetworkError('A recovery batch is running. Stop it or wait before starting another.')
                    job = app.jobs.start('candidate-recovery', lambda log: candidate_rechecks.run(app.net, slug, limit, log, retry_failed),
                                         cancel_message='Recovery stopped. Finished measurements are saved; selected C is unchanged.')
                return JSONResponse({'job':job})
            return await app.control(start_recovery)
        except (NetworkError, ValueError, KeyError, TypeError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repair-examples', methods=['GET', 'POST'])
    async def examples(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        try:
            q = request.query_params
            data = await app.body(request) if request.method == 'POST' else {}
            slug = '' if (data.get('all') if data else q.get('all') == '1') else (data.get('slug') or q.get('slug', ''))
            if not slug and not (data.get('all') if data else q.get('all') == '1'):
                raise NetworkError('Choose a project or explicitly select all projects.')
            if request.method == 'POST':
                return JSONResponse(await app.call(feedback.review, slug, int(data['id']), str(data['status']),
                                                   str(data.get('category', '')), data.get('note')))
            if q.get('id'):
                return JSONResponse(await app.read(feedback.detail, slug, int(q['id'])))
            return JSONResponse(await app.read(feedback.listing, slug, q.get('category', ''), q.get('status', ''),
                                               int(q.get('before', 0)), q.get('comparable') == '1'))
        except (NetworkError, ValueError, KeyError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repair-examples/classify', methods=['GET', 'POST'])
    async def classify_examples(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        try:
            data = await app.body(request) if request.method == 'POST' else request.query_params
            slug = str(data.get('slug', ''))
            if not slug:
                raise NetworkError('Choose a project to classify its new examples.')
            return JSONResponse(await app.call(feedback.classify_new, slug, request.method == 'POST'))
        except (NetworkError, ValueError, KeyError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repair-examples/investigate', methods=['GET', 'POST'])
    async def investigate(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        if request.method == 'GET':
            return JSONResponse({'prompt': repair_investigations.PROMPT,
                                 'agents': await app.call(repair_investigations.choices)})
        try:
            data = await app.body(request)
            slug = '' if data.get('all') else str(data.get('slug') or '')
            if not slug and not data.get('all'):
                raise NetworkError('Choose a project or explicitly select all projects.')
            example = await app.read(feedback.detail, slug, int(data['id']))
            kind = str(data.get('agent') or '')
            if not any(a['id'] == kind and a['installed'] for a in await app.call(repair_investigations.choices)):
                raise NetworkError('Choose an installed coding CLI for this investigation.')
            job = await app.control(lambda: app.jobs.start('repair investigation', lambda log: repair_investigations.launch(app.home, example, kind, log)))
            return JSONResponse({'job': job})
        except (NetworkError, ValueError, KeyError, TypeError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repair-examples/export', methods=['GET'])
    async def export(request):
        if bad := app.guard(request):
            return bad
        try:
            q = request.query_params
            slug = '' if q.get('all') == '1' else q.get('slug', '')
            if not slug and q.get('all') != '1':
                raise NetworkError('Choose a project or explicitly select all projects.')
            await app.read(feedback._where, slug, q.get('category', ''), q.get('status', ''), q.get('comparable') == '1')
            chunks = feedback.export(slug, q.get('category', ''), q.get('status', ''), q.get('include_source') == '1', q.get('comparable') == '1')
            return StreamingResponse(chunks, media_type='application/json',
                                     headers={'Content-Disposition': 'attachment; filename="repair-examples.json"'})
        except (NetworkError, ValueError) as error:
            return app.fail(error)
