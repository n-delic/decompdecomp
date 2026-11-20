"""Repair lookup reads captured evidence without invoking either judge."""
import json
import time

import anyio
import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.app import tools
from decompnet.app.engine import RomNetwork
from decompnet.network import Network

SIGNED = [('|', 'slt $a0, $a1', 'sltu $t0, $t1')]
BRANCH = [('|', 'bne $a0, $a1', 'beq $t0, $t1')]


@pytest.fixture(params=['rom', 'project'])
def corpus(tmp_path, request):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    agent = net.authenticate(net.register_agent('solver'))
    other = net.authenticate(net.register_agent('reviewer'))
    config = {'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    if request.param == 'project':
        config = {'kind': 'fork', 'platform': 'gc_wii', 'compiler': 'Metrowerks'}
    with net.store.tx() as db:
        for slug in ('demo', 'private'):
            db.execute('INSERT INTO projects(slug,name,root,config,big_endian) VALUES(?,?,?,?,1)',
                       (slug, slug, str(tmp_path), json.dumps(config)))
    return net, agent, other


def add_function(net, name, slug='demo', asm=''):
    project_id = Network._project(net, slug)['id']
    store = net.project_store(project_id)
    with store.tx() as db:
        fid = db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,asm) "
                         "VALUES(?,?,16,4,'open','main',?)", (project_id, name, asm)).lastrowid
    return store.one('SELECT * FROM functions WHERE id=?', fid)


def capture(net, agent, fn, rows=(), category='types'):
    name = fn['name']
    Network._record(net, fn, agent, 12, f'int {name}(unsigned a) {{ return a > 2; }}\n', 0, mismatch_rows=rows)
    Network._record(net, fn, agent, 0, f'int {name}(int a) {{ return a > 2; }}\n', 0, matched=True)
    store = net.project_store(fn['project_id'])
    case = store.one('SELECT id FROM main.repair_examples WHERE function_id=?', fn['id'])['id']
    slug = store.one('SELECT slug FROM projects WHERE id=?', fn['project_id'])['slug']
    net.repair_feedback.report(agent, slug, name, category=category)
    return case


def live_claim(net, agent, fn, expires_in=600):
    with net.project_store(fn['project_id']).tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',
                   (fn['id'], agent.id, time.time(), time.time() + expires_in))


def test_search_ranks_examples_with_claim_context_and_votes(corpus):
    net, agent, other = corpus
    def call(name, **args):
        return tools.call(net, agent, 'demo', name, args)

    related = capture(net, other, add_function(net, 'related'), SIGNED)
    unrelated = capture(net, other, add_function(net, 'unrelated'), BRANCH)
    voted = capture(net, other, add_function(net, 'voted'), SIGNED)
    legacy = capture(net, other, add_function(net, 'old_example', asm='/* 0 */ slt $a0, $a1'))
    excluded = capture(net, other, add_function(net, 'excluded'), SIGNED, category='casts')
    foreign = capture(net, other, add_function(net, 'foreign', 'private'), SIGNED)
    current = add_function(net, 'current')
    live_claim(net, agent, current)
    Network._record(net, current, agent, 8, 'trial', 0, mismatch_rows=SIGNED)

    assert 'Upvoted' in call('upvote_repair_example', id=voted)
    assert 'already upvoted' in call('upvote_repair_example', id=voted)
    assert 'Upvoted' in tools.call(net, other, 'demo', 'upvote_repair_example', {'id': unrelated})
    result = call('search_repair_examples', category='types')
    assert result.splitlines()[0].startswith('Repair examples for current')
    assert [line.split()[0] for line in result.splitlines()[1:]] == [f'#{i}' for i in (voted, related, legacy, unrelated)]
    assert '1 vote]' in result and '+int' not in result and 'sltu → slt' in result and 'unsigned a' not in result
    assert 'excluded' not in result and 'foreign' not in result
    assert call('upvote_repair_example', id=foreign).startswith('error:')

    batch = call('get_repair_examples', ids=[unrelated, foreign, related, unrelated])
    assert batch.index(f'#{unrelated}') < batch.index(f'#{related}')
    assert batch.count(f'#{unrelated} ') == 1
    assert f'#{foreign} is unavailable' in batch
    assert '-int related(unsigned a)' in batch and '+int related(int a)' in batch
    assert ('Metrowerks' if 'objdiff' in batch else 'gcc-2.7.2-kmc -O2') in batch
    assert call('get_repair_examples', ids=[related] * 7).startswith('error:')
    assert call('get_repair_examples', ids=[excluded]).startswith(f'#{excluded}')


