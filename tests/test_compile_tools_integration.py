"""The new tools through the real app, pinned compiler and strict objdiff on the synthetic PS4 demo."""

import re

from decompnet.app import compile_tools, tools
from decompnet.forks import csource

from .test_ps4_workflows import ROOT, ps4_demo  # noqa: F401  builds the shared synthetic fixture with pinned tools


def test_patches_and_previews_use_the_real_build_judge_and_keep_matched_neighbors(ps4_demo):  # noqa: F811 fixture
    net, slug, _ = ps4_demo
    agent, _ = net.local_agent('patch-preview-check')
    correct = csource.get((ROOT / 'projects/demo-ps4/extras.c').read_text(), 'demo_sum')
    wrong = correct.replace('a + b', 'a - b')
    neighbor = csource.get((ROOT / 'projects/demo-n64/original/game.c').read_text(), 'abs_s32')
    assert net.attempt(agent, 'abs_s32', neighbor, slug)['matched']
    assert not net.attempt(agent, 'demo_sum', wrong, slug)['matched']
    fn = net._function('demo_sum', slug)
    _, fp, _ = net.forks.get(slug)
    unit = fp.unit(fn['unit'])
    initial = compile_tools.snapshot(net.forks, slug, 'demo_sum')[2]
    result = compile_tools.compile_patch(net, agent, slug, 'demo_sum', initial, [{'old': 'a - b', 'new': 'a ^ b'}])
    assert 'revision:' in result and 'a ^ b' in (fp.root / unit.source).read_text()
    before = (fp.root / unit.source).read_bytes()
    with net.store.lock:
        database = list(net.store.conn.iterdump())
    git_head = fp.git('rev-parse', 'HEAD')
    brief = tools.call(net, agent, slug, 'function_brief', {'function': 'demo_sum'})
    revision = re.search(r'revision: ([0-9a-f]{64})', brief)[1]
    recipes = [{'name': 'add', 'edits': [{'old': 'a ^ b', 'new': 'a + b'}]}]
    result = compile_tools.try_recipes(net, agent, slug, 'demo_sum', revision, recipes)
    assert 'Preview only' in result and '100%' in result, result
    assert (fp.root / unit.source).read_bytes() == before and fp.git('rev-parse', 'HEAD') == git_head
    with net.store.lock:
        assert list(net.store.conn.iterdump()) == database
    result = compile_tools.try_recipes(net, agent, slug, 'demo_sum', revision, recipes, apply=True)
    assert 'Applying add' in result, result
    assert net._function('demo_sum', slug)['status'] == 'matched'
    assert net._function('abs_s32', slug)['status'] == 'matched'
    current = compile_tools.snapshot(net.forks, slug, 'demo_sum')[2]
    good_source = (fp.root / unit.source).read_bytes()
    result = compile_tools.compile_patch(net, agent, slug, 'demo_sum', current, [{'old': 'a + b', 'new': 'a - b'}])
    assert 'already matches' in result, result
    assert net._function('demo_sum', slug)['status'] == 'matched'
    assert (fp.root / unit.source).read_bytes() == good_source
