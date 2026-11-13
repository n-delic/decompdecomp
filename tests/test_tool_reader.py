"""Read-only tools answered by a process of their own (app/tool_reader.py), exactly as the app answers them."""
import json
import time

import httpx
import pytest

from decompnet.app import tool_reader, tools
from tests.test_agent_workflow_efficiency import game

HEADERS = {"Accept": "application/json, text/event-stream"}


def call(reader, token, name, arguments=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}
    reply = httpx.post(reader.url, json=body, headers=HEADERS | {"Authorization": f"Bearer {token}"}, timeout=60)
    reply.raise_for_status()
    result = reply.json()["result"]
    return result["content"][0]["text"], result.get("isError", False)


@pytest.fixture
def served(tmp_path):
    net, worker, fn = game(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET vram=0x80001000")
    agent, token = net.local_agent("reader-helper")
    routes = {"assignments": {str(agent.id): "game"}, "manual": {}, "app_only": [], "tasks": {}, "busy": []}
    reports = []
    reader = tool_reader.Reader(net, tmp_path / "internal")
    reader.start()
    reader.attach(lambda: routes | {"at": time.time()}, reports.append)
    deadline = time.time() + 90
    while reader.process is None and time.time() < deadline:  # set once it prints LISTENING
        time.sleep(0.1)
    if reader.process is None:
        pytest.fail(f"the reader didn't start: {(tmp_path / 'internal' / 'logs' / 'tool-reader.log').read_text()}")
    reader.changed()
    time.sleep(0.3)
    yield net, agent, token, routes, reports, reader
    reader.stop()


def test_answers_as_the_app_does_and_reports_each_call(served):
    net, agent, token, _, reports, reader = served
    net.claim_row(agent, "game", "f")
    for name, arguments in [("get_claims", {}), ("list_functions", {}), ("get_function", {"function": "f"}),
                            ("project_info", {"detail": False})]:
        text, error = call(reader, token, name, arguments)
        assert not error and tool_reader.DECLINED not in text, (name, text)
        assert text == tools.call(net, agent, "game", name, arguments), name
    deadline = time.time() + 5
    while len(reports) < 4 and time.time() < deadline:
        time.sleep(0.05)
    assert [r["call"]["name"] for r in reports] == ["get_claims", "list_functions", "get_function", "project_info"]
    assert reports[0]["call"]["agent_id"] == agent.id and reports[0]["call"]["slug"] == "game"
    assert json.loads(reports[0]["call"]["content"])["claims"][0]["function"] == "f"


def test_sees_what_the_app_changes_after_it_started(served):
    net, agent, token, *_, reader = served
    assert json.loads(call(reader, token, "get_claims")[0])["claims"] == []
    net.claim_row(agent, "game", "f")
    assert [c["function"] for c in json.loads(call(reader, token, "get_claims")[0])["claims"]] == ["f"]


def test_errors_are_the_apps_errors(served):
    net, agent, token, *_, reader = served
    text, error = call(reader, token, "get_function", {"function": "no_such_function"})
    assert error and tool_reader.DECLINED not in text
    # (the app's MCP endpoint puts the same "Error executing tool" before it)
    app = tools.call(net, agent, "game", "get_function", {"function": "no_such_function"}).removeprefix("error: ")
    assert text == f"Error executing tool get_function: {app}"


@pytest.mark.parametrize("case", ["not_a_reader_tool", "app_section", "app_only_helper", "busy_project",
                                  "stale_routes", "unknown_token", "detail"])
def test_declines_what_only_the_app_can_answer(served, case):
    net, agent, token, routes, reports, reader = served
    name, arguments = "get_claims", {}
    if case == "not_a_reader_tool":
        name, arguments = "last_diff", {"function": "f"}
    elif case == "app_section":
        name, arguments = "get_function", {"function": "f", "section": "candidate"}
    elif case == "detail":
        name, arguments = "project_info", {"detail": True}
    elif case == "app_only_helper":
        routes["app_only"] = [agent.id]
    elif case == "busy_project":
        routes["busy"] = ["game"]
    elif case == "unknown_token":
        token = "not-a-token"
    if case == "stale_routes":
        reader.routes = lambda: routes | {"at": time.time() - 60}
    reader.changed()
    time.sleep(0.3)
    text, error = call(reader, token, name, arguments)
    assert error and tool_reader.DECLINED in text
    time.sleep(0.2)
    assert reports == []


def test_never_synchronizes_saved_progress(served, monkeypatch):
    """The reader's engine reads the database; the app's own progress folder stays the app's."""
    net, agent, token, *_, reader = served
    log = reader.internal / "logs" / "tool-reader.log"
    call(reader, token, "list_functions")
    assert "Traceback" not in log.read_text()
    assert not (reader.internal / "progress").exists()


def test_the_app_keeps_transcript_budget_and_usage(tmp_path):
    from types import SimpleNamespace

    from decompnet.app import session_budget
    net, worker, fn = game(tmp_path)
    tools.build_mcp(net, lambda ctx: None)  # (the tool policy tables)
    budget = session_budget.start(net, worker.id, "game", {})
    recorded = []
    manager = SimpleNamespace(record_agent=lambda agent, slug, kind, value: recorded.append((agent.name, kind, value)))
    net.project_activity = SimpleNamespace(touch=lambda slug: recorded.append(("touch", slug)))
    tool_reader.apply_report(net, manager, {"call": {"agent_id": worker.id, "agent_name": worker.name, "slug": "game",
                                                     "name": "get_claims", "arguments": {}, "call_id": "c1",
                                                     "content": "abc", "timings": {"dispatch_ms": 1}}})
    assert budget.calls == 1 and budget.chars == 3
    assert [r[1] for r in recorded if r[0] == worker.name] == ["tool_request", "tool_response"]
    assert recorded[-1][2]["timings"]["reader"] is True
    assert net.store.one("SELECT calls FROM agent_tool_usage WHERE name='get_claims'")["calls"] == 1


def test_routes_name_helpers_with_app_only_state(tmp_path):
    from types import SimpleNamespace

    from decompnet.app import session_budget
    net, worker, fn = game(tmp_path)
    changes = []
    tool_reader.watch(net, lambda: changes.append(1))
    net.__dict__.setdefault("_pending_tool_handshakes", set()).add((worker.id, "game"))
    assert changes  # pushed at once, not within the second
    other, _ = net.local_agent("spent")
    budget = session_budget.start(net, other.id, "game", {"session_tool_limit": 1})
    budget.calls = 1
    manager = SimpleNamespace(assignments={worker.id: "game", other.id: "game"})
    snapshot = tool_reader.routes(net, manager, SimpleNamespace(data={}))
    assert set(snapshot["app_only"]) == {worker.id, other.id}
    assert snapshot["assignments"] == {str(worker.id): "game", str(other.id): "game"}


def test_a_rename_in_the_app_drops_the_readers_symbol_caches(tmp_path):
    net, worker, fn = game(tmp_path)
    service = tool_reader.Service(net, lambda message: None)
    pid = net._project("game")["id"]
    service.fresh("game")
    net._syms[pid] = net._xrefs[pid] = "built before"
    service.fresh("game")
    assert net._syms[pid] == "built before"  # nothing changed: kept
    with net.store.tx() as db:
        db.execute("UPDATE functions SET name='g' WHERE name='f'")
    service.fresh("game")
    assert pid not in net._syms and pid not in net._xrefs
