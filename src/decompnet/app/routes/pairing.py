"""Agents outside the app (a script, another tool) asking to work one project: a pairing. The agent posts its name
and a secret of its own, the user sees the request in the window and approves it for one project (or refuses), and
the agent reads the answer — the token — with its secret. The window's own routes sit behind guard(); these two ask
nothing, because the agent asking has nothing yet: they say only what a pending decision is, and their answers only
leave the machine with the requester's secret in hand."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...network import NetworkError

if TYPE_CHECKING:
    from . import App


def register(app: App) -> None:
    net, body, call, fail = app.net, app.body, app.call, app.fail

    def local(request: Request) -> Response | None:
        """The pairing routes answer 127.0.0.1 only, like everything else; no window secret: the requester hasn't
        got one (that is the point)."""
        if request.headers.get("host") not in app.allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        return None

    @app.mcp.custom_route("/api/pair/request", methods=["POST"])
    async def api_pair_request(request: Request) -> Response:
        if (bad := local(request)):
            return bad
        data = await body(request)
        try:
            return JSONResponse({"id": await call(net.pair_request, str(data.get("name") or ""),
                                                  str(data.get("secret") or ""))})
        except (NetworkError, ValueError, OSError) as e:
            return fail(e)

    @app.mcp.custom_route("/api/pair/status", methods=["POST"])
    async def api_pair_status(request: Request) -> Response:
        if (bad := local(request)):
            return bad
        data = await body(request)
        try:
            return JSONResponse(await call(net.pair_answer, str(data.get("id") or ""),
                                           str(data.get("secret") or "")))
        except (NetworkError, ValueError, OSError) as e:
            return fail(e)

    @app.mcp.custom_route("/api/pair/requests", methods=["GET"])
    async def api_pair_requests(request: Request) -> Response:
        if (bad := app.guard(request)):
            return bad
        return JSONResponse({"requests": await call(net.pairings)})

    @app.mcp.custom_route("/api/pair/decide", methods=["POST"])
    async def api_pair_decide(request: Request) -> Response:
        if (bad := app.guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            return JSONResponse(await call(net.pair_decide, str(data.get("id") or ""),
                                           bool(data.get("approve")), data.get("project")))
        except (NetworkError, ValueError, OSError) as e:
            return fail(e)
