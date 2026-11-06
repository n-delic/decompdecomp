from decompnet.app import permuter_focus, repair_ranking


def test_actual_calls_take_precedence_over_generic_instruction_changes():
    rows = [('i', 'jal original', 'jal candidate')]
    assert permuter_focus.classify(rows) == 'call'
    assert repair_ranking.order(['signedness', 'call_arity', 'stack_array'], rows)[0] == 'call_arity'
    assert permuter_focus.boosts(rows)['perm_randomize_function_type'] == 3


def test_stack_register_constants_and_unknown_evidence():
    assert permuter_focus.classify([('s', 'sw a0,32(sp)', 'sw a0,40(sp)')]) == 'stack'
    assert permuter_focus.classify([('r', 'add t0,a0,a1', 'add t1,a0,a1')]) == 'register'
    assert permuter_focus.classify([('i', 'lui a0,3', 'lui a0,4')]) == 'constant'
    assert permuter_focus.boosts([]) == {}
    families = ['unknown', 'stack_array', 'call_arity']
    assert set(repair_ranking.order(families, [('s', 'x', 'y')])) == set(families)


def test_baseline_cached_per_input_and_clears_unrelated_evidence():
    from decompnet.app.permute import State
    from tests.test_permute import AGENT, FakeNet, ScriptedPermuter
    net = FakeNet()
    net._last_diffs = {(999, 1): ([('s', 'stale', 'other source')], '')}
    net.keep_diff = lambda agent, fn, rows: net._last_diffs.update({(agent, fn): (rows, '')})
    p = ScriptedPermuter(net, {})
    st = State()
    proj, fn = net.project_row('game'), net.function()
    score, compiled = p._baseline(AGENT, proj, fn, st, None)
    assert score == 1000 and compiled == 1
    assert repair_ranking.evidence(net, 1, AGENT.id) == []
    assert p._baseline(AGENT, proj, fn, st, None) == (1000, 0)
    assert len(p.judged) == 1
    net._context = lambda *args: {'version': 2, 'header': 'new context'}
    assert p._baseline(AGENT, proj, fn, st, None) == (1000, 1)
    assert len(p.judged) == 2


def test_random_generator_receives_advisory_boosts_without_changing_verdict(monkeypatch):
    from decompnet.app.permute import State
    from tests.test_permute import AGENT, FakeNet, ScriptedPermuter, batches
    net = FakeNet()
    net._last_diffs = {(AGENT.id, 1): ([('s', 'sw a0,32(sp)', 'sw a0,40(sp)')], '')}
    p = ScriptedPermuter(net, {'candidate': 0})
    jobs = batches(monkeypatch, ['miss'], ['candidate'])
    state = State()
    p._permute(AGENT, net.project_row('game'), net.function(), state)
    assert jobs[0]['family'] == 'gcc'
    assert jobs[0]['weight_boosts'] == {}
    assert jobs[1]['weight_boosts']['perm_reorder_decls'] == 3
    assert state.matched == 1 and p.judged == ['miss', 'candidate']


def test_learned_weights_reduce_an_unproductive_family_but_default_batches_remain(monkeypatch):
    from decompnet.app import adaptive_repairs
    from decompnet.app.permute import State
    from tests.test_permute import AGENT, FakeNet, ScriptedPermuter, batches
    net = FakeNet()
    net._last_diffs = {(AGENT.id,1):([('s','sw a0,32(sp)','sw a0,40(sp)')],'')}
    proj, fn = net.project_row('game'), net.function()
    key = adaptive_repairs.profile(net, proj, fn)
    for _ in range(200):
        adaptive_repairs.record(net, key, 'stack', 'perm_reorder_decls', 20, None)
    p = ScriptedPermuter(net, {'candidate':0})
    jobs = batches(monkeypatch, ['miss'], ['candidate'])
    p._permute(AGENT, proj, fn, State())
    assert jobs[0]['weight_boosts'] == {}
    assert 0.25 <= jobs[1]['weight_boosts']['perm_reorder_decls'] < 3
    assert jobs[0]['trace'] and jobs[1]['trace']
