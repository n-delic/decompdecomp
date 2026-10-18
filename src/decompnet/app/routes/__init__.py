"""The local server's routes, one module per part of the app. server.build() makes an App and hands it to each
module's register(), which adds that part's routes to the MCP server's web app:

- pages:    the GUI's page (for the app's own window only), its assets and the health check
- state:    the app's state, the project list, imports, the function map, the log and jobs
- projects: everything under a project (/api/project, /api/function, /api/compiler, /api/export, ...)
- forks:    public decomps forked by their address, and projects started from scratch on that build
- setup:    compilers and tools, helper agents and their sign-in, settings
- life:     updates, the window, quitting
- rooms:    decompiling together (collab.py)

Routes check the request with app.guard (the window's secret, the host, and a header for writes) before anything
else, run blocking work with app.call (a thread), and answer errors with app.fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .. import agents, updates
    from ..engine import RomNetwork
    from ..support import Jobs, Settings


@dataclass
class App:
    """What every route module gets from server.build()."""
    net: RomNetwork  # the engine: projects, functions, the judge
    mcp: MCPServer  # routes are added to its web app (mcp.custom_route)
    home: Path  # decompdecomp's own folder (~/Decomp/.decomp)
    port: int
    secret: str  # the GUI's per-launch secret
    mcp_url: str
    build_id: str  # a branch install: the commit it came from
    manager: agents.Manager  # helper agents
    jobs: Jobs  # background work the GUI follows
    settings: Settings
    allowed_hosts: set[str]
    guard: Callable[..., Any]  # guard(request, write=False): None, or the response that refuses the request
    body: Callable[..., Any]  # await body(request): the JSON object sent, or {}
    call: Callable[..., Any]  # await call(fn, *args): fn run in a thread
    fail: Callable[..., Any]  # fail(error, code=400): the error as JSON
    updater: updates.Updater | None = None
    busy: Callable[[], bool] | None = None  # whether helpers or a job are working
    quit_app: Callable[[], None] | None = None  # quit: the window, every helper, the server
    # what one part of the app lends another (set by the module that defines it)
    autopilot: Callable[[str], None] | None = None  # state: start autopilot on a project
    with_project: Callable[..., Any] | None = None  # projects: a project route from fn(slug, request, data)
