import pytest

from decompnet.app import deep_permuter, draft_repairs, permuter_local
from decompnet.app.permute import Permuter
from decompnet.network import NetworkError
from tests.test_permute import AGENT, FakeNet, ProjectStub, batches


class Log:
    def __init__(self):
        self.lines, self.updates = [], []
        self.cancelled = False
    def __call__(self, line):
        self.check_cancel(); self.lines.append(line)
    def check_cancel(self):
        if self.cancelled:
            raise RuntimeError('cancelled')
    def progress(self, value):
        self.check_cancel(); self.updates.append(value)


def setup(monkeypatch, imported=False):
    net = FakeNet()
    monkeypatch.setattr('decompnet.app.compiler_preflight.warnings', lambda *args: '')
    net._function = lambda name, slug: net.function()
    net.local_agent = lambda name: (AGENT, None)
    net.fork_slug = lambda slug: 'fork' if imported else None
    class Fork(ProjectStub):
        def permuter_header(self, slug, name):
            return ''
    net.forks = Fork()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open'")
    monkeypatch.setattr(deep_permuter.Search, '_baseline', lambda *args: (40, 0))
    monkeypatch.setattr(draft_repairs, 'variants', lambda *args, **kwargs: ['local'])
    monkeypatch.setattr(permuter_local, 'variants', lambda *args: [])
    return net


