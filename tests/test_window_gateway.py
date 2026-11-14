"""The gateway's window listener (gateway/src/window.rs): every request reaches the app as the window sent it,
allow-listed reads go to the reader first, and streams stream."""
import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from decompnet.app import mcp_gateway

pytestmark = pytest.mark.skipif(mcp_gateway.built() is None,
                                reason="the gateway isn't built (cargo build --release in gateway/)")

UA = "Mozilla/5.0 decompdecomp-window/the-secret"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, routes):
        self.port = free_port()
        self.seen: list[dict] = []
        self.server = uvicorn.Server(uvicorn.Config(Starlette(routes=routes), host="127.0.0.1", port=self.port,
                                                    log_config=None))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.02)
        return self

    def __exit__(self, *_):
        self.server.should_exit = True
        self.thread.join(5)


def fake_app():
    """The app: echoes what it was sent, streams events, redirects to its own address."""
    server = None

    async def echo(request: Request):
        body = await request.body()
        server.seen.append({"method": request.method, "path": request.url.path})
        return JSONResponse({"from": "app", "method": request.method, "path": request.url.path,
                             "query": str(request.url.query), "host": request.headers.get("host"),
                             "user_agent": request.headers.get("user-agent"), "cookie": request.headers.get("cookie"),
                             "origin": request.headers.get("origin"), "length": len(body),
                             "body_end": body[-16:].decode("latin-1")},
                            headers={"set-cookie": "s=1; HttpOnly; Path=/"})

    async def stream(request: Request):
        async def events():
            yield b"data: one\n\n"
            await asyncio.sleep(1.5)
            yield b"data: two\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")

    async def moved(request: Request):
        return RedirectResponse(f"http://127.0.0.1:{server.port}/api/echo?moved=1", status_code=307)

    server = Server([Route("/api/echo", echo, methods=["GET", "POST"]),
                     Route("/api/stats", echo, methods=["GET", "POST"]),
                     Route("/api/projects", echo, methods=["GET"]),
                     Route("/api/stream", stream), Route("/moved", moved)])
    return server


def fake_reader(delay=0.0):
    """The reader: answers /api/stats, declines when asked to."""
    server = None

    async def stats(request: Request):
        server.seen.append({"method": request.method, "path": request.url.path, "host": request.headers.get("host"),
                            "user_agent": request.headers.get("user-agent")})
        if request.query_params.get("decline"):
            return Response(status_code=421)
        if delay:
            await asyncio.sleep(delay)
        return JSONResponse({"from": "reader"}, headers={"x-decomp-reader": "answered"})

    server = Server([Route("/api/stats", stats, methods=["GET", "POST"]),
                     Route("/api/projects", stats, methods=["GET"])])
    return server


@pytest.fixture
def setup(tmp_path):
    started = []

    def start(app, reader, limit=2.0):
        gw = mcp_gateway.Gateway(mcp_gateway.built(), f"http://127.0.0.1:{app.port}/mcp", tmp_path / "gateway.log",
                                 window={"upstream": f"http://127.0.0.1:{app.port}",
                                         "reader": f"http://127.0.0.1:{reader.port}" if reader else None,
                                         "reads": ["/api/stats", "/api/projects?brief=1"], "reader_limit": limit})
        gw.start()
        started.append(gw)
        assert gw.window_port and gw.window_url() == f"http://127.0.0.1:{gw.window_port}/"
        return gw
    yield start
    for gw in started:
        gw.stop()


def get(gw, path, **kwargs):
    headers = {"User-Agent": UA} | kwargs.pop("headers", {})
    return httpx.get(f"http://127.0.0.1:{gw.window_port}{path}", headers=headers, timeout=30, **kwargs)


def test_forwards_requests_to_the_app_as_the_window_sent_them(setup):
    with fake_app() as app:
        gw = setup(app, None)
        reply = get(gw, "/api/echo?x=1&y=two%20words", headers={"Cookie": "decomp-session=abc",
                                                                  "Origin": f"http://127.0.0.1:{gw.window_port}"})
        seen = reply.json()
        assert reply.status_code == 200 and seen["from"] == "app"
        assert seen["host"] == f"127.0.0.1:{app.port}"  # (the app checks its own host)
        assert seen["user_agent"] == UA and seen["cookie"] == "decomp-session=abc"
        assert seen["origin"] == f"http://127.0.0.1:{app.port}"
        assert seen["query"] == "x=1&y=two%20words"
        assert reply.headers["set-cookie"] == "s=1; HttpOnly; Path=/"
        # localhost stays localhost; another page's origin goes on as it came, for the app to refuse
        other = get(gw, "/api/echo", headers={"Host": f"localhost:{gw.window_port}", "Origin": "http://evil.example"})
        assert other.json()["host"] == f"localhost:{app.port}" and other.json()["origin"] == "http://evil.example"


