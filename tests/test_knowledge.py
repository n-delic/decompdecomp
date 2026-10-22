"""What agents learn about a game (app/knowledge.py): it reaches every agent only once two different models agree,
and the user accepts it unless the project adds agreed knowledge by itself. Compiler guesses aren't knowledge."""

import json

import pytest

from decompnet.app import tools
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def net(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        for slug in ("game", "other"):
            db.execute("INSERT INTO projects(slug, name, root, config) VALUES(?, ?, ?, ?)",
                       (slug, slug, str(tmp_path), json.dumps({"kind": "rom", "platform": "ps2"})))
    return net


def agent(net, name):
    return net.local_agent(name)[0]


def test_two_sessions_of_one_model_are_one_opinion_two_models_are_two(net):
    opus1, opus2, gpt = (agent(net, n) for n in ("claude-opus-5.5-1", "claude-opus-5.5-2", "opencode-gpt-6.1-1"))
    said = net.knowledge.share(opus1, "game", "Functions in the 0x1A0000 area take a CPed* as their first argument")
    kid = int(said.split("#")[1].split(".")[0])
    assert "needs a helper running a model other than" in net.knowledge.share(opus2, "game", supports=kid)
    assert net.knowledge.listing("game")["items"][0]["status"] == "proposed"
    assert "the user decides now" in net.knowledge.share(gpt, "game", supports=kid)
    assert net.knowledge.listing("game")["items"][0]["models"] == ["claude-opus-5.5", "opencode-gpt-6.1"]
    assert "CPed" not in net.knowledge.for_prompt("game")  # agreed, not accepted yet
    net.knowledge.decide("game", kid, accept=True)
    assert "CPed" in net.knowledge.for_prompt("game") and "CPed" not in net.knowledge.for_prompt("other")


def test_a_project_can_take_what_two_models_agree_on_by_itself(net):
    net.knowledge.set_auto("game", True)
    a, b = agent(net, "claude-opus-5.5-1"), agent(net, "agy-gemini-3.8-flash-1")
    kid = int(net.knowledge.share(a, "game", "Strings are Shift-JIS").split("#")[1].split(".")[0])
    assert "every agent gets it" in net.knowledge.share(b, "game", supports=kid)
    assert "Shift-JIS" in net.knowledge.for_prompt("game")


def test_the_same_idea_again_is_pointed_at_the_open_proposal(net):
    a, b = agent(net, "claude-opus-5.5-1"), agent(net, "opencode-gpt-6.1-1")
    net.knowledge.share(a, "game", "The player struct is 0x2F0 bytes and its health is at offset 0x540")
    said = net.knowledge.share(b, "game", "the player struct is 0x2F0 bytes; its health is at offset 0x540")
    assert "back it: share_knowledge(supports=" in said and len(net.knowledge.listing("game")["items"]) == 1


def test_compiler_guesses_go_to_the_compiler_tools(net):
    with pytest.raises(NetworkError, match="compiler map"):
        net.knowledge.share(agent(net, "claude-opus-5.5-1"), "game", "This part is built with ee-gcc 2.96 -O2 -G0")


def test_proposals_waiting_for_a_second_model_are_offered_to_agents(net):
    net.knowledge.share(agent(net, "claude-opus-5.5-1"), "game", "Every menu screen has an Init, Update and Draw function")
    text = net.knowledge.for_prompt("game")
    assert "waiting for a second model" in text and "menu screen" in text


def test_add_note_without_a_function_is_an_error_not_knowledge(net):
    """#25: a helper probing tool names called add_note with only text, and its "test" appeared as a project-wide
    knowledge proposal. A note is for one function; project-wide knowledge goes through share_knowledge."""
    who = agent(net, "opencode-gpt-6.1-1")
    for args in ({"text": "test"}, {"text": "x", "function": "  "}):
        said = tools.call(net, who, "game", "add_note", args)
        assert said.startswith("error:") and "share_knowledge" in said, said
        assert net.knowledge.listing("game")["items"] == []


def test_a_blank_function_is_no_function_for_other_tools(net):
    """#25: a whitespace-only function used to slip through _named to every tool (suggest accepted it as a name);
    a blank name is no name."""
    who = agent(net, "opencode-gpt-6.1-1")
    assert tools._named(net, who, "game", {"function": "  "})["function"] == ""
    blank = tools.call(net, who, "game", "suggest", {"text": "hello there", "function": "  "})
    plain = tools.call(net, who, "game", "suggest", {"text": "hello there"})
    assert blank == plain


def test_add_note_on_a_function_still_leaves_a_note(net):
    who = agent(net, "claude-opus-5.5-1")
    proj = net.store.one("SELECT id FROM projects WHERE slug='game'")
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns) VALUES(?,?,?,?)",
                   (proj["id"], "abs_s32", 16, 4))
    said = tools.call(net, who, "game", "add_note", {"text": "clamps to [lo, hi]", "function": "abs_s32"})
    assert not said.startswith("error:"), said
    note = net.store.one("SELECT function_id, text FROM notes WHERE project_id=?", proj["id"])
    assert note["text"] == "clamps to [lo, hi]" and note["function_id"] is not None
    assert net.knowledge.listing("game")["items"] == []


def test_the_api_cannot_call_add_note_without_a_function():
    from decompnet.agents.api import api_tool_schemas

    add_note = next(t["function"] for t in api_tool_schemas() if t["function"]["name"] == "add_note")
    assert "function" in add_note["parameters"]["required"]
