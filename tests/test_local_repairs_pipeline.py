"""The local source pass obeys the same claims, budgets and judge as random permutation."""
import ast
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import local_repairs, permute
from decompnet.app.permute import State
from tests.test_permute import AGENT, FakeNet, ScriptedPermuter


def test_the_registry_reaches_every_ported_source_generator():
    tree = ast.parse(Path(local_repairs.__file__).read_text())
    imported = {alias.name for node in tree.body if isinstance(node, ast.ImportFrom) and node.level == 1
                and node.module is None for alias in node.names}
    called = {node.func.value.id for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)}
    assert len(imported) >= 110
    assert imported <= called


def test_source_proposals_are_unique_bounded_and_preserve_other_functions():
    source = 'int f(int x){int unused;int a;a=x+1;return a;}\nint other(void){return 42;}'
    choices = local_repairs.variants(source, 'f', 'mips', limit=3)
    assert 0 < len(choices) <= 3
    assert len({text for _, text in choices}) == len(choices)
    assert all(text != source and text.endswith('int other(void){return 42;}') for _, text in choices)
    assert local_repairs.variants(source, 'f', limit=0) == []
    assert local_repairs.variants(source, 'f', stopped=lambda: True) == []
    assert local_repairs.variants(source, 'f', deadline=time.monotonic()-1) == []
    assert local_repairs.variants(source+' ' * local_repairs.MAX_SOURCE_BYTES, 'f') == []


@pytest.mark.parametrize('source', ['int f(void){asm("nop");return 1;}',
                                    'namespace N {int f(){return 1;}}'])
def test_opaque_source_is_left_to_the_existing_generator(source):
    assert local_repairs.variants(source, 'f', 'mips') == []


def test_flags_reach_line_sensitive_repairs_as_compiler_tokens(monkeypatch):
    seen = []
    monkeypatch.setattr(local_repairs.debug_boundary_repairs, 'variants',
                        lambda **kw: seen.append(kw['flags']) or [])
    local_repairs.variants('int f(void){return 0;}', 'f', 'mips', '-O2 -g3')
    assert seen == [('-O2', '-g3')]


@pytest.mark.parametrize('fork', [None, 'public-game'])
def test_both_modes_judge_local_candidates_and_stop_on_an_exact_match(monkeypatch, fork):
    net = FakeNet()
    net.fork_slug = lambda slug: fork
    net.forks = SimpleNamespace(permuter_header=lambda *args: 'typedef int s32;',
                                permuter_flags=lambda *args: '-O2 -g')
    seen = []
    def proposals(source, name, arch, flags, header, **kwargs):
        seen.append((arch, flags, header, kwargs))
        return [('broken', 'broken'), ('better', 'closer'), ('exact', 'same'), ('later', 'never')]
    monkeypatch.setattr(permute.local_repairs, 'variants', proposals)
    monkeypatch.setattr(permute, 'available', lambda: False)
    worker = ScriptedPermuter(net, {'broken': None, 'closer': 10, 'same': 0})
    state = State()
    worker._permute(AGENT, net.project_row('game'), net.function(), state)
    assert worker.judged == ['broken', 'closer', 'same']
    assert (state.variants, state.improved, state.matched) == (3, 2, 1)
    assert seen[0][0] == 'mips' and seen[0][1] == ('-O2 -g' if fork else '-O2')
    assert seen[0][3]['deadline'] > time.monotonic()-1
    assert net.store.one('SELECT COUNT(*) AS n FROM claims')['n'] == 0


def test_a_stop_during_local_judging_releases_the_function(monkeypatch):
    net = FakeNet()
    state = State()
    monkeypatch.setattr(permute, 'available', lambda: False)
    monkeypatch.setattr(permute.local_repairs, 'variants', lambda *args, **kw: [('one','a'),('two','b')])
    worker = ScriptedPermuter(net, {})
    def score(*args):
        state.stop.set()
        return None
    monkeypatch.setattr(worker, '_score', score)
    worker._permute(AGENT, net.project_row('game'), net.function(), state)
    assert state.variants == 1
    assert net.store.one('SELECT COUNT(*) AS n FROM claims')['n'] == 0


def test_debug_layout_proposals_respect_the_last_debug_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(local_repairs.pattern_repairs, 'variants',
                        lambda **kw: seen.append(kw['debug_lines']) or [])
    local_repairs.variants('int f(void){return 0;}', 'f', 'mips', '-g -g0')
    local_repairs.variants('int f(void){return 0;}', 'f', 'mips', '-g0 -g3')
    assert seen == [False, True]


def test_imported_flags_use_the_file_connector_or_known_scratch_metadata(monkeypatch):
    from decompnet.app import forkengine
    root = SimpleNamespace(unit=lambda name: SimpleNamespace(source='src/code.c',scratch={'c_flags':'-O2 -g'}))
    engine = SimpleNamespace(get=lambda slug: (None,root,{'platform':'n64'}),_fn=lambda *args:{'unit':'code'})
    monkeypatch.setattr(forkengine.consoles, 'get', lambda platform:SimpleNamespace(file_flags=None))
    assert forkengine.ForkEngine.permuter_flags(engine,'game','f') == '-O2 -g'
    monkeypatch.setattr(forkengine.consoles, 'get',
                        lambda platform:SimpleNamespace(file_flags=lambda fp,source:('-O1','-g3')))
    assert forkengine.ForkEngine.permuter_flags(engine,'game','f') == ('-O1','-g3')


def test_an_unrelated_volatile_global_does_not_disable_safe_local_storage_repairs():
    source = 'volatile int tick;void f(void){int unused;int used;used=1;consume(used);}'
    choices = local_repairs.variants(source, 'f', 'mips')
    assert any(family == 'unused_scalar_local' for family, _ in choices)
    assert all(text.startswith('volatile int tick;') for _, text in choices)