def test_search_uses_explicit_context_for_a_batch_and_ignores_expired_claims(corpus):
    net, agent, _ = corpus
    first = add_function(net, 'first')
    second = add_function(net, 'second')
    live_claim(net, agent, first)
    live_claim(net, agent, second, expires_in=-1)
    capture(net, agent, add_function(net, 'example'), SIGNED)
    args = {'category': 'types'}
    def search(**extra):
        return tools.call(net, agent, 'demo', 'search_repair_examples', args | extra)
    assert search().startswith('Repair examples for first')
    with net.store.tx() as db:
        db.execute('UPDATE claims SET expires_at=?', (time.time() + 600,))
    assert search().startswith('Repair examples by category')
    assert search(function='second').startswith('Repair examples for second')
    assert search(function='nonexistent').startswith('error:')
    assert search(category='invalid').startswith('error:')


def test_mcp_and_api_offer_the_same_lookup_tools(corpus):
    net, agent, _ = corpus
    case = capture(net, agent, add_function(net, 'example'), SIGNED)
    mcp = tools.build_mcp(net, lambda ctx: (agent, 'demo'))

    async def run():
        for name, args, expected in (
            ('search_repair_examples', {'category': 'types'}, 'Select IDs'),
            ('get_repair_examples', {'ids': [case]}, 'Exact match'),
            ('upvote_repair_example', {'id': case}, 'Upvoted'),
        ):
            content = await mcp.call_tool(name, args)
            assert expected in str(content)
    anyio.run(run)
    names = ['search_repair_examples', 'get_repair_examples', 'upvote_repair_example']
    schemas = {s['function']['name']: s['function']['parameters'] for s in api_tool_schemas(names=names)}
    assert set(schemas) == set(names)
    assert schemas['search_repair_examples']['required'] == ['category']
    assert schemas['get_repair_examples']['properties']['ids']['maxItems'] == 6


def test_memory_access_order_is_an_agent_category_and_search_filter(corpus):
    net, agent, _ = corpus
    case_id = capture(net, agent, add_function(net, 'ordered_reads'), BRANCH, category='memory_access_order')
    result = tools.call(net, agent, 'demo', 'search_repair_examples', {'category': 'memory_access_order'})
    assert f'#{case_id} ordered_reads' in result
    schema = {s['function']['name']: s['function']['parameters'] for s in api_tool_schemas(
        names=['compile_and_diff', 'report_repair', 'search_repair_examples'])}
    for name, field in (('compile_and_diff','repair_category'),('report_repair','category'),
                        ('search_repair_examples','category')):
        assert 'memory_access_order' in schema[name]['properties'][field]['description']


def test_retry_surfaces_one_verified_profile_and_shape_match(corpus):
    from decompnet.app.repair_search import retry_example

    net, agent, other = corpus
    past = add_function(net, 'past')
    case = capture(net, other, past, SIGNED)
    net.repair_feedback.report(other, 'demo', 'past', category='types', change='Recover the signed comparison')
    current = add_function(net, 'current')
    proj = net.project_row('demo')
    found = retry_example(net, proj, current, SIGNED)
    if net.fork_slug('demo'):
        assert not found  # No exact per-file flags in this imported repair record.
    else:
        assert f'#{case}' in found and 'Recover the signed comparison' in found
    assert not retry_example(net, proj, current, BRANCH)
