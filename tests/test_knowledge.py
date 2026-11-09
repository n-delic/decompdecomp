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


def test_user_can_accept_single_model_tip_without_claiming_model_consensus(net):
    a=agent(net,'claude-opus-5.5-1')
    kid=int(net.knowledge.share(a,'game','For register-only differences, shorten temporary live ranges.').split('#')[1].split('.')[0])
    net.knowledge.set_auto('game',True)
    assert net.knowledge.listing('game')['items'][0]['status']=='proposed'
    net.knowledge.decide('game',kid,accept=True)
    listing=net.knowledge.listing('game')['items'][0]
    assert listing['status']=='accepted' and len(listing['models'])==1
    assert 'shorten temporary' in net.knowledge.for_prompt('game')
    assert 'agreed by different models' not in net.knowledge.for_prompt('game')
    assert 'shorten temporary' not in net.knowledge.for_prompt('other')
    net.knowledge.decide('game',kid,accept=False)
    assert 'shorten temporary' not in net.knowledge.for_prompt('game')


def test_reusable_compiler_behavior_tips_are_allowed_and_function_note_destination_is_documented(net):
    a=agent(net,'claude-opus-5.5-1')
    assert 'Proposed' in net.knowledge.share(a,'game','GCC matching tip: reuse a local across disjoint live ranges to affect register allocation.')
    from decompnet.agents.api import api_tool_schemas
    description=next(t['function']['description'] for t in api_tool_schemas() if t['function']['name']=='share_knowledge')
    assert 'NOT function-specific' in description and 'add_note(function=' in description


def test_pending_proposal_ui_offers_manual_acceptance():
    import shutil
    import subprocess
    from pathlib import Path
    node=shutil.which('node')
    if node is None:
        pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_knowledge_accept.js'))],check=True,timeout=30)


def test_function_specific_knowledge_is_scoped_without_deleting_history(net):
    a = agent(net, 'worker')
    pid = net._project('game')['id']
    with net.store.tx() as db:
        db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)', (pid,'func_example'))
        db.execute("INSERT INTO knowledge(project_id,text,status,created_at) VALUES(?,?,'accepted',1)",
                   (pid,'Semantic caution: func_example reads an uninitialized local.'))
        db.execute("INSERT INTO knowledge(project_id,text,status,created_at) VALUES(?,?,'accepted',2)",
                   (pid,'Reuse locals to reproduce register allocation.'))
    prompt = net.knowledge.for_prompt('game')
    assert 'func_example' not in prompt and 'Reuse locals' in prompt
    assert 'uninitialized' in '\n'.join(net.knowledge.for_function(net._function('func_example','game')))
    with pytest.raises(NetworkError, match='specific function'):
        net.knowledge.share(a,'game','func_example needs a corrected pointer.')
    assert net.store.one('SELECT COUNT(*) AS n FROM knowledge')['n'] == 2


def test_reusable_rule_keeps_concrete_examples_out_of_prompts(net):
    worker=agent(net,'claude-opus-1')
    pid=net._project('game')['id']
    with net.store.tx() as db:db.execute("INSERT INTO functions(project_id,name,status) VALUES(?,?,'open')",(pid,'func_example'))
    rule='When a compiler masks a bitfield condition, cast the field to its unsigned byte type to test the extracted value.'
    response=net.knowledge.share(worker,'game',rule+' (func_example matched this way.)')
    kid=int(response.split('#')[1].split('.')[0])
    item=net.knowledge.listing('game')['items'][0]
    assert item['text']==rule and 'func_example' in item['evidence'][0]['text']
    net.knowledge.decide('game',kid,True)
    assert rule in net.knowledge.for_prompt('game') and 'func_example' not in net.knowledge.for_prompt('game')
    with pytest.raises(NetworkError):net.knowledge.share(worker,'game','In func_example improved the score from 40 to 0.')


