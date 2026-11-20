"""Frozen model comparisons and the matching evidence they can use."""

import time
from pathlib import Path

import pytest

from decompnet import consoles
from decompnet.app import (
    agent_evidence,
    agent_tokens,
    local_experiments,
    model_replay,
    model_replay_cases,
    model_replay_evidence,
    tools,
)
from decompnet.network import NetworkError
from tests.test_agent_matching_workflow import game
from tests.test_consoles import _function
from tests.test_consoles import demo as rom_fixture
from tests.test_forks import _ref, needs_tools
from tests.test_forks import fork as fork_fixture

demo = rom_fixture
fork = fork_fixture

START = 'int f(void) {\n    return 1;\n}\n'
CONTROL = 'int f(void) {\n    return 2; /* exact-control */\n}\n'
ROWS = [('i', 'li $v0, 2', 'li $v0, 1')]


def measure(source):
    exact = source == CONTROL
    return {'score': 0 if exact else 10, 'matched': exact, 'rows': [] if exact else ROWS}


def test_replay_separates_hints_budgets_and_model_sessions():
    case = {'id': 'demo', 'name': 'f', 'source': START, 'control': CONTROL, 'measure': measure,
            'scope': {'build_sha256': 'test'}, 'profile': {}, 'assembly': 'li $v0, 2', 'stages': [CONTROL]}
    events = []
    diagnostic = {'change': 'Check the return literal', 'prediction': 'The target immediate will agree'}

    def ask(model, prompt, seconds, session=None):
        assert seconds > 0
        if prompt.startswith('Diagnose'):
            events.append(('diagnosis', model, 'earlier measured edit' in prompt, session))
            assert model_replay.SOL == model
            if 'earlier measured edit' in prompt:
                assert 'exact-control' in prompt
            else:
                assert 'exact-control' not in prompt
            return diagnostic, 'specialist'
        assert 'exact-control' not in prompt
        events.append(('candidate', model, 'Check the return literal' in prompt, session))
        if model == model_replay.SOL or 'Check the return literal' in prompt:
            source = CONTROL
        else:
            source = START.replace('return 1;', f'return ({len(events)});')
        return {'source': source}, session or str(len(events))

    result = model_replay.run_case(case, ask, arms=model_replay.ARMS[:4], trials=3, seconds=10)
    arms = {r['arm']: r for r in result['arms']}
    assert result['historical_hint_calls'] == 1
    assert {a['source_sha256'] for a in arms.values()} == {model_replay.digest(START)}
    assert [arms[arm]['candidate_checks'] for arm in model_replay.ARMS[:4]] == [1, 3, 1, 3]
    assert [arms[arm]['matched'] for arm in model_replay.ARMS[:4]] == [True, False, True, True]
    assert arms['luna-diagnostic']['sol_diagnostic_calls'] == 1
    assert len([event for event in events if event[0] == 'candidate' and event[3] is None]) == 4
    assert all(event[3] != 'specialist' for event in events if event[0] == 'candidate')


def test_provider_failure_is_reported_as_incomplete_and_never_a_win():
    case = {'id': 'demo', 'name': 'f', 'source': START, 'control': CONTROL, 'measure': measure,
            'scope': {}, 'profile': {}, 'assembly': ''}
    def unavailable(*args):
        raise RuntimeError('provider unavailable')

    arm = model_replay.run_case(case, unavailable, arms=['luna'])['arms'][0]
    assert arm['incomplete'] == 'provider_error'
    assert arm['candidate_checks'] == 0 and not arm['matched']


def test_diagnosis_and_source_recovery_reject_ambiguous_inputs():
    assert not model_replay.diagnosis({'change': 'int f(void) {return 2;}', 'prediction': 'match'})
    text = f'## CLAIMED: demo/f\n```c\n{START}```\n'
    assert model_replay_cases.claim_candidate(text, 'f') == START
    assert model_replay_cases.claim_candidate(text, 'g') is None
    assert model_replay_cases.claim_candidate(text + f'```c\n{CONTROL}```', 'f') is None


def test_assisted_session_resets_once_with_verified_attempts(monkeypatch):
    monkeypatch.setattr(model_replay_evidence, '_local_candidates', lambda *args: iter(()))
    result = measure(START)
    case = {'name': 'f', 'arch': 'mips', 'header': '', 'assembly': '', 'profile': {}, 'scope': {'compiler_identity': 'test'}}
    budget = model_replay.Budget(measure, 6, time.monotonic()+10)
    state = {}
    history = [(START, result, START.replace('1', str(value)), result) for value in (3, 4)]
    source, current, hint, reset = model_replay_evidence.assist(case, budget, START, result, history, 2, state)
    assert reset and source == START and current == result
    assert 'Verified prior experiments' in hint and 'score 10 → 10' in hint
    assert 'Discriminating probe' in hint
    assert not model_replay_evidence.assist(case, budget, START, result, history, 3, state)[3]