@pytest.mark.parametrize('imported', [False, True])
def test_one_function_deep_search_uses_normal_judge_and_keeps_improvements(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    seen = []
    def judge(self, agent, proj, fn, source, best, fork):
        seen.append((source, fork))
        score = {'local':20, 'random':0}.get(source, 100)
        if score < best:
            with net.store.tx() as db:
                db.execute('UPDATE functions SET best_score=?,best_source=?,status=? WHERE id=?',
                           (score, source, 'matched' if score == 0 else 'open', fn['id']))
        return score
    monkeypatch.setattr(Permuter, '_score', judge)
    batches(monkeypatch, ['local', 'random', 'never tested'])
    log = Log()
    result = deep_permuter.run(net, 'game', 'f', 30, 10, log)
    assert result['matched'] and result['tested'] == 2 and result['skipped'] == 1
    assert seen == [('local','fork' if imported else None), ('random','fork' if imported else None)]
    assert net.function()['status'] == 'matched'
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0
    assert log.updates[-1]['best_score'] == 0


def test_variant_limit_cancellation_and_duplicate_misses_release_the_lease(monkeypatch):
    net = setup(monkeypatch)
    monkeypatch.setattr(Permuter, '_score', lambda *args: 40)
    batches(monkeypatch, ['different'])
    result = deep_permuter.run(net, 'game', 'f', 1, 1, Log())
    assert result['reason'] == 'Variant limit reached' and result['tested'] == 1
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0
    log = Log()
    def cancel(*args):
        log.cancelled = True
        return 40
    monkeypatch.setattr(Permuter, '_score', cancel)
    # Change the build inputs so the first local candidate is not a reusable old miss.
    net._context = lambda *args: {'header':'changed', 'version':2}
    with pytest.raises(RuntimeError, match='cancelled'):
        deep_permuter.run(net, 'game', 'f', 1, 10, log)
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_failed_compiles_remain_retryable_and_better_cached_results_are_rejudged(monkeypatch):
    net = setup(monkeypatch)
    calls = []
    monkeypatch.setattr(Permuter, '_score', lambda *args: calls.append(1))
    log = Log(); search = deep_permuter.Search(net, 1, 10, log)
    proj, fn = net.project_row('game'), net.function()
    search._score(AGENT, proj, fn, 'bad', 40, None)
    search._score(AGENT, proj, fn, 'bad', 40, None)
    assert len(calls) == 2
    monkeypatch.setattr(Permuter, '_score', lambda *args: calls.append(1) or 20)
    search._score(AGENT, proj, fn, 'ok', 40, None)
    search._score(AGENT, proj, fn, 'ok', 40, None)
    assert len(calls) == 4  # a cache hit cannot substitute for saving/judging an improvement


@pytest.mark.parametrize('minutes,limit', [(0,1), (121,1), (True,1), (1,0), (1,1000001), (1,True)])
def test_invalid_limits_do_not_start_a_search(monkeypatch, minutes, limit):
    net = setup(monkeypatch)
    with pytest.raises(NetworkError):
        deep_permuter.validate(net, 'game', 'f', minutes, limit)


def test_local_claims_allow_search_but_matched_and_unscored_functions_are_rejected(monkeypatch):
    net = setup(monkeypatch)
    for values in ["status='matched'", "best_source=NULL", "best_score=NULL"]:
        with net.store.tx() as db: db.execute('UPDATE functions SET '+values)
        with pytest.raises(NetworkError): deep_permuter.validate(net, 'game', 'f', 1, 10)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',best_source='int f(){return 1;}',best_score=1")
        db.execute('INSERT INTO claims VALUES(1,1,0,9999999999)')
    assert deep_permuter.validate(net, 'game', 'f', 1, 10)[1]['name'] == 'f'
    net.links = {'game': object()}
    assert deep_permuter.validate(net, 'game', 'f', 1, 10)[1]['name'] == 'f'


def test_time_limit_ends_before_a_compile_and_keeps_the_lease_free(monkeypatch):
    net = setup(monkeypatch)
    initialize = deep_permuter.Search.__init__
    def expired(self, *args):
        initialize(self, *args)
        self.deadline = 0
    monkeypatch.setattr(deep_permuter.Search, '__init__', expired)
    result = deep_permuter.run(net, 'game', 'f', 1, 100, Log())
    assert result['reason'] == 'Time limit reached' and result['tested'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_cancelling_random_search_closes_generator_and_releases_function(monkeypatch):
    net = setup(monkeypatch)
    monkeypatch.setattr(draft_repairs, 'variants', lambda *args, **kwargs: [])
    log = Log(); closed = []
    def judge(*args):
        log.cancelled = True
        return 40
    monkeypatch.setattr(Permuter, '_score', judge)
    from decompnet.app import permute
    class Generator:
        def batch(self, job): return ['one']
        def close(self): closed.append(True)
    monkeypatch.setattr(permute, 'available', lambda: True)
    monkeypatch.setattr(permute, 'Generator', Generator)
    with pytest.raises(RuntimeError, match='cancelled'):
        deep_permuter.run(net, 'game', 'f', 1, 100, log)
    assert closed and net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_a_scalar_cache_does_not_replace_baseline_instruction_evidence(monkeypatch):
    measure = deep_permuter.Search._baseline
    net = setup(monkeypatch)
    monkeypatch.setattr(deep_permuter.Search, '_baseline', measure)
    seen = []
    monkeypatch.setattr(Permuter, '_score', lambda *args: seen.append(1) or 40)
    def baseline(self, agent, proj, fn, state, fork):
        return self._score(agent, proj, fn, 'cached', 40, fork), 1
    monkeypatch.setattr(Permuter, '_baseline', baseline)
    search = deep_permuter.Search(net, 1, 10, Log())
    proj, fn = net.project_row('game'), net.function()
    search._score(AGENT, proj, fn, 'cached', 40, None)
    search._baseline(AGENT, proj, fn, None, None)
    assert len(seen) == 2


def test_invalid_generated_calls_do_not_consume_the_compile_allowance(monkeypatch):
    net = setup(monkeypatch)
    search = deep_permuter.Search(net, 1, 4, Log())
    calls = []
    monkeypatch.setattr(Permuter, '_score', lambda *args:calls.append(1) or 40)
    net._context = lambda *args: {'header':'', 'version':1}
    source = 'int send(int a,int b); int f(void) { return send(1); }'
    assert search._score(AGENT, net.project_row('game'), net.function(), source, 40, None) is None
    assert search.rejected == 1 and search.tested == 0 and not calls


def test_live_metrics_count_only_verified_strict_improvements_and_keep_starting_baseline(monkeypatch):
    net = setup(monkeypatch)
    fn = net.function()
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    answers = iter([50, 30, 30, None, 20, 20, 0])
    monkeypatch.setattr(Permuter, '_score', lambda *args:next(answers))
    proj = net.project_row('game')
    search.measuring_baseline = True
    search._score(AGENT, proj, fn, 'baseline', 40, None)
    assert search.metrics()['starting_score'] == 50  # The stale saved score was 40.
    search.measuring_baseline = False
    for source in ['better', 'equal', 'error', 'better again']:
        search._score(AGENT, proj, fn, source, 50, None)
    assert search.improvements == 2 and search.best == 20
    # A second baseline when random search starts must not erase local-repair progress.
    search.measuring_baseline = True
    search._score(AGENT, proj, fn, 'current baseline', 20, None)
    search.measuring_baseline = False
    search._score(AGENT, proj, fn, 'match', 20, None)
    metrics=search.metrics()
    efficiency=metrics.pop('efficiency')
    assert efficiency['first_exact_check']==5 and efficiency['model_tokens']==0
    assert metrics == {'starting_score':50,'starting_percent':95.0,'best_score':0,
                                'best_percent':100.0,'improvements':3,'local_search':{},
                                'allocation_trials':0,'allocation_diagnostic_compiles':0,'reviewed_seeds':{}}
    assert search.log.updates[-1]['improvements'] == 3


def test_percentages_do_not_round_a_near_match_to_100(monkeypatch):
    net = setup(monkeypatch)
    fn = {**dict(net.function()), 'n_insns':10000, 'best_score':1}
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    assert search.metrics()['starting_percent'] == search.metrics()['best_percent'] == 99.9
    assert search.improvements == 0


@pytest.mark.parametrize('imported', [False, True])
def test_local_repairs_chain_from_saved_improvements_with_fresh_diffs(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    original = net.function()['best_source']
    generated, tested, stack_rows = [], [], []
    def variants(source, *args, **kwargs):
        generated.append(source)
        return {original:['better', 'old remainder'], 'better':['best', 'better'], 'best':['exact']}[source]
    monkeypatch.setattr(draft_repairs, 'variants', variants)
    monkeypatch.setattr(deep_permuter.stack_padding_repairs, 'grow',
                        lambda source, name, arch, rows: stack_rows.append(rows) or [])
    net._last_diffs = {}
    def judge(self, agent, proj, fn, source, best, fork):
        tested.append((source, fork))
        score = {'better':30, 'best':10, 'exact':0}[source]
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_score=?,best_source=?,status=? WHERE id=?',
                       (score, source, 'matched' if score == 0 else 'open', fn['id']))
        net._last_diffs[agent.id, fn['id']] = ([source], '')
        return score
    monkeypatch.setattr(Permuter, '_score', judge)
    monkeypatch.setattr(Permuter, '_permute', lambda *args: pytest.fail('Local repairs should match first'))
    result = deep_permuter.run(net, 'game', 'f', 1, 10, Log())
    assert result['matched'] and result['tested'] == 3
    assert generated == [original, 'better', 'best']
    assert stack_rows == [[], ['better'], ['best']]
    assert tested == [(s, 'fork' if imported else None) for s in ['better', 'best', 'exact']]
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_chained_local_repairs_share_the_original_compile_limit(monkeypatch):
    net = setup(monkeypatch)
    generated = []
    def variants(source, *args, **kwargs):
        generated.append(source)
        return [source + ' ']
    monkeypatch.setattr(draft_repairs, 'variants', variants)
    def judge(self, agent, proj, fn, source, best, fork):
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_score=?,best_source=? WHERE id=?', (best-1, source, fn['id']))
        return best-1
    monkeypatch.setattr(Permuter, '_score', judge)
    result = deep_permuter.run(net, 'game', 'f', 1, 2, Log())
    assert result['reason'] == 'Variant limit reached'
    assert result['tested'] == 2 and len(generated) == 2
    assert net.function()['best_score'] == 38
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_recipe_counts_and_winner_provenance_survive_reopening(monkeypatch):
    from decompnet.app.deep_sweep import Sweeps
    from decompnet.app.support import Jobs
    from decompnet.repair_feedback import trial

    net = setup(monkeypatch)
    net.deep_sweeps = Sweeps(net, Jobs())
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    answers = iter([30, 30, None])
    monkeypatch.setattr(Permuter, '_score', lambda *args: next(answers))
    provenance = {'kind': 'random rewrite', 'family': 'perm_cast', 'lineage': ['perm_temp', 'perm_cast']}
    with trial(provenance):
        assert search._score(AGENT, proj, fn, 'winner', 40, None) == 30
        assert search._score(AGENT, proj, fn, 'worse', 30, None) == 30
        assert search._score(AGENT, proj, fn, 'bad', 30, None) is None
    search.flush_recipes()
    reopened = Sweeps(net, Jobs())
    stats = reopened.recipes(proj['id'])[0]
    assert (stats['family'], stats['tested'], stats['improvements'], stats['score_gain']) == ('perm_cast', 3, 1, 10)
    winner = reopened.recipe_wins(proj['id'])[0]
    assert (winner['previous_score'], winner['new_score']) == (40, 30)
    assert winner['provenance']['lineage'] == ['perm_temp', 'perm_cast']
    assert winner['provenance']['source_sha256']


def test_outcome_measurements_classify_trials_and_followup_phase(monkeypatch):
    from decompnet.app.deep_sweep import Sweeps
    from decompnet.app.support import Jobs
    from decompnet.repair_feedback import trial

    net = setup(monkeypatch)
    net.deep_sweeps = Sweeps(net, Jobs())
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    answers = iter([(None, 'compile_failed'), (None, 'unscored'), (40, 'scored'),
                    (45, 'scored'), (30, 'scored'), (0, 'scored')])
    def judge(self, *args):
        result, self._trial_outcome = next(answers)
        return result
    monkeypatch.setattr(Permuter, '_score', judge)
    search.phase = 'local repairs after random improvement'
    with trial({'family': 'typed_temporary'}):
        for i in range(6):
            search._score(AGENT, proj, fn, f'candidate {i}', 40, None)
    search.flush_recipes()
    row = net.deep_sweeps.measurements(proj['id'])[0]
    assert row['phase'] == 'local repairs after random improvement'
    assert tuple(row[k] for k in ('attempted', 'compile_failed', 'unscored', 'unchanged',
                                  'worse', 'improved', 'matched')) == (6, 1, 1, 1, 1, 2, 1)
    assert row['elapsed_ms'] >= 0 and row['score_gain'] == 50
    assert Sweeps(net, Jobs()).measurements(proj['id']) == [row]


def test_rom_trial_distinguishes_compile_failure_from_unscored_object(monkeypatch):
    from types import SimpleNamespace

    net = setup(monkeypatch)
    permuter = Permuter(net)
    fn, proj = net.function(), net.project_row('game')
    monkeypatch.setattr(net, '_context', lambda *args: {'header': '', 'version': 1})
    monkeypatch.setattr(net, '_compile', lambda *args: SimpleNamespace(ok=False), raising=False)
    assert permuter._score(AGENT, proj, fn, fn['best_source'], 40, None) is None
    assert permuter._trial_outcome == 'compile_failed'
    monkeypatch.setattr(net, '_compile', lambda *args: SimpleNamespace(ok=True, obj=b'object'))
    monkeypatch.setattr(net, '_evaluate', lambda *args: (None, None, []), raising=False)
    assert permuter._score(AGENT, proj, fn, fn['best_source'], 40, None) is None
    assert permuter._trial_outcome == 'unscored'


@pytest.mark.parametrize('imported', [False, True])
def test_local_detour_can_lead_to_match_without_saving_a_regression(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    original = net.function()['best_source']
    monkeypatch.setattr(draft_repairs, 'variants', lambda source,*a,**kw: {original:['detour'], 'detour':['exact']}.get(source, []))
    monkeypatch.setattr(deep_permuter.stack_padding_repairs, 'grow', lambda *a: [])
    seen = []
    def judge(self, agent, proj, fn, source, best, fork):
        seen.append(source)
        assert net.function()['best_source'] == original
        score = {'detour':48, 'exact':0}[source]
        if score < best:
            with net.store.tx() as db:
                db.execute("UPDATE functions SET best_score=?,best_source=?,status='matched' WHERE id=?", (score, source, fn['id']))
        return score
    monkeypatch.setattr(Permuter, '_score', judge)
    search = deep_permuter.Search(net, 1, 10, Log())
    assert search.local_repairs(AGENT, net.project_row('game'), net.function(), 40, 'fork' if imported else None) == (0, 'exact')
    assert seen == ['detour', 'exact']


@pytest.mark.parametrize('imported', [False, True])
def test_plateau_combinations_and_cached_rows_remain_expandable(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    original = net.function()['best_source']
    monkeypatch.setattr(draft_repairs, 'variants', lambda s,*a,**kw: {original:['plateau'], 'plateau':['exact']}.get(s, []))
    monkeypatch.setattr(deep_permuter.stack_padding_repairs, 'grow', lambda *a: [])
    net._last_diffs = {}
    calls = []
    def judge(self, agent, proj, fn, source, best, fork):
        calls.append(source)
        net._last_diffs[agent.id, fn['id']] = ([('r','move $s0,$a0','move $s1,$a0')], None)
        if source == 'exact':
            with net.store.tx() as db:
                db.execute("UPDATE functions SET best_score=0,best_source='exact',status='matched' WHERE id=?", (fn['id'],))
            return 0
        return 40
    monkeypatch.setattr(Permuter, '_score', judge)
    search = deep_permuter.Search(net, 1, 10, Log())
    fork = 'fork' if imported else None
    assert search.local_repairs(AGENT, net.project_row('game'), net.function(), 40, fork, max_trials=1)[0] == 40
    # A new search reuses the measured plateau but still explores its untried second edge.
    search = deep_permuter.Search(net, 1, 10, Log())
    assert search.local_repairs(AGENT, net.project_row('game'), net.function(), 40, fork)[0] == 0
    assert calls == ['plateau', 'exact']
    assert search.local_status['reason'] == 'matched'


def test_unproductive_deep_detour_returns_saved_source_and_respects_burst_limit(monkeypatch):
    net = setup(monkeypatch)
    original = net.function()['best_source']
    monkeypatch.setattr(draft_repairs, 'variants', lambda source,*a,**kw: {original:['detour'], 'detour':['worse']}.get(source, []))
    monkeypatch.setattr(deep_permuter.stack_padding_repairs, 'grow', lambda *a: [])
    seen = []
    monkeypatch.setattr(Permuter, '_score', lambda self,a,p,f,s,b,k: seen.append(s) or 48)
    search = deep_permuter.Search(net, 1, 10, Log())
    assert search.local_repairs(AGENT, net.project_row('game'), net.function(), 40, None, max_trials=1) == (40, original)
    assert seen == ['detour'] and net.function()['best_source'] == original


def test_cached_deep_misses_do_not_train_adaptive_policy_twice(monkeypatch):
    net = setup(monkeypatch)
    monkeypatch.setattr(Permuter, '_score', lambda *a: 40)
    search = deep_permuter.Search(net, 1, 10, Log())
    proj, fn = net.project_row('game'), net.function()
    search._score(AGENT, proj, fn, 'same candidate', 40, None)
    search._score(AGENT, proj, fn, 'same candidate', 40, None)
    assert net.store.one('SELECT SUM(attempted) n FROM repair_policy')['n'] == 1


def test_learned_connected_recipes_run_first_and_each_winner_retargets_its_own_diff(monkeypatch):
    from decompnet.app import repair_ranking, repair_sequences
    net=setup(monkeypatch);proj=net.project_row('game')
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='start'")
    initial=[('r','left','right')];middle=[('s','stack left','stack right')]
    fn=net.function();lineage=repair_sequences.Lineage(net,proj,fn,'start',initial)
    lineage.retain('start','middle','first');lineage.retain('middle','end','second',exact=True)
    def variants(source,*args,**kwargs):
        choices={'start':[('noise','slow'),('first','middle')],'middle':[('noise','slow2'),('second','end')]}.get(source,[])
        kwargs['labels'].update({candidate:family for family,candidate in choices})
        return [candidate for _,candidate in choices]
    monkeypatch.setattr(draft_repairs,'variants',variants)
    seen=[];ranked=[]
    original=repair_ranking.order
    def rank(families,rows):ranked.append(rows);return original(families,rows)
    monkeypatch.setattr(repair_ranking,'order',rank)
    search=deep_permuter.Search(net,1,20,Log(),fn);search.winner_diff=(initial,'legend')
    def judge(agent,proj,fn,source,best,fork):
        seen.append(source);score={'middle':20,'end':0}.get(source,60)
        net._last_diffs={(AGENT.id,fn['id']):(middle if source=='middle' else [],'legend')}
        if score<best:
            with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=? WHERE id=?',(source,score,fn['id']))
        return score
    monkeypatch.setattr(search,'_score',judge)
    assert search.local_repairs(AGENT,proj,fn,40,None)[0]==0
    assert seen==['middle','end'] and ranked==[initial,middle]


def test_source_recipe_lineage_and_old_diff_are_reset_when_a_profile_changes(monkeypatch):
    import json

    from decompnet.app import repair_sequences
    net=setup(monkeypatch)
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='start'")
    search=deep_permuter.Search(net,1,20,Log(),net.function())
    search.winner_diff=([('r','old left','old right')],'legend')
    def variants(source,*args,**kwargs):
        candidate='middle' if source=='start' else 'end'
        kwargs['labels'][candidate]='first' if candidate=='middle' else 'second'
        return [candidate]
    monkeypatch.setattr(draft_repairs,'variants',variants)
    def judge(agent,proj,fn,source,best,fork):
        score=20 if source=='middle' else 0
        with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=?',(source,score))
        net._last_diffs={(AGENT.id,fn['id']):([('s','old stack left','old stack right')],'legend')}
        return score
    monkeypatch.setattr(search,'_score',judge)
    def profile(agent,proj,fn,best,fork):
        if fn['best_source']=='middle':
            cfg=net.cfg(proj);cfg['compiler']['flags']='-O1'
            with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
        return best
    monkeypatch.setattr(search,'_repair_preflight',profile)
    assert search.local_repairs(AGENT,net.project_row('game'),net.function(),40,None)[0]==0
    repair_sequences.prepare(net)
    assert not net.store.one('SELECT 1 FROM repair_sequences')


def test_room_permutation_never_claims_or_renews_and_submits_candidates(monkeypatch):
    from types import SimpleNamespace

    net = setup(monkeypatch)
    calls = []
    class Link:
        def claim(self, agent, function, strategy):
            calls.append(('claim', function))
            return function
        def call(self, route, agent, **kwargs):
            calls.append((route, kwargs))
        def attempt(self, agent, function, source):
            return {'matched': False, 'score': 10}
    monkeypatch.setattr(net, 'links', {'game': Link()})
    search = deep_permuter.Search(net, 1, 10, Log(), net.function())
    assert search._hold(AGENT, net.function())
    assert not search.room_holds
    search.check()
    assert not calls
    assert not net.store.one('SELECT 1 FROM claims')
    link=net.links['game']
    from decompnet.app import deep_room
    monkeypatch.setattr(deep_room,'best',lambda *args:calls.append(('/collab/best',{})))
    monkeypatch.setattr(deep_room,'submit',lambda *args:{'matched':False,'pending':True})
    search._accept_candidate(AGENT, net.project_row('game'), net.function(), 'draft', SimpleNamespace(bytes_match=False, score=20))
    assert calls[-1][0] == '/collab/best'
    search._accept_candidate(AGENT, net.project_row('game'), net.function(), 'draft', SimpleNamespace(bytes_match=True))
    search._let_go(AGENT, net.function())
    assert not net.store.one('SELECT 1 FROM claims')
    assert search._hold(AGENT, net.function())
    def offline(*args, **kwargs):
        raise NetworkError('host offline')
    monkeypatch.setattr(link, 'call', offline)
    search._let_go(AGENT, net.function())
    assert not net.store.one('SELECT 1 FROM claims')


def test_imported_room_search_uses_host_build_and_syncs_improvements(monkeypatch):
    net = setup(monkeypatch, imported=True)
    calls = []
    class Link:
        def call(self, route, agent, **data):
            assert route == '/collab/attempt' and data['nonexclusive']
            calls.append(('judge', data['function'], data['source']))
            return {'score': 20, 'matched': False}
        def sync(self):
            calls.append(('sync',))
    monkeypatch.setattr(net, 'links', {'game': Link()})
    search = deep_permuter.Search(net, 1, 10, Log(), net.function())
    assert search._score_work(AGENT, net.project_row('game'), net.function(), 'draft', 40, 'fork') == 20
    assert calls == [('judge', 'f', 'draft'), ('sync',)]
    assert search._compiler_preflight(net.project_row('game'), net.function(), None, None) == {'tested': 0}


def test_deep_search_does_not_rotate_background_permuter_credentials(monkeypatch):
    net = setup(monkeypatch)
    names = []
    original = net.local_agent
    def agent(name):
        names.append(name)
        return original(name)
    net.local_agent = agent
    monkeypatch.setattr(Permuter, '_score', lambda *args: 40)
    deep_permuter.run(net, 'game', 'f', 1, 1, Log())
    assert names == [f'permuter-{net.function()["id"]}']
    assert names[0] != 'permuter'


@pytest.mark.parametrize('note_offline', [False, True])
def test_room_search_publishes_one_result_note_not_lease_notes(monkeypatch, note_offline):
    net = setup(monkeypatch)
    calls = []
    class Link:
        def claim(self, agent, function, strategy):
            return function
        def call(self, route, agent, **kwargs):
            calls.append((route, kwargs))
            if route == '/collab/note' and note_offline:
                raise NetworkError('host offline')
    net.links = {'game': Link()}
    monkeypatch.setattr(Permuter, '_score', lambda *args: 40)
    deep_permuter.run(net, 'game', 'f', 1, 1, Log())
    summaries = [d['text'] for route, d in calls if route == '/collab/note']
    assert len(summaries) == 1
    payload = next(d for route, d in calls if route == '/collab/note')
    assert (payload['score_before'], payload['score_after']) == (40, 40)
    assert summaries[0].startswith('Deep permuter: score 40 → 40; 0 improvements; 1 candidates tested;')
    assert 'elapsed' in summaries[0] and 'Variant limit reached' in summaries[0]
    assert all(not d['notes'] for route, d in calls if route == '/collab/release')


def test_side_search_keeps_other_agents_lease_and_defers_compiler_changes(monkeypatch):
    net = setup(monkeypatch)
    with net.store.tx() as db:
        db.execute("INSERT INTO agents(id,name,token_hash) VALUES(2,'owner','owner-token')")
        db.execute('INSERT INTO claims VALUES(1,2,123,9999999999)')
    before = tuple(net.store.one('SELECT * FROM claims'))
    search = deep_permuter.Search(net, 1, 10, Log(), net.function())
    assert search._hold(AGENT, net.function())
    monkeypatch.setattr('decompnet.app.matching_preflight.run', lambda *args, **kwargs: pytest.fail('claimed settings changed'))
    assert search._compiler_preflight(net.project_row('game'), net.function(), None, search.deadline) == {'tested': 0}
    search._let_go(AGENT, net.function())
    assert tuple(net.store.one('SELECT * FROM claims')) == before
    monkeypatch.setattr(Permuter, '_score', lambda *args: 30)
    result = deep_permuter.run(net, 'game', 'f', 1, 1, Log())
    assert result['tested'] == 1
    assert tuple(net.store.one('SELECT * FROM claims')) == before


def test_duplicate_deep_search_is_rejected_before_runner_credentials_rotate(monkeypatch):
    import threading

    net = setup(monkeypatch)
    lock = threading.Lock()
    lock.acquire()
    net._deep_run_locks = {1: lock}
    net.local_agent = lambda *args: pytest.fail('duplicate search rotated credentials')
    try:
        with pytest.raises(NetworkError, match='already running'):
            deep_permuter.run(net, 'game', 'f', 1, 10, Log())
    finally:
        lock.release()


def test_local_phase_deadline_yields_without_consuming_global_budget(monkeypatch):
    net = setup(monkeypatch)
    search = deep_permuter.Search(net,1,100,Log())
    monkeypatch.setattr(deep_permuter.time,'monotonic',lambda: 20)
    calls = []
    monkeypatch.setattr(Permuter,'_score',lambda *a: calls.append(1) or 40)
    best,_ = search.local_repairs(AGENT,net.project_row('game'),net.function(),40,None,phase_deadline=19)
    assert best==40 and calls==[]
    assert search.local_status['reason']=='phase_budget'


@pytest.mark.parametrize('imported',[False,True])
def test_initial_local_phase_reserves_compile_allowance_for_random(monkeypatch,imported):
    net = setup(monkeypatch,imported)
    monkeypatch.setattr(draft_repairs,'variants',lambda *a,**kw: ['local'+str(i) for i in range(50)])
    seen = []
    monkeypatch.setattr(Permuter,'_score',lambda self,agent,proj,fn,source,best,fork: seen.append(source) or 40)
    batches(monkeypatch,['random'])
    result = deep_permuter.run(net,'game','f',1,10,Log())
    assert len([s for s in seen if s.startswith('local')])<=5
    assert 'random' in seen
    assert result['tested']<=10


def test_structurally_distinct_high_score_seed_can_recover_exact_match(monkeypatch):
    net = setup(monkeypatch)
    original = net.function()['best_source']
    monkeypatch.setattr(draft_repairs,'variants',lambda source,*a,**kw: {original:['typed_seed'], 'typed_seed':['exact']}.get(source,[]))
    monkeypatch.setattr(deep_permuter.stack_padding_repairs,'grow',lambda *a: [])
    net._last_diffs = {}
    def judge(self,agent,proj,fn,source,best,fork):
        net._last_diffs[agent.id,fn['id']] = ([('>','','nop')],None)
        if source=='exact':
            with net.store.tx() as db:
                db.execute("UPDATE functions SET best_source='exact',best_score=0,status='matched' WHERE id=?",(fn['id'],))
            return 0
        assert net.function()['best_source']==original
        return 440
    monkeypatch.setattr(Permuter,'_score',judge)
    search = deep_permuter.Search(net,1,10,Log())
    assert search.local_repairs(AGENT,net.project_row('game'),net.function(),80,None)==(0,'exact')


@pytest.mark.parametrize('imported',[False,True])
def test_deep_search_has_the_same_earned_complementary_sequence_limits(monkeypatch,imported):
    net=setup(monkeypatch,imported)
    original=net.function()['best_source']
    sources=[original,'stage1','stage2','stage3','stage4','stage5','exact']
    families=['narrow_call_destination','branch_scalar_lifetime','inline_call_count',
              'narrow_call_destination','typed_pointer_snapshot','embedded_call_assignment']
    rows=[[' ','addiu $sp, $sp, -0x20','addiu $sp, $sp, -0x20'],
          ['r','move $s0, $a0','move $s1, $a0'],[' ','jr $ra','jr $ra']]
    net._last_diffs={}
    def variants(source,*args,**kwargs):
        index=sources.index(source)
        if index==len(families):return []
        kwargs['labels'][sources[index+1]]=families[index]
        return [sources[index+1]]
    monkeypatch.setattr(draft_repairs,'variants',variants)
    monkeypatch.setattr(deep_permuter.Search,'_repair_preflight',lambda self,agent,proj,fn,best,fork:best)
    tested=[]
    def judge(self,agent,proj,fn,source,best,fork):
        tested.append(source);index=sources.index(source);score=40-index*6 if index<6 else 0
        self._trial_output=source
        self._trial_output_granularity='function'
        self._trial_outcome='scored'
        net._last_diffs[agent.id,fn['id']]=(rows,'')
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_score=?,best_source=?,status=? WHERE id=?',
                       (score,source,'matched' if score==0 else 'open',fn['id']))
        return score
    monkeypatch.setattr(Permuter,'_score',judge)
    search=deep_permuter.Search(net,1,16,Log())
    best,_=search.local_repairs(AGENT,net.project_row('game'),net.function(),40,'fork' if imported else None)
    assert best==0 and tested==sources[1:]
    assert search.local_status['sequence_evaluations']==2
    assert deep_permuter.repair_sequences.MAX_STEPS==4


@pytest.mark.parametrize('imported', [False, True])
def test_deep_search_skips_empty_padding_before_judge_but_keeps_nonempty_blocks(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    monkeypatch.setattr(draft_repairs, 'variants', lambda *a, **kw: [])
    parent = net.function()['best_source']
    padded = parent.replace('return', 'do {} while (0); return')
    useful = parent.replace('return b * 2;', 'do { b += a; } while(0); return b * 2;')
    seen = []
    def judge(self, agent, proj, fn, source, best, fork):
        seen.append(source)
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_score=0,best_source=?,status='matched' WHERE id=?", (source,fn['id']))
        return 0
    monkeypatch.setattr(Permuter, '_score', judge)
    batches(monkeypatch, [padded, useful])
    result = deep_permuter.run(net, 'game', 'f', 1, 10, Log())
    assert result['matched'] and seen == [useful]
    assert net.function()['best_source'] == useful
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_a_new_winner_revisits_one_pass_scaffolding_before_broad_search(monkeypatch):
    net = setup(monkeypatch)
    original = net.function()['best_source']
    improved = 'int f(int a){ int delta; do { delta=a & 1; } while(0); return delta; }'
    generated = []
    def generate(source,*args,**kwargs):
        generated.append(source)
        return ['improved'] if source==original else []
    monkeypatch.setattr(draft_repairs, 'variants', generate)
    visited = []
    def judge(self, agent, proj, fn, source, best, fork):
        visited.append(source)
        actual = improved if source=='improved' else source
        score = 20 if source=='improved' else 0
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_source=?,best_score=?,status=? WHERE id=?',
                       (actual,score,'matched' if score==0 else 'open',fn['id']))
        net._last_diffs = {(agent.id,fn['id']):([('o','subu $s2, $s2, $s0',''),('o','','subu $s2, $s2, $s0')],'')}
        return score
    monkeypatch.setattr(Permuter, '_score', judge)
    monkeypatch.setattr(Permuter, '_permute', lambda *args:pytest.fail('cleanup should run first'))
    result = deep_permuter.run(net,'game','f',1,10,Log())
    assert result['matched']
    assert len(visited)==2 and 'do {' not in visited[1]
    assert generated == [original]  # cleanup precedes broad proposal generation


def test_failed_cleanup_resumes_broad_search_without_repeating_inverse(monkeypatch):
    net = setup(monkeypatch)
    source = 'int f(int a){ int delta; do { delta=a & 1; } while(0); return delta; }'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', (source,))
    visited, generated = [], []
    def generate(source,*args,**kwargs):
        generated.append(source)
        return ['exact']
    monkeypatch.setattr(draft_repairs, 'variants', generate)
    def judge(self, agent, proj, fn, source, best, fork):
        visited.append(source)
        if source == 'exact':
            with net.store.tx() as db:
                db.execute("UPDATE functions SET best_score=0,best_source='exact',status='matched'")
            return 0
        return 40
    monkeypatch.setattr(Permuter, '_score', judge)
    result = deep_permuter.run(net, 'game', 'f', 1, 10, Log())
    assert result['matched'] and len(visited)==2 and visited[-1]=='exact'
    assert len(generated)==1
