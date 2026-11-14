"""The window's read-only requests another process may answer: the reader (app/tool_reader.py), behind the
gateway's window listener (gateway/src/window.rs, docs/MCP_GATEWAY.md "The window's pages").

The app's own routes call these same functions (routes/projects.py, routes/state.py), and the reader encodes
their result the same way (JSONResponse), so its answers are the app's by construction. A route belongs here only
when its answer comes from the databases alone: nothing the app keeps in memory (running jobs, helper sessions, the
permuter, saved-progress synchronization, rooms, live streams) and nothing it writes. The reader declines anything
else and the app answers.

Chosen:
- `/api/stats` (the overview's leaderboard, asked every few seconds): project and attempt rows, and the attempt
  history summary, a cache keyed by the database's history revision.
- `/api/knowledge` GET (what agents learned): knowledge rows and the project's setting.
- `/api/attempt-history` (the function drawer's candidate list): attempts and agent names. Its function lookup uses
  the engine's symbol caches, which the reader drops when the database's revisions move (drop_stale_caches).
- `/api/projects?brief=1` (project names for the pickers): the catalog's rows and each console's title rule.

Left with the app, for now:
- `/api/map`: rooms' remote claims and running function searches (memory), a per-process cache that makes its
  `delta` answers, and derived snapshots it writes to the database.
- `/api/mapcolumns`: may build the compiler map.
- `/api/live`: helper sessions (manager.snapshot). `/api/stream`: a live event stream.
- `/api/state`, plain `/api/projects`: jobs, helpers, saved-progress errors, updates.
- `/api/project`: saves the last open project and warms briefs. `/api/function`: running deep searches.
- `/api/port-readiness`: the Play build's job and server.
"""
from __future__ import annotations

# The project routes: fn(net, slug, query) -> what the app sends as JSON. with_project (routes/projects.py) gives
# the app's: slug from the query, inside net.progress.view().
PROJECT_ROUTES: dict = {}


def project_route(path):
    def register(fn):
        PROJECT_ROUTES[path] = fn
        return fn
    return register


@project_route("/api/stats")
def stats(net, slug, query):
    return net.stats(slug)


@project_route("/api/knowledge")
def knowledge(net, slug, query):
    return net.knowledge.listing(slug)


@project_route("/api/attempt-history")
def attempt_history(net, slug, query):
    from .database_storage import history
    return history(net, slug, query.get('function', ''), int(query.get('before', 0)),
                   int(query['attempt']) if query.get('attempt') else None)


def projects_brief(net):
    """`/api/projects?brief=1`: every project's slug and title."""
    from .project_labels import projects
    return projects(net)


# What the gateway sends the reader first (--window-reads): a path, with the query parameters it must carry.
GATEWAY_READS = (*PROJECT_ROUTES, "/api/projects?brief=1")
