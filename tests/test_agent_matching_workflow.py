"""Scoped matching feedback, continuations and recorded diagnostic evidence."""
import json

import anyio
import pytest

from decompnet.app import (
    agent_evidence,
    agent_review,
    agent_tokens,
    compiler_experiments,
    search_brief,
    search_evidence,
    tools,
)
from decompnet.app.efficiency_feedback import draft_triage, frontload, patch_anchors
from decompnet.network import NetworkError
from tests.test_claims import _game

SOURCE = 'int f(void) {\n    return 1;\n}\n'


def game(tmp_path, size=32):
    net, pid = _game(tmp_path, [('f', size, 'open')])
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=60', (SOURCE,))
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'','')", (pid,))
    agent, _ = net.local_agent('worker')
    net.claim_row(agent, 'game', 'f')
    return net, agent, net._function('f', 'game')


@pytest.mark.parametrize('transport', ['api', 'mcp'])
def test_patch_transports_share_one_scoped_preflight(tmp_path, monkeypatch, transport):
    net, agent, _ = game(tmp_path)
    guards = []
    monkeypatch.setattr(net.progress, 'check_for_judging', lambda slug, *, function='': guards.append(('judge', slug, function)))
    monkeypatch.setattr(tools, '_dispatch', lambda *a, **k: 'feedback')
    args = {'function': 'f', 'old': '1', 'new': '2', 'revision': 'revision'}
    if transport == 'mcp':
        mcp = tools.build_mcp(net, lambda ctx: (agent, 'game'))

        async def run():
            return await mcp.call_tool('compile_patch', args)

        assert 'feedback' in str(anyio.run(run))
    else:
        assert tools.call(net, agent, 'game', 'compile_patch', args) == 'feedback'
    assert guards == [('judge', 'game', 'f')]


def test_exact_anchors_and_edit_use_the_selected_continuation(tmp_path):
    net, agent, fn = game(tmp_path)
    mapping = '### Compiled C lines associated with differences\n| 2 | C | Operand | target | candidate |'
    anchors = patch_anchors(mapping, SOURCE)
    assert anchors == ['    return 1;']
    assert not patch_anchors(mapping.replace('| 2 |', '| 99 |'), SOURCE)
    net._agent_outcomes = {(agent.id, fn['id']): {'source': SOURCE, 'anchors': anchors,
                           'patch_receipt': {'old': 'return 0;', 'new': 'return 1;'}}}
    reply = frontload(net, agent, 'game', 'f', 'a compile result')
    assert json.dumps(anchors) in reply and 'Accepted edit in this continuation' in reply
    net._agent_outcomes[agent.id, fn['id']]['source'] = 'obsolete C'
    assert 'Exact C anchors' not in frontload(net, agent, 'game', 'f', 'a compile result')


def test_output_history_is_shared_and_invalidated_by_header(tmp_path):
    net, agent, fn = game(tmp_path)
    net.keep_diff(agent.id, fn['id'], [('i', 'addi r3, r3, 2', 'addi r3, r3, 1')])
    proj = net.project_row('game')
    search_evidence.remember(net, agent, proj, fn, SOURCE, 10, output='measured')
    search_evidence.remember(net, agent, proj, fn, SOURCE.replace('1', '(1)'), 10, output='measured')
    net._search_evidence.clear()
    assert '1 other source variants' in search_evidence.repeated_hint(net, proj, fn, SOURCE)
    assert not search_evidence.repeated_hint(net, proj, fn, SOURCE.replace('1', '8'))
    with net.store.tx() as db:
        db.execute("UPDATE context_versions SET header='typedef int changed;'")
    assert not search_evidence.repeated_hint(net, proj, fn, SOURCE)


def test_large_draft_triage_uses_observed_blocker():
    fn = {'n_insns': 128}
    assert not draft_triage({'score': 5}, fn, 'mips')
    assert 'linker' in draft_triage({'score': 2000, 'warnings': 'when linking your code'}, fn, 'mips')
    assert 'declaration' in draft_triage({'score': 2000, 'rows': [('i', 'jal a', 'jal b')]}, fn, 'mips')
    assert 'profile' in draft_triage({'score': 2000, 'rows': [('r', 'addu $a1, $a2', 'addu $a0, $a2')]}, fn, 'mips')
    assert 'control flow' in draft_triage({'score': 2000, 'rows': [('>', '', 'nop')]}, fn, 'mips')


