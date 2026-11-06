"""Compiler drift and misleading scalar scores must not send another agent through the same search."""

import json
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from decompnet import field_probes, matching_search
from decompnet.app.permute import Permuter, State
from decompnet.elfobj import FunctionObj
from tests.test_permute import AGENT, FakeNet

HEADER = 'typedef signed int s32;'
SOURCE = '''extern s32 input[];
extern s32 output[];
void setup(s32);
void f(void) {
    s32 base;
    setup(1);
    base = input[0];
    output[0] = base;
    output[1] = base + 16;
    output[8] = base + 32;
    output[9] = base + 48;
    output[16] = 0;
}
'''


def test_layout_probes_include_mixed_fields_and_preserve_padding_and_stores():
    variants = field_probes.candidates(SOURCE, 'f', HEADER)
    assert len(variants) == len(set(variants)) == 16
    mixed = variants[3]
    assert 'unsigned char *f_0;' in mixed and 'unsigned char *f_4;' in mixed
    assert 's32 f_32;' in mixed and 's32 f_36;' in mixed
    assert 'unsigned char pad_8[24];' in mixed and 'unsigned char pad_40[24];' in mixed
    assert 'output.f_32 = (s32)(base + 32);' in mixed
    assert 'output.f_64 = (0);' in mixed
    assert 'extern unsigned char *input[];' in mixed
    assert 'setup(1);' in mixed


def test_layout_probes_refuse_shared_types_unknown_widths_aliases_and_extra_effects():
    for source, header in [
        (SOURCE, HEADER + '\nextern s32 output[];'),
        (SOURCE, 'typedef long long s32;'),
        (SOURCE.replace('output[9]', 'output[8]'), HEADER),
        (SOURCE.replace('output', 'input'), HEADER),
        (SOURCE.replace('setup(1);', 'setup(base);'), HEADER),
        (SOURCE.replace('output[16] = 0;', 'output[16] = input[1];'), HEADER),
        (SOURCE.replace('output[16] = 0;', 'output[16] = 0; setup(2);'), HEADER),
        (SOURCE.replace('output[16]', 'output[2000]'), HEADER),
        (SOURCE + 'void helper(void) {}', HEADER),
    ]:
        assert field_probes.candidates(source, 'f', header) == []


def test_layout_indices_use_c_octal_and_hexadecimal_rules():
    source = SOURCE.replace('output[8]', 'output[010]').replace('output[9]', 'output[0x9]')
    assert field_probes.candidates(source, 'f', HEADER) == field_probes.candidates(SOURCE, 'f', HEADER)
    assert field_probes.candidates(SOURCE.replace('output[8]', 'output[08]'), 'f', HEADER) == []


def test_order_metric_prefers_one_moved_store_to_wrong_values():
    target = ['load base', 'add a', 'add b', 'store base, first', 'store a, second', 'store b, third']
    moved = ['load base', 'store base, first', 'add a', 'add b', 'store a, second', 'store b, third']
    wrong = ['load base', 'add a', 'add b', 'store b, first', 'store base, second', 'store a, third']
    assert matching_search.instruction_distance(target, moved) == 2
    assert matching_search.instruction_distance(target, wrong) == 3


def test_history_counts_across_agents_and_does_not_invent_old_compiler_settings():
    net = FakeNet()
    with net.store.tx() as db:
        for i in range(26):
            db.execute('INSERT INTO attempts(function_id,agent_id,score,source,context_version) VALUES(1,?,?,?,1)',
                       (1 + i % 3, 40, net.function()['best_source']))
    text = '\n'.join(matching_search.history(net.store, net.function(), {'toolchain': 'new'}))
    assert '24 recorded attempts across 3 agents' in text
    assert 'not recorded' in text
    with net.store.tx() as db:
        db.execute('UPDATE attempts SET compiler=?', (matching_search.compiler_stamp({'toolchain': 'old'}),))
    text = '\n'.join(matching_search.history(net.store, net.function(), {'toolchain': 'new'}))
    assert 'Compiler mismatch' in text and 'old' in text and 'new' in text
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_score=10 WHERE id=1')
        db.execute('INSERT INTO attempts(function_id,agent_id,score) VALUES(1,1,10)')
    assert not any('Search stalled' in line for line in matching_search.history(net.store, net.function()))


