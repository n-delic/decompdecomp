"""The tools helpers get are the ones their project's console can do (#232): a DS helper isn't offered joins it
can't make, a GameCube one no function ends or C++ classes, and the API helper's schemas say the same as the MCP
tool list."""

import json

import pytest

from decompnet import consoles
from decompnet.agents.api import api_tool_schemas
from decompnet.app import tools
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
    ("n64", False, {"report_boundary", "get_class", "guess_struct", "compiler_map", "redraft", "search_flags"},
     {"join_functions", "read_file", "split_file"}),
    ("ps2", False, {"report_boundary", "get_class", "guess_struct", "compiler_map", "redraft", "search_flags"},
     {"join_functions", "read_file", "split_file"}),
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
