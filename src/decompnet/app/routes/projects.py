"""Everything under one project: its overview and live feed, the function drawer and diffs, the compiler and its
ranges, the compiler map's survey, export, priorities, notes, suggestions and function-end reports, autopilot.
Each is fn(slug, request, data) wrapped by with_project, which checks the request and answers errors.
"""

from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
from contextlib import nullcontext
from pathlib import Path

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from ... import consoles, hostenv, toolchains
from ...forks.project import SCOPES
from ...network import NetworkError
from .. import compiler_cleanup, saves, window
from ..engine import parse_ranges
from ..live_sections import changed, message
from . import App

STREAM_CHECK_SECONDS = 0.5  # how often /api/stream compares the live feed's change counters
STREAM_PING_SECONDS = 15
STREAM_SECONDS = 300  # a stream's longest life; EventSource reconnects on its own
STREAM_LIMIT = 16  # open streams at once, for every window and browser together


def register(app: App) -> None:
    net, mcp, manager, jobs = app.net, app.mcp, app.manager, app.jobs
    settings, guard, body, call, fail = app.settings, app.guard, app.body, app.call, app.fail

    slow_requests = {}
    compiler_comparisons = {}

    def compiler_comparison(slug):
        with jobs._lock:
            job = jobs.jobs.get(compiler_comparisons.get(slug))
            return ({'id':job['id'], 'state':job['state'], 'scope':job['scope'],
                     'progress':{key:value for key,value in job['progress'].items() if key != 'results'}} if job else None)

    def with_project(fn):
        async def handler(request: Request) -> Response:
            if (bad := guard(request, write=request.method == "POST")):
                return bad
            data = await body(request) if request.method == "POST" else {}
            slug = data.get("slug") or request.query_params.get("slug", "")
            started = time.monotonic()
            try:
                def perform():
                    if request.url.path == '/api/priority':
                        return fn(slug, request, data)  # Metadata is published by autosave, without another recheck here.
                    if request.url.path == '/api/try' or (request.method=='POST' and request.url.path=='/api/reimport'):
                        return fn(slug, request, data)  # The worker synchronizes; return its job without waiting.
                    if request.method == "GET" or request.url.path == "/api/permuter/pass":
                        # Deep-pass controls schedule local workers; the workers synchronize before judging.
                        # Starting/stopping or reading their status must not wait for a project sync.
                        # A restored project's byte checks can take minutes. Show the saved database
                        # while they run; mutations and the judge retain their synchronization gate.
                        with net.progress.view():
                            return fn(slug, request, data)
                    # These operations need no second import while a saved-code recheck is active.
                    foreground = request.url.path in ("/api/try", "/api/note", "/api/release")
                    with net.progress.foreground() if foreground else nullcontext():
                        result = fn(slug, request, data)
                        if foreground:
                            net.progress.foreground_changed(slug, str(data.get("function") or data.get("name") or ""))
                            net.progress.sync(slug, force=True)
                            return result
                    # A scheduled comparison must return its job ID before any saved-file
                    # synchronization, so the UI can follow progress and offer Stop immediately.
                    comparison_started = request.url.path == "/api/compilermap" and data.get("all_installed")
                    # The per-function publisher uses current DB inputs with exact/revision
                    # guards. Let autosave export it rather than blocking the response on a full import.
                    function_profile = request.url.path == '/api/compiler' and data.get('function')
                    if request.method == "POST" and not function_profile and request.url.path not in ("/api/verification", "/api/compiler/test-default", "/api/agent-review", "/api/data-regions", "/api/rom-explorer", "/api/rom-explorer/annotation", "/api/rom-explorer/preview", "/api/rom-explorer/loaders", "/api/rom-explorer/inspect", "/api/rom-explorer/discovery", "/api/knowledge/verify") and not comparison_started and not (request.url.path == "/api/suggestion" and data.get("background") and data.get("action") in ("apply", "apply-all")) and net.store.one("SELECT 1 FROM projects WHERE slug=?", slug):
                        net.progress.sync(slug, force=True)
                    return result
                reader = app.fast_call if (request.url.path == '/api/compilermap' and request.query_params.get('view') == 'comparison') or (request.url.path == '/api/project' and request.query_params.get('quick') == '1') or (request.url.path == '/api/function' and request.query_params.get('quick') == '1') else app.ui_call
                if request.url.path == '/api/live':
                    reader = app.live_call or app.fast_call
                response, = await app.imports('decompnet.request_timing', ('response',))
                def respond():
                    from ..project_activity import current_project
                    token = current_project.set(slug)
                    try:
                        policy = getattr(net, 'project_activity', None)
                        if policy is not None:policy.touch(slug)
                        return response(perform, started, JSONResponse, request.url.path)
                    finally:current_project.reset(token)
                execute=(reader or call) if request.method=="GET" else call
                if request.url.path=='/api/rom-explorer/discovery' and (request.query_params.get('view')=='activity' or data.get('action')=='stop'):
                    execute=app.control  # Discovery admission/cancellation must not queue behind builds.
                return await execute(respond)
            except (NetworkError, ValueError, toolchains.ToolchainError, OSError) as e:
                return fail(e)
            finally:
                elapsed = time.monotonic() - started
                path = request.url.path
                if request.method == 'GET' and elapsed >= 0.5 and started - slow_requests.get(path, float('-inf')) >= 60:
                    slow_requests[path] = started
                    await app.control(lambda: jobs.logger.warning('Slow UI request %s %.3fs (includes worker queue and response encoding)', path, elapsed))
        return handler

    def g_boundary_evidence(slug, request, data):
        from .. import boundary_evidence, boundary_investigations
        packet=boundary_evidence.read(net,slug,request.query_params.get('function',''))
        return {**packet,'suggestions':boundary_investigations.suggestions(net,slug,packet)}

    def g_boundary_investigations(slug, request, data):
        name=request.query_params.get('function','')
        net._function(name,slug)
        return {'sessions':[{'id':s.id,'state':s.state,'why':s.why,'error':s.error,'label':s.label}
                            for s in manager.sessions.values() if s.slug==slug and s.options.get('task')=='boundary'
                            and s.options.get('boundary_function')==name]}

    def p_boundary_repairs(slug, request, data):
        from .. import boundary_repairs as repairs
        if request.method == 'GET':return repairs.audit(net,slug)
        action=data.get('action')
        if action=='preview':return repairs.public(repairs.preview(net,slug,data.get('function'),data.get('repair'),data.get('end')))
        if action=='undo':return {'job':jobs.start('Undo boundary decision',lambda log:repairs.undo(net,slug,data.get('key'),log))}
        return {'job':jobs.start('Verify boundary repair',lambda log:repairs.apply(net,slug,data.get('function'),data.get('repair'),data.get('revision'),data.get('image'),data.get('end'),data.get('note',''),log))}

    def p_symbol_names(slug, request, data):
        from .. import symbol_names
        rows = data.get('names')
        pairs = symbol_names.prepare(net, slug, rows)
        message = str(data.get('message') or 'Restore verified symbol names')[:4000]
        if data.get('preview', False):
            return {'renames': len(pairs)}
        return {'job': jobs.start('Restore symbol names',
                                 lambda log: symbol_names.apply(net, slug, rows, message, log))}

    def p_data_regions(slug, request, data):
        from .. import data_regions
        if request.method == 'POST':
            return data_regions.decide(net,slug,data.get('id',''),data.get('action',''))
        return data_regions.listing(net,slug)

    def g_agent_reviews(slug, request, data):
        from ..agent_review import listing
        return listing(net,slug)

    def p_agent_review(slug, request, data):
        from ..agent_review import acknowledge
        return acknowledge(net,slug,data.get('function',''))

    def p_overview(slug, request, data):
        settings.data["last_project"] = slug
        settings.save()
        overview = net.overview(slug, quick=True) if request.query_params.get("quick") == "1" else net.overview(slug)
        net.warm_briefs_soon(slug)  # the project is open: agents' first briefs come next
        return overview

    def p_live(slug, request, data):
        from ..live_sections import compact, parse_sections
        q = request.query_params
        # `only`: the sections /api/stream said moved (feed, claims, overview, sessions); the rest isn't read
        sections = parse_sections(q.get('only'))
        out = net.live(slug, int(q.get("e", 0) or 0), int(q.get("a", 0) or 0), sections=sections)
        if sections is None or 'sessions' in sections:
            try:
                since = json.loads(q.get("s") or "{}")
            except ValueError:
                since = {}
            out["sessions"] = manager.snapshot(since if isinstance(since, dict) else {}, project=slug)
        if q.get('compact') == '1':
            out = compact(out, q.get('v'))
        else:
            out.pop('versions', None)
        return out

    def p_transcript(slug, request, data):
        q = request.query_params
        if request.method == 'POST':
            return manager.transcripts.export(slug, int(data.get('helper', 0)))
        return manager.transcript(slug, int(q.get('helper', 0)), int(q.get('session', 0)),
                                  int(q.get('after', 0)), int(q.get('before', 0)), whole=q.get('whole') == '1')

    def p_compiler_default_test(slug, request, data):
        from .. import compiler_default_test
        if data.get('action')=='nonmatches':
            # Reopening the same profile drawer follows the existing project task.
            key=(slug,json.dumps(data.get('profile'),sort_keys=True))
            with jobs._lock:
                active=getattr(net,'_nonmatch_test_jobs',None)
                if active is None:active=net._nonmatch_test_jobs={}
                for scope,identity in list(active.items()):
                    if jobs.jobs.get(identity,{}).get('state')!='running':active.pop(scope)
                previous=active.get(key)
                if previous and jobs.jobs.get(previous,{}).get('state')=='running':return {'job':previous}
                active[key]=jobs.start('Test compiler profile as default',
                    lambda log:compiler_default_test.run(net,slug,data.get('profile'),log,nonmatches=True))
                return {'job':active[key]}
        return {"job": jobs.start("Test compiler profile as default", lambda log:(compiler_default_test.check_affected(net, slug, data.get('profile'), data.get('functions'), log) if data.get('action') == 'check-affected' else compiler_default_test.apply_matches(net, slug, data.get('profile'), data.get('functions'), log, safe_ranges=data.get('action') == 'apply-safe-range', receipt=data.get('receipt')) if data.get('action') in ('apply-matches', 'apply-safe-range') else compiler_default_test.replace_default(net, slug, data.get('profile'), log) if data.get('action') == 'replace' else compiler_default_test.run(net, slug, data.get('profile'), log, nonmatches=data.get('action') == 'nonmatches')))}

    def p_compiler_cleanup(slug, request, data):
        limit = int(data.get("rare_limit", 3))
        if not 1 <= limit <= 25:
            raise NetworkError("Choose a rare-profile limit between 1 and 25 functions.")
        net._rom_only(slug, "compiler profile cleanup")
        return {"job": jobs.start("compiler-cleanup", lambda log: compiler_cleanup.cleanup(net, slug, limit, log, source_profile=data.get("profile")))}

    def p_compiler(slug, request, data):
        if data.get("function"):
            return net.apply_function_compiler(slug, str(data["function"]), str(data.get("toolchain", "")),
                                               data.get("flags") or [], allow_non_improving=data.get("allow_non_improving") is True)
        if net.fork_slug(slug) and "auto" not in data:  # its source files each have a compiler (DS)
            tc = str(data.get("toolchain", ""))
            if not str(data.get("ranges") or "").strip():  # the whole game: every file, one at a time
                return {"job": jobs.start("compiler", lambda log: net.forks.set_compiler_everywhere(slug, tc, log))}
            said = net.forks.set_compiler_ranges(slug, parse_ranges(str(data["ranges"])), None if data.get("remove") else tc)
            return net.overview(slug) | {"said": said}
        if "auto" in data:  # this project's suggestions apply by themselves once proven, or wait for the user
            net.set_auto_adjust(slug, bool(data["auto"]))
        elif str(data.get("ranges") or "").strip():  # these address ranges only (or back to the project's: remove)
            net.set_compiler_ranges(slug, parse_ranges(str(data["ranges"])),
                                    None if data.get("remove") else str(data.get("toolchain", "")), data.get("flags") or None,
                                    reopen=bool(data.get("reopen")))
        else:
            def change_compiler(log):
                net.set_compiler(slug, str(data.get("toolchain", "")), data.get("flags") or None, log=log)
                return {"said": "Compiler settings saved; all affected matches checked."}
            return {"job": jobs.start("compiler", change_compiler)}
        return net.overview(slug)

    def p_export(slug, request, data):
        def work(log):
            res = net.export_rom(slug, log=log)
            return {k: v for k, v in res.items() if k != "log"} | {"log": res.get("log", "")[-1500:]}
        return {"job": jobs.start("export", work)}

    def p_data_unit(slug, request, data):
        from .. import data_units
        if request.method == "GET":
            return data_units.unit(net, slug, request.query_params.get("name", ""))
        result = data_units.compile_data(net, slug, str(data.get("name", "")), str(data.get("source", "")))
        return {"exact": result.exact, "problems": result.problems, "size": result.size, "span": result.span}

    def p_data_drafts(slug, request, data):
        """Draft the game's data as typed C from its code (app/data_drafts.py), in the background."""
        from .. import data_drafts

        def work(log):
            counts = data_drafts.run(net, slug, log)
            return {k: v for k, v in counts.items() if k != "reasons"} | {"reasons": dict(sorted(
                counts["reasons"].items(), key=lambda kv: -kv[1])[:6])}
        return {"job": jobs.start("data-drafts", work, project=slug)}

    def p_pc_compile(slug, request, data):
        """Compile the game's C for a PC and keep what breaks (port/pc_compile.py), in the background."""
        from ...port import pc_compile
        return {"job": jobs.start("pc-compile", lambda log: pc_compile.check(net, slug, log), project=slug)}

    def p_port_identity(slug, request, data):
        """The user confirms which library function a function is, for the port (port/identities.py)."""
        from ...port import identities
        agent, _ = net.local_agent("you")
        return {"message": identities.confirm(net, agent, slug, str(data.get("function", "")),
                                              str(data.get("name", "")), str(data.get("note", "")))}

    def p_rename_calls(slug, request, data):
        """Rewrite calls by a function's old name to its name now, each function judged twice
        (port/renamed_calls.py), in the background."""
        from ...port import renamed_calls
        return {"job": jobs.start("rename-calls", lambda log: renamed_calls.fix(net, slug, log), project=slug)}

    def p_play(slug, request, data):
        """Build the game for the PC and run it with its live view in the browser (port/wasm/play.py), in the
        background; Stop ends it."""
        from ...port.wasm import play
        if data.get("stop"):
            return {"stopped": play.stop(slug)}
        if (url := play.running(slug)):
            return {"url": url}
        return {"job": jobs.start("play", lambda log: play.play(net, slug, log), project=slug)}

    def p_asset_map(slug, request, data):
        """Write the ROM's data as the kit's extractor config into the project's folder (port/asset_map.py)."""
        from ...port import asset_map
        with net.progress.view():
            return asset_map.write(net, slug)

    def p_asset_extract(slug, request, data):
        """Extract the asset map from the user's ROM with the kit's extractor, and check it (in the background: the
        first run builds Torch)."""
        from ...port import asset_map
        return {"job": jobs.start("asset-extract", lambda log: asset_map.extract(net, slug, log), project=slug)}

    def g_port_readiness(slug, request, data):
        from ...port.readiness import readiness
        return readiness(net, slug)

    def p_rom_explorer(slug, request, data):
        with net.progress.view():
            return rom_explorer_request(slug,request,data)

    def rom_explorer_request(slug, request, data):
        from .. import rom_explorer
        if request.method == 'GET':
            q = request.query_params
            if q.get('view') == 'status':return rom_explorer.status(net,slug,jobs)
            if q.get('region'):return rom_explorer.detail(net,slug,q['region'],q.get('image_hash',''))
            return rom_explorer.listing(net,slug)
        # Reserve discovery and start atomically: double clicks and two windows
        # must attach to the same project job rather than launch duplicate scans.
        with jobs._lock:
            active = rom_explorer.status(net,slug,jobs)['job']
            if active and active['state']=='running':return {'job':active['id']}
            return {'job':jobs.start('rom-explorer',lambda log:rom_explorer.run(net,slug,log),project=slug,
                                     cancel_message='ROM scan stopped. The previous completed map and notes are retained.')}

    def p_rom_annotation(slug, request, data):
        from ..rom_explorer import annotate
        with net.progress.view():
            return annotate(net,slug,data)

    def p_rom_preview(slug, request, data):
        from ..rom_explorer import asset_preview
        with net.progress.view():return asset_preview(net,slug,data)

    def p_rom_inspect(slug, request, data):
        from ..rom_explorer import asset_inspect
        with net.progress.view():return asset_inspect(net,slug,data)

    def p_asset_discovery(slug,request,data):
        from .. import asset_agents
        with net.progress.view():
            if request.method=='GET':
                if request.query_params.get('view')=='activity':
                    net.project_row(slug)
                    with jobs._lock:
                        job=next((job for job in reversed(list(jobs.jobs.values())) if job['kind']=='Create asset discovery agents' and job.get('activity_project')==slug),None)
                        return {'creation_job':{key:job[key] for key in ('id','state','progress','error') if key in job} if job else None}
                if request.query_params.get('finding'):return asset_agents.review(net,slug,request.query_params['finding'])
                return asset_agents.status(net,slug,manager,request.query_params.get('page',0))
            action=data.get('action')
            if action in ('prepare','resume'):
                return {'run':asset_agents.prepare(net,slug,data.get('scope'),data.get('run',''),resume=action=='resume')}
            if action=='stop':
                with jobs._lock:creating=[job['id'] for job in jobs.jobs.values() if job['kind']=='Create asset discovery agents' and job['state']=='running' and job.get('activity_project')==slug]
                for identity in creating:jobs.cancel(identity)
                return asset_agents.stop(net,slug,manager)
            if action in ('accept','reject','similar'):
                return asset_agents.review(net,slug,data.get('finding'),action,data.get('edits'),data.get('annotation_version',''),data.get('feedback',''))
            raise NetworkError('Choose a discovery pass or proposal review action.')

    def p_rom_loaders(slug, request, data):
        from ..rom_explorer import loader_contracts
        with net.progress.view():return loader_contracts(net,slug,data if request.method=='POST' else None)

    def p_code_audit(slug, request, data):
        from .. import rom_gap_audit
        return {"job": jobs.start("code-audit",lambda log:rom_gap_audit.run(net,slug,log))}

    def p_recover_code(slug, request, data):
        from ..reviewed_code import run
        return {"job":jobs.start('reviewed-code-recovery',lambda log:run(net,slug,data.get('changes'),log))}

    def g_code_audit(slug, request, data):
        from ..code_audit_reviews import decorated, load
        return decorated(load(net,net.project_row(slug)['id']))

    def p_code_audit_review(slug, request, data):
        from ..code_audit_reviews import review
        return review(net,slug,data.get('review_id'),data.get('state'),data.get('notes',''))

    reimport_lock = threading.RLock()

    def active_reimport(slug):
        with jobs._lock:
            return next((j['id'] for j in jobs.jobs.values() if j['kind']=='reimport'
                         and j.get('activity_project')==slug and j['state']=='running'),None)

    def p_reimport(slug, request, data):
        from ..reimport_source import stored
        if request.method=='GET':
            with jobs._lock:
                latest=next((j for j in reversed(list(jobs.jobs.values())) if j['kind']=='reimport'
                             and j.get('activity_project')==slug),None)
                snapshot={'job':latest['id'],'state':latest['state']} if latest else {'job':None}
            if snapshot.get('state')=='running':return snapshot
            try:
                path=stored(net,slug)
                return {**snapshot,'available':True,'name':path.name}
            except (NetworkError,OSError,ValueError) as error:
                return {**snapshot,'available':False,'reason':str(error)}
        with reimport_lock:
            if identity:=active_reimport(slug):return {'job':identity}
            return start_reimport(slug,request,data)

    def start_reimport(slug, request, data):
        if not data.get("confirmed"):
            raise ValueError("confirm that this is your own legally obtained copy of the game")
        from ..reimport_source import stored
        path = hostenv.local_path(str(data['path'])) if data.get('path') else stored(net,slug)
        if not path.is_file():raise NetworkError('Choose a game file to reimport.')
        proj = net.project_row(slug)
        cfg = json.loads(proj["config"])
        fork = bool(net.fork_slug(slug))
        if cfg.get("kind") != "rom" and not (fork and net.forks.overview(slug).get("reimports")):
            raise NetworkError("only ROM projects and PC projects started from scratch can be reimported")
        if slug in net.rooms or slug in net.links:
            raise NetworkError("leave the shared room before reimporting this game")
        if consoles.identify(path).id != consoles.of(cfg).id:
            raise NetworkError("choose a game for the same console as this project")
        if net.surveys.get(slug, {}).get("state") == "running":
            raise NetworkError("wait for the compiler survey to finish before reimporting this game")
        manager.stop_project(slug)
        net.stop_autopilot(slug)
        net.twins.stop(slug)
        net.permuter.stop(slug)
        if fork:  # (its own analysis again: Console.reimport)
            return {"job": jobs.start("reimport", lambda log: net.forks.reimport(slug, path, log=log), project=slug, cancel_message="Reimport stopped. Completed saved work is retained.")}
        return {"job": jobs.start(
            "reimport", lambda log: net.reimport_rom(slug, path, confirmed=True, name=path.name, log=log), project=slug, cancel_message="Reimport stopped. Completed saved work is retained.")}

    compiler_maintenance = {}
    compiler_maintenance_lock = threading.Lock()

    def p_compilermap(slug, request, data):
        """The compiler answer (GET): which compiler built the game, and its parts; the app checks by itself when the
        answer is old. POST: check again now (the survey and the applied ranges)."""
        if request.method == 'GET' and request.query_params.get('view') == 'comparison':
            active = compiler_comparison(slug)
            return {'job':active['id'], **active} if active else {'job':None}
        if request.method == "POST" and data.get("all_installed"):
            with net.progress.view():
                fork=net.fork_slug(slug)
            if fork:
                raise NetworkError("This comparison currently needs a ROM-mode project.")
            if type(data.get("auto_apply", False)) is not bool:
                raise NetworkError("Auto-apply must be true or false.")
            scope = {'slug':slug, 'function':str(data.get('function') or ''),
                     'untested_only':bool(data.get('untested_only', True)), 'flags_only':bool(data.get('flags_only', False)),
                     'used_profiles_only':bool(data.get('used_profiles_only', False)), 'auto_apply':data.get('auto_apply', False)}
            with jobs._lock:
                active = compiler_comparison(slug)
                if active and active['state'] == 'running':return {'job':active['id'], **active}
                job = jobs.start("installed compilers", lambda log: net.run_compiler_comparison(
                    slug, log=log, **{key:value for key,value in scope.items() if key != 'slug'}),
                    cancel_message="Compiler comparison stopped. Completed results are saved.")
                jobs.jobs[job]['scope'] = scope
                jobs.jobs[job].setdefault('progress', {'phase':'preparation', 'stage':'Starting compiler comparison'})
                compiler_comparisons[slug] = job
                return {'job':job}
        if request.method == 'POST':
            return net.compiler_answer(slug, check_now=True)
        project = net.store.one('SELECT id FROM projects WHERE slug=?', slug)
        version = net.project_store(slug).read_version('inputs', project['id'] if project else None)
        with compiler_maintenance_lock:
            previous = compiler_maintenance.get(slug)
            if previous is None or (not previous['running'] and (previous['version'] != version or time.monotonic()-previous['at'] > 60)):
                state = {'version': version, 'at': time.monotonic(), 'running': True}
                compiler_maintenance[slug] = state
                def maintain():
                    try:
                        net.compiler_answer(slug)
                    except Exception:
                        jobs.logger.exception('Automatic compiler checks failed for %s', slug)
                    finally:
                        with compiler_maintenance_lock:
                            state['running'] = False
                threading.Thread(target=maintain, daemon=True, name=f'compiler-status-{slug}').start()
        return net.compiler_answer(slug, display_only=True)

    def p_verification(slug, request, data):
        if data.get("paused"):
            net.progress.pause(slug)
        else:
            net.progress.resume(slug)
            jobs.start("saved progress verification", lambda log: net.progress.sync(slug, force=True, logger=log))
        return {"ok": True}

    def p_open(slug, request, data):
        if app.web_login and not secrets.compare_digest(window.request_token(request), app.secret):
            return {'ok':False, 'message':'Project folder on the server: '+str(net.project_row(slug)['root'])}
        why = hostenv.open_folder(Path(net.project_row(slug)["root"]))
        return {"ok": why is None, "message": why}

    def p_delete(slug, request, data):
        manager.forget_project(slug)  # (it stopped every project's helpers)
        proj = net.project_row(slug)
        net.knowledge.forget(slug)
        net.remove_project(slug)
        from .. import manual_mcp
        settings.data['manual_mcp'] = [c for c in manual_mcp.connections(settings) if c['slug'] != slug]
        settings.save()
        if data.get("files"):
            shutil.rmtree(proj["root"], ignore_errors=True)
        if settings.data.get("last_project") == slug:
            settings.data["last_project"] = None
            settings.save()
        return {"ok": True}

    def function_attempt(slug, name):
        with jobs._lock:
            job = next((j for j in reversed(jobs.jobs.values()) if j['kind']=='function-attempt'
                        and j['state']=='running' and j.get('scope')=={'slug':slug,'function':name}), None)
            return {k:job[k] for k in ('id','state','progress','scope') if k in job} if job else None

    def g_try_status(slug, request, data):
        active = function_attempt(slug, request.query_params.get('name',''))
        return {'job':active['id'], **active} if active else {'job':None}

    def p_try(slug, request, data):
        """You, editing a function in the GUI: judged like an agent's attempt (and credited to "you")."""
        def run(log=None):
            from ...attempt_status import watch
            name = str(data.get('name', ''))
            observer = watch(name, log.progress) if log is not None else nullcontext()
            with observer, net.progress.foreground():
                agent, _ = net.local_agent("you")
                res = net.attempt(agent, name, str(data.get("source", "")), slug)
                if log is not None:
                    net.progress.foreground_changed(slug, name)
                    net.progress.sync(slug, force=True)
                return {"score": res["score"], "matched": res["matched"], "exact": res.get('exact',False),
                        "new_match": res.get('new_match',False), "adopted": res.get('adopted',False),
                        "text": res["text"][-4000:], "checks": res.get("checks", {})}
        import hashlib
        digest = hashlib.sha256(str(data.get('source','')).encode()).hexdigest()
        with jobs._lock:
            active = function_attempt(slug, str(data.get('name','')))
            if active:
                job = active['id']
                if jobs.jobs[job].get('source_sha256') != digest:
                    raise NetworkError('This function is already compiling another candidate. Wait for its result before submitting again.')
            else:
                job = jobs.start('function-attempt', run, project=slug)
                jobs.jobs[job]['scope'] = {'slug':slug,'function':str(data.get('name',''))}
                jobs.jobs[job]['source_sha256'] = digest
        if data.get('background'):return {'job':job}
        # Keep the existing synchronous API contract for CLIs. The server-owned job
        # remains visible if a caller times out or opens the function in the GUI.
        while True:
            with jobs._lock:
                snapshot = jobs.jobs[job]
                if snapshot['state']=='done':return snapshot['result']
                if snapshot['state']!='running':raise NetworkError(snapshot.get('error') or 'Attempt ended without a result.')
            time.sleep(.05)

    def p_release(slug, request, data):
        agent, _ = net.local_agent("you")
        try:
            net.release(agent, str(data.get("name", "")), "", slug)
        except NetworkError:
            pass  # not yours (any more): nothing to give back
        return {"ok": True}

    def p_claim_handover(slug, request, data):
        agent, _ = net.local_agent('you')
        from ...release_receipts import current
        fn=net._function(str(data.get('name') or ''),slug)
        lease=current(net,fn,agent)
        if not lease:
            raise NetworkError(f"You don't hold a claim on {fn['name']}.")
        net.release(agent, fn['name'], '', slug, claim_token=lease)
        return {'ok': True}

    def p_recheck(slug, request, data):
        """Check again: the project looked at with what this version knows (Core.recheck), in the background."""
        return {"job": jobs.start("recheck", lambda log: {
            "said": net.recheck(slug, log=log) or "nothing to do on this computer, as the room's host checks the game"})}

    def p_set_aside(slug, request, data):
        """You set a function aside: no C makes it (Network.set_aside_by_you)."""
        net.set_aside_by_you(str(data.get("name", "")), slug, str(data.get("why", "")))
        return {"ok": True}

    def p_reopen(slug, request, data):
        """You give a set-aside function back to the agents."""
        net.reopen(str(data.get("name", "")), slug)
        return {"ok": True}

    def p_instructions(slug, request, data):
        net.set_instructions(slug, str(data.get("text", "")))
        return net.overview(slug)

    def p_claim_order(slug, request, data):
        net.set_claim_order(slug, str(data.get("order", "")))
        return net.overview(slug)

    def p_title(slug, request, data):
        net.set_title(slug, str(data.get("title", "")))
        return net.overview(slug)

    def p_priority(slug, request, data):
        return {"priority": net.set_priority(slug, str(data.get("name", "")), bool(data.get("on", True)))}

    def p_scope(slug, request, data):
        """What the project covers ({scope: "game" | "program"}): kept in its manifest, then the project is measured
        again (a job: a big project takes a while), so its set-aside functions open to agents and count, or stop."""
        scope = str(data.get("scope", ""))
        if scope not in SCOPES:
            raise ValueError(f"a project covers the game's own code or the whole program, not {scope!r}")
        if not net.fork_slug(slug):
            raise ValueError("only projects started from scratch or forked have code set aside to choose for")

        def work(log):
            net.forks.set_scope(slug, scope, log=log)
            return net.overview(slug)
        return {"job": jobs.start("scope", work)}

    def p_unanalysed(slug, request, data):
        """Bring in the code the project's import left unanalysed (Console.unanalysed), once: a new analysis of that
        code alone (a job: it runs the console's analysis, Ghidra for some), then the project measured again."""
        if not net.fork_slug(slug):
            raise ValueError("only projects started from scratch have code a new analysis can bring in")

        def work(log):
            net.forks.analyse_unanalysed(slug, log=log)
            return net.overview(slug)
        return {"job": jobs.start("analysis", work)}

    def p_note(slug, request, data):
        """A note from you on a function (or the whole project): agents read notes when they claim it."""
        if data.get('action') == 'remove':
            proj = net._project(slug)
            fn = net._function(str(data.get('function', '')), slug)
            ids = data.get('ids')
            if not isinstance(ids, list) or not ids or len(ids) > 100 or any(type(i) is not int for i in ids):
                raise ValueError('Choose a note to remove.')
            with net.project_store(slug).tx() as db:
                db.executemany('DELETE FROM notes WHERE id=? AND project_id=? AND function_id=?',
                               [(i, proj['id'], fn['id']) for i in ids])
            return {'message': 'Note removed.'}
        agent, _ = net.local_agent("you")
        text = str(data.get("text", "")).strip()
        if not text:
            raise ValueError("write the note first")
        return {"message": net.add_note(agent, slug, text[:2000], str(data.get("function", "")) or None)}

    suggestion_jobs = {}

    def p_suggestion(slug, request, data):
        if data.get('action') == 'dismiss-all':
            from ..function_profile_suggestions import dismiss_all
            count = dismiss_all(net, slug, data.get('ids'))
            return {'dismissed':count, 'overview':net.overview(slug)}
        if data.get('action') == 'apply-all':
            from ..function_profile_suggestions import accept_all
            with jobs._lock:
                previous = suggestion_jobs.get((slug, 'all'))
                if previous and jobs.jobs.get(previous, {}).get('state') == 'running':
                    return {'job':previous}
                job = jobs.start('Apply waiting compiler suggestions', lambda log:accept_all(net, slug, log))
                suggestion_jobs[slug, 'all'] = job
                return {'job':job}
        if data.get("background") and data.get("action") == "apply":
            sid = int(data.get("id", 0))
            with jobs._lock:
                previous = suggestion_jobs.get((slug, sid))
                if previous and jobs.jobs.get(previous, {}).get('state') == 'running':
                    return {"job": previous}
                def apply_suggestion(log):
                    log("Checking the suggested settings. Existing matches must still match byte for byte.")
                    net.resolve_suggestion(slug, sid, True, log=log)
                    log.progress({'phase':'Saving project progress', 'done':0, 'total':1})
                    log("Saving project progress to disk.")
                    net.progress.sync(slug, force=True, logger=log)
                    log.progress({'phase':'Refreshing project results', 'done':0, 'total':1})
                    log("Refreshing project results.")
                    return net.overview(slug)
                job = jobs.start("Apply compiler suggestion", apply_suggestion)
                suggestion_jobs[slug, sid] = job
                return {"job": job}
        net.resolve_suggestion(slug, int(data.get("id", 0)), data.get("action") == "apply")
        return net.overview(slug)

    def p_boundary(slug, request, data):
        net.resolve_boundary(slug, int(data.get("id", 0)), data.get("action") == "apply")
        return net.overview(slug)

    def p_source_preparation(slug, request, data):
        from .. import source_preparation
        from ..permute import State
        proj = net.project_row(slug)
        if request.method == "GET":
            return source_preparation.settings(net, proj)
        if data.get('action') == 'assembly-scan':
            from ..source_assembly import scan
            return {'candidates':scan(net,proj,source_preparation.settings(net,proj))}
        if data.get('action') == 'assembly-run':
            from ..source_assembly import run
            return {'job':jobs.start('source-assembly',lambda log:run(net,net.project_row(slug),source_preparation.settings(net,proj),log,retry=True))}
        if data.get('action') == 'queue-status':
            return net.source_queue.status(slug)
        if data.get('action') == 'queue-start':
            return net.source_queue.start(slug,data.get('include_assembly',False))
        if data.get('action') == 'queue-stop':
            return net.source_queue.stop(slug)
        if data.get('action') == 'analysis':
            from ..source_lead_analysis import listing
            return listing(net, proj)
        if data.get('action') == 'reject-lead':
            from ..source_lead_analysis import reject
            return reject(net,proj,str(data.get('id','')),data.get('reason',''))
        if data.get('action') == 'reviews':
            from ..prepared_evidence import listing
            return {'reviews': listing(net, proj)}
        if data.get('action') == 'recheck-exact':
            from ..prepared_evidence import recheck_exact
            return {'job': jobs.start('prepared-bundles', lambda log: recheck_exact(net,proj,log,data.get('limit',20)))}
        if data.get('action') == 'repair-export':
            from ..export_source_recovery import run
            names=data.get('functions',[])
            if not isinstance(names,list) or not 1<=len(names)<=20 or not all(isinstance(n,str) for n in names):
                raise ValueError('Choose between 1 and 20 matched functions for export repair.')
            return {'job':jobs.start('export-source-recovery',lambda log:run(net,proj,names,State(),log))}
        if data.get('action') == 'try-pair':
            from ..prepared_evidence import try_pair
            return {'job': jobs.start('prepared-pair', lambda log: try_pair(net, proj, str(data.get('function','')),
                    str(data.get('source','')), str(data.get('header','')), log, data.get('settings')))}
        if data.get('action') in {'apply-review','recheck-review'}:
            from ..prepared_evidence import apply
            key = str(data.get('id', ''))
            return {'job': jobs.start('prepared-bundle', lambda log: apply(net, proj, key, log, refresh=data.get('action')=='recheck-review'))}
        if data.get('action') not in (None, 'save', 'run'):
            raise ValueError('Choose save or run for source preparation.')
        if data.get('action') == 'run':
            def work(log):
                report = source_preparation.run(net.permuter, net.project_row(slug), State(), log)
                log(f"Prepared {report['tested']}: {report['improved']} improved, {report['matched']} matched.")
                return report
            return {'job': jobs.start('source-preparation', work)}
        return source_preparation.configure(net, proj, data)

    def p_deep_sweep(slug, request, data):
        sweeps = net.deep_sweeps
        if request.method == 'GET':
            return sweeps.control(slug) if request.query_params.get('view') == 'control' else sweeps.status(slug)
        action = data.get('action', 'start')
        if action == 'export-metrics':
            from ..metrics_export import save
            return save(slug, data.get('csv'))
        if action == 'stop':
            return sweeps.stop(slug)
        if action == 'reset-metrics':
            return sweeps.reset_metrics(slug)
        if action != 'start':
            raise ValueError('Choose start, stop, reset-metrics or export-metrics for the deep pass.')
        if not isinstance(data.get('repeat', False), bool):
            raise ValueError('Repeat must be true or false.')
        return sweeps.start(slug, data.get('minutes', 5), data.get('max_variants', 10000), data.get('repeat', False),
                            data.get('threads', 0), data.get('compiler_profiles', 1), data.get('loop', False),
                            data.get('threads_per_function', 1), data.get('maximum_throughput', False), data.get('compile_slots', 0))

    def p_function_notes(slug, request, data):
        from ..function_history import notes_page
        fn = net._function(request.query_params.get('name', ''), slug)
        return notes_page(net.project_store(fn['project_id']), fn, request.query_params.get('before'), net=net)

    from ..function_searches import Searches
    function_searches = net.function_searches = Searches(jobs)

    def p_function(slug, request, data):
        name = request.query_params.get("name", "")
        # The drawer loads name suggestions separately when its peers disclosure opens.
        # Do not delay the core function response by repeating that cross-project search.
        detail = (net.function_preview(slug, name) if request.query_params.get('quick') == '1'
                  else net.function_detail(slug, name, include_hints=False))
        claim = net.project_store(slug).one('SELECT c.agent_id FROM claims c JOIN functions f ON f.id=c.function_id JOIN projects p ON p.id=f.project_id '
                                             'WHERE p.slug=? AND f.name=? AND c.expires_at>?',slug,detail['name'],time.time())
        claim = net.store.one('SELECT name FROM agents WHERE id=?',claim['agent_id']) if claim else None
        detail['claim_owner'] = claim['name'] if claim else None
        detail['can_handover'] = bool(claim and claim['name'] == 'you')

        with jobs._lock:
            job = function_searches.get((slug, name)) or function_searches.get((slug, detail['name']))
            if job in jobs.jobs:
                detail['deep_job'] = job
        return detail

    def p_deep_permuter(slug, request, data):
        from .. import deep_permuter
        name = str(data.get('function') or '')
        minutes, variants = data.get('minutes', 30), data.get('max_variants', 100000)
        from .. import deep_profiles
        profiles = deep_profiles.validate(data.get('compiler_profiles', 1))
        from ..deep_parallel import validate_threads
        function_threads = validate_threads(data.get('threads_per_function', 1))
        from ..deep_throughput import options
        throughput = options(data.get('maximum_throughput', False), data.get('compile_slots', 0))
        with jobs._lock:
            previous = function_searches.get((slug, name))
            if previous and jobs.jobs.get(previous, {}).get('state') == 'running':
                return {'job': previous}
            deep_permuter.validate(net, slug, name, minutes, variants)
            job = jobs.start('deep-permuter', lambda log: deep_permuter.run(net, slug, name, minutes, variants, log, compiler_profiles=profiles, threads_per_function=function_threads, **throughput),
                             cancel_message='Deep permuter stopped. Verified improvements are saved.')
            function_searches[slug, name] = job
            return {'job': job}

    def p_finish_near(slug, request, data):
        from .. import finish_near
        seconds,attempts=data.get('seconds',120),data.get('attempts',128)
        finish_near.validate(net,slug,seconds,attempts)
        active=getattr(net,'_finish_near_jobs',{})
        net._finish_near_jobs=active
        previous=active.get(slug)
        if previous and jobs.jobs.get(previous,{}).get('state')=='running':return {'job':previous}
        active[slug]=jobs.start('finish-near',lambda log:finish_near.run(net,slug,seconds,attempts,log),
            cancel_message='Near-match pass stopped. Verified matches and pending search paths are saved.')
        return {'job':active[slug]}

    def p_reconcile_matches(slug, request, data):
        from .. import match_reconciliation
        seconds,attempts=data.get('seconds'),data.get('attempts')
        match_reconciliation.validate(net,slug,seconds,attempts)
        active=getattr(net,'_finish_near_jobs',{})
        net._finish_near_jobs=active
        previous=active.get(slug)
        if previous and jobs.jobs.get(previous,{}).get('state')=='running':return {'job':previous}
        active[slug]=jobs.start('reconcile-matches',lambda log:match_reconciliation.run(net,slug,seconds,attempts,log),
            cancel_message='Saved-match recovery stopped. Freshly verified matches are saved.')
        return {'job':active[slug]}

    def p_permuter(slug, request, data):
        """Run free local permutations without starting a paid helper or the full autopilot."""
        net.project_row(slug)
        action = data.get("action", "start")
        if action not in {"start", "stop"}:
            raise ValueError("Choose start or stop for the permuter.")
        if action == "stop":
            net.permuter.stop(slug)
        else:
            net.permuter.start(slug)
        return {"permuter": net.permuter.status(slug)}

    draft_jobs = {}

    def p_attempt_history(slug, request, data):
        from ..database_storage import history
        q=request.query_params
        return history(net,slug,q.get('function',''),int(q.get('before',0)),int(q['attempt']) if q.get('attempt') else None)
    mcp.custom_route('/api/attempt-history',methods=['GET'])(with_project(p_attempt_history))

    def p_machine_drafts(slug, request, data):
        from .. import draft_regeneration, draft_reports
        proj = net.project_row(slug)
        if request.method == 'GET':
            name = request.query_params.get('function')
            if name:
                fn = net._function(name, slug)
                return {'function':fn['name'], 'report':draft_reports.read(net,fn['id'],project_id=proj['id'])}
            rows = net.project_store(slug).all("SELECT value FROM settings WHERE key IN (SELECT ? || id FROM functions WHERE project_id=?)",
                                 draft_reports.PREFIX,proj['id'])
            reports = [json.loads(row['value']) for row in rows]
            return {'checked':len(reports), 'compiled':sum(bool(r.get('compiled')) for r in reports),
                    'failed':sum(not r.get('compiled',False) for r in reports), 'job':draft_jobs.get(slug)}
        include_all = data.get('include_all',False)
        if not isinstance(include_all,bool):
            raise ValueError('Include all unmatched functions must be true or false.')
        threads = data.get('threads',0)
        draft_regeneration.thread_count(threads)
        draft_regeneration.validate(net,slug)
        previous = draft_jobs.get(slug)
        if previous and jobs.jobs.get(previous,{}).get('state') == 'running':
            return {'job':previous}
        draft_jobs[slug] = jobs.start('Regenerate machine drafts',lambda log:draft_regeneration.run(net,slug,log,include_all=include_all,threads=threads),
                                     cancel_message='Draft refresh stopped. Completed drafts and verified attempts are saved.')
        return {'job':draft_jobs[slug]}

    def p_autopilot(slug, request, data):
        if data.get("action") == "stop":
            net.stop_autopilot(slug)
        else:
            net.start_autopilot(slug,foreground=True)
        return net.overview(slug)

    def p_cross_run(slug, request, data):
        from ..cross_game_progress import report
        from ..twins import Job
        net.project_row(slug)
        if slug in net.links:
            raise ValueError('Run cross-game checks on the room host.')
        with net.twins._lock:
            if net.twins._full_running(slug) or (net.autopilot_status(slug) or {}).get('state') == 'running':
                raise ValueError('A free matching pass is already running. Wait for it to finish.')
            check = Job()
            check.ordered.set()
            net.twins.jobs[slug] = check
        def work(log):
            from ..free_candidates import budget
            budget(check, seconds=float("inf"), attempts=float("inf"))
            check.cross_exhaustive = True
            check.stop = log.cancel_event
            check.cross_report = log.progress
            check.cross_started = time.monotonic()
            check.cross_seconds = 0
            # Manual searches exhaust the eligible queue; automatic passes retain their caps.
            check.partial_limit = float("inf")
            try:
                report(check, 'Reading saved transfer results')
                log('Checking all eligible candidates. Stop at any time; verified results are saved.')
                before = net.twins.cross_game_results(slug)['counts'].get('improved', 0)
                matched = net.twins.across_games(slug, check)
                improved = net.twins.cross_game_results(slug)['counts'].get('improved', 0) - before
                reason = 'Search stopped' if check.stop.is_set() else 'All eligible candidates checked'
                report(check, reason)
                log(f'{reason}. {matched} verified matches · {improved} partial improvements · {check.cross_tried} checks.')
                return {'matched': matched, 'improved': improved, 'tried': check.cross_tried}
            finally:
                check.state = 'stopped' if check.stop.is_set() else 'done'
        try:
            check.cross_job = jobs.start('cross-game', work,
                                         cancel_message='Cross-game check stopped. Verified matches and improvements are saved.')
            return {'job': check.cross_job}
        except Exception:
            check.state = 'done'
            raise

    REGIONAL_JOBS = {'names': 'Names from another release', 'code': 'Code from another release'}

    def p_regional(slug, request, data):
        from .. import regional
        if request.method == 'POST':
            out = regional.link(net, slug, str(data.get('other') or '') or None)
        else:
            out = regional.status(net, slug, shared=request.query_params.get('shared') == '1')
        # The panel follows a job it didn't start too (a reload, another window, a restart of the page).
        return out | {'running': {key: regional_running(slug, kind) for key, kind in REGIONAL_JOBS.items()}}

    def regional_running(slug, kind):
        """The id of this project's running job of this kind, or None."""
        with jobs._lock:
            return next((j['id'] for j in jobs.jobs.values() if j['kind'] == kind and j['state'] == 'running'
                         and j.get('activity_project') == slug), None)

    def p_regional_names(slug, request, data):
        from .. import regional  # (the job checks the link: reading the project waits while it syncs)
        if regional_running(slug, REGIONAL_JOBS['names']):
            raise ValueError('Names are already coming over from the other release: its progress is in the panel.')
        return {'job': jobs.start(REGIONAL_JOBS['names'], lambda log: regional.bring_names(net, slug, log),
                                  cancel_message='Stopped. The names already checked and saved stay.')}

    def p_regional_code(slug, request, data):
        from .. import regional
        if regional_running(slug, REGIONAL_JOBS['code']):
            raise ValueError('The other release\'s code is already being tried: its progress is in the panel.')
        if (net.autopilot_status(slug) or {}).get('state') == 'running':
            raise ValueError('Autopilot is running and tries the other release\'s code itself. Wait for it to finish.')

        def work(log):
            from ..twins import Job
            log.progress(dict(phase='Reading this project and the linked release'))
            if not regional.linked(net, net.project_row(slug)):
                raise ValueError('Link this project with another release of the game first.')
            job = Job()
            job.stop = log.cancel_event
            return regional.transfer(net, slug, job, log)
        return {'job': jobs.start(REGIONAL_JOBS['code'], work,
                                  cancel_message='Stopped. Matches and polished functions found so far are saved.')}

    def p_sdk(slug, request, data):
        path = hostenv.local_path(str(data.get("path") or ""))
        corpus = str(data.get("corpus") or path.stem).strip()[:80]
        if not corpus:
            raise ValueError("give the SDK corpus a name and version")
        signatures = net.twins.import_sdk_objects(path, corpus)
        sources = 0
        if data.get('source_path'):
            from ..free_candidates import import_sources
            source_path = hostenv.local_path(str(data['source_path']))
            header = hostenv.local_path(str(data['header_path'])) if data.get('header_path') else None
            sources = import_sources(net, net.project_row(slug), corpus, source_path, header)
        return {"signatures": signatures, "sources": sources, "corpus": corpus}

    def p_cross_name(slug, request, data):
        old, new = str(data.get("function") or ""), str(data.get("name") or "")
        if new.startswith('func_'):
            raise ValueError('This is a generated function name. Keep the current name or choose a descriptive naming lead.')
        if data.get('peer_id'):
            from ..project_functions import listing
            peers = listing(net, slug, old)['rows']
            if not any(str(p['id']) == str(data['peer_id']) and p['name'] == new for p in peers):
                raise ValueError('That project function has changed or is no longer a naming lead. Refresh the table.')
        else:
            from ..sdk_identity import identify
            proj=net.project_row(slug);fn=net._function(old,slug)
            sdk=identify(net,proj,{},include_asm=True).get(fn['id'],{})
            sdk_name=sdk.get('names')==[new]
            if not sdk_name and not any(h["name"] == new and h["score"] == 1 and "unverified" not in h["kind"]
                       for h in net.twins.suggestions(slug, old, limit=100)):
                raise ValueError("that exact SDK or cross-game name is no longer suggested for this function")
        agent, _ = net.local_agent("you")
        def rename(log=None):
            publish = getattr(log, "progress", None)
            message = net.rename_symbol(agent, slug, old, new, "name selected from a cross-project naming lead",
                                        **({"progress": publish} if publish else {}))
            if message.startswith("❌"):
                raise NetworkError(message)
            return {"message": message}
        if data.get("background"):
            return {"job": jobs.start("Apply suggested name", rename)}
        return rename()

    def p_project_functions(slug, request, data):
        from ..project_functions import listing, name_hints
        function = str(request.query_params.get('function') or '')
        return {**listing(net, slug, function), 'name_hints': name_hints(net, slug, function)}

    def p_save(slug, request, data):
        """Save the project's work as plain files (app/saves.py): to its progress/ folder, or one the user picks."""
        return saves.save(net, slug, hostenv.local_path(data["folder"]) if data.get("folder") else None)

    def p_knowledge_verify(slug, request, data):
        return net.knowledge.request_verification(slug,int(data.get('id') or 0),list(manager.sessions.values()))

    def p_knowledge(slug, request, data):
        """What agents learned: GET lists it; POST accepts or rejects one ({id, accept}), or sets whether agreed
        knowledge is added by itself ({auto})."""
        if request.method == "POST":
            if "auto" in data:
                net.knowledge.set_auto(slug, bool(data["auto"]))
            else:
                net.knowledge.decide(slug, int(data.get("id") or 0), bool(data.get("accept")))
        return net.knowledge.listing(slug)

    def p_split_rom(slug, request, data):
        return {"message": net.split_rom_by_you(slug, str(data.get("name", "")), str(data.get("at", "")),
                                                str(data.get("new_name", "")))}

    def p_join_rom(slug, request, data):
        names = data.get("functions") or []
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError("functions must be a list of names")
        return {"message": net.join_rom_functions(slug, names, str(data.get("reason") or "operator boundary correction"))}

    def g_generation_metrics(slug,request,data):
        from ..repair_generation import measurements
        return measurements(net)

    routes = {
        "/api/rom-explorer/preview": (p_rom_preview,["POST"]),
        "/api/rom-explorer/loaders": (p_rom_loaders,["GET","POST"]),
        "/api/rom-explorer/discovery": (p_asset_discovery,["GET","POST"]),
        "/api/rom-explorer/inspect": (p_rom_inspect, ["POST"]),
        "/api/rom-explorer": (p_rom_explorer, ["GET", "POST"]),
        "/api/rom-explorer/annotation": (p_rom_annotation, ["POST"]),
        "/api/boundary-evidence": (g_boundary_evidence, ["GET"]),
        "/api/boundary-investigations": (g_boundary_investigations, ["GET"]),
        "/api/boundary-repairs": (p_boundary_repairs, ["GET", "POST"]),
        '/api/repair-generation-metrics': (g_generation_metrics, ['GET']),
        "/api/project": (p_overview, ["GET"]),
        "/api/split-rom": (p_split_rom, ["POST"]),
        "/api/join-rom": (p_join_rom, ["POST"]),
        "/api/knowledge/verify": (p_knowledge_verify, ["POST"]),
        "/api/knowledge": (p_knowledge, ["GET", "POST"]),
        "/api/modelcosts": (lambda s, r, d: manager.costs(s), ["GET"]),
        "/api/save": (p_save, ["POST"]),
        "/api/live": (p_live, ["GET"]),
        "/api/transcript": (p_transcript, ["GET", "POST"]),
        "/api/function": (p_function, ["GET"]),
        "/api/function-notes": (p_function_notes, ["GET"]),
        "/api/diff": (lambda s, r, d: net.function_diff(s, r.query_params.get("name", ""),
                                                        r.query_params.get("which", "latest")), ["GET"]),
        "/api/symbol-names": (p_symbol_names, ["POST"]),
        "/api/compiler": (p_compiler, ["POST"]),
        "/api/compiler/test-default": (p_compiler_default_test, ["POST"]),
        "/api/compiler/cleanup": (p_compiler_cleanup, ["POST"]), "/api/export": (p_export, ["POST"]), "/api/open": (p_open, ["POST"]),
        "/api/reimport": (p_reimport, ["GET", "POST"]), "/api/code-audit": (p_code_audit, ["POST"]),
        "/api/recover-code": (p_recover_code, ["POST"]),
        "/api/data-regions": (p_data_regions, ["GET", "POST"]),
        "/api/agent-reviews": (g_agent_reviews, ["GET"]), "/api/agent-review": (p_agent_review, ["POST"]),
        "/api/code-audit-results": (g_code_audit, ["GET"]), "/api/code-audit-review": (p_code_audit_review, ["POST"]),
        "/api/delete": (p_delete, ["POST"]),
        "/api/try/status": (g_try_status, ["GET"]),
        "/api/try": (p_try, ["POST"]), "/api/release": (p_release, ["POST"]), "/api/claim/handover": (p_claim_handover, ["POST"]), "/api/reopen": (p_reopen, ["POST"]), "/api/setaside": (p_set_aside, ["POST"]),
        "/api/recheck": (p_recheck, ["POST"]),
        "/api/finish-near": (p_finish_near, ["POST"]),
        "/api/reconcile-matches": (p_reconcile_matches, ["POST"]),
        "/api/permuter": (p_permuter, ["POST"]),
        "/api/permuter/pass": (p_deep_sweep, ["GET", "POST"]),
        "/api/permuter/function": (p_deep_permuter, ["POST"]),
        "/api/source-preparation": (p_source_preparation, ["GET", "POST"]),
        "/api/drafts": (p_machine_drafts, ["GET", "POST"]), "/api/autopilot": (p_autopilot, ["POST"]), "/api/instructions": (p_instructions, ["POST"]), "/api/title": (p_title, ["POST"]),
        "/api/cross-game": (lambda s, r, d: {"results": net.twins.cross_game_results(s, all_matches=r.query_params.get("all_matches") == "1"),
                            "suggestions": net.twins.suggestions(s, r.query_params.get("function", ""))
                            if r.query_params.get("function") else []}, ["GET"]),
        "/api/sdk-signatures": (p_sdk, ["POST"]),
        "/api/cross-game/run": (p_cross_run, ["POST"]),
        "/api/regional": (p_regional, ["GET", "POST"]),
        "/api/regional/names": (p_regional_names, ["POST"]),
        "/api/regional/code": (p_regional_code, ["POST"]),
        "/api/cross-game/name": (p_cross_name, ["POST"]),
        "/api/cross-game/functions": (p_project_functions, ["GET"]),
        "/api/claim-order": (p_claim_order, ["POST"]),
        "/api/priority": (p_priority, ["POST"]), "/api/note": (p_note, ["POST"]), "/api/scope": (p_scope, ["POST"]),
        "/api/unanalysed": (p_unanalysed, ["POST"]),
        "/api/suggestion": (p_suggestion, ["POST"]), "/api/boundary": (p_boundary, ["POST"]),
        "/api/agent-results": (lambda s,r,d: net.agent_results(s,r.query_params.get('agent',''),r.query_params.get('kind','matched'),int(r.query_params.get('before',0)),int(r.query_params.get('after',0))), ["GET"]),
        "/api/stats": (lambda s, r, d: net.stats(s), ["GET"]),
        "/api/port-readiness": (g_port_readiness, ["GET"]),
        "/api/data-unit": (p_data_unit, ["GET", "POST"]),
        "/api/data-drafts": (p_data_drafts, ["POST"]),
        "/api/pc-compile": (p_pc_compile, ["POST"]),
        "/api/asset-map": (p_asset_map, ["POST"]),
        "/api/play": (p_play, ["POST"]),
        "/api/rename-calls": (p_rename_calls, ["POST"]),
        "/api/port-identity": (p_port_identity, ["POST"]),
        "/api/asset-extract": (p_asset_extract, ["POST"]),
        "/api/compilermap": (p_compilermap, ["GET", "POST"]),
        "/api/verification": (p_verification, ["POST"]),
    }
    for path, (fn, methods) in routes.items():
        mcp.custom_route(path, methods=methods)(with_project(fn))

    from ..live_sections import Watcher
    watcher = Watcher(net, manager)
    streams = {'open': 0}

    @mcp.custom_route('/api/stream', methods=['GET'])
    async def api_stream(request: Request) -> Response:
        """Server-sent events for the open project's page: "changed" names the live feed's sections that moved
        (feed, claims, overview, sessions, map) with the newest event's id, and the page asks /api/live for only
        those. Checked by the same rules as every GUI read (guard: the window's secret in its user agent, or a
        signed-in browser session; EventSource sends both). Between checks the stream holds no worker thread, only
        an asyncio sleep; it ends when the page goes, the app shuts down, or after STREAM_SECONDS (the browser
        reconnects by itself)."""
        if (bad := guard(request)):
            return bad
        slug = request.query_params.get('slug', '')
        if streams['open'] >= STREAM_LIMIT:
            return JSONResponse({'error': 'Too many live streams are open; the page polls instead.'}, status_code=429)
        check = app.stream_call or call
        try:
            first = await check(watcher.signature, slug)
        except (NetworkError, ValueError) as e:
            return fail(e)

        async def events():
            streams['open'] += 1
            began = last_sent = time.monotonic()
            seen = first
            try:
                yield 'retry: 3000\n\n' + message('hello', {'sections': sorted(k for k in first if k != 'event'),
                                                            'event': first.get('event', 0)})
                while time.monotonic() - began < STREAM_SECONDS and not getattr(net, 'shutting_down', False):
                    await anyio.sleep(STREAM_CHECK_SECONDS)
                    if await request.is_disconnected():
                        return
                    try:
                        now = await check(watcher.signature, slug)
                    except (NetworkError, ValueError) as e:  # (the project was removed or renamed)
                        yield message('gone', {'error': str(e)})
                        return
                    except Exception:  # a busy database: the next check tries again
                        continue
                    moved = changed(seen, now)
                    if moved:
                        seen = now
                        last_sent = time.monotonic()
                        yield message('changed', {'sections': moved, 'event': now.get('event', 0)})
                    elif time.monotonic() - last_sent >= STREAM_PING_SECONDS:
                        last_sent = time.monotonic()
                        yield ': ping\n\n'  # (a comment: keeps proxies and the webview from closing an idle stream)
            finally:
                streams['open'] -= 1

        return StreamingResponse(events(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    app.with_project = with_project
