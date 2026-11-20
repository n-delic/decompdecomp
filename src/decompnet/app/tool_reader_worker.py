"""The read-only tool and window handlers loaded by the tool reader subprocess."""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

from decompnet.app.tool_reader import (
    DECLINED,
    DRAIN_SECONDS,
    STALE_SECONDS,
    WINDOW_ANSWERED,
    WINDOW_ENV,
    allowed,
    drop_stale_caches,
    reader_progress,
)

log = logging.getLogger("decompdecomp")
WINDOW_THREADS = 4


class Declined(Exception):
    pass


class Service:
    """The child's engine and what the app last said about its helpers."""

    def __init__(self, net, emit):
        self.net, self.emit = net, emit
        self.routes: dict = {}
        self.window_auth: dict | None = None  # {"secret", "port"}: the app window's (Reader.window)
        self.revisions: dict[int, tuple] = {}
        self.revisions_lock = threading.Lock()

    def project_for(self, agent_id: int) -> str:
        routes = self.routes
        if not routes or time.time() - float(routes.get("at", 0)) > STALE_SECONDS:
            raise Declined("no current routes from the app")
        if agent_id in set(routes.get("app_only", ())):
            raise Declined("the app keeps state for this helper")
        key = str(agent_id)
        slug = (routes.get("assignments", {}).get(key) or self.net.pairing_project(agent_id)
                or routes.get("manual", {}).get(key))
        if not slug:
            raise Declined("no project: the app explains")
        if slug in set(routes.get("busy", ())):
            raise Declined("the app is rechecking, importing or sharing this project")
        return slug

    def fresh(self, slug: str) -> None:
        drop_stale_caches(self.net, slug, self.revisions, self.revisions_lock)

    def window(self, method: str, path: str, query, headers) -> object:
        """The app's answer to one of the window's reads (window_reads.py), or Declined. The checks are the app's
        guard (server.build) for a GET: the host, the window's secret, and nothing it would refuse."""
        import secrets as secrets_module

        from decompnet.app import window as app_window
        from decompnet.app import window_reads
        given = self.window_auth
        if not given:
            raise Declined("not answering the window")
        if method != "GET":
            raise Declined("only reads")
        routes = self.routes
        if not routes or time.time() - float(routes.get("at", 0)) > STALE_SECONDS:
            raise Declined("no current routes from the app")
        port = int(given["port"])
        if headers.get("host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise Declined("not the app's host: the app refuses it")
        token = app_window.token_of(headers.get("user-agent", ""))
        if not token or not secrets_module.compare_digest(token, str(given["secret"])):
            raise Declined("not the window: the app checks browser sign-ins")
        if headers.get("origin"):
            raise Declined("an Origin: the app checks it")
        if routes.get("preparing"):
            raise Declined("the app is preparing an update")
        if path == "/api/projects":
            if query.get("brief") != "1":
                raise Declined("the full project list is the app's")
            with self.net.progress.view():
                return window_reads.projects_brief(self.net)
        fn = window_reads.PROJECT_ROUTES.get(path)
        if fn is None:
            raise Declined(f"{path} is answered by the app")
        slug = query.get("slug", "")
        if not slug or slug in set(routes.get("busy", ())):
            raise Declined("the app is rechecking, importing or sharing this project")
        self.fresh(slug)
        with self.net.progress.view():
            result = fn(self.net, slug, query)
        self.emit({"viewed": slug})
        return result

    def call(self, headers: dict, name: str, args: dict) -> str:
        from decompnet.app import tools
        from decompnet.app.tool_policy import visible
        from decompnet.network import NetworkError
        from decompnet.read_cache import ReadPending
        if not allowed(name, args):
            raise Declined(f"{name} is answered by the app")
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        try:
            agent = self.net.authenticate(auth.removeprefix("Bearer ").strip() or None)
        except NetworkError:
            raise Declined("not a helper this process knows") from None
        slug = self.project_for(agent.id)
        started = time.monotonic()
        call_id = uuid.uuid4().hex
        report = {"agent_id": agent.id, "agent_name": agent.name, "slug": slug, "name": name, "arguments": args,
                  "call_id": call_id}
        try:
            self.fresh(slug)
            if name not in visible(self.net, slug, list(tools.HANDLERS), agent):
                raise Declined("disabled for this helper: the app explains")
            named = tools._named(self.net, agent, slug, args, name)
            result = tools.HANDLERS[name](self.net, agent, slug, **named)
            if name == "get_function" and args.get("function") != named.get("function"):
                result = (f"Resolved read {str(args.get('function'))[:200]!r} to {named['function']}. This does not "
                          "rename or create a function.\n" + result)
        except (NetworkError, ReadPending) as error:
            report.update(error=str(error), timings={"dispatch_ms": round((time.monotonic() - started) * 1000, 2)})
            self.emit({"call": report})
            raise
        report.update(content=result, timings={"dispatch_ms": round((time.monotonic() - started) * 1000, 2)})
        self.emit({"call": report})
        return result


def window_routes(mcp, service: Service) -> None:
    """The window's reads (window_reads.py) at their own paths, encoded as the app encodes them (JSONResponse).
    Anything this process doesn't answer is a 421 without WINDOW_ANSWERED, and the gateway asks the app."""
    import anyio
    from starlette.responses import JSONResponse, Response

    from decompnet.app import window_reads
    limiter = anyio.CapacityLimiter(WINDOW_THREADS)

    async def read(request):
        def answer():
            return service.window(request.method, request.url.path, request.query_params, request.headers)
        try:
            result = await anyio.to_thread.run_sync(answer, limiter=limiter)
            return JSONResponse(result, headers={WINDOW_ANSWERED: "answered"})
        except Declined as why:
            log.info("declined %s: %s", request.url.path, why)
        except Exception:  # the app answers, with its own error if it has one
            log.exception("declined %s after an error", request.url.path)
        return Response(status_code=421)

    for path in {*window_reads.PROJECT_ROUTES, "/api/projects"}:
        mcp.custom_route(path, methods=["GET"])(read)


def child(db_path: str, workspace: str, port: int) -> None:
    import uvicorn

    from decompnet import store as storage
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s [tool-reader pid=%(process)d %(threadName)s] %(message)s")
    for chatty in ("mcp", "httpx", "uvicorn"):  # a line per request otherwise
        logging.getLogger(chatty).setLevel(logging.WARNING)
    storage.ATTACH_ONLY = True
    os.environ.setdefault("DECOMP_REPAIR_ANALYSIS", "off")
    from decompnet.app import tools
    from decompnet.app.engine import RomNetwork
    from decompnet.app.workers import lower_priority
    from decompnet.network import NetworkError
    from decompnet.read_cache import ReadPending
    lower_priority()  # the app's own work comes first
    net = RomNetwork(db_path, Path(workspace))
    reader_progress(net)
    output = threading.Lock()

    def emit(message):
        with output:
            sys.stdout.write(json.dumps(message, default=str) + "\n")
            sys.stdout.flush()

    service = Service(net, emit)
    window_auth = os.environ.pop(WINDOW_ENV, "")
    service.window_auth = json.loads(window_auth) if window_auth else None

    def listen():
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("drain"):
                server.should_exit = True
            if isinstance(message.get("routes"), dict):
                routes = message["routes"]
                routes["app_only"] = {int(a) for a in routes.get("app_only", ())}
                net._agent_tool_tasks = {int(a): t for a, t in routes.get("tasks", {}).items()}
                service.routes = routes
        os._exit(0)  # the app closed our standard input: it ended

    from mcp.server.mcpserver.exceptions import ToolError

    def run(ctx, name, args):
        headers = dict(ctx.headers or {})
        try:
            return service.call(headers, name, args)
        except Declined as why:
            log.info("declined %s: %s", name, why)
            raise ToolError(DECLINED) from None
        except (NetworkError, ReadPending) as error:
            raise ToolError(str(error)) from None
        except Exception:
            log.exception("declined %s after an error", name)
            raise ToolError(DECLINED) from None

    def no_agent(ctx):
        raise ToolError(DECLINED)  # (unused: `reader` answers every call)

    mcp = tools.build_mcp(net, no_agent, reader=run)
    if service.window_auth:
        window_routes(mcp, service)
    from mcp.server.transport_security import TransportSecuritySettings
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=[f"127.0.0.1:{port}", f"localhost:{port}"],
                                         allowed_origins=[])
    app = mcp.streamable_http_app(transport_security=security, stateless_http=True, json_response=True,
                                  host="127.0.0.1")
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    # Keep-alive outlasts the gateway's 30 s connection pool (cli.KEEP_ALIVE).
    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                with output:
                    sys.stdout.write(f"LISTENING {port}\n")
                    sys.stdout.flush()

    server = Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="on",
                                  timeout_keep_alive=75, timeout_graceful_shutdown=DRAIN_SECONDS))
    threading.Thread(target=listen, daemon=True, name="tool-reader-routes").start()
    server.run(sockets=[sock])


if __name__ == "__main__":
    child(sys.argv[1], sys.argv[2], int(sys.argv[3]))