def test_generic_rule_can_name_a_library_call_in_code(net):
    worker=agent(net,'claude-opus-1');pid=net._project('game')['id']
    with net.store.tx() as db:db.execute("INSERT INTO functions(project_id,name,status) VALUES(?,?,'open')",(pid,'sqrtf'))
    rule='When the compiler gives a sum its own register, use separate assignments such as `sq=x*x; sq+=y*y; len=sqrtf(sq);` rather than nesting all arithmetic in one expression.'
    response=net.knowledge.share(worker,'game',rule)
    kid=int(response.split('#')[1].split('.')[0]);net.knowledge.decide('game',kid,True)
    assert rule in net.knowledge.for_prompt('game')
    with pytest.raises(NetworkError):net.knowledge.share(worker,'game','I tried to cast `sqrtf` and improved its score by 10; remaining differences are in the loop.')


@pytest.mark.parametrize('flag',['-g3','-O2','-G0'])
def test_flags_can_describe_source_technique_conditions_without_selecting_settings(net,flag):
    worker=agent(net,'claude-opus-1');pid=net._project('game')['id']
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name) VALUES(?,?)",(pid,'library_begin'))
    rule=(f'With GCC and {flag}, debug line notes can affect delay-slot scheduling. '
          'If calls expand from a macro, keep the expanded calls on one source line '
          'such as `library_begin(task); library_end(task);` to test their shared line note.')
    response=net.knowledge.share(worker,'game',rule,evidence='Synthetic caller: score 40 to 20 with unchanged flags.')
    kid=int(response.split('#')[1].split('.')[0])
    item=net.knowledge.listing('game')['items'][0]
    assert item['text']==rule and item['status']=='proposed'
    assert item['evidence'][0]['text'].startswith('Synthetic caller:')
    assert rule not in net.knowledge.for_prompt('game',worker)
    # Context does not exempt an actual function progress report.
    with pytest.raises(NetworkError,match='specific function'):
        net.knowledge.share(worker,'game',f'With GCC {flag}, library_begin is stuck at score 40.')
    net.knowledge.share(agent(net,'gpt-1'),'game',supports=kid)
    assert net.knowledge.listing('game')['items'][0]['status']=='agreed'
    assert rule not in net.knowledge.for_prompt('game',worker)
    net.knowledge.decide('game',kid,True)
    assert rule in net.knowledge.for_prompt('game')


@pytest.mark.parametrize('selection',[
    'This part is built with GCC -O2.', 'The compiler is GCC.',
    'Use the compiler GCC.', 'Switch to GCC.', 'Change flags to -g3.',
    'Try -O2.', 'Use -g3.', 'Switch to -G0.',
])
def test_explicit_compiler_or_flag_selection_still_requires_compiler_tools(net,selection):
    with pytest.raises(NetworkError,match='compiler map'):
        net.knowledge.share(agent(net,'claude-opus-1'),'game',selection)
    assert net.knowledge.listing('game')['items']==[]


def test_function_mentions_only_fetch_token_matches_in_owning_project(net,monkeypatch):
    proj=net.project_row('game');other=net.project_row('other')
    with net.store.tx() as db:
        db.executemany('INSERT INTO functions(project_id,name) VALUES(?,?)',
                       [(proj['id'],f'noise_{i}') for i in range(300)]+[(proj['id'],'zeta'),(proj['id'],'alpha'),(other['id'],'other_only')])
    store=net.knowledge._store(proj['id']);original=store.all
    expected=[r['name'] for r in original('SELECT name FROM functions WHERE project_id=?',proj['id'])
              if r['name'] in {'alpha','zeta','other_only'}]
    fetched=[]
    def read(sql,*args):
        rows=original(sql,*args)
        if 'FROM functions' in sql:fetched.append(len(rows))
        return rows
    monkeypatch.setattr(store,'all',read)
    assert net.knowledge._function_mentions(proj,'alpha alpha zeta other_only')==expected
    assert fetched==[2]
    assert net.knowledge._function_mentions(proj,'123 !!!')==[] and fetched==[2]
    with pytest.raises(ValueError):
        with store.tx() as db:
            db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)',(proj['id'],'new_symbol'))
            assert net.knowledge._function_mentions(proj,'new_symbol')==['new_symbol']
            raise ValueError('rollback')
    assert net.knowledge._function_mentions(proj,'new_symbol')==[]
