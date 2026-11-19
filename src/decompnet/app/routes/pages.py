"""The GUI's page, for the app's own window only (its user agent carries the per-launch secret: window.py) — or, under
`decomp serve`, a browser that paired (sessions.py) — its assets, and the health check `decomp` asks to find a running
app and its version.
"""

from __future__ import annotations

import mimetypes
import secrets

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ... import __version__, hostenv
from .. import edition, sessions, window
from ..support import static_file
from . import App

# what the browser tab of 1.0.2 and older shows after updating into this version
OLD_PAGE = ("<!doctype html><meta charset=utf-8><title>decompdecomp</title>"
            "<body style='font:16px/1.5 system-ui;background:#0b0c0e;color:#e8e6e1;padding:3rem;max-width:46rem'>")
OLD_PAGE_LINUX = ("decompdecomp now opens in a window of its own (it just did, if it was updated): you can close this one. "
                  "Next time, use its app icon or run <code>decomp</code>.")
UNPAIRED_PAGE = ("<h1 style='font-size:1.4rem'>Pair this browser with decompdecomp</h1>"
                 "<p>This decompdecomp runs with no window (<code>decomp serve</code>). A browser reaches it through a "
                 "one-time link, which pairs it once.</p>"
                 "<p>Get a link on the server: run <code>decomp serve</code> again in another shell there, and it "
                 "prints a fresh one (where <code>decomp serve</code> shows its screens in a terminal, <b>b</b> there "
                 "shows one too). Open it here; if your end of the SSH tunnel isn't the server's port, change the "
                 "port in the link to yours.</p>"
                 "<p>A link works once, within 15 minutes. After decompdecomp restarts, pair again.</p>")
OLD_PAGE_WINDOWS = ("<p><b>This version of decompdecomp doesn't run on Windows.</b> Windows comes back in 1.0.4. Your projects "
                    "are safe, and update checks are now off, so 1.0.2 won't update itself again.</p>"
                    "<p>Go back to 1.0.2: in PowerShell, run</p><pre style='white-space:pre-wrap'>"
                    + hostenv.WSL_GO_BACK + "</pre><p>then start decompdecomp as before.</p>")


def register(app: App) -> None:
    mcp, secret, build_id, allowed_hosts = app.mcp, app.secret, app.build_id, app.allowed_hosts

    @mcp.custom_route("/", methods=["GET"])
    async def index(request: Request) -> Response:
        paired = app.sessions is not None and app.sessions.paired(request)
        if not paired and request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        if not paired and not secrets.compare_digest(window.request_token(request), secret):
            if app.sessions is not None:  # `decomp serve`: a browser that hasn't paired yet
                return HTMLResponse(OLD_PAGE + UNPAIRED_PAGE, status_code=401)
            return HTMLResponse(OLD_PAGE + (OLD_PAGE_WINDOWS if hostenv.in_old_windows_install() else OLD_PAGE_LINUX), status_code=401)
        return HTMLResponse(static_file("index.html").decode(), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/pair", methods=["GET"])
    async def pair(request: Request) -> Response:
        """`decomp serve`'s one-time link: a code shown once in the server's terminal becomes a session cookie, and
        the browser lands on the GUI. The app with its own window doesn't have sessions, and says so. The link may
        be opened on another port than the app's (the tunnel's end on the user's machine: sessions.py)."""
        host = request.headers.get("host")
        if host not in allowed_hosts and (app.sessions is None or sessions.cookie_for(host) is None):
            return JSONResponse({"error": "bad host"}, status_code=403)
        if app.sessions is None:
            return Response("decompdecomp isn't serving its GUI to browsers here: it has a window of its own", status_code=404)
        try:
            token = app.sessions.spend(request.query_params.get("code", ""))
        except ValueError as e:
            return Response(str(e), status_code=403)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(sessions.cookie_for(host), token, httponly=True, samesite="strict")
        return response

    @mcp.custom_route("/api/pair", methods=["POST"])
    async def new_link(request: Request) -> Response:
        """A fresh one-time code, for `decomp serve` run again beside the running one (cli.py): where its own
        terminal can't show the TUI's `b`, or the first link went unused for too long. Only `decomp`'s own commands
        ask (the secret from server.json), not a paired browser."""
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        if not secrets.compare_digest(window.request_token(request), secret) or request.headers.get("x-decomp") != "1":
            return JSONResponse({"error": "only decompdecomp's own commands ask for a link"}, status_code=401)
        if app.sessions is None:
            return JSONResponse({"error": "this decompdecomp has a window of its own: it doesn't pair browsers"},
                                status_code=404)
        return JSONResponse({"code": app.sessions.new_code()})

    @mcp.custom_route("/assets/{name:path}", methods=["GET"])
    async def asset(request: Request) -> Response:
        host = request.headers.get("host")  # (a paired browser asks through its tunnel's port: sessions.py)
        if host not in allowed_hosts and (app.sessions is None or sessions.cookie_for(host) is None):
            return Response(status_code=403)
        name = request.path_params["name"]
        data = static_file(name)
        if data is None:
            return Response(status_code=404)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return Response(data, media_type=ctype, headers={"Cache-Control": "no-cache"})

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "app": "decomp-wtf", "edition": edition.NAME, "version": __version__,
                             "build": build_id})
