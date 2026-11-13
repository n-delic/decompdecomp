"""Synthetic matched-callee hypotheses; no game sources or live projects."""
import json

import pytest

from decompnet.app import draft_repairs, local_experiments
from decomprepair import matched_prototype_repairs as repair
from tests.test_claims import _game

SOURCE = 'extern short helper(short value); int sample(int x){return helper((short)x)+1;}'
SELECTED = 'int helper(int value){return value+3;}'


def test_matched_contract_updates_integer_types_but_preserves_calls_and_names():
    results = repair.variants(SOURCE, 'sample', callees=SELECTED)
    assert results == [(repair.FAMILY, 'extern int helper(int value); int sample(int x){return helper((short)x)+1;}')]
    assert not repair.variants(SOURCE, 'sample')
    assert not repair.variants(results[0][1], 'sample', callees=SELECTED)


def test_known_aliases_require_explicit_compatible_typedefs():
    source = 's16 helper(u16); int sample(int x){return helper(x);}'
    selected = 's32 helper(s32 value){return value;}'
    header = 'typedef short s16; typedef unsigned short u16; typedef int s32;'
    assert repair.variants(source, 'sample', header, selected)[0][1].startswith('int helper(int);')
    assert not repair.variants(source, 'sample', '', selected)
    assert not repair.variants(source, 'sample', header.replace('typedef int s32;', 'typedef short s32;'), selected)


@pytest.mark.parametrize('source,selected,header', [
    (SOURCE, SELECTED+SELECTED, ''),
    (SOURCE+ ' extern short helper(short);', SELECTED, ''),
    (SOURCE, SELECTED, 'short helper(short);'),
    (SOURCE.replace('extern short helper(short value);', 'short helper(short value){return value;}'), SELECTED, ''),
    (SOURCE.replace('short value', 'short *value'), SELECTED, ''),
    (SOURCE, SELECTED.replace('int value', 'int *value'), ''),
    (SOURCE.replace('short value', 'void (*value)(void)'), SELECTED, ''),
    (SOURCE.replace('short value', 'Other value'), SELECTED, ''),
    (SOURCE.replace('short value', 'float value'), SELECTED, ''),
    (SOURCE.replace('short value', 'const short value'), SELECTED, ''),
    (SOURCE.replace('short value', 'short value, ...'), SELECTED, ''),
    (SOURCE.replace('short value', ''), SELECTED, ''),
    (SOURCE, SELECTED.replace('int value', 'int value, int extra'), ''),
    (SOURCE.replace('helper((short)x)', 'helper(x,x)'), SELECTED, ''),
    (SOURCE, SELECTED.replace('int helper', 'void helper'), ''),
    (SOURCE, SELECTED.replace('int helper', 'char helper'), ''),
    (SOURCE, SELECTED, '#define helper other'),
    (SOURCE, SELECTED, '#define int short'),
    (SOURCE, SELECTED, '#define char int'),
    (SOURCE, '#if ENABLED\n'+SELECTED+'\n#endif', ''),
    (SOURCE.replace('return helper', 'short helper(short);return helper'), SELECTED, ''),
    ('#if FEATURE\n'+SOURCE+'\n#endif', SELECTED, ''),
    (SOURCE.replace('return helper', 'escape(helper);return helper'), SELECTED, ''),
    (SOURCE.replace('int sample(int x)', 'int sample(int x, int (*helper)(short))'), SELECTED, ''),
])
def test_uncertain_or_shared_interfaces_are_not_automatically_changed(source, selected, header):
    assert not repair.variants(source, 'sample', header, selected)


def test_multiple_direct_calls_share_one_prototype_change():
    source = SOURCE.replace('helper((short)x)+1', 'helper((short)x)+helper(x+1)')
    result = repair.variants(source, 'sample', callees=SELECTED)
    assert len(result) == 1 and 'helper((short)x)+helper(x+1)' in result[0][1]


def test_callee_and_bundle_limits_are_deterministic():
    source = ''.join(f'short h{i}(short);' for i in range(6))
    source += 'int sample(int x){return '+ '+'.join(f'h{i}(x)' for i in range(6))+';}'
    selected = ''.join(f'int h{i}(int x){{return x;}}' for i in range(6))
    results = repair.variants(source, 'sample', callees=selected)
    assert len(results) == 4
    assert all(f'int h{i}(int);' in results[-1][1] for i in range(3))
    assert all(f'short h{i}(short);' in results[-1][1] for i in range(3, 6))


