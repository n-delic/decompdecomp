"""claim_function answered by the gateway from a claim the app prepared (app/claim_ahead.py), with the claim then
recorded by the app in the background (gateway/src/main.rs claim_from_here)."""
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from decompnet.app import claim_ahead, mcp_gateway, tool_reader, tools
from tests.test_claims import _game


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def holder(net, name):
    row = net.store.one("SELECT c.agent_id FROM claims c JOIN functions f ON f.id=c.function_id WHERE f.name=?", name)
    return row["agent_id"] if row else None


def test_prepares_what_the_claim_would_take_without_claiming(tmp_path):
    net, _ = _game(tmp_path, [("a", 4, "open"), ("b", 8, "open"), ("c", 12, "open"), ("d", 16, "open")])
    first, _ = net.local_agent("first")
    second, _ = net.local_agent("second")
    ahead = claim_ahead.Ahead(net, lambda: {first.id: "game", second.id: "game"})
    ahead.tick()
    prepared = ahead.routes()
    picks = {prepared[str(first.id)]["function"], prepared[str(second.id)]["function"]}
    assert len(picks) == 2, "two helpers are never prepared the same function"
    assert net.store.one("SELECT COUNT(*) AS n FROM claims")["n"] == 0
    entry = prepared[str(first.id)]
    assert entry["text"].startswith(f"## CLAIMED: game/{entry['function']}")
    assert claim_ahead.PENDING_TOKEN in entry["text"]
    # The app's own pick for the first helper is the one prepared for it.
    assert net.claim_row(first, "game")["name"] == entry["function"]
    ahead.tick()  # once the second helper's pick is taken by someone else, it gets another
    net.claim_row(net.local_agent("third")[0], "game", prepared[str(second.id)]["function"])
    ahead.tick()
    assert ahead.routes()[str(second.id)]["function"] not in picks


def test_leaves_focused_helpers_and_forks_to_the_app(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [("a", 4, "open")])
    agent, _ = net.local_agent("helper")
    net._agent_focus = {agent.id: {}}
    assert claim_ahead.prepare(net, agent.id, "game") is None
    net._agent_focus = {}
    monkeypatch.setattr(net, "fork_slug", lambda slug: "fork")
    assert claim_ahead.prepare(net, agent.id, "game") is None


def test_named_claim_can_decline_a_replacement(tmp_path):
    from decompnet.network import NetworkError

    net, _ = _game(tmp_path, [("a", 4, "open"), ("b", 8, "open")])
    first, _ = net.local_agent("first")
    second, _ = net.local_agent("second")
    net.claim_row(first, "game", "a")
    with pytest.raises(NetworkError, match="is claimed by first"):
        tools._invoke(net, second, "game", "claim_function", {"function": "a", "fallback": False})
    assert holder(net, "b") is None
    text = tools._invoke(net, second, "game", "claim_function", {"function": "a"})
    assert "## CLAIMED: game/b" in text
    assert holder(net, "a") == first.id
    assert holder(net, "b") == second.id


needs_gateway = pytest.mark.skipif(mcp_gateway.built() is None,
                                        reason="the gateway isn't built (cargo build --release in gateway/)")


@pytest.fixture
def served(tmp_path):
    net, _ = _game(tmp_path, [("a", 4, "open"), ("b", 8, "open"), ("c", 12, "open")])
    agent, token = net.local_agent("helper")
    other, other_token = net.local_agent("other")
    agents = {token: agent, other_token: other}
    reached = []

    def agent_of(ctx):
        auth = (ctx.headers or {}).get("authorization", "")
        return agents[auth.removeprefix("Bearer ")], "game"

    def observe(a, s, kind, v):
        if kind == "tool_request":
            reached.append((a.id, v["name"], v.get("arguments")))
    mcp = tools.build_mcp(net, agent_of, observe=observe)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(stateless_http=True, json_response=True),
                                           host="127.0.0.1", port=port, log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    gateway = mcp_gateway.Gateway(mcp_gateway.built(), f"http://127.0.0.1:{port}/mcp", tmp_path / "gateway.log",
                                  journal=tmp_path / "journal.jsonl")
    gateway.start()
    manager = SimpleNamespace(assignments={agent.id: "game", other.id: "game"})
    net._claims_ahead = claim_ahead.Ahead(net, lambda: dict(manager.assignments))
    net._claims_ahead.tick()
    gateway.attach(lambda: tool_reader.routes(net, manager, SimpleNamespace(data={})), lambda message: None)
    time.sleep(0.3)
    yield SimpleNamespace(net=net, agent=agent, token=token, other=other, other_token=other_token,
                          gateway=gateway, reached=reached, ahead=net._claims_ahead)
    gateway.stop()
    server.should_exit = True


