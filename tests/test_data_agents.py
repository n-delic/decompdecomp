"""Data agents (app/data_agents.py): the queue, claims, their tool scope and the judge's verdicts, on the homemade N64
image of test_data_units (a table at DATA and a count at DATA+0x10, both used by `boot`)."""
from types import SimpleNamespace

import anyio
import pytest
from mcp.server.mcpserver import Context

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


def test_jump_tables_belong_to_their_functions_not_the_data_queue(project):
    net = project
    with net.store.tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'jtbl_boot',?,16,'jtbl')", (DATA,))
    with net.progress.view():
        assert data_agents.queue(net, net.project_row("game")) == [DATA + 0x10]


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


def test_starting_counting_and_tool_calls_never_wait_for_a_progress_sync(project, monkeypatch):
    """Starting a data agent waited 35 seconds at "Validating worker selection": each read outside a progress view
    first synced the project's saved progress, which a big project just after the app starts takes a minute for."""
    from decompnet.app.tools import _invoke_once
    net = project
    monkeypatch.setattr(net.progress, "sync", lambda *a, **k: pytest.fail("data agents must not sync saved progress"))
    assert data_agents.configure(net, "game", {"task": "data"}, 1) == {"task": "data"}
    assert data_agents.left(net, "game") == 2
    agent = start(net, "writer")
    assert _invoke_once(net, agent, "game", "claim_data", {}).startswith("Claimed D_80000500")


@pytest.mark.parametrize('reader_process', [False, True])
@pytest.mark.parametrize('tool,arguments', [
    ('get_data_unit', {'name': 'D_80000500'}),
    ('claim_data', {'name': 'D_80000500'}),
    ('claim_data', {}),
    ('release_data', {'name': 'D_80000500', 'note': 'Synthetic table'}),
    ('compile_data', {'name': 'D_80000500', 'source': TABLE}),
])
def test_mcp_data_tools_keep_the_unit_name_separate_from_the_tool(project, monkeypatch, reader_process, tool, arguments):
    """Exercise MCP's registered wrappers: direct dispatch missed the duplicate `name` argument."""
    from decompnet.app import tools
    net = project
    agent = start(net, 'writer')
    monkeypatch.setattr(Context, 'headers', property(lambda self: {}))
    calls = []
    def dispatch(net, who, slug, name, args, **kwargs):
        assert who == agent and slug == 'game'
        calls.append((name, args))
        return 'Synthetic verdict'
    def reader(ctx, name, args):
        return dispatch(net, agent, 'game', name, args)
    monkeypatch.setattr(tools, '_dispatch', dispatch)
    records = []
    server = tools.build_mcp(net, lambda ctx: (agent, 'game'),
                             lambda *args: records.append(args[-1]), reader=reader if reader_process else None)
    async def request():return await server.call_tool(tool, arguments)
    assert 'Synthetic verdict' in str(anyio.run(request))
    expected = dict(arguments)
    if tool == 'claim_data':expected.setdefault('name', '')
    assert calls == [(tool, expected)]
    if not reader_process:
        assert records[0]['name'] == tool and records[0]['arguments'] == expected
        assert records[-1]['content'] == 'Synthetic verdict'


def test_mcp_data_claim_and_release_use_real_handlers(project, monkeypatch):
    from decompnet.app import tools
    net = project
    agent = start(net, 'writer')
    monkeypatch.setattr(Context, 'headers', property(lambda self: {}))
    server = tools.build_mcp(net, lambda ctx: (agent, 'game'))
    async def request():
        claim = await server.call_tool('claim_data', {'name': 'D_80000500'})
        assert 'Claimed D_80000500' in str(claim)
        assert data_agents._held(net, net.project_row('game'), agent) == {DATA}
        await server.call_tool('release_data', {'name': 'D_80000500', 'note': 'Synthetic table'})
        assert data_agents._held(net, net.project_row('game'), agent) == set()
    anyio.run(request)
