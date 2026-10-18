"""The local decompdecomp server: the GUI, its API, and the MCP endpoint agents connect to.

It listens on 127.0.0.1 only. The GUI needs a per-launch secret (handed to the browser in the URL the
app opens, then kept in a same-site cookie); agents need their own bearer token. Host headers are
checked on every request, so web pages can't reach it through DNS rebinding.

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

from ..network import NetworkError
from . import agents, chat, collab, routes, tools, updates, window
from .engine import RomNetwork
from .routes import forks, life, pages, projects, rooms, setup, state
from .support import COOKIE, Jobs, Settings, _session_options


def build(net: RomNetwork, home: Path, port: int, secret: str):
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    build_id = (updates.installed().get("commit") or "")[:12]  # a branch install: the commit it came from
    manager = agents.Manager(net, home, mcp_url)
    jobs = Jobs(home / "logs" / "decomp.log")

    def window_closed():
        """The app window was closed: quit, unless helper agents or a job are still working (they carry on in the
        background, and `decomp` opens the window again)."""
        if window.IN_TRAY:
            jobs.logger.info("window closed; decompdecomp stays in the tray")
            return
        busy = manager.running() or any(j["state"] == "running" for j in jobs.jobs.values())
        if busy:
            jobs.logger.info("window closed; agents or a job keep working in the background")
            return
        jobs.logger.info("window closed; quitting")
        manager.shutdown()
        os._exit(0)
    window.WINDOW = window.Window(window.profile_dir(home), window_closed)
    settings = Settings(home / "settings.json")
    net.auto_compiler = bool(settings.data.get("auto_compiler"))
    manager.budget = lambda: float(settings.data.get("budget_usd") or 0)
    # the project each agent you talk to opened stays theirs after a restart: they used to fall back to the project the
    # GUI showed last (opencode sessions on GTA San Andreas found its functions missing from another game)
    for agent_id, slug in (settings.data.get("agent_projects") or {}).items():
        manager.assignments.setdefault(int(agent_id), slug)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def agent_of(ctx: Context):
        headers = ctx.headers or {}
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        agent = net.authenticate(auth.removeprefix("Bearer ").strip() or None)
        slug = manager.project_of(agent.id) or settings.data.get("last_project")
        if not slug:
            raise NetworkError("no project is open in decompdecomp; open a ROM in the app first")
        return agent, slug

    threading.Thread(target=collab.local_addresses, daemon=True).start()  # `tailscale status` can take seconds
    # rooms this computer belongs to reconnect (members keep their place; their agents wait while the host is away)
    net.restore_links()

    def options_now(kind: str, options: dict) -> dict | None:
        """A resumed helper's options as they are now: an API helper's endpoint as saved, with its key (None: the
        endpoint was removed since)."""
        if kind != "api":
            return options
        saved = settings.endpoint(str(options.get("endpoint") or "")) if options.get("endpoint") else (
            next((e for e in settings.endpoints() if e["base_url"] == options.get("base_url")), None))
        if saved is None and options.get("endpoint"):
            return None
        return options | (_session_options(saved) if saved is not None else {})
    manager.options_now = options_now
    manager.resume()  # helpers that were working when the app last stopped carry on (their records say which)

    mcp = tools.build_mcp(net, agent_of)
    chat.register(mcp, net, manager, jobs, settings, port, secret)

    # Each agent sees the tools for its job: the agent you talk to sees everything; a helper only the work tools
    # for its project's kind (fewer tool definitions in every request, nothing it shouldn't call).
    from mcp_types import ListToolsResult, PaginatedRequestParams

    async def scoped_tools(ctx, params):
        everything = await mcp.list_tools()
        auth = (getattr(ctx.request, "headers", None) or {}).get("authorization", "")
        try:
            agent = net.authenticate(auth.removeprefix("Bearer ").strip() or None)
        except NetworkError:
            return ListToolsResult(tools=everything)
        if net.is_chat(agent):
            return ListToolsResult(tools=everything)
        slug = manager.project_of(agent.id) or settings.data.get("last_project")
        keep = set(tools.tool_names(bool(slug and net.fork_slug(slug))))
        return ListToolsResult(tools=[t for t in everything if t.name in keep])

    mcp._lowlevel_server.add_request_handler("tools/list", PaginatedRequestParams, scoped_tools)

    def guard(request: Request, write: bool = False) -> Response | None:
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        if not secrets.compare_digest(request.cookies.get(COOKIE, ""), secret):
            return JSONResponse({"error": "open decompdecomp from the app or the `decomp` command"}, status_code=401)
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

    def before_restart() -> None:  # what quitting does: helpers stop now and resume in the new version
        manager.shutdown()

    # the app's parts add their routes (routes/__init__.py says which is where); forks come after state and projects,
    # whose autopilot and with_project they use
    app = routes.App(net, mcp, home, port, secret, mcp_url, build_id, manager, jobs, settings, allowed_hosts,
                     guard, body, call, fail, updater=updates.Updater(before_restart, port, secret))
    for part in (pages, state, projects, forks, setup, life, rooms):
        part.register(app)

    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=sorted(allowed_hosts),
                                         allowed_origins=[f"http://{h}" for h in sorted(allowed_hosts)])
    return mcp, manager, security
