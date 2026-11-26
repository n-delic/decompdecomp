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


def test_a_family_unmeasured_for_this_mismatch_kind_borrows_the_profiles_other_kinds_upward():
    net = FakeNet()
    for _ in range(40):
        policy.record(net, 'profile', 'control', 'useful', 20, 0, 10)
    assert 'useful' not in policy.policy_rows(net, 'profile', 'call')
    factors = policy.factors(net, 'profile', 'call')
    assert 1 < factors['useful'] <= policy.POOLED_MAX_FACTOR
    assert policy.factors(net, 'other profile', 'call') == {}  # nothing says these two compile for the same processor


def test_borrowed_evidence_never_demotes_and_never_overrides_the_kinds_own_measurement():
    net = FakeNet()
    for _ in range(200):
        policy.record(net, 'profile', 'register', 'bad', 20, None)
    for _ in range(40):
        policy.record(net, 'profile', 'control', 'useful', 20, 0, 10)
        policy.record(net, 'profile', 'call', 'useful', 20, None, 10)
    factors = policy.factors(net, 'profile', 'call')
    assert 'bad' not in factors              # poor elsewhere says nothing against it here
    assert factors['useful'] < 1             # this kind's own 40 failures outweigh its wins in another kind


def test_pooling_counts_a_trial_once_even_though_it_is_recorded_under_its_shape_too():
    net = FakeNet()
    for _ in range(5):
        policy.record(net, 'profile', 'call', 'narrow', 20, 0, 10)
        policy.record(net, 'profile', 'call@field', 'narrow', 20, 0, 10, coverage=False)
    [row] = policy.pooled_rows(net, 'profile', 'register')
    assert row['attempted'] == row['matched'] == 5
    assert policy.pooled_rows(net, 'profile', 'call') == []
    assert policy.factors(net, 'profile', 'call@field')['narrow'] > 1   # a shape key keeps its own evidence and no pool


def test_a_combinations_matches_credit_each_of_its_repairs():
    net = FakeNet()
    for _ in range(40):
        policy.record(net, 'profile', 'call', 'hoist+sink', 20, 0, 10)
    factors = policy.factors(net, 'profile', 'call')
    assert factors['hoist'] > 1 and factors['sink'] > 1
    assert policy.factors(net, 'profile', 'register')['hoist'] > 1  # and the profile's other mismatch kinds borrow it
    assert 'hoist' not in policy.factors(net, 'other profile', 'call')


def test_a_combinations_misses_never_demote_its_repairs():
    net = FakeNet()
    for _ in range(40):
        policy.record(net, 'profile', 'call', 'hoist', 20, 0, 10)
    alone = policy.factors(net, 'profile', 'call')['hoist']
    for _ in range(500):
        policy.record(net, 'profile', 'call', 'hoist+sink', 20, None, 10)
    factors = policy.factors(net, 'profile', 'call')
    assert factors['hoist'] >= alone
    assert 'sink' not in factors  # only ever seen missing in a pair: still unmeasured, not poor


def test_a_repair_applied_twice_counts_that_trial_once():
    [row] = policy._credited([{'family': 'hoist+hoist', 'attempted': 3, 'improved': 1, 'matched': 1,
                               'failed': 0, 'elapsed_ms': 30}])
    assert (row['family'], row['attempted'], row['matched']) == ('hoist', 3, 1)


def setups(net, monkeypatch, *flags):
    """The policy keys of one project built with each of `flags`."""
    proj, fn = net.project_row('game'), net.function()
    original = net.cfg(proj)
    keys = []
    for flag in flags:
        monkeypatch.setattr(net, 'cfg', lambda p, flag=flag: {**original, 'compiler': {**original['compiler'], 'flags': [flag]}})
        keys.append(policy.profile(net, proj, fn))
    return keys


