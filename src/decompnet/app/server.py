"""The local decompdecomp server: the GUI, its API, and the MCP endpoint agents connect to.

It listens on 127.0.0.1 only. The GUI answers only the app's own window, whose user agent carries the per-launch
secret (window.py), or an authenticated browser session in opt-in `decomp serve` mode; managed and manual agents need their own project-scoped bearer token. Host headers are checked on every request, so web
pages can't reach it through DNS rebinding.

build() sets up what the routes share (the engine, helper agents, jobs, settings, the request checks) and hands it to
the modules in routes/, one per part of the app; what they share beyond that is in support.py.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

import anyio
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import agents as kinds
from .. import hostenv, wine
from ..network import NetworkError
from . import agents, collab, routes, saves, tools, updates, window, workers
from .engine import RomNetwork
from .routes import browser_bridge, forks, life, pages, projects, repair_feedback, repository_sync, rooms, setup, state
from .support import Jobs, Settings, session_options


def build(net: RomNetwork, home: Path, port: int, secret: str, *, headless: bool = False):
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    from . import build_info
    build_id = (build_info.commit() or updates.installed().get("commit") or "")[:12]  # a branch install: the commit it came from
    manager = agents.Manager(net, home, mcp_url)
    jobs = Jobs(home / "logs" / "decomp.log")
    from .startup import StartupQueue
    startup = StartupQueue(jobs.logger)
    from .deep_sweep import Sweeps
    net.deep_sweeps = Sweeps(net, jobs)
    from .source_queue import Queue
    net.source_queue = Queue(net, jobs)

    def busy() -> bool:
        return bool(getattr(jobs, "source_update_pending", False)) or bool(manager.running()) or any(j["state"] == "running" for j in jobs.jobs.values())

    def window_ended():
        """The window ended without the app asking (the page found nothing working, or it crashed): quit, unless
        helpers or a job are working; they carry on, and the tray icon or `decomp` opens the window again."""
        if busy():
            jobs.logger.info("window closed; helpers or a job keep working in the background")
            return
        jobs.logger.info("window closed; quitting")
        quit_app()

    def quit_app() -> None:
        """Quit means quit: the window, every helper with everything it started, then the server."""
        startup.close()
        window.close_app()
        net.deep_sweeps.shutdown()
        manager.shutdown()
        flush_progress()
        wine.stop()  # (its wine server would otherwise stay up to 10 minutes after the last compile)
        os._exit(0)

    def flush_progress() -> None:
        """Try every final save and leave a clear log entry when the user's progress folder is blocked."""
        try:
            for slug, error in net.progress.flush().items():
                jobs.logger.error("could not save %s before exit: %s", slug, error)
        except Exception:  # shutdown must continue even if the progress store itself is damaged
            jobs.logger.exception("could not flush progress before exit")
    window.WINDOW = window.Window(home, f"http://127.0.0.1:{port}/", secret, window_ended)
    settings = Settings(home / "settings.json")
    if hostenv.in_old_windows_install() and (settings.data.get("auto_update") or settings.data.get("check_updates", True)):
        # updated here from 1.0.2 on Windows: the way back is 1.0.2, which shares these settings and mustn't update
        # itself into this version again (README: Coming from an older version)
        settings.data.update(auto_update=False, check_updates=False)
        settings.save()
    net.cascades.configure(settings.data.get('reuse_auto', True), settings.data.get('reuse_seconds', 120),
                           settings.data.get('reuse_attempts', 128), settings.data.get('iterative_enabled', False),
                           settings.data.get('iterative_seconds', 120), settings.data.get('iterative_attempts', 128),
                           settings.data.get('iterative_threads', 0))
    net.permuter.configure(settings.data)
    workers.configure(settings.data.get("worker_threads"))
    net.auto_compiler = bool(settings.data.get("auto_compiler"))
    from .agent_local_pass import configure as configure_local_repairs
    configure_local_repairs(net,settings.data)
    manager.budget = lambda: float(settings.data.get("budget_usd") or 0)
    from .web_login import WebLogin, remote_origin
    remote_url = remote_origin(settings.data.get('web_remote_url', ''))
    net.web_login = WebLogin(port, remote_url) if headless or settings.data.get('web_interface_enabled', False) else None
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if remote_url:
        from urllib.parse import urlsplit
        allowed_hosts.add(urlsplit(remote_url).netloc)

    from . import manual_mcp

    def project_for(agent_id) -> str | None:
        """The one project an agent may work: a helper the app started (mapped while it runs), a connection the user
        created in Agents & models, or an outside agent the user approved there for one project (pairing)."""
        return (manager.project_of(agent_id) or net.pairing_project(agent_id)
                or manual_mcp.project_for(net, settings, agent_id))

    def agent_of(ctx: Context):
        headers = ctx.headers or {}
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        agent = net.authenticate(auth.removeprefix("Bearer ").strip() or None)
        slug = project_for(agent.id)
        if not slug:
            raise NetworkError("This MCP connection is no longer assigned to a project. Create or pair one in "
                               "Agents & models.")
        return agent, slug

    threading.Thread(target=collab.local_addresses, daemon=True).start()  # `tailscale status` can take seconds
    # rooms this computer belongs to reconnect (members keep their place; their agents wait while the host is away)
    net.restore_links()

    from .routes import pairing
    from .sdk_names import name_functions
    def console_start(slug):
        row = net.project_row(slug)
        cfg = json.loads(row['config'])
        if cfg.get('kind') == 'fork' and not cfg.get('platform'):
            row = net.forks.get(slug)[0]
        if net.is_rom(row):
            net.rangechecks.reopen(slug)
        net.console(row).on_start(net, slug, log=jobs.logger.info)
    startup_projects = list(net.store.all('SELECT slug FROM projects'))
    startup_projects.sort(key=lambda r: r['slug'] != settings.data.get('last_project'))
    startup.add("Save existing matches to the local library", net.match_library.backfill)
    for row in startup_projects:
        slug = row['slug']
        startup.add(f'Console startup {slug}', lambda slug=slug: console_start(slug))
    for row in startup_projects:
        slug = row['slug']
        startup.add(f'SDK names {slug}', lambda slug=slug: name_functions(net, slug))
    startup.start()
    saves.Autosave(net).start()  # follow work and contributions in each ROM project's authoritative progress folder

    def options_now(kind: str, options: dict) -> dict | None:
        """A resumed helper's options as they are now: an API helper's endpoint as saved, with its key (None: the
        endpoint was removed since). A model it was started on stays: the endpoint's default would record its costs
        against another model."""
        if not kinds.get(kind).uses_endpoints:
            return options
        saved = settings.endpoint(str(options.get("endpoint") or "")) if options.get("endpoint") else (
            next((e for e in settings.endpoints() if e["base_url"] == options.get("base_url")), None))
        if saved is None and options.get("endpoint"):
            return None
        now = options | (session_options(saved) if saved is not None else {})
        if options.get("picked_model"):
            now["model"] = str(options["picked_model"])
        return now
    manager.options_now = options_now
    def resume_work():
        # Project synchronization can rejudge thousands of saved functions. It must not block the HTTP server
        # and window from starting, while the existing project locks still protect the restored work.
        try:
            net.source_queue.resume()
        except Exception:
            jobs.logger.exception("Could not resume local source recovery")
        try:
            net.deep_sweeps.resume()
        except Exception:
            jobs.logger.exception("Could not resume deep passes")
        try:
            manager.resume()
        except Exception:
            jobs.logger.exception("Could not resume helpers")

    threading.Thread(target=resume_work, daemon=True, name="resume-work").start()

    mcp = tools.build_mcp(net, agent_of, observe=manager.record_agent)

    mcp_metadata_limiter = anyio.CapacityLimiter(2)

    async def personalized_instructions(ctx, call_next):
        result = await call_next(ctx)
        if ctx.method not in {'initialize', 'server/discover'}:
            return result
        auth = (getattr(ctx.request, 'headers', None) or {}).get('authorization', '')
        def personalized():
            try:
                agent=net.authenticate(auth.removeprefix('Bearer ').strip() or None)
                slug=project_for(agent.id)
                return manager.instructions_for(agent,slug) if slug else None
            except NetworkError:
                return None
        instructions=await anyio.to_thread.run_sync(personalized,limiter=mcp_metadata_limiter)
        if instructions is not None:
            return ({**result,'instructions':instructions} if isinstance(result,dict)
                    else result.model_copy(update={'instructions':instructions}))
        return result

    mcp._lowlevel_server.middleware.append(personalized_instructions)

    # Each helper sees only the work tools for its project's kind (fewer tool definitions in every request, nothing
    # it shouldn't call).
    from mcp_types import ListToolsResult, PaginatedRequestParams

    async def scoped_tools(ctx, params):
        everything = await mcp.list_tools()
        auth = (getattr(ctx.request, "headers", None) or {}).get("authorization", "")
        checking = (getattr(ctx.request, 'headers', None) or {}).get('x-decomp-connection-check') == '1'
        def select():
            try:
                agent=net.authenticate(auth.removeprefix('Bearer ').strip() or None)
            except NetworkError:
                return ListToolsResult(tools=[])
            slug=project_for(agent.id)
            if not slug:
                return ListToolsResult(tools=[])
            keep=set(tools.project_tools(net,slug,agent))
            selected=[t for t in everything if t.name in keep]
            manager.record_agent(agent,slug,'connection_check_tools' if checking else 'tool_definitions',
                                 [t.model_dump(mode='json') for t in selected])
            return ListToolsResult(tools=selected)
        return await anyio.to_thread.run_sync(select,limiter=mcp_metadata_limiter)

    mcp._lowlevel_server.add_request_handler("tools/list", PaginatedRequestParams, scoped_tools)

    def guard(request: Request, write: bool = False) -> Response | None:
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        if not secrets.compare_digest(window.request_token(request), secret) and not (net.web_login and net.web_login.authorized(request)):
            return JSONResponse({"error": "Sign in at /login to use this server." if net.web_login else "decompdecomp only answers its own window: open it with `decomp`"},
                                status_code=401)
        if net.web_login and request.headers.get('origin') and not net.web_login.same_origin(request):
            return JSONResponse({'error':'Use this server’s own browser page.'}, status_code=403)
        if write and request.headers.get("x-decomp") != "1":
            return JSONResponse({"error": "missing header"}, status_code=403)
        if getattr(jobs, 'source_update_preparing', False) and request.url.path not in (
                '/api/update/runtime', '/api/close', '/api/log'):
            return JSONResponse({'error':'Preparing the source runtime. Wait for restart; no new operations can start.'}, status_code=503)
        if request.url.path in ('/api/project', '/api/function', '/api/map', '/api/compilermap'):
            startup.touch()
        return None

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    ui_limiter = anyio.CapacityLimiter(4)
    fast_limiter = anyio.CapacityLimiter(2)

    async def ui_call(fn, *args):
        def snapshot():
            with net.progress.view():
                return fn(*args)
        return await anyio.to_thread.run_sync(snapshot, limiter=ui_limiter)

    async def fast_call(fn, *args):
        # Sidebar and live counters must not wait behind four expensive function/diff requests.
        def snapshot():
            with net.progress.view():
                return fn(*args)
        return await anyio.to_thread.run_sync(snapshot, limiter=fast_limiter)

    async def call(fn, *args):
        return await anyio.to_thread.run_sync(fn, *args)

    def fail(e: Exception, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=code)

    def before_restart() -> None:  # helpers stop now and resume in the new version; the window closes with the process
        startup.close()
        net.deep_sweeps.shutdown()
        manager.shutdown()
        flush_progress()

    # the app's parts add their routes (routes/__init__.py says which is where); forks come after state and projects,
    # whose autopilot and with_project they use
    app = routes.App(net, mcp, home, port, secret, mcp_url, build_id, manager, jobs, settings, allowed_hosts,
                     guard, body, call, fail, ui_call=ui_call, fast_call=fast_call, updater=updates.Updater(before_restart, port, secret),
                     busy=busy, quit_app=quit_app, web_login=net.web_login, headless=headless)
    from . import web_login
    web_login.register(app)
    for part in (pages, state, projects, repair_feedback, repository_sync, forks, setup, life, rooms, pairing, browser_bridge):
        part.register(app)

    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=sorted(allowed_hosts),
                                         allowed_origins=[f"http://{h}" for h in sorted(allowed_hosts)])
    return mcp, manager, security, quit_app