def test_refuses_other_hosts(setup):
    with fake_app() as app:
        gw = setup(app, None)
        reply = get(gw, "/api/echo", headers={"Host": f"evil.example:{gw.window_port}"})
        assert reply.status_code == 403 and app.seen == []


def test_bodies_reach_only_the_app_and_never_twice(setup):
    with fake_app() as app, fake_reader() as reader:
        gw = setup(app, reader)
        body = b"x" * (5 * 1024 * 1024) + b"end-of-the-body!"
        reply = httpx.post(f"http://127.0.0.1:{gw.window_port}/api/stats?slug=game", content=body,
                           headers={"User-Agent": UA, "X-Decomp": "1"}, timeout=60)
        seen = reply.json()
        assert seen["from"] == "app" and seen["method"] == "POST"
        assert seen["length"] == len(body) and seen["body_end"] == "end-of-the-body!"
        assert reader.seen == [] and [s["method"] for s in app.seen] == ["POST"]


def test_reads_go_to_the_reader_first(setup):
    with fake_app() as app, fake_reader() as reader:
        gw = setup(app, reader)
        reply = get(gw, "/api/stats?slug=game")
        assert reply.json() == {"from": "reader"} and "x-decomp-reader" not in reply.headers
        assert app.seen == [] and reader.seen[0]["host"] == f"127.0.0.1:{app.port}"
        assert reader.seen[0]["user_agent"] == UA
        # declined: the app answers; not on the list: straight to the app
        assert get(gw, "/api/stats?slug=game&decline=1").json()["from"] == "app"
        assert get(gw, "/api/projects?since=abc").json()["from"] == "app"
        assert get(gw, "/api/projects?brief=1").json() == {"from": "reader"}
        assert len(reader.seen) == 3
        health = httpx.get(f"http://127.0.0.1:{gw.port}/health", timeout=5).json()["window"]
        assert health["read_by_reader"] == 2 and health["passed_to_app"] == 1


def test_reads_go_to_the_app_when_the_reader_is_down_or_slow(setup):
    with fake_app() as app:
        gw = setup(app, Server([]))  # (a port nothing listens on)
        assert get(gw, "/api/stats?slug=game").json()["from"] == "app"
    with fake_app() as app, fake_reader(delay=5) as reader:
        gw = setup(app, reader, limit=0.5)
        began = time.monotonic()
        assert get(gw, "/api/stats?slug=game").json()["from"] == "app"
        assert time.monotonic() - began < 4


def test_streams_events_as_they_come(setup):
    with fake_app() as app:
        gw = setup(app, None)
        began = time.monotonic()
        with httpx.stream("GET", f"http://127.0.0.1:{gw.window_port}/api/stream", headers={"User-Agent": UA},
                          timeout=30) as reply:
            assert reply.headers["content-type"].startswith("text/event-stream")
            chunks = reply.iter_bytes()
            first = next(chunks)
            assert b"data: one" in first and time.monotonic() - began < 1.2
            rest = b"".join(chunks)
        assert b"data: two" in first + rest


def test_redirects_stay_on_the_windows_origin(setup):
    with fake_app() as app:
        gw = setup(app, None)
        reply = get(gw, "/moved", follow_redirects=False)
        assert reply.status_code == 307
        assert reply.headers["location"] == f"http://127.0.0.1:{gw.window_port}/api/echo?moved=1"


def test_the_app_down_is_an_error_the_page_can_show(setup):
    app = fake_app()  # never started
    gw = setup(app, None)
    reply = get(gw, "/api/echo")
    assert reply.status_code == 502 and "isn't answering" in json.loads(reply.text)["error"]


def test_keeps_its_ports_across_restarts(setup):
    with fake_app() as app:
        gw = setup(app, None)
        port, window_port = gw.port, gw.window_port
        gw.process.kill()
        deadline = time.time() + 15
        while time.time() < deadline:
            if gw.restarts and gw.process.poll() is None:
                break
            time.sleep(0.1)
        assert gw.port == port and gw.window_port == window_port
        assert get(gw, "/api/echo").json()["from"] == "app"
