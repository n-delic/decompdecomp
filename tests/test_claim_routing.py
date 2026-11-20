"""Fixed outcomes isolate assignment from model variability and exercise the claim and judge entry points."""
import json
import time

import pytest

from decompnet import claim_routing
from tests.test_claims import _game


def project(tmp_path, mode, functions):
    net, pid = _game(tmp_path, [(name, size, 'open') for name, size, _, _ in functions])
    if mode == 'rom':
        cfg = {'kind': 'rom', 'compiler': {'toolchain': 'test', 'flags': ['-O1']},
               'compiler_ranges': [{'start': 200, 'end': 300, 'toolchain': 'test', 'flags': ['-O2']}]}
    else:
        cfg = {'kind': 'fork', 'platform': 'n64'}
        units = [{'name': unit, 'metadata': {'source_path': unit + '.c'}} for unit in ('a', 'b')]
        (tmp_path / 'objdiff.json').write_text(json.dumps({'units': units}))
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        for name, _, profile, score in functions:
            db.execute('UPDATE functions SET unit=?,vram=?,best_score=?,abandons=3 WHERE name=?',
                       ('a' if profile == 'A' else 'b', 100 if profile == 'A' else 200, score, name))
    return net, pid


def history(net, pid, agent, case, attempts, wins, cursor):
    """The same outcome history feeds both policies, including the legacy events and credited matches."""
    fn = net._function(case, 'game')
    profile = claim_routing.build_profiles(net.project_row('game'))(fn)
    band = claim_routing.candidate_band(dict(fn) | {'has_draft': False})
    now = time.time()
    with net.store.tx() as db:
        for index in range(attempts):
            # The completed cases are disjoint from the held-out open queue.
            ident = cursor + index
            db.execute("INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(?,?,'claim',?,?)",
                       (pid, agent.id, f'old-{ident}', now))
            if fn['abandons'] >= 3:
                db.execute('INSERT INTO claim_routing_samples VALUES(?,?,?,?,?,?,1,?)',
                           (pid, -ident, agent.id, now, profile, band, index < wins))
            if index < wins:
                db.execute("INSERT INTO functions(project_id,name,n_insns,status,matched_agent_id) VALUES(?,?,?,'matched',?)",
                           (pid, f'old-{ident}', fn['n_insns'], agent.id))


@pytest.mark.parametrize('mode', ['rom', 'fork'])
@pytest.mark.parametrize('policy', ['legacy', 'comparable'])
@pytest.mark.parametrize('queued_profile,near,correct_model', [('A', True, 'specialist'), ('A', False, 'generalist'),
                                                              ('B', True, 'generalist')])
def test_hard_assignment_on_fixed_held_out_cases(tmp_path, monkeypatch, mode, policy, queued_profile, near, correct_model):
    net, pid = project(tmp_path, mode, [
        ('easy', 12, 'A', None), ('a-near', 12, 'A', 20),
        ('a-early', 12, 'A', 850), ('b-near', 12, 'B', 20),
    ])
    generalist, _ = net.local_agent('generalist-1')
    specialist, _ = net.local_agent('specialist-1')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET abandons=0 WHERE name='easy'")
    history(net, pid, generalist, 'easy', 400, 395, 1000)
    history(net, pid, specialist, 'easy', 30, 0, 2000)
    for index, (case, generalist_wins, specialist_wins) in enumerate([
        ('a-near', 2, 24), ('a-early', 24, 2), ('b-near', 24, 2),
    ]):
        history(net, pid, generalist, case, 30, generalist_wins, 3000 + index * 100)
        history(net, pid, specialist, case, 30, specialist_wins, 4000 + index * 100)

    hard = ('b-near' if queued_profile == 'B' else 'a-near' if near else 'a-early')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='asm' WHERE name IN ('a-near','a-early','b-near') AND name!=?", (hard,))

    # The deployed project-wide comparison crowns the generalist for all three cases.
    totals = net.store.all("SELECT agent_id,COUNT(*) AS n FROM events WHERE kind='claim' GROUP BY agent_id")
    wins = {row['matched_agent_id']: row['n'] for row in net.store.all(
        "SELECT matched_agent_id,COUNT(*) AS n FROM functions WHERE status='matched' GROUP BY matched_agent_id")}
    pooled = sum(wins.values()) / sum(row['n'] for row in totals)
    rates = {row['agent_id']: (wins[row['agent_id']] + 10 * pooled) / (row['n'] + 10) for row in totals}
    assert rates[specialist.id] < 0.6 * rates[generalist.id]

    if policy == 'legacy':
        # The original policy preferred or deferred the whole escalated queue from this aggregate.
        ids = [net._function(hard, 'game')['id']]

        def legacy_routes(_store, _proj, agent, _now, _names):
            return (ids, []) if agent.id == generalist.id else ([], ids)

        monkeypatch.setattr(claim_routing, 'routes', legacy_routes)

    # Ask the less suitable model first and leave both queues otherwise identical.
    expected, other = (specialist, generalist) if correct_model == 'specialist' else (generalist, specialist)
    owner = other if net.claim_row(other, 'game')['name'] == hard else expected
    if owner == expected:
        assert net.claim_row(expected, 'game')['name'] == hard
    assert owner == (generalist if policy == 'legacy' else expected)


