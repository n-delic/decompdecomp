"""The MCP gateway answers every request in time, and a repeated tool call never runs twice."""
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from decompnet.app import mcp_gateway

pytestmark = pytest.mark.skipif(mcp_gateway.built() is None,
                                reason="the gateway isn't built (cargo build --release in gateway/)")

AUTH = {"Authorization": "Bearer test-token", "Accept": "application/json, text/event-stream"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class App:
    """A stateless MCP server with a fast tool and a slow one, standing in for decompdecomp's /mcp."""

    def __init__(self, port):
        from mcp.server.mcpserver import MCPServer
        self.port, self.runs = port, {"slow": 0}
        server = MCPServer(name="decomp")

        @server.tool()
        def fast(text: str) -> str:
            return "fast:" + text

        @server.tool()
        def slow(seconds: float) -> str:
            self.runs["slow"] += 1
            time.sleep(seconds)
            return f"slow done after {seconds}"

        app = server.streamable_http_app(stateless_http=True, json_response=True)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
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


def rpc(url, method, params=None, id=1, timeout=30):
    body = {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}
    response = httpx.post(url, json=body, headers=AUTH, timeout=timeout)
    response.raise_for_status()
    return response.json()


def text(reply):
    return reply["result"]["content"][0]["text"]


@pytest.fixture
def gateway(tmp_path):
    port = free_port()
    gw = mcp_gateway.Gateway(mcp_gateway.built(), f"http://127.0.0.1:{port}/mcp", tmp_path / "gateway.log",
                             call_deadline=1.0, meta_deadline=1.5)
    gw.start()
    gw.app_port = port
    yield gw
    gw.stop()


def test_passes_requests_through(gateway):
    with App(gateway.app_port):
        init = rpc(gateway.url, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                               "clientInfo": {"name": "t", "version": "1"}})
        assert init["result"]["serverInfo"]["name"] == "decomp"
        names = {t["name"] for t in rpc(gateway.url, "tools/list", id=2)["result"]["tools"]}
        assert {"fast", "slow"} <= names
        assert "fast:hi" in text(rpc(gateway.url, "tools/call", {"name": "fast", "arguments": {"text": "hi"}}, id=3))
        assert rpc(gateway.url, "ping", id=4)["result"] == {}
        notification = httpx.post(gateway.url, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                                   headers=AUTH, timeout=5)
        assert notification.status_code == 202


def test_slow_call_answers_in_time_and_never_runs_twice(gateway):
    with App(gateway.app_port) as app:
        call = {"name": "slow", "arguments": {"seconds": 2.5}, "_meta": {"progressToken": 1}}
        began = time.monotonic()
        first = rpc(gateway.url, "tools/call", call, id=10)
        assert time.monotonic() - began < 2.0
        assert "still running" in text(first) and first["id"] == 10
        # The repeat waits for the same run. Arguments in another order and the new progress token clients put on
        # every request make no difference.
        result = None
        for attempt in range(6):
            reply = rpc(gateway.url, "tools/call", {"arguments": {"seconds": 2.5}, "name": "slow",
                                                    "_meta": {"progressToken": 2 + attempt}}, id=11 + attempt)
            if "slow done" in text(reply):
                result = reply
                break
        assert result is not None and result["id"] >= 11
        assert app.runs["slow"] == 1
        # Delivered: the same call again is a new run.
        rpc(gateway.url, "tools/call", {"name": "slow", "arguments": {"seconds": 0.1}}, id=20)
        assert app.runs["slow"] == 2


def test_answers_while_the_app_is_down(gateway):
    with App(gateway.app_port):
        live = rpc(gateway.url, "tools/list", id=1)
        rpc(gateway.url, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "t", "version": "1"}}, id=2)
    began = time.monotonic()
    cached = rpc(gateway.url, "tools/list", id=3)
    assert cached["result"] == live["result"]
    assert time.monotonic() - began < 5
    down = rpc(gateway.url, "tools/call", {"name": "fast", "arguments": {"text": "x"}}, id=4)
    assert down["result"]["isError"] and ("still running" in text(down) or "isn't running" in text(down))