def test_family_is_available_and_prioritized_in_shared_generation():
    from decompnet.app.repair_ranking import guarded_first
    labels = {}
    results = draft_repairs.variants(SOURCE, 'sample', 'mips', labels=labels, callees=SELECTED, limit=None)
    assert repair.FAMILY in draft_repairs.AGENT_FAMILIES
    assert any(labels[s] == repair.FAMILY for s in results)
    assert labels[guarded_first(results, labels)[0]] == repair.FAMILY


@pytest.mark.parametrize('mode', ['rom', 'fork'])
@pytest.mark.parametrize('status', ['matched', 'open'])
def test_local_pass_uses_only_referenced_matched_callees_without_editing_them(tmp_path, monkeypatch, mode, status):
    net, _ = _game(tmp_path, [('f', 10, 'open'), ('helper', 0, status)])
    agent, _ = net.local_agent('solver')
    source = SOURCE.replace('sample', 'f')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind': mode, 'compiler': {'toolchain': 'example', 'flags': []}}),))
        db.execute("UPDATE functions SET best_source=? WHERE name='f'", (source,))
        db.execute("UPDATE functions SET matched_source=? WHERE name='helper'", (SELECTED,))
    net.claim_row(agent, 'game', 'f')
    fn = net._function('f', 'game')
    monkeypatch.setattr(net.forks, 'permuter_header', lambda *args: '')
    from decompnet.app.agent_tokens import candidate
    _, revision = candidate(net, agent, 'game', 'f')
    pools = local_experiments.available(net, agent, 'game', fn, source, revision, [repair.FAMILY])
    assert bool(pools[repair.FAMILY]) == (status == 'matched')
    assert net._function('helper', 'game')['matched_source'] == SELECTED
    assert net._function('f', 'game')['best_source'] == source


def test_pinned_compiler_candidate_recovers_independent_integer_interface_control(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('Pinned KMC compiler unavailable; never download during tests')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                          'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']}})
    before = 'short helper(short); int sample(int x){return helper(x)+1;}'
    reference = 'int helper(int); int sample(int x){return helper(x)+1;}'
    generated = repair.variants(before, 'sample', callees=SELECTED)[0][1]
    objects = []
    for source in (before, reference, generated):
        result = compile_source(cfg, '', source)
        assert result.ok, result.log
        objects.append(extract_function(result.obj, 'sample'))
    assert objects[0].code != objects[1].code
    assert objects[2].code == objects[1].code
    assert objects[2].relocs == objects[1].relocs


@pytest.mark.parametrize('exact', [False, True])
def test_local_runner_keeps_baseline_unless_normal_judge_accepts_candidate(tmp_path, monkeypatch, exact):
    from decompnet.app import agent_tokens
    net, _ = _game(tmp_path, [('f', 10, 'open'), ('helper', 0, 'matched')])
    agent, _ = net.local_agent('solver')
    source = SOURCE.replace('sample', 'f')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind': 'rom', 'compiler': {'toolchain': 'example', 'flags': []}}),))
        db.execute("UPDATE functions SET matched_source=? WHERE name='helper'", (SELECTED,))
    fn = net._function('f', 'game')
    net._record(fn, agent, 10, source, 1)
    net.claim_row(agent, 'game', 'f')
    _, revision = agent_tokens.candidate(net, agent, 'game', 'f')
    monkeypatch.setattr(local_experiments.agent_evidence, 'preflight', lambda *args: None)
    tested = []
    def judge(worker, function, candidate, slug):
        tested.append(candidate)
        net._record(fn, worker, 0 if exact else 20, candidate, 1, matched=exact)
        return 'Synthetic ordinary judge verdict'
    monkeypatch.setattr(net, 'compile_and_diff', judge)
    local_experiments.run(net, agent, 'game', 'f', revision, [repair.FAMILY], max_trials=1)
    assert len(tested) == 1 and tested[0].startswith('extern int helper(int value);')
    current = net._function('f', 'game')
    assert current['status'] == ('matched' if exact else 'open')
    assert current['best_source'] == (tested[0] if exact else source)
    assert net._function('helper', 'game')['matched_source'] == SELECTED
