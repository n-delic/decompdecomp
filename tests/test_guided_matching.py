"""Guided claims, scoped measured options and independent replay skill outcomes."""

import json
import time

import pytest

from decompnet import agent_notes, claim_routing, consoles, toolchains
from decompnet.app import (
    agent_evidence,
    agent_tokens,
    candidate_behaviour,
    guided_matching,
    model_replay,
    tools,
)
from decompnet.network import NetworkError
from tests.test_agent_matching_workflow import game
from tests.test_claim_routing import history, project
from tests.test_consoles import _function
from tests.test_consoles import demo as rom_fixture
from tests.test_forks import _ref, needs_tools
from tests.test_forks import fork as fork_fixture

demo = rom_fixture
fork = fork_fixture

DIFF = [('i', 'li $v0, 2', 'li $v0, 1'), ('i', 'li $v1, 4', 'li $v1, 3')]
PARTIAL = [(' ', DIFF[0][1], DIFF[0][1]), DIFF[1]]


def test_work_packet_saves_one_measured_objective_and_drops_stale_evidence(tmp_path):
    net, agent, fn = game(tmp_path)
    source, rev = agent_tokens.candidate(net, agent, 'game', 'f')
    scope = agent_evidence.build_key(net, fn)[0]
    net._agent_outcomes = {(agent.id, fn['id']): {'source': source, 'input_scope': scope, 'rows': DIFF, 'score': 40}}
    packet = tools.call(net, agent, 'game', 'get_function', {'function': 'f', 'section': 'work'})
    assert 'Resolve one changed literal' in packet and rev in packet and source in packet
    edited = source.replace('return 1', 'return 2')
    net._record(fn, agent, 20, edited, 1)
    fresh = net._function('f', 'game')
    proj = net.project_row('game', sync=False)
    result = guided_matching.observe(net, proj, fresh, agent, edited,
                                     {'source': edited, 'input_scope': scope, 'rows': PARTIAL, 'score': 20})
    assert 'objective measured and kept' in result
    assert 'target instruction 1' in guided_matching.milestone(net, proj, fresh, edited)
    with net.store.tx() as db:
        db.execute("UPDATE context_versions SET header='typedef int changed;'")
    assert not guided_matching.milestone(net, proj, fresh, edited)
    assert 'changed literal' not in guided_matching.packet(net, agent, 'game', 'f')


def test_options_select_measured_c_and_expire_on_new_build(tmp_path, monkeypatch):
    net, agent, fn = game(tmp_path)
    source, rev = agent_tokens.candidate(net, agent, 'game', 'f')

    def compile_candidate(net, agent, slug, function, candidate):
        score = 20 if 'return 2' in candidate else 80
        net._record(fn, agent, score, candidate, 1)
        net._agent_outcomes = {(agent.id, fn['id']): {'source': candidate, 'rows': PARTIAL,
                               'score': score, 'input_scope': agent_evidence.build_key(net, fn)[0]}}
        return f'score {score}'

    monkeypatch.setattr(tools, '_compile', compile_candidate)
    patches = [{'old': 'return 1', 'new': f'return {i}'} for i in (2, 3)]
    result = agent_tokens.variants(net, agent, 'game', 'f', rev, patches, present_options=True)
    assert 'option=NUMBER' in result
    assert 'return 3' in agent_tokens.get_candidate(net, agent, 'game', 'f', 'measured_option', option=2)
    assert net._function('f', 'game')['best_source'] == source.replace('return 1', 'return 2')
    with net.store.tx() as db:
        db.execute("UPDATE context_versions SET header='typedef int changed;'")
    with pytest.raises(NetworkError, match='expired'):
        agent_tokens.get_candidate(net, agent, 'game', 'f', 'measured_option', option=2)


def test_recovery_actions_distinguish_build_output_and_regressions():
    assert 'missing declaration' in guided_matching.recovery(None, [], {'build_failed': True}, 10, 'mips')
    assert 'Change the hypothesis' in guided_matching.recovery(DIFF, [], {'repeated': True}, 10, 'mips')
    assert 'saved_best' in guided_matching.recovery(DIFF, [], {'score': 20}, 10, 'mips')