def test_a_new_connection_gets_the_tools_while_the_app_is_down_even_after_a_restart(tmp_path):
    """A helper starting while the app was busy or restarting got "decompdecomp is busy or starting" for its tool
    list: answers were kept per token, and a new token had none. Now the last list is kept (on disk too) and given to
    a new connection; the app still judges every call."""
    port, state = free_port(), tmp_path / "answers.json"
    gw = mcp_gateway.Gateway(mcp_gateway.built(), f"http://127.0.0.1:{port}/mcp", tmp_path / "gateway.log",
                             call_deadline=1.0, meta_deadline=1.5, state=state)
    gw.start()
    try:
        with App(port):
            live = rpc(gw.url, "tools/list", id=1)
        assert state.is_file()
        other = {**AUTH, "Authorization": "Bearer another-token"}
        reply = httpx.post(gw.url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                           headers=other, timeout=30).json()
        assert reply["result"] == live["result"], "a new connection gets the last tool list, not busy"
    finally:
        gw.stop()
    again = mcp_gateway.Gateway(mcp_gateway.built(), f"http://127.0.0.1:{port}/mcp", tmp_path / "gateway2.log",
                                call_deadline=1.0, meta_deadline=1.5, state=state)
    again.start()
    try:  # a restarted gateway, the app still down: the list comes from the state file
        third = {**AUTH, "Authorization": "Bearer third-token"}
        reply = httpx.post(again.url, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
                           headers=third, timeout=30).json()
        assert reply["result"] == live["result"]
    finally:
        again.stop()


def test_restarts_on_the_same_port(gateway):
    port = gateway.port
    gateway.process.kill()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).json()["ok"]:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        pytest.fail("the gateway didn't come back")
    assert gateway.restarts == 1 and gateway.port == port


def test_missing_program_means_the_app_endpoint(monkeypatch):
    monkeypatch.setenv("DECOMP_MCP_GATEWAY", "0")
    assert mcp_gateway.program() is None
    assert mcp_gateway.start(1234) is None


class Reader(App):
    """A stand-in for the read-only tool process: it answers `fast` and declines everything else."""

    def __init__(self, port):
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError

        from decompnet.app.tool_reader import DECLINED
        self.port, self.runs = port, {"fast": 0, "slow": 0}
        server = MCPServer(name="decomp")

        @server.tool()
        def fast(text: str) -> str:
            self.runs["fast"] += 1
            return "reader:" + text

        @server.tool()
        def slow(seconds: float) -> str:
            self.runs["slow"] += 1
            raise ToolError(DECLINED)

        app = server.streamable_http_app(stateless_http=True, json_response=True)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
        self.thread = threading.Thread(target=self.server.run, daemon=True)


@pytest.fixture
def with_reader(tmp_path):
    port, reader_port = free_port(), free_port()
    gw = mcp_gateway.Gateway(mcp_gateway.built(), f"http://127.0.0.1:{port}/mcp", tmp_path / "gateway.log",
                             call_deadline=5.0, meta_deadline=1.5, reader=f"http://127.0.0.1:{reader_port}/mcp",
                             read_tools={"fast", "slow"})
    gw.start()
    gw.app_port, gw.reader_port = port, reader_port
    yield gw
    gw.stop()


def test_read_only_tools_go_to_the_reader_first(with_reader):
    with App(with_reader.app_port) as app, Reader(with_reader.reader_port) as reader:
        assert text(rpc(with_reader.url, "tools/call", {"name": "fast", "arguments": {"text": "a"}})) == "reader:a"
        # Declined: the app answers, once.
        assert "slow done" in text(rpc(with_reader.url, "tools/call", {"name": "slow", "arguments": {"seconds": 0}}))
        assert reader.runs == {"fast": 1, "slow": 1} and app.runs["slow"] == 1
        health = httpx.get(f"http://127.0.0.1:{with_reader.port}/health", timeout=5).json()
        assert health["reader"] and health["read_by_reader"] == 1 and health["passed_to_app"] == 1


def test_without_its_reader_the_app_answers(with_reader):
    with App(with_reader.app_port):
        assert text(rpc(with_reader.url, "tools/call", {"name": "fast", "arguments": {"text": "b"}})) == "fast:b"