def test_batch_retains_regional_alternative_for_one_follow_up(tmp_path, monkeypatch):
    net, agent, fn = game(tmp_path)
    targets = [(' ', f'addi r3, r3, {i}', f'addi r3, r3, {i}') for i in range(32)]

    def rows(indices):
        return [('i', target, 'changed operand') if i in indices else (marker, target, value)
                for i, (marker, target, value) in enumerate(targets)]

    base = rows({1, 17})
    net._agent_outcomes = {(agent.id, fn['id']): {'source': SOURCE, 'score': 60, 'rows': base}}

    def compile_candidate(net, agent, slug, function, source):
        primary = 'return 2' in source
        score = 30 if primary else 70
        evidence = rows({1} if primary else {17, 20})
        net._record(fn, agent, score, source, 1)
        net._agent_outcomes[agent.id, fn['id']] = {'source': source, 'score': score, 'rows': evidence, 'legend': 'markers:'}
        return f'score {score}'

    monkeypatch.setattr(tools, '_compile', compile_candidate)
    _, rev = agent_tokens.candidate(net, agent, 'game', 'f')
    patches = [{'old': 'return 1', 'new': f'return {i}'} for i in (2, 3)]
    reply = agent_tokens.variants(net, agent, 'game', 'f', rev, patches)
    assert 'Variant 2 improves a target region' in reply
    assert 'return 2' in agent_tokens.candidate(net, agent, 'game', 'f')[0]
    assert 'return 3' in agent_tokens.get_candidate(net, agent, 'game', 'f', 'regional_alternative')
    assert 'return 2' in net._function('f', 'game')['best_source']
    net._record(fn, agent, 65, SOURCE, 1)
    with pytest.raises(NetworkError, match='no current regional alternative'):
        agent_tokens.get_candidate(net, agent, 'game', 'f', 'regional_alternative')


def test_search_brief_selects_current_or_saved_source(tmp_path, monkeypatch):
    net, agent, fn = game(tmp_path)
    newer = SOURCE.replace('1', '2')
    net._record(fn, agent, 100, newer, 1)
    inputs = []
    monkeypatch.setattr(search_evidence, 'brief', lambda net, proj, fn, source: inputs.append(source) or 'Focused evidence')
    monkeypatch.setattr(net, '_compile', lambda *a: pytest.fail('brief should not compile'))
    assert 'current candidate revision' in search_brief.read(net, agent, 'game', 'f')
    assert 'saved_best candidate revision' in search_brief.read(net, agent, 'game', 'f', 'saved_best')
    assert inputs == [newer, SOURCE]


def test_compiler_trial_receipt_uses_saved_pair_and_rejects_stale_scope(tmp_path, monkeypatch):
    net, agent, fn = game(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=?", (json.dumps({'kind': 'rom', 'compiler': {'toolchain': 'test', 'flags': []}}),))
    from decompnet.app.reviewed_search_seeds import scope
    proj = net.project_row('game')
    receipt = {'agent': agent.id, 'slug': 'game', 'function': 'f', 'source': SOURCE,
               'flags': '-O1', 'compiler': 'new', 'result': {'exact': True},
               'seed_scope': scope(net, proj, fn, SOURCE)}
    compiler_experiments.save_trial(net, fn, 'exact', receipt)
    proposed = []
    monkeypatch.setattr(net, 'suggest', lambda *args, **kw: proposed.append((args[3:], kw)) or 'Queued for approval')
    assert 'Queued for approval' in tools._suggest(net, agent, 'game', function='f', trial_id='exact')
    assert proposed == [(('new', '-O1', 'f'), {'source': SOURCE})]
    with net.store.tx() as db:
        db.execute("UPDATE context_versions SET header='typedef int changed;'")
    with pytest.raises(NetworkError, match='trial is stale'):
        tools._suggest(net, agent, 'game', function='f', trial_id='exact')


def test_automatic_release_publishes_current_handoff(tmp_path, monkeypatch):
    net, agent, fn = game(tmp_path)
    net._record(fn, agent, 60, SOURCE, 1)
    proj = net.project_row('game')
    fn = net._function('f', 'game')
    rows = [('i', 'li r3, 2', 'li r3, 1')]
    monkeypatch.setattr(net, 'selection', lambda *a: {'unchanged_compiles': 1})
    net._agent_outcomes = {(agent.id, fn['id']): {'source': SOURCE, 'score': 60, 'rows': rows,
                           'input_scope': agent_evidence.build_key(net, fn)[0]}}
    assert '0/1' in agent_evidence.track(net, agent, 'game', fn)
    assert 'limit reached' in agent_evidence.track(net, agent, 'game', fn)
    assert net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id']) is None
    handoff = agent_review.handoff_brief(net, 'game', fn, SOURCE)
    assert 'Tried: 1 recorded attempts' in handoff
    assert 'Mismatch focus' in handoff and 'Next check:' in handoff
    with net.store.tx() as db:
        db.execute("UPDATE context_versions SET header='typedef int changed;'")
    assert not agent_review.handoff_brief(net, 'game', fn, SOURCE)
    assert proj['id'] == fn['project_id']