def test_focus_can_reverse_broad_routing_and_header_changes_remove_it(tmp_path):
    net, pid = project(tmp_path, 'rom', [('easy', 12, 'A', None), ('hard', 12, 'A', 20)])
    expert, _ = net.local_agent('width-model-1')
    general, _ = net.local_agent('general-1')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET abandons=0 WHERE name='easy'")
        db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,?,?)', (pid, '', 'a'))
    for model, start, broad_wins in ((expert, 1000, 0), (general, 3000, 90)):
        history(net, pid, model, 'hard', 90, broad_wins, start)
    for model, start, focused_wins in ((expert, 2000, 29), (general, 4000, 0)):
        history(net, pid, model, 'hard', 30, focused_wins, start)
        with net.store.tx() as db:
            db.execute("INSERT INTO claim_routing_focus SELECT function_id,agent_id,claimed_at,'constant','old',1 "
                       "FROM claim_routing_samples WHERE agent_id=? AND function_id BETWEEN ? AND ?",
                       (model.id, -start-29, -start))
    proj = net.project_row('game')
    fn = net._function('hard', 'game')
    inputs = agent_notes.rom_inputs(net.store.one, proj, fn, 1)
    with net.store.tx() as db:
        claim_routing.remember_focus(db, fn, 'mips', DIFF, inputs, 1)
    names = {expert.id: expert.name, general.id: general.name}
    assert claim_routing.routes(net.store, proj, expert, time.time(), names)[0] == [fn['id']]
    assert claim_routing.routes(net.store, proj, general, time.time(), names)[1] == [fn['id']]
    with net.store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,2,?,?)', (pid, 'changed', 'a'))
    assert claim_routing.routes(net.store, proj, expert, time.time(), names)[1] == [fn['id']]


def test_replay_scores_diagnosis_editing_and_recovery_independently():
    source = 'int f(void) { return 1; }'
    control = 'int f(void) { return 2; }'

    def measure(candidate):
        if candidate == source:
            return {'score': 40, 'rows': DIFF}
        if candidate == control:
            return {'score': 0, 'matched': True}
        return {'score': None, 'error': 'unknown declaration'}

    answers = iter([{'category': 'constant', 'target_index': 1}, {'source': 'wrong return'}, {'source': control}])
    case = {'name': 'f', 'id': 'test', 'arch': 'mips', 'source': source, 'control': control, 'measure': measure,
            'scope': {}, 'profile': {}, 'assembly': 'li $v0, 2'}
    result = model_replay.run_case(case, lambda *args: (next(answers), None), arms=['luna'], trials=2,
                                   score_skills=True)['arms'][0]
    assert result['matched'] and result['candidate_checks'] == 2
    scores = result['skills']
    assert scores['diagnosis_category_agreed'] == 1 and scores['diagnosis_prediction_tested'] == 0
    assert scores['edits_submitted'] == 2 and scores['edits_scored'] == 1
    assert scores['recovery_opportunities'] == scores['recovery_successes'] == 1


def test_guided_replay_compiles_options_under_the_shared_budget(monkeypatch):
    from decompnet.app import model_replay_evidence

    source, control = 'before', 'judge exact'
    def measure(candidate):
        if candidate == control:
            return {'score': 0, 'matched': True}
        return {'score': 10 if candidate == 'better' else 20, 'rows': DIFF}
    monkeypatch.setattr(model_replay_evidence, '_local_candidates',
                        lambda *args: iter([('local', 'better'), ('local', 'other')]))

    def ask(model, prompt, seconds, session=None):
        if prompt.startswith('Choose'):
            return {'option': 1, 'reason': 'lower score'}, None
        assert 'better' in prompt and 'One objective.' in prompt and 'Target assembly:' not in prompt
        return {'source': control}, None

    case = {'name': 'f', 'id': 'test', 'arch': 'mips', 'source': source, 'control': control, 'measure': measure,
            'scope': {}, 'profile': {}, 'assembly': 'original'}
    arm = model_replay.run_case(case, ask, arms=['luna-guided'], trials=3)['arms'][0]
    assert arm['matched'] and arm['candidate_checks'] == 3 and arm['assistance']['measured_options'] == 2