def test_a_setup_borrows_upward_from_the_other_setups_of_its_processor(monkeypatch):
    net = FakeNet()
    proven, new = setups(net, monkeypatch, '-O2', '-O1')
    for _ in range(100):
        policy.record(net, proven, 'register', 'useful', 20, 0, 10)
        policy.record(net, proven, 'register', 'poor', 20, None, 10)
    factors = policy.factors(net, new, 'register')
    assert set(factors) == {'useful'} and 1 < factors['useful'] <= policy.BORROWED_MAX_FACTOR
    assert policy.factors(net, new, 'call') == {}            # only the same mismatch kind
    assert policy.factors(net, new, 'register@field') == {}  # a statement shape keeps its own evidence


def test_a_setups_own_verdict_outweighs_what_others_measured(monkeypatch):
    net = FakeNet()
    proven, here, kind = setups(net, monkeypatch, '-O2', '-O1', '-O0')
    for _ in range(100):
        policy.record(net, proven, 'register', 'useful', 20, 0, 10)
        policy.record(net, here, 'register', 'useful', 20, None, 10)
        policy.record(net, kind, 'call', 'useful', 20, None, 10)
    assert policy.factors(net, here, 'register')['useful'] < 1   # measured poor for this mismatch kind
    assert 'useful' not in policy.factors(net, kind, 'register')  # measured poor in its other kinds


def test_another_processor_lends_nothing(monkeypatch):
    net = FakeNet()
    mips, = setups(net, monkeypatch, '-O2')
    proj = dict(net.project_row('game')) | {'arch': 'ppc', 'big_endian': 1}
    other = policy.profile(net, proj, net.function())
    for _ in range(100):
        policy.record(net, mips, 'register', 'useful', 20, 0, 10)
    assert policy.factors(net, other, 'register') == {}


def test_history_of_a_setup_the_project_settings_name_is_borrowed_without_searching_it(monkeypatch):
    net = FakeNet()
    old = policy._key(policy._rom_settings(net.project_row('game'), net.cfg(net.project_row('game')),
                                           net.cfg(net.project_row('game'))['compiler']))
    for _ in range(100):
        policy.record(net, old, 'register', 'useful', 20, 0, 10)   # counted before setups were noted
    new, = setups(net, monkeypatch, '-O1')
    assert policy.factors(net, new, 'register')['useful'] > 1


def test_pooling_matches_a_plain_sum_while_the_cache_serves_and_picks_up_new_counts(monkeypatch):
    """pooled_rows keeps per-profile totals between reads; its answer must stay what summing the other kinds gives."""
    net = FakeNet()
    for kind, family, n in (('control', 'a', 3), ('call', 'a', 2), ('call', 'b', 4), ('register', 'a', 5)):
        for _ in range(n):
            policy.record(net, 'profile', kind, family, 20, 0, 10)
    def totals(focus):
        return {r['family']: (r['attempted'], r['matched']) for r in policy.pooled_rows(net, 'profile', focus)}
    assert totals('call') == {'a': (8, 8)}
    assert totals('register') == {'a': (5, 5), 'b': (4, 4)}
    assert totals('call') == {'a': (8, 8)}                       # (served from the kept totals)
    policy.record(net, 'profile', 'control', 'a', 20, None, 10)  # a new count shows at once, before any flush
    assert totals('call') == {'a': (9, 8)}
    assert totals('control') == {'a': (7, 7), 'b': (4, 4)}


def test_sequence_rankings_read_their_tables_once_between_counts():
    from decompnet.app import repair_sequence_metrics as metrics
    net = FakeNet()
    reads = []
    original = net.store.all
    net.store.all = lambda sql, *args: (reads.append(sql), original(sql, *args))[1]
    metrics.prepare(net)
    metrics.record_outcome(net, 'p', 'f', [], 'a', True, True, False)
    for _ in range(5):
        assert [r['family'] for r in metrics.outcome_rows(net, 'p', 'f', [])] == ['a']
    assert sum('repair_sequence_outcomes' in sql for sql in reads) <= 1