def call(url, token, name, arguments):
    body = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    reply = httpx.post(url, json=body, timeout=30, headers={"Authorization": f"Bearer {token}",
                                                            "Accept": "application/json, text/event-stream"})
    reply.raise_for_status()
    return reply.json()["result"]["content"][0]["text"]


def wait(condition, seconds=10):
    deadline = time.time() + seconds
    while not (met := condition()) and time.time() < deadline:
        time.sleep(0.05)
    return met


@needs_gateway
def test_hands_over_the_prepared_claim_and_the_app_records_it(served):
    prepared = served.ahead.routes()[str(served.agent.id)]
    text = call(served.gateway.url, served.token, "claim_function", {})
    assert text == prepared["text"]
    assert wait(lambda: holder(served.net, prepared["function"]) == served.agent.id)
    assert [args["function"] for _, name, args in served.reached if name == "claim_function"] == [prepared["function"]]
    # get_claims (answered by the gateway itself) has the token once the claim is recorded.
    assert prepared["function"] in call(served.gateway.url, served.token, "get_claims", {})
    # Holding it, the helper's next plain claim goes to the app (which says it still holds it).
    served.reached.clear()
    call(served.gateway.url, served.token, "claim_function", {})
    assert any(name == "claim_function" and args == {"function": "", "strategy": "", "count": 1}
               or name == "claim_function" and not (args or {}).get("function")
               for _, name, args in served.reached)


@needs_gateway
@pytest.mark.parametrize("arguments", [{"function": "c"}, {"count": 3}, {"strategy": "hardest"}])
def test_a_named_function_batch_or_strategy_goes_to_the_app(served, arguments):
    text = call(served.gateway.url, served.token, "claim_function", arguments)
    assert "being recorded" not in text
    assert any(name == "claim_function" for _, name, _ in served.reached)


@needs_gateway
def test_named_claim_with_fallback_disabled_reports_only_the_conflict(served):
    served.net.claim_row(served.other, "game", "a")
    text = call(served.gateway.url, served.token, "claim_function", {"function": "a", "fallback": False})
    assert "is claimed by other" in text
    assert "## CLAIMED" not in text
    assert holder(served.net, "a") == served.other.id
    assert holder(served.net, "b") is None
    assert holder(served.net, "c") is None


@needs_gateway
def test_a_claim_the_app_refuses_is_reported_on_the_next_reply(served, monkeypatch):
    """The app records the handed-over claim with its own checks. One it refuses (another helper took the function
    in the moment between) comes back at the top of the
    helper's next reply, with the function the app gave it instead."""
    from decompnet.network import NetworkError
    prepared = served.ahead.routes()[str(served.agent.id)]

    claim = served.net.claim

    def taken(agent, project=None, function=None, strategy="easiest"):
        if function == prepared["function"]:
            raise NetworkError(f"{function} is claimed by other for another 5 min")
        return claim(agent, project, function, strategy)
    monkeypatch.setattr(served.net, "claim", taken)
    assert call(served.gateway.url, served.token, "claim_function", {}) == prepared["text"]
    replies = []
    assert wait(lambda: replies.append(call(served.gateway.url, served.token, "get_claims", {})) or
                "finished your earlier claim_function call" in replies[-1])
    assert "is claimed by other" in replies[-1] and "## CLAIMED" in replies[-1], replies[-1]
