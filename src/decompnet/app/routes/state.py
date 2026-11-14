"""The app as a whole: its state (versions, compilers, agents, settings), the project list, importing a
game, the function map (every function, sent only when it changed), the log and background jobs.
"""

from __future__ import annotations

import json
import platform
import secrets
import shutil
import time
import uuid
from pathlib import Path

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import __version__, consoles, hostenv, linuxvm, toolchains
from ...agents.api import API_PRESETS
from ...forks.project import SCOPES
from ...network import NetworkError
from ...read_cache import ReadPending
from .. import build_info, cleanup, edition, picker, saves, updates, window
from ..engine import COMPILERS, TOOLS
from ..support import MAX_DISC, job_error, static_file
from . import App

DEBUG_SUFFIXES = (".pdb", ".map")  # files sent with a game: debug information its import reads (consoles/pc/debuginfo.py)


def list_signature(projects) -> str:
    """A short fingerprint of the project list as the page would draw it: equal lists give equal signatures."""
    import hashlib
    return hashlib.sha1(json.dumps(projects, sort_keys=True, default=str).encode()).hexdigest()[:16]


def system_name() -> str:
    """The Linux distribution, as it names itself ("Ubuntu 24.04.1 LTS"), for bug reports: problems often depend on it
    (the window's WebKitGTK, the compilers' libraries)."""
    try:
        return platform.freedesktop_os_release().get("PRETTY_NAME", "")
    except OSError:
        return ""


