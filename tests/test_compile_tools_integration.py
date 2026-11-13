"""The new tools through the real app, pinned compiler and strict objdiff on the synthetic PS4 demo."""

import json
import re
import subprocess
import sys

import pytest

from decompnet import consoles
from decompnet.app import compile_tools, tools
from decompnet.app.engine import RomNetwork
from decompnet.forks import csource
from decompnet.limits import capped

from .test_consoles import _function, _missing
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


@pytest.fixture(scope='module', params=['gc', 'pc', 'xbox', 'nds'])
def patch_demo(request, tmp_path_factory):
    console = consoles.get(request.param)
    missing = _missing(console)
    if missing:
        pytest.skip(f"{console.name}'s demo needs {', '.join(missing)}")
    folder = ROOT / 'projects' / console.demo
    key = folder / 'build/demo.json'
    if not key.exists():
        subprocess.run(capped([sys.executable, str(folder / 'build.py')]), check=True, timeout=300)
    built = json.loads(key.read_text())
    home = tmp_path_factory.mktemp('patch-' + console.id)
    net = RomNetwork(home / 'app.db', home)
    slug = net.import_rom(folder / 'build' / built['game'], confirmed=True, log=lambda *_: None)
    return console, net, slug, built['symbols']


def test_each_project_demo_dispatches_briefs_patches_and_reversible_recipes(patch_demo):
    console, net, slug, symbols = patch_demo
    agent, _ = net.local_agent('patch-' + console.id)
    name, correct = _function(net, slug, symbols, 'abs_s32')
    wrong = correct.replace('v < 0 ? -v : v', 'v + 1')
    assert wrong != correct
    assert not net.attempt(agent, name, wrong, slug)['matched']
    brief = tools.call(net, agent, slug, 'function_brief', {'function': name})
    revision = re.search(r'revision: ([0-9a-f]{64})', brief)[1]
    result = tools.call(net, agent, slug, 'compile_patch', {
        'function': name, 'revision': revision, 'edits': [{'old': 'v + 1', 'new': 'v + 2'}]})
    assert not result.startswith('error:'), result
    brief = tools.call(net, agent, slug, 'function_brief', {'function': name})
    revision = re.search(r'revision: ([0-9a-f]{64})', brief)[1]
    fn = net._function(name, slug)
    _, fp, _ = net.forks.get(slug)
    unit = fp.unit(fn['unit'])
    before, head = (fp.root / unit.source).read_bytes(), fp.git('rev-parse', 'HEAD')
    with net.store.lock:
        database = list(net.store.conn.iterdump())
    # A compiler may score +2 worse than +1; the ordinary judge must keep the better source in that case.
    expression = 'v + 2' if 'v + 2' in brief else 'v + 1'
    args = {'function': name, 'revision': revision, 'recipes': [
        {'name': 'absolute value', 'edits': [{'old': expression, 'new': 'v < 0 ? -v : v'}]}]}
    result = tools.call(net, agent, slug, 'try_recipes', args)
    assert 'Preview only' in result and '100%' in result, result
    assert (fp.root / unit.source).read_bytes() == before and fp.git('rev-parse', 'HEAD') == head
    with net.store.lock:
        assert list(net.store.conn.iterdump()) == database
    result = tools.call(net, agent, slug, 'try_recipes', args | {'apply': True})
    assert 'Applying absolute value' in result, result
    assert net._function(name, slug)['status'] == 'matched'
