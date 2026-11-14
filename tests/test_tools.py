"""The tools helpers get are the ones their project's console (or, for a ROM join, its engine) can do (#232): a DS
helper isn't offered joins it can't make, a GameCube one no function ends or C++ classes, and the API helper's
schemas say the same as the MCP tool list."""

import json

import pytest

from decompnet import consoles
from decompnet.agents.api import api_tool_schemas
from decompnet.app import agents, tools
from decompnet.app.engine import RomNetwork

# platform, project mode (a fork), tools that must be there, tools that must not
CASES = [
    ("gc", True, {"claim_function", "file_status", "split_file", "compiler_map"},
     {"join_functions", "report_boundary", "get_class", "guess_struct", "redraft", "search_flags"}),
    ("wii", True, {"claim_function", "file_status", "split_file", "compiler_map"},
     {"join_functions", "report_boundary", "get_class", "guess_struct", "redraft", "search_flags"}),
    ("nds", True, {"claim_function", "file_status", "split_file", "join_functions", "compiler_map"},
     {"report_boundary", "get_class", "guess_struct", "redraft", "search_flags"}),
    ("pc", True, {"join_functions", "report_boundary", "get_class", "guess_struct", "compiler_map", "redraft",
                  "search_flags"}, set()),
    ("n64", False, {"join_functions", "report_boundary", "get_class", "guess_struct", "compiler_map", "redraft",
                    "search_flags"}, {"read_file", "split_file"}),
    ("ps2", False, {"join_functions", "report_boundary", "get_class", "guess_struct", "compiler_map", "redraft",
                    "search_flags"}, {"read_file", "split_file"}),
]


@pytest.mark.parametrize("platform,fork,present,absent", CASES, ids=[c[0] for c in CASES])
def test_the_mcp_tools_and_api_schemas_are_what_the_console_can_do(platform, fork, present, absent):
    console = consoles.get(platform)
    names = tools.tool_names(fork, console)
    assert present <= set(names)
    assert absent.isdisjoint(names)
    # the API helper gets the same list, not a schema for a tool the MCP list leaves out
    assert {t["function"]["name"] for t in api_tool_schemas(fork=fork, names=names)} == set(names)


def test_an_old_prompt_calling_an_unoffered_tool_gets_a_sentence(tmp_path):
    """An older prompt (or a cached tool list) calling join_functions on a GameCube project is told why, not a
    traceback from the console's default."""
    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    root = tmp_path / "ws" / "gc"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("gc", "GC", "fork", root,
                        {"kind": "fork", "platform": "gc", "origin": "scratch", "ready": True}, "x")
    agent, _ = net.local_agent("old-prompt")
    text = tools.call(net, agent, "gc", "join_functions",
                      {"function": "a", "with_function": "b", "reason": "a has no epilogue"})
    assert text.startswith("error: join_functions isn't offered in GameCube projects")
    assert "can't join functions" in text and "Traceback" not in text


def test_a_rom_engine_that_can_join_offers_it_to_rom_consoles(monkeypatch):
    """#192 adds ROM joins in the engine, not on Console: once its join_functions handler exists (and FORK_ONLY no
    longer hides the tool), every ROM console gets it. Project mode still asks the console: PC joins, GameCube not."""
    monkeypatch.setattr(tools, "FORK_ONLY", tools.FORK_ONLY - {"join_functions"})
    monkeypatch.setattr(tools.RomNetwork, "join_functions", lambda *args, **kwargs: "", raising=False)
    for platform in ("n64", "ps2", "gba"):
        assert "join_functions" in tools.tool_names(False, consoles.get(platform))
    assert "join_functions" in tools.tool_names(True, consoles.get("pc"))
    assert "join_functions" not in tools.tool_names(True, consoles.get("gc"))


def test_the_rom_prompt_names_joins_only_when_the_engine_has_them(tmp_path, monkeypatch):
    """A ROM prompt follows the same gate as its tool list: the join guidance once the engine has join_functions
    (#192), and none when it can't."""
    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    manager = agents.Manager(net, tmp_path, "http://127.0.0.1:1/mcp")
    session = agents.Session("1", "claude", "claude-1", "rom", "claude-1", {})
    monkeypatch.setattr(net, "overview", lambda slug: {
        "kind": "rom", "title": "Game", "platform": "n64", "compiler": {"toolchain": "gcc-2.7.2-kmc"}})
    assert "join_functions" in manager._prompt(session)        # the engine joins (#192)
    monkeypatch.setattr(tools, "_rom_joins", lambda: False)     # an engine that can't join names no join tool
    assert "join_functions" not in manager._prompt(session)


def test_a_rom_project_without_engine_joins_names_the_engine(tmp_path, monkeypatch):
    """The refusal follows the same gate: ROM joins are the engine's (#192), not a console's, so say so."""
    monkeypatch.setattr(tools, "_rom_joins", lambda: False)  # an engine that can't join: the refusal names the engine
    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                   ("rom", "ROM", "", "mips", 0, str(tmp_path), json.dumps({"kind": "rom", "platform": "n64"})))
    agent, _ = net.local_agent("old-prompt")
    text = tools.call(net, agent, "rom", "join_functions",
                      {"function": "a", "with_function": "b", "reason": "a has no epilogue"})
    assert text.startswith("error: join_functions isn't offered in Nintendo 64 projects")
    assert "the ROM engine can't join functions" in text and "Traceback" not in text