def register(app: App) -> None:
    net, mcp, home, mcp_url = app.net, app.mcp, app.home, app.mcp_url
    manager, jobs, settings, guard, call, body = app.manager, app.jobs, app.settings, app.guard, app.call, app.body
    fail = app.fail

    @mcp.custom_route('/api/match-library', methods=['GET','POST'])
    async def api_match_library(request):
        if bad := guard(request, write=request.method=='POST'):
            return bad
        try:
            if request.method=='POST':
                data=await body(request)
                if data.get('action') in ('name','apply-name'):
                    fid=int(data['id']);name=await call(net.match_library.set_name,fid,data.get('name',''))
                    if data['action']=='apply-name':
                        return JSONResponse({'job':await app.control(lambda: jobs.start('Apply library function name',
                            lambda log:net.match_library.apply_name(fid,name,log)))})
                    return JSONResponse({'name':name})
                return JSONResponse({'job':await app.control(lambda: jobs.start('Index verified match library', net.match_library.backfill))})
            if request.query_params.get('id'):
                return JSONResponse(await call(net.match_library.detail,int(request.query_params['id'])))
            return JSONResponse(await call(net.match_library.summary, str(request.query_params.get('search',''))[:200]))
        except (ValueError,KeyError,NetworkError) as error:return fail(error)



    @mcp.custom_route('/api/settings-jobs', methods=['GET'])
    async def api_settings_jobs(request):
        if bad := guard(request):return bad
        # Discover work before loading statistics: a busy/compacting database must
        # not hide a job which the user can still inspect or stop.
        return JSONResponse({
            'storage':await app.control(lambda: jobs.active(('Compress candidate history','Clean old unsuccessful source'))),
            'headers':await app.control(lambda: jobs.active(('C header cleanup','Database compaction'))),
            'cleanup':await app.control(lambda: jobs.active(('Database transcript cleanup',))),
        })

    @mcp.custom_route('/api/database-cleanup', methods=['GET','POST'])
    async def api_database_cleanup(request):
        database_cleanup, = await app.imports('decompnet.app', ('database_cleanup',))
        if bad := guard(request,write=request.method=='POST'):
            return bad
        try:
            if request.method=='GET':
                return JSONResponse(await call(database_cleanup.summary,net))
            data=await body(request)
            value,_=database_cleanup.options(data)
            if data.get('action')=='preview':
                return JSONResponse(await call(database_cleanup.summary,net,value))
            if data.get('action')=='save':
                return JSONResponse({'options':await call(database_cleanup.save,net,value)})
            if data.get('action')!='start':raise ValueError('Choose preview, save or start for database cleanup.')
            await call(database_cleanup.save,net,value)
            return JSONResponse({'job':await app.control(lambda: database_cleanup.start(net,jobs,value))})
        except (ValueError,NetworkError) as error:return fail(error)

    @mcp.custom_route('/api/database-storage', methods=['GET','POST'])
    async def api_database_storage(request):
        database_storage, = await app.imports('decompnet.app', ('database_storage',))
        if bad := guard(request,write=request.method=='POST'):return bad
        try:
            if request.method=='GET':return JSONResponse(await call(database_storage.stats,net))
            data=await body(request)
            if data.get('action')=='save-repair-evidence':
                save, = await app.imports('decompnet.repair_corpus', ('save',))
                return JSONResponse({'repair_evidence':await call(save,net.store,data)})
            if data.get('action')=='save':return JSONResponse({'retention':await call(database_storage.save,net,data)})
            if data.get('action') not in ('pack','prune'):raise ValueError('Choose pack, prune or save.')
            if data['action']=='prune':database_storage.options(data)
            return JSONResponse({'job':await app.control(lambda: database_storage.start(net,jobs,data['action'],data))})
        except (NetworkError,ValueError) as error:return fail(error)

    @mcp.custom_route('/api/performance', methods=['GET'])
    async def api_performance(request):
        if bad := guard(request):return bad
        snapshot, = await app.imports('decompnet.ui_diagnostics', ('snapshot',))
        return JSONResponse({'slow_operations':await app.control(snapshot)})

    @mcp.custom_route('/api/header-storage', methods=['GET','POST'])
    async def api_header_storage(request):
        header_cleanup, = await app.imports('decompnet.app', ('header_cleanup',))
        if bad := guard(request,write=request.method=='POST'):
            return bad
        try:
            if request.method=='GET':
                stats, = await app.imports('decompnet.header_storage', ('stats',))
                return JSONResponse(await call(stats,net.store))
            data=await body(request)
            if data.get('action')=='preview':
                result,_=await call(header_cleanup.summary,net)
                return JSONResponse(result)
            return JSONResponse({'job':await app.control(lambda: header_cleanup.start(net,jobs,data.get('action')))})
        except (ValueError,NetworkError) as error:return fail(error)

    def state() -> dict:
        tcs = [t for t in toolchains.status() if t["id"] in COMPILERS or t["id"] in TOOLS]
        for t in tcs:
            t["use"] = COMPILERS.get(t["id"]) or TOOLS[t["id"]]
            t["name"] = consoles.compiler_name(t["id"]) if t["id"] in COMPILERS else t["name"]
        return {"version": __version__, "fork": build_info.FORK, "build": app.build_id, "install": updates.installed(), "dev": edition.DEV,
                "platform": toolchains.current_platform(), "system": system_name(), "home": hostenv.shown_path(home.parent),
                "log_file": hostenv.shown_path(home / "logs" / "decomp.log"),
                "linux_vm": ({'ready': linuxvm.status()[0], 'why': linuxvm.status()[1]} if linuxvm.wanted() else None),
                "toolchains": tcs, "agents": manager.detected(), "agents_detecting": manager.detecting(),
                "bridges_removed": [] if settings.data.get("bridges_removed_seen") else cleanup.report(home),
                "account_usage": manager.account_usage(), "api_presets": API_PRESETS, "settings": settings.public(), "mcp_url": mcp_url,
                # every console: names, status, compilers, layout, features (consoles/, Console.describe)
                "consoles": [c.describe() for c in consoles.CONSOLES]}

    def autopilot(slug: str) -> None:
        """Free matches first, in the background, when the user wants that (the default): a game started from
        scratch, on any platform, as soon as it's ready. A public decomp, or one not set up yet, says no."""
        if settings.data.get("autopilot", True) and not getattr(app.jobs, "source_update_pending", False):
            try:
                net.start_autopilot(slug)
            except NetworkError:
                pass

    def project_list() -> list[dict]:
        """The sidebar and the home page: every project with its progress and how many helper agents work on it
        right now. One that can't be read is still listed, with the reason: before, one odd project emptied the
        whole sidebar, or quietly went missing from it."""
        running: dict[str, int] = {}
        for s in manager.sessions.values():
            if s.state in ("starting", "working", "waiting"):
                running[s.slug] = running.get(s.slug, 0) + 1
        out = []
        with net.summary_batch() as projects:
            for p in projects:
                try:
                    out.append(net.summary(p["slug"]) | {"working": running.get(p["slug"], 0)})
                except Exception as e:  # noqa: BLE001
                    jobs.logger.warning("project %s can't be read: %s", p["slug"], e, exc_info=not isinstance(e, NetworkError))
                    progress_error = net.progress.errors.get(p["slug"])
                    if progress_error:
                        out.append({"slug": p["slug"], "title": p["name"] or p["slug"], "working": 0,
                                    "kind": "rom", "platform_short": "?", "functions": 0, "matched": 0,
                                    "progress_error": progress_error})
                    else:
                        out.append({"slug": p["slug"], "title": p["name"] or p["slug"], "working": 0,
                                    "broken": job_error(e, jobs.log_path)})
        from ..variants import visible
        return visible(net, out)

    @mcp.custom_route("/api/projects", methods=["GET"])
    async def api_projects(request: Request) -> Response:
        if (bad := guard(request)):
            return bad
        if request.query_params.get('brief') == '1':
            # (the reader process may answer it for the window with the same function: window_reads.py)
            projects, = await app.imports('decompnet.app.window_reads', ('projects_brief',))
            return JSONResponse(await (app.ui_call or call)(projects,net))
        projects = await (app.fast_call or app.ui_call or call)(project_list)
        if 'since' not in request.query_params:  # (older pages, and every other caller: the plain list)
            return JSONResponse(projects)
        # The sidebar asks every 5 s: with hundreds of projects the list is ~80 KB and its redraw rebuilds every row,
        # though between two asks it rarely changes. It sends the signature it has; an unchanged list answers that.
        sig = list_signature(projects)
        if request.query_params['since'] == sig:
            return JSONResponse({'sig': sig, 'same': True})
        return JSONResponse({'sig': sig, 'projects': projects})

    @mcp.custom_route("/api/state", methods=["GET"])
    async def api_state(request: Request) -> Response:
        return guard(request) or JSONResponse(await (app.ui_call or call)(state))

    @mcp.custom_route('/api/agent-prompts', methods=['GET', 'POST'])
    async def agent_prompts(request: Request) -> Response:
        from dataclasses import replace

        prompt_settings, = await app.imports('decompnet.app', ('prompt_settings',))
        if bad := guard(request, write=request.method == 'POST'):
            return bad
        try:
            data = await body(request) if request.method == 'POST' else request.query_params
            helper = manager.sessions.get(str(data.get('helper', ''))) if data.get('helper') else None
            if data.get('helper') and helper is None:
                raise NetworkError('This agent no longer exists. Refresh the agents list.')
            if request.method == 'POST':
                action = data.get('action', 'save')
                overrides = prompt_settings.validate(data.get('overrides', {}))
                if action not in {'save','preview'}:
                    raise ValueError('Choose save or preview.')
                if action == 'preview':
                    if helper:
                        draft = replace(helper, options={**helper.options,'prompts':overrides}, prompt_config=None)
                        return JSONResponse({'initial':await call(manager.prompt, draft),
                                             'instructions':await app.control(lambda: manager.app_instructions(draft))})
                    templates = (await app.control(prompt_settings.defaults)) | overrides
                    return JSONResponse({'initial':'\n\n'.join(prompt_settings.LABELS[k]+'\n'+templates[k]
                                         for k in prompt_settings.FIELDS if k != 'instructions'),
                                         'instructions':templates['instructions']})
                if helper:
                    await call(manager.set_prompts, helper.id, overrides)
                else:
                    await call(prompt_settings.save_global, net, overrides)
            return JSONResponse(await call(prompt_settings.view, net, helper))
        except (NetworkError, ValueError, KeyError) as error:
            return fail(error)

    @mcp.custom_route('/api/agent-tools', methods=['GET', 'POST'])
    async def agent_tools(request: Request) -> Response:
        tool_policy, tools = await app.imports('decompnet.app', ('tool_policy', 'tools'))
        if bad := guard(request, write=request.method == 'POST'):
            return bad
        try:
            await call(tool_policy.ensure, net)
            data = await body(request) if request.method == 'POST' else request.query_params
            helper = manager.sessions.get(str(data.get('helper', ''))) if data.get('helper') else None
            if data.get('helper') and helper is None:
                raise NetworkError('This agent no longer exists. Refresh the agents list.')
            if request.method == 'POST':
                if data.get('preset'):
                    disabled = tool_policy.preset_disabled(data['preset'])
                    if helper:
                        await call(manager.set_selection, helper.id, {**(helper.options.get('selection') or {}), 'disabled_tools': disabled})
                    else:
                        await call(tool_policy.set_preset, net, data['preset'])
                else:
                    name, enabled = data.get('name'), data.get('enabled')
                    tool_policy.validate([name])
                    if type(enabled) is not bool:
                        raise NetworkError('Tool enabled must be true or false.')
                    if helper:
                        await call(manager.set_tool, helper.id, name, enabled)
                    else:
                        await call(tool_policy.set_global, net, name, enabled)
            definitions = await mcp.list_tools()
            def tool_snapshot():
                blocked = tool_policy.disabled(net)
                own = set((helper.options.get('selection') or {}).get('disabled_tools', [])) if helper else set()
                usage = {r['name']:dict(r) for r in net.store.all('SELECT * FROM agent_tool_usage')}
                tasks = {}
                for row in net.store.all('SELECT * FROM agent_tool_task_usage'):
                    tasks.setdefault(row['name'], {})[row['task']] = row['calls']
                if helper:
                    names = set(tools.project_tools(net, helper.slug, include_disabled=True))
                    selected = [t for t in definitions if t.name in names]
                else:
                    selected = definitions
                return JSONResponse({'tools':[{**t.model_dump(mode='json', by_alias=True),
                    'calls':usage.get(t.name, {}).get('calls', 0), 'task_calls':tasks.get(t.name, {}), 'schema_chars':len(json.dumps(t.model_dump(mode='json', by_alias=True))), 'required':t.name in tool_policy.REQUIRED,
                    'global_disabled':t.name in blocked, 'enabled':t.name not in blocked | own}
                    for t in selected], 'helper':helper.id if helper else None,
                    'presets':[{'id':key, 'label':label, 'tools':sum(t.name not in tool_policy.preset_disabled(key) and (not helper or t.name not in blocked) for t in selected)} for key,(label,_) in tool_policy.PRESETS.items()],
                    'coverage':'Local counts include surviving saved transcript requests plus calls recorded since tool tracking began. Older unrecorded calls cannot be reconstructed. Counts survive app restarts and agent removal.'})
            return await app.read(tool_snapshot)
        except (NetworkError, ValueError, KeyError) as error:
            return fail(error)

    @mcp.custom_route("/api/manual-mcp", methods=["GET", "POST"])
    async def manual_mcp_connection(request: Request) -> Response:
        manual_mcp, prompt_settings, tools = await app.imports('decompnet.app', ('manual_mcp', 'prompt_settings', 'tools'))
        if (bad := guard(request, write=request.method == "POST")):
            return bad
        try:
            if request.method == "POST":
                data = await body(request)
                if data.get('action') == 'revoke':
                    await call(manual_mcp.revoke, net, settings, int(data['id']))
                    return JSONResponse({'ok': True})
                if data.get('action') != 'create':
                    raise ValueError('Choose create or revoke.')
                item = await call(manual_mcp.create, net, settings, str(data.get('slug') or ''))
                return JSONResponse({**item, 'url': mcp_url, 'instructions': (await app.read(prompt_settings.effective, net, {}))['instructions']})
            slug = request.query_params.get('slug')
            def project_tool_names():
                if slug:
                    net.project_row(slug)
                return set(tools.project_tools(net, slug)) if slug else set()
            keep = await (app.ui_call or call)(project_tool_names)
            definitions = await mcp.list_tools()
            return JSONResponse({'url': mcp_url, 'transport': 'Streamable HTTP',
                                 'connections': manual_mcp.connections(settings),
                                 'tools': [t.model_dump(mode='json', by_alias=True) for t in definitions if t.name in keep]})
        except (NetworkError, ValueError, KeyError) as e:
            return fail(e)

    @mcp.custom_route("/api/restore", methods=["POST"])
    async def api_restore(request: Request) -> Response:
        """A project rebuilt from a save (a folder, or a git repository's address) and the user's own copy of the
        game (app/saves.py), in the background."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if not data.get("confirmed"):
            return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
        source, path = str(data.get("source") or "").strip(), str(data.get("path") or "").strip()
        game = await app.call(hostenv.local_path, path) if path else None
        if not source or (game is not None and not await app.call(lambda: game.is_file())):
            return fail(ValueError("Choose the saved project and a valid game file, or leave the game field blank to find a matching local copy."))
        return JSONResponse({"job": await app.control(lambda: jobs.start("restore", lambda log: saves.restore(net, source, game, log=log)))})

    @mcp.custom_route("/api/pick", methods=["POST"])
    async def api_pick(request: Request) -> Response:
        """A folder or file the user picks in the system's own chooser (picker.py): "" if they cancelled."""
        if (bad := guard(request, write=True)):
            return bad
        if app.web_login and not secrets.compare_digest(window.request_token(request), app.secret):
            return fail(ValueError('Enter the file or folder path on the server computer; desktop pickers are disabled in server mode.'))
        data = await body(request)
        try:
            return JSONResponse({"path": await call(picker.choose, str(data.get("kind") or ""), str(data.get("title") or ""))})
        except ValueError as e:
            return fail(e)

    @mcp.custom_route("/api/events", methods=["GET"])
    async def api_events(request: Request) -> Response:
        """The notification centre, for every project: `after=N` what happened since event N (no `after`: where the
        feed starts now), `recent=1` the latest, newest first, for its history panel."""
        if (bad := guard(request)):
            return bad
        q = request.query_params
        if q.get("recent"):
            cleared = int(settings.data.get("notes_cleared_to") or 0)
            return JSONResponse({"events": [e for e in await (app.ui_call or call)(net.recent_events) if e["id"] > cleared]})
        after = q.get("after", "")
        return JSONResponse(await (app.ui_call or call)(net.events_after, int(after) if after.isdigit() else None))

    def split_extras(game: Path, size: int, extras: str | None) -> None:
        """The files dropped with a game, sent after it in one upload (`extras`: [[name, bytes], ...]): a PC game's
        program database or linker map, cut off the end and put beside it, where its import looks for them."""
        if not extras:
            return
        try:
            wanted = [(Path(str(name)).name, int(n)) for name, n in json.loads(extras)]
        except (ValueError, TypeError):
            raise ValueError("the files sent with the game aren't listed right") from None
        if any(not name.lower().endswith(DEBUG_SUFFIXES) or name == game.name or n < 0 for name, n in wanted) or \
                sum(n for _, n in wanted) >= size:
            raise ValueError("only a PC game's .pdb or .map can be sent with it")
        with open(game, "r+b") as f:
            f.seek(size - sum(n for _, n in wanted))
            for name, n in wanted:
                (game.parent / name).write_bytes(f.read(n))
            f.truncate(size - sum(n for _, n in wanted))

    @mcp.custom_route("/api/import", methods=["POST"])
    async def api_import(request: Request) -> Response:
        """A game the user owns: the demo, an upload streamed to disk (discs are gigabytes), or a local path."""
        if (bad := guard(request, write=True)):
            return bad
        try:
            await app.control(lambda: jobs.check_start())
        except ValueError as e:
            return fail(e, 503)
        q = request.query_params
        toolchain = q.get("toolchain") or None
        scope = q.get("scope") or None  # what the project covers, where its console sets library code aside
        try:  # the answers to the console's import_options (JSON, an object of strings)
            options = json.loads(q.get("options") or "{}")
            if not isinstance(options, dict) or not all(isinstance(v, str) for v in options.values()):
                raise ValueError
        except ValueError:
            return fail(ValueError("options must be a JSON object of strings"))
        # every compiler of the game's console first (the proprietary ones with `accept`), so its test tries them all
        all_compilers, accept = q.get("all_compilers") == "1", q.get("accept") == "1"
        if scope not in (None, *SCOPES):
            return fail(ValueError(f"a project covers the game's own code or the whole program, not {scope!r}"))
        if q.get("demo") == "1":
            game: bytes | Path | None = static_file("demo/demo-quest-64.z64")
            if game is None:
                return fail(ValueError("the demo ROM is missing from this install"))
            name = "demo-quest-64.z64"
        else:
            if q.get("confirmed") != "1":
                return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
            if q.get("path"):
                game = hostenv.local_path(q["path"])
                if not await app.call(lambda: game.exists()):  # (a folder too: a PC game's, whose own DLLs come along)
                    return fail(ValueError(f"no file or folder at {game}"))
                name = game.name
            else:
                name = Path(q.get("name") or "game.bin").name
                upload_dir = home / "uploads" / uuid.uuid4().hex[:8]  # keeps the file's own name
                await app.call(lambda: upload_dir.mkdir(parents=True, exist_ok=True))
                game = upload_dir / name
                size = 0
                async with await anyio.open_file(game, "wb") as f:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_DISC:
                            await f.aclose()
                            await app.call(lambda: game.unlink(missing_ok=True))
                            return fail(ValueError("that file is larger than any game disc"))
                        await f.write(chunk)
                if size == 0:
                    await app.call(lambda: game.unlink(missing_ok=True))
                    return fail(ValueError("the file was empty"))
                try:
                    await call(split_extras, game, size, q.get("extras"))
                except ValueError as e:
                    await app.call(lambda: shutil.rmtree(game.parent, ignore_errors=True))
                    return fail(e)
        uploaded = isinstance(game, Path) and game.parent.parent == home / "uploads"

        def work(log):
            try:
                slug = net.import_rom(game, toolchain=toolchain, confirmed=True, name=name, log=log, scope=scope,
                                     options=options)
            finally:
                if uploaded:
                    shutil.rmtree(game.parent, ignore_errors=True)
            if all_compilers:
                net.install_compilers(slug, accept, retest=toolchain is None, log=log)
            settings.data["last_project"] = slug
            settings.save()
            autopilot(slug)
            return {"slug": slug}
        try:
            return JSONResponse({"job": await app.control(lambda: jobs.start("import", work))})
        except ValueError as e:
            # A source update can queue while a disc is uploading, before it becomes a job.
            if uploaded:
                await app.call(lambda: shutil.rmtree(game.parent, ignore_errors=True))
            return fail(e, 503)

    COLUMN_KEYS = ("vram", "cc", "ccx")
    GZIP_FROM_BYTES = 4096  # smaller answers aren't worth compressing

    from ..view_snapshots import MapPayload, Snapshots
    encoded_maps = Snapshots(limit=8*1024*1024)

    def packed(request: Request, data: dict) -> Response:
        gzip_ok = 'gzip' in request.headers.get('accept-encoding', '')
        def encode():
            raw = json.dumps(data, separators=(",", ":")).encode()
            compressed = len(raw) > GZIP_FROM_BYTES and gzip_ok
            if compressed:
                import gzip
                raw = gzip.compress(raw, 1)
            return raw, compressed
        # Unique tokens avoid retaining entire map inputs or reusing another object's cached bytes.
        raw, compressed = (encoded_maps.get((data.wire_key,gzip_ok),encode,weight=lambda value:len(value[0]))
                           if isinstance(data,MapPayload) else encode())
        return Response(raw, media_type='application/json', headers={
            'Vary':'Accept-Encoding', **({'Content-Encoding':'gzip'} if compressed else {})})

    @mcp.custom_route('/api/project-loading', methods=['GET'])
    async def api_project_loading(request: Request) -> Response:
        if (bad := guard(request)):
            return bad
        # In-memory activity only: never start a sync or queue behind expensive UI reads.
        return JSONResponse({'verification':await app.control(lambda: net.progress.status(request.query_params.get('slug','')))})

    @mcp.custom_route("/api/map", methods=["GET"])
    async def api_map(request: Request) -> Response:
        """Every function, for the map and the table. `since`: the signature the GUI has; when nothing changed, the
        answer is just that (a big game's map is megabytes, and the GUI asks every few seconds). Rows go as arrays
        under `keys`, gzipped when the browser takes it."""
        if (bad := guard(request)):
            return bad
        slug = request.query_params.get("slug", "")

        def build() -> dict:
            from ..view_snapshots import map_payload
            return map_payload(net, slug, request.query_params.get('since'), request.query_params.get('delta') == '1', quick=request.query_params.get('quick') == '1')

        try:
            def snapshot():
                with net.progress.view():
                    return build()
            response, = await app.imports('decompnet.request_timing', ('response',))
            queued = time.monotonic()
            def respond():
                return response(snapshot, queued, lambda data: packed(request, data), '/api/map')
            reader = app.fast_call if request.query_params.get('quick') == '1' else app.ui_call
            result = await (reader or call)(respond)
        except ReadPending as e:
            # A bounded single-flight wait is temporary contention, not an ASGI crash.
            saves.log.warning('Function map metadata busy; retry the request')
            return JSONResponse({'error': str(e)}, status_code=503, headers={'Retry-After': '1'})
        except (NetworkError, ValueError) as e:
            return fail(e)
        return result

    @mcp.custom_route("/api/mapcolumns", methods=["GET"])
    async def api_map_columns(request: Request) -> Response:
        """The compiler map's colours for the ROM map (MIPS games), asked for after the map is drawn."""
        if (bad := guard(request)):
            return bad
        try:
            def snapshot():
                with net.progress.view():
                    return net.map_columns(request.query_params.get("slug", ""))
            response, = await app.imports('decompnet.request_timing', ('response',))
            queued = time.monotonic()
            def respond():
                return response(snapshot, queued, lambda rows: packed(request, {"keys": COLUMN_KEYS, "rows": rows}), '/api/mapcolumns')
            result = await (app.ui_call or call)(respond)
        except (NetworkError, ValueError) as e:
            return fail(e)
        return result

    @mcp.custom_route("/api/log", methods=["GET"])
    async def api_log(request: Request) -> Response:
        """The end of decompdecomp's log, to read in the app or attach to a bug report."""
        if (bad := guard(request)):
            return bad
        path = home / "logs" / "decomp.log"
        def read_log():
            try:
                with open(path, "rb") as f:
                    f.seek(max(0, path.stat().st_size - (512 << 10)))
                    text = f.read().decode("utf-8", "replace")
            except OSError:
                text = "(nothing logged yet)"
            return Response(text, media_type="text/plain; charset=utf-8",
                            headers={"Content-Disposition": 'inline; filename="decomp.log"'})

        return await app.call(read_log)

    from ..work_monitor import Monitor
    monitor = Monitor(net, jobs, manager)

    @mcp.custom_route("/api/background-work", methods=["GET", "POST"])
    async def background_work(request: Request) -> Response:
        if (bad := guard(request, write=request.method == "POST")):
            return bad
        try:
            if request.method == "POST":
                data = await body(request)
                operation = monitor.resume if data.get('action') == 'resume' else monitor.stop
                return JSONResponse(await call(operation, str(data.get('kind','')), str(data.get('key',''))))
            snapshot = await (app.ui_call or call)(monitor.snapshot)
            return JSONResponse(snapshot | {'worker_setting':settings.data.get('worker_threads',0),
                                             'maintenance_cpu_percent':settings.data.get('maintenance_cpu_percent',0)})
        except NetworkError as error:
            return fail(error)

    @mcp.custom_route("/api/job", methods=["GET"])
    async def api_job(request: Request) -> Response:
        if (bad := guard(request)):
            return bad
        job = jobs.jobs.get(request.query_params.get("id", ""))
        return await app.control(lambda: JSONResponse(job)) if job else fail(ValueError("no such job"), 404)

    @mcp.custom_route("/api/job/cancel", methods=["POST"])
    async def api_cancel_job(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        return JSONResponse({"cancelled": await app.control(lambda: jobs.cancel(str(data.get("id") or "")))})

    app.autopilot = autopilot
