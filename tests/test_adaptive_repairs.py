"""Outcome histories alter priorities without disabling exploration or crossing build profiles."""
from decompnet.app import adaptive_repairs as policy
from tests.test_permute import FakeNet


def test_sparse_history_does_not_reweight_and_measured_failures_never_disable_a_family():
    net = FakeNet()
    for _ in range(31):
        policy.record(net, 'profile', 'register', 'bad', 20, None)
    assert policy.factors(net, 'profile', 'register') == {}
    for _ in range(200):
        policy.record(net, 'profile', 'register', 'bad', 20, None)
    assert policy.MIN_FACTOR <= policy.factors(net, 'profile', 'register')['bad'] < 1
    assert policy.factors(net, 'other compiler', 'register') == {}
    assert policy.factors(net, 'profile', 'call') == {}


def test_verified_wins_receive_more_weight_and_policy_survives_a_new_reader():
    net = FakeNet()
    for _ in range(32):
        policy.record(net, 'profile', 'control', 'useful', 20, 0, 10)
    assert 1 < policy.factors(net, 'profile', 'control')['useful'] <= 4
    net._repair_policy_ready = False
    assert policy.factors(net, 'profile', 'control')['useful'] > 1
    row = net.store.one('SELECT * FROM repair_policy')
    assert row['attempted'] == row['matched'] == row['improved'] == 32
    assert row['elapsed_ms'] == 320


def test_compiler_flags_are_part_of_the_policy_profile(monkeypatch):
    net = FakeNet()
    proj, fn = net.project_row('game'), net.function()
    first = policy.profile(net, proj, fn)
    original = net.cfg(proj)
    monkeypatch.setattr(net, 'cfg', lambda p: {**original, 'compiler':{**original['compiler'], 'flags':['-O3']}})
    assert policy.profile(net, proj, fn) != first


def test_adaptive_family_order_feeds_unmeasured_families_in_and_leaves_poor_ones_last(monkeypatch):
    net = FakeNet()
    proj, fn = net.project_row('game'), net.function()
    monkeypatch.setattr(policy, 'factors', lambda *a: {'useful':4,'bad':0.25})
    assert policy.order(net, proj, fn, [], ['bad','unknown','useful']) == ['useful','unknown','bad']


def test_metrics_audit_records_origin_and_collection_window_without_merging_legacy_rates():
    net = FakeNet()
    policy.record(net,'profile','call','call_arity',20,10,5,origin='deep permuter')
    report = policy.coverage(net)
    assert report['policy']['attempted'] == 1
    source = report['sources'][0]
    assert source['origin'] == 'deep permuter' and source['attempted'] == source['improved'] == 1
    assert source['first_at'] <= source['last_at'] and source['elapsed_ms'] == 5
    assert 'different collection window' in report['scope']


def test_equal_success_counts_prefer_faster_verified_throughput():
    net = FakeNet()
    for _ in range(40):
        policy.record(net, 'same', 'register', 'fast', 20, 0, 100)
        policy.record(net, 'same', 'register', 'slow', 20, 0, 10000)
    weights = policy.factors(net, 'same', 'register')
    assert weights['fast'] > weights['slow'] >= policy.MIN_FACTOR


def test_explicit_nonexact_zero_does_not_train_a_verified_win():
    net = FakeNet()
    policy.record(net,'same','register','family',20,0,100,exact=False)
    row = net.store.one('SELECT * FROM repair_policy')
    assert row['improved'] == 1 and row['matched'] == 0


def test_a_family_with_a_few_matches_counts_as_measured_before_it_has_enough_trials():
    net = FakeNet()
    for _ in range(13):
        policy.record(net, 'profile', 'call', 'narrow_repair', 20, 0, 10)
    for _ in range(13):
        policy.record(net, 'profile', 'call', 'misses', 20, None, 10)
    factors = policy.factors(net, 'profile', 'call')
    assert 1 < factors['narrow_repair'] <= policy.EARLY_MAX_FACTOR
    assert 'misses' not in factors  # no match, too few trials: still unmeasured


def test_early_evidence_never_outranks_a_family_with_enough_trials_of_the_same_yield():
    net = FakeNet()
    for _ in range(40):
        policy.record(net, 'profile', 'call', 'proven', 20, 0, 10)
    policy.record(net, 'profile', 'call', 'lucky', 20, 0, 10)
    factors = policy.factors(net, 'profile', 'call')
    assert factors['lucky'] <= policy.EARLY_MAX_FACTOR < factors['proven'] <= policy.MAX_FACTOR


def test_an_early_match_family_is_ranked_ahead_of_unmeasured_ones(monkeypatch):
    net = FakeNet()
    proj, fn = net.project_row('game'), net.function()
    for _ in range(5):
        policy.record(net, policy.profile(net, proj, fn), '', 'narrow_repair', 20, 0, 10)
    assert policy.order(net, proj, fn, [], ['other', 'narrow_repair'])[0] == 'narrow_repair'
