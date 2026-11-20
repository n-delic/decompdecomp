"""Opted-in helpers receive one coordinated claim at each completed handoff."""
import json

import anyio
import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.app import automatic_assignment, session_budget, tool_policy, tools
from decompnet.app.agents import Session
from decompnet.app.tool_reader import _hidden
from tests.test_claims import _game, _held


def setup(tmp_path, monkeypatch, *, kind='rom', **options):
    net, pid = _game(tmp_path, [('first', 8, 'open'), ('second', 16, 'open')])
    if kind == 'fork':
        with net.store.tx() as db:
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps({'kind': kind}), pid))
    worker, _ = net.local_agent('automatic-worker')
    s = Session('auto', 'api', 'Automatic worker', 'game', worker.name,
                {'automatic_assignment': True, **options})
    s.state = 'working'
    automatic_assignment.bind(net, worker, s)
    budget = session_budget.start(net, worker.id, s.slug, s.options)
    def brief(fn, agent, header):
        return f'{header} {fn["name"]}'
    monkeypatch.setattr(net, '_brief', brief)
    monkeypatch.setattr(net.forks, 'brief', brief)
    monkeypatch.setattr(net, 'sdk_functions', lambda _: {})
    monkeypatch.setattr(tools, '_project_info', lambda *a, **kw: 'Project ready')
    return net, worker, s, budget


@pytest.mark.parametrize('kind', ['rom', 'fork'])
def test_authenticated_assignment_hands_over_a_verified_match(tmp_path, monkeypatch, kind):
    net, worker, _, budget = setup(tmp_path, monkeypatch, kind=kind)
    net._pending_tool_handshakes = {(worker.id, 'game')}
    first = tools.call(net, worker, 'game', 'project_info', {'detail': False})
    assert 'CLAIMED first' in first and _held(net, worker) == 'first'

    def match(net, agent, slug, function, source):
        fn = net._function(function, slug)
        net._record(fn, agent, 0, source, 1, matched=True)
        return 'Verified match'
    monkeypatch.setitem(tools.HANDLERS, 'compile_and_diff', match)
    second = tools.call(net, worker, 'game', 'compile_and_diff',
                        {'function': 'first', 'source': 'void first(void) {}'})
    assert 'Verified match' in second and 'CLAIMED second' in second
    assert _held(net, worker) == 'second' and budget.assignments == 1


@pytest.mark.parametrize('limit', ['functions', 'context', 'empty', 'stop', None])
def test_declined_release_keeps_claim_and_accepted_release_hands_over(tmp_path, monkeypatch, limit):
    net, worker, session, budget = setup(tmp_path, monkeypatch, per_session=1 if limit == 'functions' else 8,
                                  session_tool_limit=2 if limit == 'context' else 80)
    fn = net.claim_row(worker, 'game', 'first')
    call = lambda **args: tools.call(net, worker, 'game', 'release_and_continue',
                                     {'function': 'first', 'notes': 'Missing compiler evidence', **args})
    assert 'CLAIMED second' not in call()
    assert _held(net, worker) == 'first' and budget.assignments == 0
    if limit == 'empty':
        other, _ = net.local_agent('busy-worker')
        net.claim_row(other, 'game', 'second')
    if limit == 'stop':
        session.stop.set()
    net._record(fn, worker, 5, 'void first(void) {}', 1)
    accepted = call(move_on=True)
    assert budget.assignments == 1
    if limit:
        assert _held(net, worker) is None and budget.ready()
        assert ('No eligible work' in accepted if limit == 'empty' else 'session is complete' in accepted)
    else:
        assert 'CLAIMED second' in accepted and _held(net, worker) == 'second'


def test_tool_policy_is_per_worker_and_matches_mcp_and_api(tmp_path, monkeypatch):
    net, worker, session, _ = setup(tmp_path, monkeypatch)
    manual, _ = net.local_agent('manual-worker')
    assert tool_policy.connection_required(net, worker) <= set(tools.project_tools(net, 'game', worker))
    mcp = tools.build_mcp(net, lambda ctx: (worker, 'game'))
    async def catalog():
        return {tool.name for tool in await mcp.list_tools()}
    definitions = anyio.run(catalog)
    for agent, expected in [(worker, {'release_and_continue'}), (manual, {'claim_function', 'release_function'})]:
        names = tools.project_tools(net, 'game', agent)
        api = {tool['function']['name'] for tool in api_tool_schemas(names=names)}
        claim_tools = {'claim_function', 'release_function', 'release_and_continue'}
        assert api & claim_tools == definitions.intersection(names) & claim_tools == expected
    assert _hidden(net, {str(worker.id): 'game'}) == {str(worker.id): ['claim_function']}
    assert 'app assigns' in tools.call(net, worker, 'game', 'claim_function', {})
    assert _held(net, worker) is None
    assert 'disabled' in tools.call(net, manual, 'game', 'release_and_continue',
                                    {'function': 'first', 'notes': 'Test'})
    assert session.snapshot()['automatic_assignment']