def test_profile_change_during_claim_cannot_credit_another_compiler(tmp_path):
    net, _ = project(tmp_path, 'rom', [('f', 15, 'A', 20)])
    agent, _ = net.local_agent('model-1')
    fn = net.claim_row(agent, 'game', 'f')
    net.claim_row(agent, 'game', 'f')
    sample = net.store.one('SELECT * FROM claim_routing_samples')
    assert sample['band'] == 'small:near' and not sample['tested']
    assert net.store.one('SELECT COUNT(*) AS n FROM claim_routing_samples')['n'] == 1

    with net.store.tx() as db:
        cfg = json.loads(net.project_row('game')['config'])
        cfg['compiler']['flags'] = ['-O3']
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
    net._record(fn, agent, 0, 'int f(void) {return 1;}', 0, matched=True)
    sample = net.store.one('SELECT * FROM claim_routing_samples')
    assert not sample['tested'] and not sample['won']


def test_completion_and_same_cohort_requirement(tmp_path):
    net, pid = project(tmp_path, 'rom', [('claimed', 12, 'A', 20), ('left', 12, 'A', 20), ('different', 12, 'B', 20)])
    model, _ = net.local_agent('model-1')
    peer, _ = net.local_agent('peer-1')
    fn = net.claim_row(model, 'game', 'claimed')
    net._record(fn, model, 0, 'int claimed(void) {return 1;}', 0, matched=True)
    sample = net.store.one('SELECT * FROM claim_routing_samples')
    assert sample['tested'] and sample['won']
    assert net.store.one('SELECT 1 FROM claims') is None
    history(net, pid, model, 'left', 30, 24, 1000)
    history(net, pid, peer, 'different', 30, 1, 2000)
    assert claim_routing.routes(net.store, net.project_row('game'), model, time.time(),
                                {model.id: model.name, peer.id: peer.name}) == ([], [])


def test_pending_claim_does_not_turn_progress_into_a_completed_loss(tmp_path):
    net, pid = project(tmp_path, 'rom', [('ongoing', 12, 'A', 20), ('hard', 12, 'A', 20), ('easy', 30, 'A', None)])
    model, _ = net.local_agent('model-1')
    peer, _ = net.local_agent('peer-1')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET abandons=0 WHERE name='easy'")
    history(net, pid, model, 'hard', 20, 20, 1000)
    history(net, pid, peer, 'hard', 19, 0, 2000)
    fn = net.claim_row(peer, 'game', 'ongoing')
    net._record(fn, peer, 40, 'int ongoing(void) {return 0;}', 0)
    names = {model.id: model.name, peer.id: peer.name}
    assert claim_routing.routes(net.store, net.project_row('game'), model, time.time(), names) == ([], [])
    net.release(peer, 'ongoing', '', 'game')
    hard = net._function('hard', 'game')
    assert hard['id'] in claim_routing.routes(net.store, net.project_row('game'), model, time.time(), names)[0]


def test_explicit_order_and_named_claim_override_routing(tmp_path):
    net, pid = project(tmp_path, 'rom', [('hard', 8, 'A', 20), ('easy', 64, 'A', None)])
    model, _ = net.local_agent('model-1')
    peer, _ = net.local_agent('peer-1')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET abandons=0 WHERE name='easy'")
    history(net, pid, model, 'hard', 30, 25, 1000)
    history(net, pid, peer, 'hard', 30, 0, 2000)
    cfg = json.loads(net.project_row('game')['config']) | {'claim_order': 'hardest'}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
    assert net.claim_row(model, 'game')['name'] == 'easy'
    assert net.claim_row(peer, 'game', 'hard')['name'] == 'hard'


def test_retries_by_one_model_do_not_count_as_distinct_functions():
    rows = [
        {'function_id': 1, 'agent_id': 1 + index % 2, 'profile': 'same', 'band': 'small:near', 'won': 1}
        for index in range(60)
    ]
    rows += [
        {'function_id': index + 2, 'agent_id': 3, 'profile': 'same', 'band': 'small:near', 'won': 0}
        for index in range(30)
    ]
    assert claim_routing.preferences(rows, {1: 'model', 2: 'model', 3: 'peer'}, 'model') == (set(), set())


def test_model_results_keeps_starting_cohorts_and_counts_done_claims_once(tmp_path):
    net, pid = _game(tmp_path, [('f', 10, 'open'), ('g', 50, 'open')])
    agent, _ = net.local_agent('codex-example-1')
    peer, _ = net.local_agent('codex-example-2')
    f = net._function('f', 'game')['id']
    g = net._function('g', 'game')['id']
    now = time.time()
    with net.store.tx() as db:
        db.executemany('INSERT INTO claim_routing_samples VALUES(?,?,?,?,?,?,?,?)', [
            (pid, f, agent.id, now-3, 'profile', 'small:near', 1, 0),
            (pid, f, peer.id, now-2, 'profile', 'small:near', 1, 1),
            (pid, g, agent.id, now-1, 'profile', 'medium:partial', 1, 0),
            (pid, g, peer.id, now, 'profile', 'medium:partial', 1, 1),
        ])
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (g, peer.id, now, now+60))
    assert claim_routing.model_results(net.store, 'game', now, {
        agent.id: agent.name, peer.id: peer.name,
    }) == {'codex-example': [
        {'band': 'medium:partial', 'claims': 1, 'matches': 0},
        {'band': 'small:near', 'claims': 1, 'matches': 1},
    ]}