def prepare_net():
    net = FakeNet()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source=?, best_score=200, n_insns=19, target=X'00000000' WHERE id=1",
                   (SOURCE,))
    net._context = lambda *args: {'header': HEADER, 'version': 1}
    return net


def test_stale_baseline_stops_before_mutations_and_survives_restart():
    net = prepare_net()
    p = Permuter(net)
    calls = []
    p._measure = lambda *args: calls.append(args[3]) or (900, 10)
    assert p._prepare(AGENT, net.project_row('game'), net.function(), State(), None, time.monotonic() + 5) is None
    assert calls == [SOURCE]
    text = '\n'.join(matching_search.history(net.store, net.function()))
    assert 'does not reproduce' in text and 'Random permutations paused' in text


def test_failed_baseline_diagnostic_does_not_cache_untried_field_probes():
    net = prepare_net()
    p = Permuter(net)
    p._measure = lambda *args: None
    assert p._prepare(AGENT, net.project_row('game'), net.function(), State(), None, time.monotonic() + 5) is None
    calls = []
    p = Permuter(net)
    p._measure = lambda *args: calls.append(args[3]) or (200, 4)
    p._prepare(AGENT, net.project_row('game'), net.function(), State(), None, time.monotonic() + 5)
    assert len(calls) == 17  # recovered compiler: baseline plus all sixteen previously untried layouts


def test_negative_probes_are_cached_but_a_header_change_retries():
    net = prepare_net()
    calls = []
    def measure(*args):
        calls.append(args[3])
        return (200, 4) if args[3] == SOURCE else (240, 2)
    for turn in range(3):
        p = Permuter(net)  # a new worker / app instance still sees the persisted cache
        p._measure = measure
        if turn == 2:
            net._context = lambda *args: {'header': HEADER + '\n/* changed */', 'version': 2}
        result = p._prepare(AGENT, net.project_row('game'), net.function(), State(), None, time.monotonic() + 5)
        assert result and len(result[0]) == 4
        assert result[0][0] == SOURCE and result[1] == 200  # retain structurally promising, worse scalar probes
        assert len(calls) == [17, 18, 35][turn]


def test_compile_failure_is_retryable_and_cancellation_does_not_cache_incomplete_probes():
    net = prepare_net()
    p = Permuter(net)
    p._measure = lambda *args: (200, 4) if args[3] == SOURCE else None
    p._prepare(AGENT, net.project_row('game'), net.function(), State(), None, time.monotonic() + 5)
    assert net.store.one('SELECT COUNT(*) AS n FROM matching_probes')['n'] == 0
    p._measure = lambda *args: (200, 4)
    st = State()
    st.stop.set()
    p._prepare(AGENT, net.project_row('game'), net.function(), st, None, time.monotonic() + 5)
    assert net.store.one('SELECT COUNT(*) AS n FROM matching_probes')['n'] == 0


def test_only_strict_judge_acceptance_can_match_even_when_order_distance_is_zero():
    net = prepare_net()
    net.progress = SimpleNamespace(judging=lambda *args: nullcontext())
    net._compile = lambda *args: SimpleNamespace(ok=True, obj=b'object')
    obj = FunctionObj('f', 'mips', False, bytes.fromhex('0800e00300000000'), [])
    net._evaluate = lambda *args: (obj, False, [])
    net._target_obj = lambda *args: obj
    records = []
    net._record = lambda *args, **kwargs: records.append(kwargs)
    p = Permuter(net)
    measured = p._measure(AGENT, net.project_row('game'), net.function(), SOURCE, 200, None)
    assert measured == (1, 0)
    assert records and records[0]['matched'] is False
    net._evaluate = lambda *args: (obj, True, ['unverified data'])
    assert p._measure(AGENT, net.project_row('game'), net.function(), SOURCE, 200, None) is None


