"""Data agents (app/data_agents.py): the queue, claims, their tool scope and the judge's verdicts, on the homemade N64
image of test_data_units (a table at DATA and a count at DATA+0x10, both used by `boot`)."""
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app import data_agents, data_units, tool_policy
from decompnet.app.tools import HANDLERS, _dispatch, tool_names
from decompnet.network import NetworkError
from tests.test_data_units import DATA, build


@pytest.fixture
def project(tmp_path):
    net = build(tmp_path)
    yield net
    if hasattr(net, "autosave"):
        net.autosave.close()


@pytest.fixture
def compiles():
    if not toolchains.installed("gcc-2.7.2-kmc"):
        pytest.skip("Pinned KMC compiler is not installed; never download it here.")

TABLE = "extern void boot(void);\nvoid (*D_80000500[4])(void) = { boot, boot, 0, 0 };"


def start(net, name):
    """An agent bound to a data session, as agents.py binds one."""
    agent, _ = net.local_agent(name)
    data_agents.bind(net, SimpleNamespace(options={"task": "data", "per_session": 3}, slug="game"), agent)
    return agent


def test_agents_claim_the_game_s_data_one_unit_each_in_address_order(project):
    net = project
    with net.progress.view():
        proj = net.project_row("game")
        assert data_agents.queue(net, proj) == [DATA, DATA + 0x10]
        first, second = start(net, "one"), start(net, "two")
        text = data_agents.claim(net, first, "game")
        assert text.startswith("Claimed D_80000500 (2 units left)") and "Used by: boot" in text
        assert data_agents.claim(net, first, "game").startswith("Claimed D_80000500")  # holding one, it's that one
        assert data_agents.claim(net, second, "game").startswith("Claimed D_80000510")
        assert data_agents.claim(net, second, "game", "D_80000500") == "Another agent holds that unit."
        data_agents.release(net, first, "game", "D_80000500", "it's a table of handlers")
        # given back, it goes behind the units nobody has tried: the agent isn't handed the same one again
        assert data_agents.queue(net, proj) == [DATA + 0x10, DATA]
        data_agents.release(net, second, "game", "D_80000510")
        third = start(net, "three")
        again = data_agents.claim(net, third, "game", "D_80000500")
        assert "A previous try left this note:\nit's a table of handlers" in again
        assert data_agents.left(net, "game") == 2


def test_a_data_agent_sees_only_its_tools_and_compiles_only_what_it_holds(project):
    net = project
    with net.progress.view():
        agent = start(net, "writer")
        visible = set(tool_policy.visible(net, "game", list(HANDLERS), agent))
        assert visible <= data_agents.TOOLS and data_agents.REQUIRED <= visible
        assert tool_policy.connection_required(net, agent) == data_agents.REQUIRED
        with pytest.raises(NetworkError, match="isn't one of their tools"):
            _dispatch(net, agent, "game", "claim_function", {})
        with pytest.raises(NetworkError, match="Claim this unit first"):
            data_agents.guard(net, agent, "game", "compile_data", {"name": "D_80000500", "source": TABLE})
        data_agents.claim(net, agent, "game")
        data_agents.guard(net, agent, "game", "compile_data", {"name": "D_80000500", "source": TABLE})
        other, _ = net.local_agent("matcher")  # an agent of another task isn't held to it
        data_agents.guard(net, other, "game", "claim_function", {})


def test_a_match_frees_the_unit_counts_as_work_and_leaves_the_queue(project, compiles):
    net = project
    with net.progress.view():
        agent = start(net, "writer")
        data_agents.claim(net, agent, "game")
        wrong = HANDLERS["compile_data"](net, agent, "game", "D_80000500", TABLE.replace("boot, boot, 0", "boot, 0, 0"))
        assert wrong.startswith("Not yet")
        text = HANDLERS["compile_data"](net, agent, "game", "D_80000500", TABLE)
        assert text.startswith("MATCH") and text.endswith("claim_data() for the next unit.")
        assert net._session_diagnostic_compiles[agent.id] == 2  # the session's progress counts data compiles
        proj = net.project_row("game")
        assert data_agents.queue(net, proj) == [DATA + 0x10]
        assert data_agents._held(net, proj, agent) == set()
        assert data_units.unit(net, "game", "D_80000500")["status"] == "matched"


def test_the_tools_are_for_rom_projects_and_the_prompt_says_the_loop(project):
    assert {"claim_data", "release_data"} <= set(tool_names(fork=False))
    assert not {"claim_data", "release_data"} & set(tool_names(fork=True))
    net = project
    with net.progress.view():
        text = data_agents.prompt(net, SimpleNamespace(options={"per_session": 3}, slug="game"))
    assert "claim_data()" in text and "compile_data(name, source)" in text and "up to 3 units" in text