def test_target_trace_and_contract_are_tied_to_measured_rows():
    rows = [(' ', 'addiu $s0, $a0, 4', 'addiu $s0, $a0, 4'),
            ('r', 'lbu $t0, 0($s0)', 'lbu $t0, 0($a0)'),
            (' ', 'jal helper', 'jal helper'),
            (' ', 'nop', 'nop'),
            (' ', 'sb $v0, 1($s0)', 'sb $v0, 1($s0)')]
    trace = model_replay_evidence.target_trace(rows, 'mips')
    assert 'reaching local definition 1' in trace and 'call at 3' in trace and 'next local read 5' in trace
    assert not model_replay_evidence.target_trace(rows, 'ppc')
    evidence = model_replay_evidence.contract('int a(void) { return helper(1); }',
                                              'extern int helper(unsigned int);',
                                              rows, [{'name': 'helper', 'matched_source': 'int helper(int a) { return a; }'}])
    assert 'unsigned 8-bit load' in evidence and 'Matched callee helper: int (int)' in evidence


def test_combine_adds_only_one_normal_judge_attempt(tmp_path, monkeypatch):
    net, agent, fn = game(tmp_path)
    source = 'int f(void) {\n    int x = 1;\n    return x + 2;\n}\n'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=40', (source,))
    scope = agent_evidence.build_key(net, fn)[0]
    rows = [('i', 'li r1, 4', 'li r1, 1'), ('i', 'li r2, 5', 'li r2, 2')]
    net._agent_outcomes = {(agent.id, fn['id']): {'source': source, 'score': 40, 'rows': rows, 'input_scope': scope}}
    compiled = []

    def evaluate(net, agent, slug, function, submitted):
        compiled.append(submitted)
        left, right = '= 4' in submitted, '+ 5' in submitted
        trial_rows = [(' ' if fixed else row[0], row[1], row[1] if fixed else row[2])
                      for fixed, row in zip((left, right), rows, strict=True)]
        score = 40 - 20*(left+right)
        net._record(fn, agent, score, submitted, 1)
        net._agent_outcomes[agent.id, fn['id']] = {'source': submitted, 'score': score, 'rows': trial_rows,
                                                 'input_scope': scope}
        return f'score {score}'

    monkeypatch.setattr(tools, '_compile', evaluate)
    _, rev = agent_tokens.candidate(net, agent, 'game', 'f')
    reply = tools.call(net, agent, 'game', 'compile_variants', {'function': 'f', 'revision': rev,
                       'combine': True, 'patches': [{'old': '= 1', 'new': '= 4'}, {'old': '+ 2', 'new': '+ 5'}]})
    assert len(compiled) == 3 and '= 4' in compiled[2] and '+ 5' in compiled[2]
    assert 'Variant 3 combines nonoverlapping edits' in reply


def test_local_sweep_filters_to_exact_anchor_before_judging(tmp_path, monkeypatch):
    net, agent, fn = game(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=?",
                   ('{"kind":"rom","compiler":{"toolchain":"gcc-2.7.2-kmc","flags":[]}}',))
    source, rev = agent_tokens.candidate(net, agent, 'game', 'f')
    inside = source.replace('return 1;', 'return 2;')
    outside = source.replace('int', 'unsigned')
    monkeypatch.setattr(agent_evidence, 'preflight', lambda *args: {'source': source, 'score': 60, 'rows': []})
    monkeypatch.setattr(local_experiments, 'available', lambda *args, **kw: {'source_lines': [inside, outside]})
    selected = []
    monkeypatch.setattr(agent_tokens, 'variants', lambda *args, **kw: selected.extend(args[5]) or 'Scored')
    with pytest.raises(NetworkError, match='exact, unique'):
        local_experiments.run(net, agent, 'game', 'f', rev, ['source_lines'], anchor='absent')
    assert 'Scored' in local_experiments.run(net, agent, 'game', 'f', rev, ['source_lines'], anchor='return 1;')
    assert selected == [{'old': source, 'new': inside}]
    assert fn['status'] == 'open'


def _assert_judge(net, slug, name, exact, context_version):
    proj = net.project_row(slug, sync=False)
    near = exact.replace('0x1234', '0x1235')
    case = model_replay_cases.prepare(net, proj, {'id': 'fixture', 'name': name, 'source': near, 'control': exact,
                                                 'context_version': context_version})
    def judge_agent(model, prompt, seconds, session=None):
        assert '0x1235' in prompt
        return {'source': exact}, None

    count = net.project_store(slug).one('SELECT COUNT(*) AS n FROM attempts')['n']
    result = model_replay.run_case(case, judge_agent, arms=('luna', 'sol'), trials=1)
    assert all(arm['matched'] and arm['candidate_checks'] == 1 for arm in result['arms'])
    assert net.project_store(slug).one('SELECT COUNT(*) AS n FROM attempts')['n'] == count
    with pytest.raises(ValueError, match='frozen'):
        model_replay_cases.prepare(net, proj, {**case, 'source': exact})


@pytest.mark.parametrize('demo', [consoles.get('n64')], indirect=True)
def test_frozen_rom_pair_runs_the_linked_judge(demo):
    _, net, slug, _, symbols = demo
    name, exact = _function(net, slug, symbols, 'checksum_save')
    fn = net._function(name, slug)
    _assert_judge(net, slug, name, exact, net._context(fn['project_id'], fn['unit'])['version'])


@needs_tools
def test_frozen_import_pair_runs_objdiff_and_restores_the_c(fork):
    net, slug, _ = fork
    proj = net.project_row(slug, sync=False)
    root = Path(proj['root'])
    before = {path: path.read_bytes() for path in root.glob('src/**/*.c')}
    _assert_judge(net, slug, 'checksum_save', _ref('checksum_save'), 0)
    assert all(path.read_bytes() == content for path, content in before.items())
