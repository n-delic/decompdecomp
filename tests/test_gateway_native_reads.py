"""get_assembly, get_context and guide answered by the MCP gateway itself (gateway/src/native.rs), byte for byte as
the app's endpoint answers them, and left to the app where its memory decides the answer."""
import json
import time

import pytest

from decompnet.app import mcp_gateway, tool_reader
from tests.test_gateway_native import call, setup  # noqa: F401  (the fixture)

pytestmark = pytest.mark.skipif(mcp_gateway.built() is None,
                                reason="the gateway isn't built (cargo build --release in gateway/)")

# A call through an automatic name, data by address, and a branch to a label an older import lost.
ASM = ("glabel f\n"
       "/* 1000 80001000 0C000404 */  jal       func_80001010\n"
       "/* 1004 80001004 3C028000 */  lui       $v0, %hi(D_80002004)\n"
       "/* 1008 80001008 14800002 */  bnez      $a0, .L80001014\n"
       "/* 100C 8000100C 00000000 */   nop\n"
       "/* 1010 80001010 03E00008 */  jr        $ra\n"
       "/* 1014 80001014 00000000 */   nop\n")


@pytest.fixture
def game_with_code(setup):  # noqa: F811
    net = setup.net
    with net.store.tx() as db:
        pid = db.execute("SELECT id FROM projects WHERE slug='game'").fetchone()[0]
        db.execute("UPDATE functions SET asm=? WHERE name='f'", (ASM,))
        db.execute("UPDATE projects SET config=json_set(config,'$.platform','n64','$.rom',json(?),'$.compiler',json(?))",
                   (json.dumps({"title": "GAME", "game_code": "NGME", "region": "Europe", "size": 0, "sha1": ""}),
                    json.dumps({"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]})))
        db.executemany("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       [(pid, "helper", 0x80001010, 8, "func"), (pid, "gTable", 0x80002000, 16, "data")])
        for unit, version, header in (("", 1, "typedef int s32;"), ("src/café.c", 4, "typedef int s32; /* café */")):
            db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                       "VALUES(?,?,?,NULL,'',0,?)", (pid, version, header, unit))
    setup.gateway.guides = tool_reader.guides
    setup.gateway.guided = set()
    setup.gateway.changed()
    time.sleep(0.3)
    return setup


CASES = [
    ("get_assembly", {"function": "f"}),
    ("get_assembly", {"function": "f", "start": 2, "limit": 3, "focus": "page"}),
    ("get_assembly", {"function": "f", "focus": "jr"}),
    ("get_assembly", {"function": "f", "focus": "nowhere"}),
    ("get_assembly", {"function": "f", "focus": "mismatch:2"}),
    ("get_context", {}),
    ("get_context", {"function": "f"}),
    ("guide", {}),
    ("guide", {"topic": " Workflow "}),
    ("guide", {"topic": "mips"}),
    ("guide", {"topic": "tool:get_assembly"}),
    ("guide", {"topic": "tool:no_such_tool"}),
]


@pytest.mark.parametrize("name,arguments", CASES)
def test_answers_exactly_as_the_app_does_without_asking_it(game_with_code, name, arguments):
    s = game_with_code
    from_app = call(s.app, s.token, name, arguments)
    assert not from_app["result"].get("isError"), from_app
    s.reached.clear()
    assert call(s.gateway.url, s.token, name, arguments) == from_app
    assert s.reached == []


def test_the_assembly_is_shown_under_current_names(game_with_code):
    s = game_with_code
    text = call(s.gateway.url, s.token, "get_assembly", {"function": "f", "focus": "page"})["result"]["content"][0]["text"]
    assert "jal       helper" in text and "gTable+0x4" in text and ".L80001014:" in text
    with s.net.store.tx() as db:  # a rename in the app: the gateway's symbol table follows
        db.execute("UPDATE symbols SET name='helper_renamed' WHERE name='helper'")
    from_app = call(s.app, s.token, "get_assembly", {"function": "f", "focus": "page"})
    assert call(s.gateway.url, s.token, "get_assembly", {"function": "f", "focus": "page"}) == from_app
    assert "helper_renamed" in from_app["result"]["content"][0]["text"]


def test_what_the_app_remembers_stays_with_the_app(game_with_code):
    s = game_with_code
    fn = s.net._function("f", "game")
    s.net._last_diffs[(s.agent.id, fn["id"])] = ([], "")  # its last compile: the default view starts at a mismatch
    s.gateway.changed()
    time.sleep(0.3)
    s.reached.clear()
    call(s.gateway.url, s.token, "get_assembly", {"function": "f"})
    assert s.reached == ["get_assembly"]
    s.reached.clear()
    call(s.gateway.url, s.token, "get_assembly", {"function": "f", "focus": "page"})  # (an explicit page doesn't)
    assert s.reached == []
    routes = tool_reader.routes(s.net, type("M", (), {"assignments": {s.agent.id: "game"}})(), type("S", (), {"data": {}})())
    routes["hidden"] = {str(s.agent.id): ["guide"]}  # disabled in this helper's tool settings
    s.state["routes"] = lambda: routes
    s.gateway.changed()
    time.sleep(0.3)
    s.reached.clear()
    call(s.gateway.url, s.token, "guide", {})
    assert s.reached == ["guide"]


def test_a_name_the_app_would_resolve_goes_to_the_app(game_with_code):
    s = game_with_code
    s.reached.clear()
    from_gateway = call(s.gateway.url, s.token, "get_assembly", {"function": "0x80001000"})
    assert s.reached == ["get_assembly"]
    assert from_gateway == call(s.app, s.token, "get_assembly", {"function": "0x80001000"})
