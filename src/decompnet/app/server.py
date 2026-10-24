"""The local decompdecomp server: the GUI, its API, and the MCP endpoint agents connect to.

It listens on 127.0.0.1 only. The GUI answers only the app's own window, whose user agent carries the per-launch
secret (window.py); helper agents need their own bearer token. Host headers are checked on every request, so web
pages can't reach it through DNS rebinding.

build() sets up what the routes share (the engine, helper agents, jobs, settings, the request checks) and hands it to
the modules in routes/, one per part of the app; what they share beyond that is in support.py.
"""

from __future__ import annotations

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
from .. import hostenv
from ..network import NetworkError
from . import agents, collab, routes, saves, tools, updates, window
from .engine import RomNetwork
from .routes import forks, life, pages, projects, rooms, setup, state
from .support import Jobs, Settings, session_options


def build(net: RomNetwork, home: Path, port: int, secret: str):
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    build_id = (updates.installed().get("commit") or "")[:12]  # a branch install: the commit it came from
    manager = agents.Manager(net, home, mcp_url)
    jobs = Jobs(home / "logs" / "decomp.log")

    def busy() -> bool:
        return bool(manager.running()) or any(j["state"] == "running" for j in jobs.jobs.values())

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
        window.close_app()
        manager.shutdown()
        os._exit(0)
    window.WINDOW = window.Window(home, f"http://127.0.0.1:{port}/", secret, window_ended)
    settings = Settings(home / "settings.json")
    if hostenv.in_old_windows_install() and (settings.data.get("auto_update") or settings.data.get("check_updates", True)):
        # updated here from 1.0.2 on Windows: the way back is 1.0.2, which shares these settings and mustn't update
        # itself into this version again (README: Coming from an older version)
        settings.data.update(auto_update=False, check_updates=False)
        settings.save()
    net.auto_compiler = bool(settings.data.get("auto_compiler"))
    manager.budget = lambda: float(settings.data.get("budget_usd") or 0)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def agent_of(ctx: Context):
        headers = ctx.headers or {}
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        agent = net.authenticate(auth.removeprefix("Bearer ").strip() or None)
        slug = manager.project_of(agent.id)  # only helpers the app starts have tokens, each for one project
        if not slug:
            raise NetworkError("this agent isn't one of this app's helpers: start helpers from the app")
        return agent, slug

    threading.Thread(target=collab.local_addresses, daemon=True).start()  # `tailscale status` can take seconds
    # rooms this computer belongs to reconnect (members keep their place; their agents wait while the host is away)
    net.restore_links()

    def consoles_start() -> None:  # what each console does for its projects at start (Console.on_start)
        for row in net.store.all("SELECT slug, config FROM projects"):
            try:
                if net.is_rom(row):
                    net.rangechecks.reopen(row["slug"])
                net.console(row).on_start(net, row["slug"], log=jobs.logger.info)
            except Exception:  # never stands between the user and the app
                jobs.logger.exception("%s's console work at start failed", row["slug"])
    threading.Thread(target=consoles_start, daemon=True, name="consoles-start").start()
    saves.Autosave(net).start()  # follow work and contributions in each ROM project's authoritative progress folder

    def options_now(kind: str, options: dict) -> dict | None:
        """A resumed helper's options as they are now: an API helper's endpoint as saved, with its key (None: the
        endpoint was removed since)."""
        if not kinds.get(kind).uses_endpoints:
            return options
        saved = settings.endpoint(str(options.get("endpoint") or "")) if options.get("endpoint") else (
            next((e for e in settings.endpoints() if e["base_url"] == options.get("base_url")), None))
        if saved is None and options.get("endpoint"):
            return None
        return options | (session_options(saved) if saved is not None else {})
    manager.options_now = options_now
    manager.resume()  # helpers that were working when the app last stopped carry on (their records say which)

    mcp = tools.build_mcp(net, agent_of)

    # Each helper sees only the work tools for its project's kind (fewer tool definitions in every request, nothing
    # it shouldn't call).
    from mcp_types import ListToolsResult, PaginatedRequestParams

    async def scoped_tools(ctx, params):
        everything = await mcp.list_tools()
        auth = (getattr(ctx.request, "headers", None) or {}).get("authorization", "")
        try:
            agent = net.authenticate(auth.removeprefix("Bearer ").strip() or None)
        except NetworkError:
            return ListToolsResult(tools=[])
        slug = manager.project_of(agent.id)
        keep = set(tools.tool_names(bool(slug and net.fork_slug(slug))))
        return ListToolsResult(tools=[t for t in everything if t.name in keep])

    mcp._lowlevel_server.add_request_handler("tools/list", PaginatedRequestParams, scoped_tools)

    def guard(request: Request, write: bool = False) -> Response | None:
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        if not secrets.compare_digest(window.request_token(request), secret):
            return JSONResponse({"error": "decompdecomp only answers its own window: open it with `decomp`"},
                                status_code=401)
        if write and request.headers.get("x-decomp") != "1":
            return JSONResponse({"error": "missing header"}, status_code=403)
        return None

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    async def call(fn, *args):
        return await anyio.to_thread.run_sync(fn, *args)

    def fail(e: Exception, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=code)

    def before_restart() -> None:  # helpers stop now and resume in the new version; the window closes with the process
        manager.shutdown()

    # the app's parts add their routes (routes/__init__.py says which is where); forks come after state and projects,
    # whose autopilot and with_project they use
    app = routes.App(net, mcp, home, port, secret, mcp_url, build_id, manager, jobs, settings, allowed_hosts,
                     guard, body, call, fail, updater=updates.Updater(before_restart, port, secret),
                     busy=busy, quit_app=quit_app)
    for part in (pages, state, projects, forks, setup, life, rooms):
        part.register(app)

    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=sorted(allowed_hosts),
                                         allowed_origins=[f"http://{h}" for h in sorted(allowed_hosts)])
    return mcp, manager, security, quit_app
