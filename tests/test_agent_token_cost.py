"""What a matching helper reads on every turn and every session (perf/agent-token-cost): tool definitions it can't
use stay out of its list, the part of its prompt that doesn't change comes first so providers serve it from their
cache, and the automatic claim leaves autopilot's free tries their turn before a model spends tokens."""

import json
import threading

import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.app import tool_policy, tools
from decompnet.app.agents import Manager, Session
from decompnet.network import NetworkError
from tests.test_claims import _game, _held

# A ROM project's definitions for a matching helper, in characters. Every tool listed was 32,069 characters (about
# 10,700 tokens a turn before caching); without other helpers' tools it was 24,301.
MATCHING_SCHEMA_CHARS = 25_000


def test_a_matching_helper_is_not_shown_other_helpers_tools(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    worker, _ = net.local_agent("worker")
    outside, _ = net.local_agent("outside")
    polisher, _ = net.local_agent("polisher")
    net._agent_tool_tasks = {worker.id: "matching", polisher.id: "polishing"}
    shown = set(tools.project_tools(net, "game", worker))
    assert shown and not shown & tool_policy.OTHER_TASKS
    assert tool_policy.REQUIRED <= shown and {"get_data_unit", "compile_data", "get_boundary_evidence"} <= shown
    # an MCP client the app didn't start, and a polishing helper, keep what they use
    assert {"claim_port_fix", "claim_asset_region"} <= set(tools.project_tools(net, "game", outside))
    assert {"claim_readable", "polish_function"} <= set(tools.project_tools(net, "game", polisher))
    schema = json.dumps(api_tool_schemas(fork=False, names=tools.project_tools(net, "game", worker)))
    assert len(schema) < MATCHING_SCHEMA_CHARS < len(json.dumps(api_tool_schemas(fork=False)))


def test_a_matching_helper_calling_another_helpers_tool_is_told_what_to_do(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    worker, _ = net.local_agent("worker")
    net._agent_tool_tasks = {worker.id: "matching"}
    with pytest.raises(NetworkError, match="another kind of helper.*claim_function"):
        tool_policy.record(net, worker, "game", "claim_port_fix")
    assert tools.call(net, worker, "game", "claim_data", {}).startswith("error:")


def _prompt_manager(net, monkeypatch):
    manager = Manager.__new__(Manager)
    manager.net = net
    monkeypatch.setattr(manager, "_prompt", lambda s: "WORK PROMPT for the game\n")
    monkeypatch.setattr(net, "instructions", lambda slug: "Prefer s32 over int.")
    monkeypatch.setattr(net, "claim_order", lambda slug: "closest")
    monkeypatch.setattr(net.knowledge, "for_prompt", lambda slug, agent=None: "Accepted: globals are in .sdata.")
    return manager


def test_two_sessions_prompts_share_every_stable_part_before_what_changes(tmp_path, monkeypatch):
    """A provider caches a prompt's longest unchanged start. The handoff from the last session and the claimed
    function's brief differ every session; the guides line, the owner's order and instructions and the shared knowledge
    don't, so they come before them (they came after the handoff, and the claim order after the brief)."""
    from decompnet.app import session_handoffs

    net, _ = _game(tmp_path, [("f", 10, "open"), ("g", 12, "open")])
    worker, _ = net.local_agent("worker")
    manager = _prompt_manager(net, monkeypatch)
    handoffs = iter(["\nHandoff: f released, stack slots differ.\n", "\nLast session matched g.\n"])
    monkeypatch.setattr(session_handoffs, "previous", lambda net, helper, slug: next(handoffs))
    first, second = (Session(sid, "api", "W", "game", worker.name, {}) for sid in ("s1", "s2"))
    first.startup_context, second.startup_context = "## CLAIMED: game/f\nbrief one", "## CLAIMED: game/g\nbrief two"
    one, two = manager.prompt(first), manager.prompt(second)
    shared = 0
    while shared < min(len(one), len(two)) and one[shared] == two[shared]:
        shared += 1
    stable = one[:shared]
    for part in ("WORK PROMPT", "guide(", "functions closest to matching first", "Prefer s32 over int.",
                 "Accepted: globals are in .sdata."):
        assert part in stable, part
    assert "Handoff" not in stable and "CLAIMED" not in stable
    assert one.index("Handoff") < one.index("## CLAIMED: game/f")


def _running_autopilot(net, slug, pending):
    net.autopilots[slug] = {"state": "running", "_pending": set(pending), "stop": threading.Event()}


def test_the_automatic_claim_leaves_autopilots_next_tries_for_last(tmp_path):
    net, _ = _game(tmp_path, [("tiny", 2, "open"), ("small", 4, "open"), ("big", 300, "open")])
    ids = {r["name"]: r["id"] for r in net.store.all("SELECT id, name FROM functions")}
    _running_autopilot(net, "game", [ids["tiny"], ids["small"]])
    a, _ = net.local_agent("a")
    b, _ = net.local_agent("b")
    c, _ = net.local_agent("c")
    assert net.claim_row(a, "game")["name"] == "big"  # the free tries still to come wait for autopilot
    assert net.claim_row(b, "game")["name"] == "tiny"  # only an order: with nothing else left, one comes anyway
    assert _held(net, a) == "big"
    net.autopilots["game"]["state"] = "done"
    assert net.claim_row(c, "game")["name"] == "small"


def test_autopilots_order_can_be_turned_off_and_skips_named_and_own_strategy_claims(tmp_path):
    net, pid = _game(tmp_path, [("tiny", 2, "open"), ("big", 300, "open")])
    tiny = net.store.one("SELECT id FROM functions WHERE name='tiny'")["id"]
    _running_autopilot(net, "game", [tiny])
    a, _ = net.local_agent("a")
    net.set_selection("game", a.name, {"strategy": "easiest"})  # the agent's own order: no preferences
    assert net.claim_row(a, "game")["name"] == "tiny"
    with net.store.tx() as db:
        db.execute("DELETE FROM claims")
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps({"kind": "rom", "machine_first": False}), pid))
    b, _ = net.local_agent("b")
    assert net.claim_row(b, "game")["name"] == "tiny"
