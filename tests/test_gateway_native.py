"""The MCP gateway answers get_claims, list_functions and project_info itself (gateway/src/native.rs), with exactly
the reply the app's own MCP endpoint gives, and leaves everything else to the app."""
import json
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from decompnet.app import mcp_gateway, tool_reader, tools
from tests.test_agent_workflow_efficiency import game

pytestmark = pytest.mark.skipif(mcp_gateway.built() is None,
                                reason="the gateway isn't built (cargo build --release in gateway/)")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def setup(tmp_path):
    net, worker, fn = game(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET vram=0x80001000, unit='src/café.c', best_score=12")
        db.execute("UPDATE projects SET config=json_set(config,'$.instructions','Name things “plainly”.',"
                   "'$.claim_order','easiest')")
    agent, token = net.local_agent("native-helper")
    net.claim_row(agent, "game", "f")
    reached = []  # tool calls the app itself ran
    agents = {token: agent}

    def agent_of(ctx):
        auth = (ctx.headers or {}).get("authorization", "")
        return agents[auth.removeprefix("Bearer ")], "game"

    mcp = tools.build_mcp(net, agent_of, observe=lambda a, s, kind, v: kind == "tool_request" and reached.append(v["name"]))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(stateless_http=True, json_response=True),
                                           host="127.0.0.1", port=port, log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    gateway = mcp_gateway.Gateway(mcp_gateway.built(), f"http://127.0.0.1:{port}/mcp", tmp_path / "gateway.log")
    gateway.start()
    manager = SimpleNamespace(assignments={agent.id: "game"})
    state = {"routes": lambda: tool_reader.routes(net, manager, SimpleNamespace(data={}))}
    reports = []
    gateway.attach(lambda: state["routes"](), reports.append)
    time.sleep(0.3)
    yield SimpleNamespace(net=net, agent=agent, token=token, app=f"http://127.0.0.1:{port}/mcp", gateway=gateway,
                          reached=reached, reports=reports, state=state)
    gateway.stop()
    server.should_exit = True


def call(url, token, name, arguments):
    body = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    reply = httpx.post(url, json=body, timeout=30, headers={"Authorization": f"Bearer {token}",
                                                            "Accept": "application/json, text/event-stream"})
    reply.raise_for_status()
    return reply.json()


CASES = [("get_claims", {}), ("list_functions", {}), ("list_functions", {"status": "all", "limit": 5}),
         ("list_functions", {"status": "matched"}), ("project_info", {}), ("project_info", {"detail": False})]


@pytest.mark.parametrize("name,arguments", CASES)
def test_answers_exactly_as_the_app_does_without_asking_it(setup, name, arguments):
    from_app = call(setup.app, setup.token, name, arguments)
    setup.reached.clear()
    from_gateway = call(setup.gateway.url, setup.token, name, arguments)
    assert from_gateway == from_app
    assert setup.reached == []  # answered by the gateway itself
    deadline = time.time() + 5
    while not setup.reports and time.time() < deadline:
        time.sleep(0.02)
    report = setup.reports[-1]["call"]
    assert (report["name"], report["slug"], report["agent_id"]) == (name, "game", setup.agent.id)
    assert report["content"] == from_app["result"]["content"][0]["text"]


def test_the_claim_token_is_the_apps(setup):
    text = call(setup.gateway.url, setup.token, "get_claims", {})["result"]["content"][0]["text"]
    claims = json.loads(text)["claims"]
    fn = setup.net._function("f", "game")
    from decompnet.release_receipts import current
    assert claims[0]["claim_token"] == current(setup.net, fn, setup.agent) != ""


@pytest.mark.parametrize("case", ["detail", "app_only", "busy", "fork", "no_routes", "other_tool"])
def test_leaves_the_rest_to_the_app(setup, case):
    name, arguments = "get_claims", {}
    routes = tool_reader.routes(setup.net, SimpleNamespace(assignments={setup.agent.id: "game"}), SimpleNamespace(data={}))
    if case == "detail":
        name, arguments = "project_info", {"detail": True}
    elif case == "app_only":
        routes["app_only"] = [setup.agent.id]
    elif case == "busy":
        routes["busy"] = ["game"]
    elif case == "fork":
        routes["projects"]["game"]["fork"] = True
    elif case == "no_routes":
        routes["at"] = 0
        routes["assignments"] = {}
    elif case == "other_tool":
        name, arguments = "search_notes", {"query": "x"}
    setup.state["routes"] = lambda: routes
    setup.gateway.changed()
    time.sleep(0.2)
    setup.reached.clear()
    from_gateway = call(setup.gateway.url, setup.token, name, arguments)
    assert setup.reached == [name]  # the app answered
    assert from_gateway == call(setup.app, setup.token, name, arguments)


def test_stale_routes_go_to_the_app(setup):
    setup.state["routes"] = lambda: (_ for _ in ()).throw(RuntimeError("the app stalled"))  # no new routes
    time.sleep(5.5)
    setup.reached.clear()
    call(setup.gateway.url, setup.token, "get_claims", {})
    assert setup.reached == ["get_claims"]
