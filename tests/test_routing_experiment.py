"""Randomized assignment receipts whose outcome and budget are enforced by the claim path."""
import json

from decompnet import routing_experiment
from tests.test_claim_routing import history, project


def test_sampled_overlap_uses_same_cohort_and_stops_at_budget(tmp_path, monkeypatch):
    net, pid = project(tmp_path, 'rom', [
        ('control', 12, 'A', 20), ('probe', 12, 'A', 20),
        ('after_cap', 12, 'A', 20), ('easy', 30, 'A', None),
    ])
    strong, _ = net.local_agent('strong-1')
    weak, _ = net.local_agent('weak-1')
    third, _ = net.local_agent('weak-2')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET abandons=0 WHERE name='easy'")
    history(net, pid, strong, 'control', 30, 25, 1000)
    history(net, pid, weak, 'control', 30, 0, 2000)
    cfg = json.loads(net.project_row('game')['config'])
    routing_experiment.configure(cfg, 2)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
    rows = {name: net._function(name, 'game') for name in ('control', 'probe', 'after_cap')}
    monkeypatch.setattr(routing_experiment, 'arm', lambda _, fid: 'policy' if fid == rows['control']['id'] else 'overlap')
    # Exclude the second overlap case until the two planned claims have finished.
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='asm' WHERE name='after_cap'")
    assert net.claim_row(weak, 'game')['name'] == 'probe'
    assert net.claim_row(strong, 'game')['name'] == 'control'
    assert routing_experiment.exclusive(net, net.project_row('game'), rows['probe']['id'], weak.id)
    net._record(rows['probe'], weak, 30, 'int probe(void) {return 1;}', 0)
    net.release(weak, 'probe', '', 'game')
    net._record(rows['control'], strong, 0, 'int control(void) {return 1;}', 0, matched=True)
    with net.store.tx() as db:
        db.execute('UPDATE claim_routing_experiments SET ended_at=claimed_at+1800')
        db.execute("UPDATE functions SET status='open' WHERE name='after_cap'")
    result = routing_experiment.report(net.store, net.project_row('game'), cfg['routing_experiment'])
    assert result['used'] == 2 and result['pending'] == 0 and result['cohorts'] == 1
    assert result['results']['policy']['matches_per_helper_hour'] == 2
    assert result['results']['policy']['matches_per_claim'] == 1
    assert result['results']['overlap']['matches_per_claim'] == 0
    assert net.claim_row(third, 'game')['name'] == 'easy'
    assert net.store.one('SELECT COUNT(*) AS n FROM claim_routing_experiments')['n'] == 2
    before = cfg['routing_experiment']['run']
    routing_experiment.configure(cfg, 2, used=2)
    assert cfg['routing_experiment']['run'] != before


def test_sample_arm_is_stable_small_and_split():
    from collections import Counter

    counts = Counter(routing_experiment.arm('fixed-run', i) for i in range(1024))
    assert 35 < counts['policy'] < 100
    assert 35 < counts['overlap'] < 100
    assert routing_experiment.arm('fixed-run', 2) == routing_experiment.arm('fixed-run', 2)
