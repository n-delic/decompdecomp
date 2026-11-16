"""The TUI's one way to its app: GET requests over HTTP on 127.0.0.1, carrying the per-launch secret in the user
agent the way the window and `decomp`'s own commands do (window.py, cli._call). The first version only reads.

READS lists every field the TUI takes from each answer. tests/test_tui.py asks the real app for each route here and
checks the fields are there with these types: a change to the API that the TUI depends on fails there, with the
route and field named, and a change to any other route doesn't concern the TUI at all. A screen that reads a new
field adds it here.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .. import window

TIMEOUT = 5  # a poll that hangs this long is given up; the screens ask again on their next poll

# route -> {field path: JSON type}; "rows[]" a list's items, "keys" the names /api/map's rows go by
READS: dict[str, dict[str, str]] = {
    "/api/projects": {"sig": "string", "same": "boolean", "projects[].slug": "string", "projects[].title": "string",
                      "projects[].platform_short": "string", "projects[].matched": "integer",
                      "projects[].functions": "integer", "projects[].working": "integer",
                      "projects[].bytes": "integer", "projects[].matched_bytes": "integer",
                      "projects[].set_aside": "integer", "projects[].set_aside_bytes": "integer",
                      "projects[].progress_error": "string", "projects[].broken": "string"},
    "/api/map": {"sig": "string", "same": "boolean", "keys": "array", "rows[].name": "string",
                 "rows[].status": "string", "rows[].polished": "boolean", "rows[].best": "integer",
                 "rows[].attempts": "integer", "rows[].agent": "string", "rows[].insns": "integer",
                 "rows[].vram": "integer", "rows[].unit": "string", "rows[].size": "integer",
                 "rows[].matched_by": "string", "rows[].matched_at": "number"},
    "/api/live": {"cut": "boolean", "claims[].name": "string", "claims[].agent": "string",
                  "claims[].best_score": "integer", "claims[].attempts": "integer", "claims[].claimed_at": "number",
                  "attempts[].id": "integer", "attempts[].function": "string", "attempts[].agent": "string",
                  "attempts[].score": "integer", "attempts[].compiled": "integer", "events[].id": "integer",
                  "overview.title": "string", "overview.functions": "integer", "overview.matched": "integer",
                  "overview.set_aside": "integer"},
    "/api/stats": {"agents[].agent": "string", "agents[].compiles": "integer", "agents[].matched": "integer",
                   "matches_per_hour": "array"},
    "/api/events": {"last": "integer", "events[].id": "integer", "events[].kind": "string",
                    "events[].detail": "string", "events[].project": "string"},
    "/api/log": {"": "text"},
}
# what /api/map's status can be, as the TUI paints it (mapgrid.status_of): a new one must be taught to it
MAP_STATUSES = {"matched", "working", "asm", "open"}


class Client:
    """Talks to the app the TUI runs beside (cli.run started both)."""

    def __init__(self, port: int, secret: str):
        self.base = f"http://127.0.0.1:{port}"
        self.agent = f"{window.TOKEN_AGENT}{secret}"

    def _ask(self, path: str, params: dict) -> bytes | None:
        ask = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(self.base + path + ("?" + ask if ask else ""), headers={"User-Agent": self.agent})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except OSError:  # (an HTTP error status, too: the app said no, or isn't there)
            return None

    def get(self, path: str, **params) -> object | None:
        """One GET and its JSON answer; None when the app didn't answer, or not in time (restarting, or gone: the
        screens say so and try again on their next poll)."""
        raw = self._ask(path, params)
        try:
            return None if raw is None else json.loads(raw or b"null")
        except ValueError:
            return None

    def text(self, path: str, **params) -> str | None:
        """One GET whose answer is text (/api/log), or None."""
        raw = self._ask(path, params)
        return None if raw is None else raw.decode("utf-8", "replace")