def test_behavior_displays_first_difference_and_excludes_stubbed_calls(tmp_path):
    folder = tmp_path / 'candidate'
    candidate_behaviour._write_workspace(folder, {'name': 'f'}, 'candidate', 'header', [], [{'name': 'f', 'vram': 12}])
    assert (folder / 'progress/src/f.c').read_text() == 'candidate'
    assert json.loads((folder / 'progress/decompdecomp.json').read_text())['layout'][0]['vram'] == 12
    answer = candidate_behaviour.describe_call(1, 'differs', ['first write', 'second write'], {})
    assert 'first write' in answer and 'second write' not in answer
    assert 'Inconclusive' in candidate_behaviour.describe_call(2, 'differs', ['write'], {'runtime': ['stub']})
    assert 'compare_candidate_behaviour' not in tools.tool_names(True, consoles.get('n64'))


def _guided_real(net, slug, name, exact):
    agent, _ = net.local_agent('guided-helper')
    assert 'CLAIMED' in tools._claim(net, agent, slug, name, '')
    near = exact.replace('0x1234', '0x1235')
    assert 'MATCH!' not in tools._compile(net, agent, slug, name, near)
    packet = tools.call(net, agent, slug, 'get_function', {'function': name, 'section': 'work'})
    assert 'Current C' in packet and '0x1235' in packet and 'Mismatch focus:' in packet
    fn = net._function(name, slug)
    proj = net.project_row(slug, sync=False)
    store = net.project_store(slug)
    focus = store.one('SELECT focus,inputs,context_version FROM matching_focus WHERE function_id=?', fn['id'])
    assert claim_routing.current_focus(store.one, proj, fn, focus) == 'constant'
    assert 'MATCH!' in tools._compile(net, agent, slug, name, exact)


@pytest.mark.parametrize('demo', [consoles.get('n64')], indirect=True)
def test_guided_packet_continues_through_the_rom_judge(demo):
    _, net, slug, _, symbols = demo
    name, exact = _function(net, slug, symbols, 'checksum_save')
    _guided_real(net, slug, name, exact)


@needs_tools
def test_guided_packet_continues_through_the_imported_judge(fork):
    net, slug, _ = fork
    _guided_real(net, slug, 'checksum_save', _ref('checksum_save'))


@pytest.mark.parametrize('demo', [consoles.get('n64')], indirect=True)
def test_real_recorded_candidate_behaviour(demo, tmp_path, monkeypatch):
    from decompnet.app.engine import RomNetwork

    console, _, _, image, symbols = demo
    if not console.emulator.installed() or not all(toolchains.installed(n) for n in ('n64recomp', 'zig', 'wabt')):
        pytest.skip('needs pinned emulator, recompiler and port toolchains')
    # The split fixture has a blank IPL3. This original test stub copies its
    # code from cartridge to RDRAM, then calls checksum_save(NULL, 0).
    rom = bytearray(image.read_bytes())
    address = symbols['checksum_save']
    words = (
        0x3c08b000, 0x35081000, 0x3c09a000, 0x35290400, 0x3c0ab001,
        0x8d0b0000, 0xad2b0000, 0x25080004, 0x150afffc, 0x25290004,
        0x3c1d803f, 0x37bdfff0, 0x00002025, 0x00002825,
        0x3c080000 | (address >> 16), 0x35080000 | (address & 0xffff), 0x0100f809, 0,
        0x1000ffff, 0,
    )
    rom[0x40:0x40+4*len(words)] = b''.join(word.to_bytes(4, 'big') for word in words)
    bootable = tmp_path / 'bootable-demo.z64'
    bootable.write_bytes(rom)
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    slug = net.import_rom(image, confirmed=True, log=lambda *_: None)
    # Only the recorded fixture needs the boot stub. Its function bytes and
    # addresses are identical to the original split fixture used by the judge.
    record = console.emulator.run
    monkeypatch.setattr(console.emulator, 'run', lambda _image, *args, **kw: record(bootable, *args, **kw))
    name, exact = _function(net, slug, symbols, 'checksum_save')
    agent, _ = net.local_agent('guided-behaviour')
    assert 'CLAIMED' in tools._claim(net, agent, slug, name, '')
    near = exact.replace('0x1234', '0x1235')
    tools._compile(net, agent, slug, name, near)
    _, revision = agent_tokens.candidate(net, agent, slug, name)
    end = time.monotonic()+180
    while time.monotonic() < end:
        report = tools.call(net, agent, slug, 'compare_candidate_behaviour',
                            {'function': name, 'revision': revision, 'calls': 1})
        if 'still running' not in report:
            break
    assert 'First observable difference: $v0: recompiled 00001234, decompiled 00001235' in report
    assert net._function(name, slug)['status'] == 'open'
