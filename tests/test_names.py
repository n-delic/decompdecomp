"""Agents call functions by the names they read: in assembly, in a source file, in another tool's output. A fork
stores a name that repeats across its files as symbol@unit; every tool must still find the function the agent
means, or ask which one when it can't tell."""

import json

import pytest

from decompnet.app import tools
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def net(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "ppc", 1, str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        for name, unit, status, meta in [
                ("fn_1_16F0@mdsingdll/REL/mdsingdll/mdsing", "mdsingdll/REL/mdsingdll/mdsing", "open", {"symbol": "fn_1_16F0"}),
                ("fn_1_16F0@m656DLL/REL/m656DLL/matrix", "m656DLL/REL/m656DLL/matrix", "open", {"symbol": "fn_1_16F0"}),
                ("fn_1_16F0@m615DLL/auto_00_text", "m615DLL/auto_00_text", "nosource", {"symbol": "fn_1_16F0"}),
                ("fn_1_8CE4@m605DLL/auto_text", "m605DLL/auto_text", "nosource", {"symbol": "fn_1_8CE4"}),
                ("fn_1_8CE4@mdsingdll/REL/mdsingdll/mdsing", "mdsingdll/REL/mdsingdll/mdsing", "open", {"symbol": "fn_1_8CE4"}),
                ("fn_1_17958", "mdsingdll/REL/mdsingdll/mdsing", "open", {"symbol": "fn_1_17958"}),
                ("Update__6PlayerFv", "game/player", "open", {"symbol": "Update__6PlayerFv",
                                                              "demangled": "Player::Update(void)"}),
                # MSVC: the demangled name starts with the return type and the convention
                ("?InitFog@@YA_NPAUSFog@@@Z", "render/fog", "open",
                 {"symbol": "?InitFog@@YA_NPAUSFog@@@Z", "demangled": "bool __cdecl InitFog(struct SFog *)"}),
                ("?CalcSpeed@CDriver@@QAEMM@Z", "ai/driver", "open",
                 {"symbol": "?CalcSpeed@CDriver@@QAEMM@Z",
                  "demangled": "public: float __thiscall CDriver::CalcSpeed(float)"})]:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,meta) VALUES(?,?,?,?,?,?,?)",
                       (pid, name, 40, 10, status, unit, json.dumps(meta)))
    return net


def test_names_as_agents_write_them(net):
    find = lambda n: net._function(n, "game")["name"]
    assert find("fn_1_17958@mdsingdll/REL/mdsingdll/mdsing") == "fn_1_17958"  # @unit on a name that doesn't repeat
    assert find("fn_1_8CE4") == "fn_1_8CE4@mdsingdll/REL/mdsingdll/mdsing"  # the other one has no source file
    assert find("fn_1_16F0@mdsing") == "fn_1_16F0@mdsingdll/REL/mdsingdll/mdsing"
    assert find("`fn_1_17958()`") == "fn_1_17958"
    assert find("Player::Update") == "Update__6PlayerFv"
    # MSVC-decorated names, as agents write them (they got "no function 'InitFog'" back)
    assert find("InitFog") == "?InitFog@@YA_NPAUSFog@@@Z"
    assert find("CDriver::CalcSpeed") == "?CalcSpeed@CDriver@@QAEMM@Z"
    assert find("CalcSpeed") == "?CalcSpeed@CDriver@@QAEMM@Z"
    with pytest.raises(NetworkError) as e:
        find("fn_1_16F0")
    assert "fn_1_16F0@mdsingdll/REL/mdsingdll/mdsing" in str(e.value) and "matrix" in str(e.value)
    with pytest.raises(NetworkError):
        find("fn_1_99999")


def test_a_plain_name_means_the_function_the_agent_works_on(net):
    agent, _ = net.local_agent("agent-1")
    full = "fn_1_16F0@mdsingdll/REL/mdsingdll/mdsing"
    assert net.claim_row(agent, "game", full)["name"] == full
    # claiming it again by its full name (units have slashes) renews the claim instead of refusing
    assert net.claim_row(agent, "game", full)["name"] == full
    assert tools._named(net, agent, "game", {"function": "fn_1_16F0"})["function"] == full
    assert net.resolve_function(agent, "game", "fn_1_8CE4") == "fn_1_8CE4@mdsingdll/REL/mdsingdll/mdsing"
    other, _ = net.local_agent("agent-2")
    with pytest.raises(NetworkError):
        net.resolve_function(other, "game", "fn_1_16F0")


@pytest.mark.parametrize('name', ['fn_1_17958', 'fn_1_16F0@mdsingdll/REL/mdsingdll/mdsing',
                                  'fn_1_17958@mdsing', 'Player::Update'])
def test_claim_labels_work_with_an_explicit_project(net, name):
    """Claim headings print project/name; tools already know the same project."""
    agent, _ = net.local_agent('reader')
    expected = net._function(name, 'game')['id']
    assert net._function('game/' + name, 'game')['id'] == expected
    assert tools._named(net, agent, 'game', {'function': 'game/' + name})['function'] == net._function(name, 'game')['name']


def test_qualified_names_retain_ambiguity_and_claim_selection(net):
    agent, _ = net.local_agent('reader')
    with pytest.raises(NetworkError, match='different files'):
        net._function('game/fn_1_16F0', 'game')
    full = 'fn_1_16F0@mdsingdll/REL/mdsingdll/mdsing'
    net.claim_row(agent, 'game', full)
    assert net.resolve_function(agent, 'game', 'game/fn_1_16F0') == full
    assert net.claim_row(agent, 'game', 'game/' + full)['name'] == full
    other, _ = net.local_agent('other-reader')
    with pytest.raises(NetworkError, match='different files'):
        net.resolve_function(other, 'game', 'game/fn_1_16F0')


def test_qualification_never_switches_an_explicit_project(net):
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,config) VALUES('other','Other','.', '{}')")
    for query in ('game/fn_1_17958', 'game/Player::Update', 'game/fn_1_16F0@mdsing'):
        with pytest.raises(NetworkError):
            net._function(query, 'other')
    assert net._function('game/fn_1_17958', None)['name'] == 'fn_1_17958'
    assert net._function('fn_1_16F0@mdsingdll/REL/mdsingdll/mdsing', None)['unit'].startswith('mdsingdll/')


def test_exact_stored_path_names_take_precedence(net):
    from decompnet.network import Network

    pid = net._project('game')['id']
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?,?,?,?,?,?)",
                   (pid, 'game/fn_1_17958', 4, 1, 'open', 'path'))
    for lookup in (net._function, lambda name, project: Network._function(net, name, project)):
        assert lookup('game/fn_1_17958', 'game')['unit'] == 'path'
        assert lookup('game/fn_1_16F0@mdsingdll/REL/mdsingdll/mdsing', 'game')['unit'].startswith('mdsingdll/')