def test_probe_cache_is_bounded_per_function():
    net = prepare_net()
    p = Permuter(net)
    for i in range(20):
        p._save_probe(net.function(), str(i), {'contexts': [], 'diagnostic': 'done'})
    assert net.store.one('SELECT COUNT(*) AS n FROM matching_probes')['n'] == 8
    assert json.loads(net.store.one('SELECT result FROM matching_probes LIMIT 1')['result'])['diagnostic'] == 'done'


def test_autopilot_records_its_build_snapshot_even_if_settings_change_during_compile():
    from decompnet.app.engine.autopilot import AutopilotMixin

    net = prepare_net()
    proj = net.project_row('game')
    original = net.cfg(proj)['compiler']
    def compile_it(*args):
        with net.store.tx() as db:
            db.execute('UPDATE projects SET config=? WHERE id=1', (json.dumps({'compiler': {'toolchain': 'new'}}),))
        return SimpleNamespace(ok=True, obj=b'object')
    net._compile = compile_it
    obj = FunctionObj('f', 'mips', False, bytes.fromhex('0800e00300000000'), [])
    net._evaluate = lambda *args: (obj, False, [])
    net._target_obj = lambda *args: obj
    records = []
    net._record = lambda *args, **kwargs: records.append(kwargs)
    AutopilotMixin._try_quietly(net, AGENT, proj, net.function(), SOURCE)
    assert records[0]['compiler'] == original
    assert records[0]['compiler'] != net.cfg(net.project_row('game'))['compiler']


def test_stalled_small_functions_get_layout_probes_outside_the_normal_near_miss_cutoff():
    net = prepare_net()
    p = Permuter(net)
    assert p.candidates(net.project_row('game')) == []  # 200 / 1900 exceeds the usual 6%
    with net.store.tx() as db:
        db.execute('UPDATE functions SET attempts=24 WHERE id=1')
    assert len(p.candidates(net.project_row('game'))) == 1
    with net.store.tx() as db:
        db.execute('UPDATE functions SET permuted=4 WHERE id=1')
    assert p.candidates(net.project_row('game')) == []  # restarts do not buy an unbounded search


def test_period_compiler_layout_search_matches_a_homemade_target(tmp_path):
    from decompnet import scoring, toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    if toolchains.installed('ee-gcc-2.9-991111') is None:
        pytest.skip('needs the pinned EE-GCC 2.9 compiler')
    cfg = {'compiler': {'toolchain': 'ee-gcc-2.9-991111', 'flags': ['-O2', '-G0']}}
    cc = compiler_config(tmp_path, cfg)
    # Written independently of the generator; different offsets and symbols from any private game.
    target_source = '''extern unsigned char *input[];
extern struct {
 unsigned char *a; unsigned char *b; unsigned char pad[24];
 int c; int d; unsigned char tail[24]; int count;
} output;
void setup(int);
void f(void) {
 unsigned char *base;
 setup(1);
 base = input[0];
 output.a = base;
 output.b = base + 16;
 output.c = (int)(base + 32);
 output.d = (int)(base + 48);
 output.count = 0;
}
'''
    def compile_function(source):
        result = compile_source(cc, HEADER, source)
        assert result.ok, result.log
        return extract_function(result.obj, 'f')
    target = compile_function(target_source)
    assert not scoring.diff(target, compile_function(SOURCE)).bytes_match
    for count, source in enumerate(field_probes.candidates(SOURCE, 'f', HEADER), 1):
        if scoring.diff(target, compile_function(source)).bytes_match:
            break
    else:
        pytest.fail('the bounded field search missed a known representable layout')
    assert count <= 16
