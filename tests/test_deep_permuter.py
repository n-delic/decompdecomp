import json
import time

import pytest

from decompnet.app import deep_permuter, draft_repairs, permuter_local
from decompnet.app.permute import Permuter
from decompnet.network import NetworkError
from decompnet.repair_feedback import capture_suppressed
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
    # Keep synthetic judge tests in process so their patched proposal generators apply.
    from decompnet.app import deep_proposals
    def generate(self, args, kwargs, check):
        check()
        labels = {}
        return draft_repairs.variants(*args, labels=labels, **kwargs), labels, 0
    monkeypatch.setattr(deep_proposals.Pool, 'generate', generate)
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
@pytest.mark.parametrize('minutes, variants, options', [(30, 10, {}), (240, 1_200_000, {'expanded_budget': True})])
def test_one_function_deep_search_uses_normal_judge_and_keeps_improvements(monkeypatch, imported, minutes, variants, options):
    net = setup(monkeypatch, imported)
    seen = []
    def judge(self, agent, proj, fn, source, best, fork):
        assert capture_suppressed()
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
    result = deep_permuter.run(net, 'game', 'f', minutes, variants, log, **options)
    assert not capture_suppressed()
    assert result['matched'] and result['tested'] == 2 and result['skipped'] == 1
    assert seen == [('local','fork' if imported else None), ('random','fork' if imported else None)]
    assert net.function()['status'] == 'matched'
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0
    assert log.updates[-1]['best_score'] == 0
    # the drawer's run chart: the starting score, then the best each time it improved, by candidates tested
    assert result['history'][0] == {'tested': 0, 'score': 40} and result['history'][-1]['score'] == 0
    assert [point['score'] for point in result['history']] == sorted((p['score'] for p in result['history']), reverse=True)
    assert result['elapsed'] >= 0 and log.updates[-1]['history'] == result['history']


def test_the_run_chart_keeps_its_first_and_latest_points():
    search = deep_permuter.Search.__new__(deep_permuter.Search)
    search.history, search.tested = [{'tested': 0, 'score': 1000}], 0
    for score in range(999, 699, -1):
        search.tested += 1
        search._chart(score)
    assert len(search.history) <= 200
    assert search.history[0] == {'tested': 0, 'score': 1000} and search.history[-1] == {'tested': 300, 'score': 700}


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
    assert metrics.pop('elapsed') >= 0
    # the run chart: the measured baseline, then each verified strict improvement (not the equal or the second baseline)
    assert metrics.pop('history') == [{'tested':0,'score':50},{'tested':2,'score':30},{'tested':5,'score':20},
                                      {'tested':7,'score':0}]
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
    monkeypatch.setattr(deep_permuter, 'EXTEND_MAX', 0)  # the hard limit, not the extension
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
    assert winner['provenance']['phase'] == 'random rewrites'
    measured = reopened.measurements(proj['id'])[0]
    assert (measured['phase'], measured['attempted']) == ('random rewrites', 3)

    # A stored row written before the phase fix still belongs to the random phase.
    reopened.record_recipes(fn['id'], {}, [], {('perm_cast', 'compiler preflight'): (1, 0, 0, 1, 0, 0, 0, 18, 0)}, project=proj['id'])
    assert len(reopened.measurements(proj['id'])) == 1
    assert reopened.measurements(proj['id'])[0]['attempted'] == 4
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
    monkeypatch.setattr(deep_permuter, 'EXTEND_MAX', 0)
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
def test_random_probe_returns_unused_allowance_to_local_repairs(monkeypatch,imported):
    net = setup(monkeypatch,imported)
    monkeypatch.setattr(draft_repairs,'variants',lambda *a,**kw: ['local'+str(i) for i in range(50)])
    seen = []
    monkeypatch.setattr(Permuter,'_score',lambda self,agent,proj,fn,source,best,fork: seen.append(source) or 40)
    batches(monkeypatch,['random'])
    result = deep_permuter.run(net,'game','f',1,10,Log())
    assert seen.index('random') == 8
    assert seen[-1] == 'local8'
    assert result['tested'] == 10


def test_random_probe_extends_only_when_a_candidate_improves(monkeypatch):
    net = setup(monkeypatch)
    monkeypatch.setattr(deep_permuter, 'RANDOM_TRIAL_WINDOW', 2)
    seen = []

    def judge(self, agent, proj, fn, source, best, fork):
        seen.append(source)
        score = 30 if source == 'winner' else best
        if score < best:
            with net.store.tx() as db:
                db.execute('UPDATE functions SET best_score=?,best_source=? WHERE id=?', (score, source, fn['id']))
        return score

    monkeypatch.setattr(Permuter, '_score', judge)
    batches(monkeypatch, ['miss1', 'winner', 'obsolete'], ['miss2', 'miss3', 'too late'])
    result = deep_permuter.run(net, 'game', 'f', 1, 20, Log())
    assert [source for source in seen if source != 'local'] == ['miss1', 'winner', 'miss2', 'miss3']
    assert result['best_score'] == 30


@pytest.mark.parametrize('fails', [False, True])
def test_compiler_preflight_restores_callers_phase(monkeypatch, fails):
    net = setup(monkeypatch)
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    search.phase = 'random rewrites'

    def preflight(*args, **kwargs):
        assert search.phase == 'compiler preflight'
        if fails:
            raise NetworkError('preflight failed')
        return {'tested': 0}

    monkeypatch.setattr('decompnet.app.matching_preflight.run', preflight)
    if fails:
        with pytest.raises(NetworkError, match='preflight failed'):
            search._compiler_preflight(proj, fn, None, search.deadline)
    else:
        search._compiler_preflight(proj, fn, None, search.deadline)
    assert search.phase == 'random rewrites'


def test_evidence_prioritises_targeted_repairs_and_limits_generic_ones():
    from decompnet.app import repair_ranking

    rows = [('|', 'lbu $v0, 0($a0)', 'lw $v0, 0($a0)'),
            ('r', 'jal foo', 'jal bar')]
    assert list(repair_ranking.evidence_priority(rows)) == ['symbol_access_width', 'float_argument_bits', 'm2c_refinement']
    assert repair_ranking.weak_limits(rows, 'mips') == {
        'global_scalar_type': 2, 'local_storage': 2, 'local_declaration_order': 2}
    assert repair_ranking.weak_limits([('s', 'sw $v0, 4($sp)', 'sw $v0, 8($sp)')], 'mips')['local_storage'] == 8


def test_local_queue_caps_generic_storage_trials_without_supporting_evidence(monkeypatch):
    net = setup(monkeypatch)
    candidates = [f'generic{i}' for i in range(8)]

    def proposals(*args, labels=None, **kwargs):
        labels.update(dict.fromkeys(candidates, 'local_storage'))
        return candidates

    seen = []
    monkeypatch.setattr(draft_repairs, 'variants', proposals)
    monkeypatch.setattr(Permuter, '_score', lambda self, agent, proj, fn, source, best, fork: seen.append(source) or best)
    fn = net.function()
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    search.local_repairs(AGENT, net.project_row('game'), fn, fn['best_score'], None)
    assert seen == candidates[:2]


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
    # The local pass also judges the library's own rewrites of the saved best before the cleanup (decomprepair
    # 0.5.583's `mask_parameter_narrow` offers `u8 a` for this masked parameter), so the visits are that, the
    # cleanup and the broad search's match. What this test pins: the cleanup's inverse is not judged again and the
    # broad search still runs once, to the match.
    assert result['matched'] and visited[-1]=='exact'
    assert len(visited)==len(set(visited))
    assert source not in visited
    assert len(generated)==1


@pytest.mark.parametrize('imported', [False, True])
def test_exact_local_match_skips_followup_feedback(monkeypatch, imported):
    from decompnet.app import deep_repair_feedback
    net = setup(monkeypatch, imported)
    monkeypatch.setattr(draft_repairs, 'variants', lambda *args, **kwargs: ['exact'])
    monkeypatch.setattr(deep_permuter.stack_padding_repairs, 'grow', lambda *args: [])

    def judge(self, agent, proj, fn, source, best, fork):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_score=0,best_source=?,status='matched' WHERE id=?", (source, fn['id']))
        return 0

    def unexpected(*args, **kwargs):
        pytest.fail('Exact match must stop before generating feedback or follow-ups')

    monkeypatch.setattr(Permuter, '_score', judge)
    monkeypatch.setattr(deep_repair_feedback.Feedback, 'record', unexpected)
    monkeypatch.setattr(deep_repair_feedback.Feedback, 'pairs', unexpected)
    search = deep_permuter.Search(net, 1, 10, Log())
    assert search.local_repairs(AGENT, net.project_row('game'), net.function(), 40,
                                'fork' if imported else None) == (0, 'exact')
    assert search.local_status['reason'] == 'matched'


@pytest.mark.parametrize('imported',[False,True])
def test_predicate_lane_retunes_improvement_before_any_broad_generation(monkeypatch,imported):
    from decompnet.app import promoted_entry_repairs
    from decomprepair import bit_predicate_repairs
    from tests.test_bit_predicate_repairs import SOURCE

    net=setup(monkeypatch,imported)
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=55 WHERE id=1',(SOURCE,))
    net._context=lambda *a:{'header':'','version':1}
    net.forks.permuter_header=lambda *a:''
    rows=[(' ','addiu $sp, $sp, -0x20','addiu $sp, $sp, -0x20'),
          ('r','move $s0, $a0','move $s1, $a0'),(' ','jr $ra','jr $ra')]
    net._last_diffs={(AGENT.id,1):(rows,'')}
    monkeypatch.setattr(promoted_entry_repairs,'scope',lambda *a:dict(language='c',int_bits=32))
    monkeypatch.setattr(deep_permuter.Search,'_repair_preflight',lambda self,agent,proj,fn,best,fork:best)
    monkeypatch.setattr(draft_repairs,'variants',lambda *a,**k:pytest.fail('Focused predicate page must precede broad generation'))
    tested=[]
    def judge(self,agent,proj,fn,source,best,fork):
        span=bit_predicate_repairs.find(source,'__repair_bit_predicate')
        depth=source[span[0]:span[1]].count('do{');tested.append(depth)
        score={0:55,1:55,3:20,5:0}[depth]
        net._last_diffs[agent.id,fn['id']]=(rows,'')
        if score<best:
            with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=?,status=? WHERE id=1',
                                                 (source,score,'matched'if score==0 else'open'))
        return score
    monkeypatch.setattr(Permuter,'_score',judge)
    search=deep_permuter.Search(net,1,8,Log(),net.function())
    best,_=search.local_repairs(AGENT,net.project_row('game'),net.function(),55,'fork'if imported else None)
    assert best==0 and tested==[0,1,3,5] and search.tested==4
    assert search.allocation_diagnostic_compiles==0


@pytest.mark.parametrize('imported', [False, True])
@pytest.mark.parametrize('maximum', [False, True])
def test_both_throughput_modes_generate_outside_the_search_lane(monkeypatch, imported, maximum):
    from types import SimpleNamespace

    from decompnet.app import deep_throughput

    net = setup(monkeypatch, imported)
    calls = []
    class Proposals:
        def generate(self, args, kwargs, check):
            check();calls.append('ordered')
            return ['worker candidate'], {'worker candidate':'local_repair'}, .1
        def stream(self, args, kwargs, check):
            check();calls.append('streamed')
            yield ['worker candidate'], {'worker candidate':'local_repair'}, .1
    pool = SimpleNamespace(maximum=maximum, metrics=deep_throughput.Metrics(), proposal_pool=lambda:Proposals())
    log = Log();log.compile_pool = pool
    search = deep_permuter.Search(net, 1, 10, log)
    search.compile_pool = pool
    monkeypatch.setattr(draft_repairs, 'variants', lambda *a, **k:pytest.fail('Generation must leave the search lane'))
    def judge(self, agent, proj, fn, source, best, fork):
        assert source == 'worker candidate'
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source=?,best_score=0,status='matched' WHERE id=?", (source, fn['id']))
        return 0
    monkeypatch.setattr(Permuter, '_score', judge)
    assert search.local_repairs(AGENT, net.project_row('game'), net.function(), 40,
                                'fork' if imported else None) == (0, 'worker candidate')
    assert calls == ['streamed' if maximum else 'ordered']


def test_combined_store_reaches_judge_with_one_trial(monkeypatch):
    net = setup(monkeypatch)
    source = """void f(void) {
    s32 x;
    s32 *p;
    s32 *out;
    u32 bits;
    s32 base;
    s32 half;
    bits = x * 8;
    base = *p;
    half = (s32) (bits + (bits >> 31)) >> 1;
    *out = (s32) (base + half);
}"""
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', (source,))
    seen = []
    def judge(self, agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        return best
    monkeypatch.setattr(Permuter, '_score', judge)
    deep_permuter.run(net, 'game', 'f', 30, 1, Log())
    assert len(seen) == 1
    assert '*out = *p + ((s32)(x * 8) / 2);' in seen[0]


def test_deep_local_generator_includes_targeted_mips_repairs():
    from decompnet.app import draft_repairs

    source = """void f(int flag) {
    if (flag) { state = 0; goto clean; }
    if (flag == 2) { send(91, 2); }
    else { send(91, 3); }
clean:
    pending = 0;
}
"""
    asm = 'jal send\naddiu $a0, $zero, 64\njal send\naddiu $a0, $zero, 64\n'
    labels = {}
    candidates = draft_repairs.variants(source, 'f', 'mips', asm, labels=labels)
    assert any(labels[text] == 'target_call_constant' and text.count('send(64,') == 2
               for text in candidates)
    assert any(labels[text] == 'out_of_line_guard_failure' and 'decomp_failure:' in text
               for text in candidates)


def test_deep_local_generator_prioritizes_a_proven_loop_sentinel():
    from decompnet.app import draft_repairs, repair_ranking

    source = """int f(unsigned short *p) {
    if (*p != 0xFFFF) {
        do { p++; } while (*p != 0xFFFF);
    }
    return *p;
}
"""
    asm = """ori $v0, $zero, 0xffff
beq $a1, $v0, .Lexit
nop
ori $a2, $zero, 0xffff
.Lloop:
lhu $a1, 0($a0)
bne $a1, $a2, .Lloop
nop
.Lexit:
jr $ra
nop
"""
    labels = {}
    candidates = draft_repairs.variants(source, 'f', 'mips', asm, labels=labels)
    winner = next(text for text in candidates if labels[text] == 'loop_sentinel_placement')
    assert 'while (*p != repair_sentinel)' in winner
    assert repair_ranking.guarded_first([source, winner], labels)[0] == winner


def test_linked_case_labels_are_the_first_local_candidate(monkeypatch):
    from decompnet.app import agent_tokens

    net = setup(monkeypatch)
    source = 'int f(int k) { switch (k) { case 1: return 3; case 2: return 7; } return 0; }'
    winner = 'int f(int k) { switch (k) { case 2: return 3; case 1: return 7; } return 0; }'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=1', (source,))

    def measured(search, agent, proj, fn, state, fork):
        search.winner_source = source
        search.winner_scope = agent_tokens.target_scope(net,dict(fn),'')
        search.winner_diff = ([(' ', 'jr $v0', 'jr $v0')], '')
        search.winner_jump_tables = [{'table':100,'case':1,'target':20,'candidate':10},
                                     {'table':100,'case':2,'target':10,'candidate':20}]
        return 1, 0

    seen = []
    def judge(search, agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        assert best == 1 and candidate == winner
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source=?,best_score=0,status='matched'", (candidate,))
        return 0

    monkeypatch.setattr(deep_permuter.Search, '_baseline', measured)
    monkeypatch.setattr(Permuter, '_score', judge)
    result = deep_permuter.run(net, 'game', 'f', 30, 1, Log())
    assert result['matched'] and result['tested'] == 1
    assert seen == [winner]


def test_measured_selector_reaches_judge_before_broad_generation(monkeypatch):
    from decompnet.app import agent_tokens

    net = setup(monkeypatch)
    source = 'int f(int n) { int mode = 0; n = helper(n); if (n) mode = 1; return mode; }'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', (source,))
    rows = [('>', '', 'sw $s0, 0x18($sp)'), ('>', '', 'move $s0, $zero'),
            (' ', 'jal helper', 'jal helper'), (' ', 'nop', 'nop'), ('<', 'move $v1, $zero', '')]

    def measured(search, agent, proj, fn, state, fork):
        search.winner_source = source
        search.winner_scope = agent_tokens.target_scope(net,dict(fn),'')
        search.winner_diff = (rows, '')
        return 40, 0

    seen = []
    def judge(search, agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        assert 'n = helper(n); mode = 0;' in candidate
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source=?,best_score=0,status='matched'", (candidate,))
        return 0

    monkeypatch.setattr(deep_permuter.Search, '_baseline', measured)
    monkeypatch.setattr(Permuter, '_score', judge)
    result = deep_permuter.run(net, 'game', 'f', 30, 1, Log())
    assert result['matched'] and len(seen)==1


def test_measured_float_lifetime_reaches_the_normal_judge_first(monkeypatch):
    from decompnet.app import agent_tokens

    net = setup(monkeypatch)
    source = 'void f(void) { float n,d,q; n=read(); d=read(); q=n/d; use(q*q); }'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', (source,))
    rows = [('r','div.s $f2, $f2, $f4','div.s $f6, $f2, $f4'),
            ('r','mul.s $f2, $f2, $f2','mul.s $f0, $f6, $f6')]

    def measured(search, agent, proj, fn, state, fork):
        search.winner_source = source
        search.winner_scope = agent_tokens.target_scope(net,dict(fn),'')
        search.winner_diff = (rows, '')
        return 40, 0

    def judge(search, agent, proj, fn, candidate, best, fork):
        assert 'n /= d; n *= n; use(n);' in candidate
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source=?,best_score=0,status='matched'", (candidate,))
        return 0

    monkeypatch.setattr(deep_permuter.Search, '_baseline', measured)
    monkeypatch.setattr(Permuter, '_score', judge)
    result = deep_permuter.run(net, 'game', 'f', 30, 1, Log())
    assert result['matched'] and result['tested']==1


def test_volatile_postfix_reaches_judge_before_broad_proposals(monkeypatch):
    from decompnet.app import agent_tokens

    net = setup(monkeypatch)
    source = 'extern volatile u8 count; void f(void) { count += 1; consume(); }'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', (source,))
    rows = [
        (' ', 'lbu $v0, %lo(count)($v0)', 'lbu $v0, %lo(count)($v0)'),
        (' ', 'addiu $v0, $v0, 1', 'addiu $v0, $v0, 1'),
        (' ', 'sb $v0, %lo(count)($at)', 'sb $v0, %lo(count)($at)'),
        (' ', 'jal consume', 'jal consume'),
        ('|', 'lbu $v0, %lo(count)($v0)', 'nop'),
    ]

    def measured(search, agent, proj, fn, state, fork):
        search.winner_source = source
        search.winner_scope = agent_tokens.target_scope(net,dict(fn),'')
        search.winner_diff = (rows, '')
        return 40, 0

    def judge(search, agent, proj, fn, candidate, best, fork):
        assert candidate==source.replace('count += 1;', 'count++;')
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source=?,best_score=0,status='matched'", (candidate,))
        return 0

    monkeypatch.setattr(deep_permuter.Search, '_baseline', measured)
    monkeypatch.setattr(Permuter, '_score', judge)
    result = deep_permuter.run(net, 'game', 'f', 30, 1, Log())
    assert result['matched'] and result['tested']==1


def test_targeted_register_pass_compiles_bitwise_and_float_lifetime_candidates(monkeypatch):
    net = setup(monkeypatch)
    log = Log()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=5,best_source=? WHERE name='f'",
                   ('unsigned f(unsigned x) { unsigned a=x; unsigned b=7; a &= b; return a; }',))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 8, log, fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        return best

    monkeypatch.setattr(search, '_score', score)
    search.winner_diff = ([('r', 'and $v0, $v0, $v1', 'and $v0, $v1, $v0')], '')
    assert search.operand_order_pass(AGENT, proj, fn, 5, None) == 5
    assert len(seen) == 2 and any('a = b & a' in candidate for candidate in seen)

    # Give the independent floating fixture its own compile allowance.
    search.tested = 0
    seen.clear()
    field = 'M2C_FIELD(obj, f32 *, 4)'
    draft = f'void f(void *obj) {{ {field} = (compute({field}) * 0.5f) + {field}; }}'
    search.winner_diff = ([('r', 'add.s $f2, $f2, $f0', 'add.s $f0, $f0, $f2')], '')
    assert search.operand_order_pass(AGENT, proj, dict(fn, best_source=draft), 5, None) == 5
    assert any('float repair_float_increment' in candidate for candidate in seen)


def test_loop_vector_shape_pass_tries_interleaving_first_for_scheduling_diffs(monkeypatch):
    from decompnet.app import generation_calls

    net = setup(monkeypatch)
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    left, right = 'addiu $v0, $v0, 1', 'addiu $v1, $v1, 1'
    search.winner_diff = ([('o', left, right), ('o', right, left)], '')
    monkeypatch.setattr(generation_calls, 'library', lambda calls: [
        [('loop_vector_shapes', 'loop'), ('statement_interleave', 'interleaved')]])
    seen = []
    monkeypatch.setattr(search, '_score', lambda agent, project, function, source, best, fork: seen.append(source) or 0)
    assert search.loop_vector_shape_pass(AGENT, proj, fn, 40, None) == 0
    assert seen == ['interleaved']


def test_loop_vector_shape_pass_composes_shapes_that_only_help_together(monkeypatch):
    net = setup(monkeypatch)
    source = ('void f(Obj *obj, Bone *bones) {\n    Bone *bone;\n    int k;\n'
              '    for (k = 0, bone = bones; bone->idx >= 0; k++, bone++) {\n        obj->out[k] = bone->a;\n    }\n}\n')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=50,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        # neither shape alone helps; the pair is exact
        return 0 if 'const Bone *bone' in candidate and 'bones[k]' in candidate else 50

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source), 50, None) == 0
    assert any('const Bone *bone' in c and 'bones[k]' in c for c in seen) and len(seen) <= 15


def test_loop_vector_shape_pass_reaches_the_scheduling_boundary_that_matched_a_display_list_function(monkeypatch):
    """Every source order stayed at 60; splitting the tail with a do { } while (0), its call's global read first, was 0."""
    net = setup(monkeypatch)
    source = ('extern int D_8010A4D8;\nextern void *cursor;\nextern void *list;\nint g();\n'
              'void f(int arg0) {\n    void *t;\n    g(arg0, 2);\n    ((S *)list)->a = 1;\n    ((S *)cursor)->a = 2;\n'
              '    list += 8;\n    t = cursor + 8;\n    cursor = t;\n    g(arg0, D_8010A4D8, t);\n}\n')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=60,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        # wrapping alone or hoisting alone stays at 60; the pair is exact
        return 0 if 'do {' in candidate and 'repair_scope_1 = D_8010A4D8;' in candidate else 60

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source, name='f'), 60, None) == 0
    assert any('do {' in c and 'repair_scope_1' not in c for c in seen) or len(seen) < 64


def test_loop_vector_shape_pass_offers_comparison_scope_with_volatile_load(monkeypatch):
    net = setup(monkeypatch)
    source = ("int f(void) { volatile int *p; int zero, count, present; "
              "do { zero = 0; count = *p; present = (short) (zero < count); } while (0); return present; }")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=350,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        return 85 if 'do { present = (short) (zero < count); } while (0);' in candidate else 350

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source, name='f'), 350, None) == 85
    assert any('do { present = (short) (zero < count); } while (0);' in c for c in seen)


def test_loop_vector_shape_pass_offers_known_zero_conversion(monkeypatch):
    net = setup(monkeypatch)
    source = ("int f(void) { volatile int *p;\nlong value;\nint zero;\nint present;\n"
              "do { value = *p; zero = 0; do { present = (short) (zero < value); } while (0); } while (0); "
              "return present; }")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=85,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        return 75 if '(unsigned short) value ^ ((unsigned int) value & 0xFFFF)' in candidate else 85

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source, name='f'), 85, None) == 75
    assert any('(unsigned short) value ^ ((unsigned int) value & 0xFFFF)' in c for c in seen)


def test_loop_vector_shape_pass_interleaves_increment_and_reset(monkeypatch):
    net = setup(monkeypatch)
    source = ("int f(void) { volatile int *p;\nint counter;\nint found;\n"
              "next:\n    counter++;\n\n    found = 0;\n    if (*p) goto next;\n"
              "return found + counter; }")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=75,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        return 25 if 'found = 0;\n\n    counter++;' in candidate else 75

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source, name='f'), 75, None) == 25
    assert any('found = 0;\n\n    counter++;' in c for c in seen)


def test_loop_vector_shape_pass_offers_pointer_zero_identity(monkeypatch):
    net = setup(monkeypatch)
    source = ("int f(void) { volatile int *p;\nlong value;\nint zero;\nint present;\n"
              "do { value = *p; zero = (unsigned short) value ^ ((unsigned int) value & 0xFFFF); "
              "do { present = (short) (zero < value); } while (0); } while (0); return present; }")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=25,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        return 0 if '(unsigned short) (unsigned int) p ^ ((unsigned int) p & 0xFFFF)' in candidate else 25

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source, name='f'), 25, None) == 0
    assert any('(unsigned short) (unsigned int) p ^ ((unsigned int) p & 0xFFFF)' in c for c in seen)


def test_loop_vector_shape_pass_reaches_the_guard_initializer_scope_that_matched_a_timer_loop(monkeypatch):
    """A guard's `i = 0;` hoisted before it in a one-shot scope, the copy before the guard sunk: 10 -> 0 in one child."""
    net = setup(monkeypatch)
    source = ('extern int D_800C078C;\nvoid f(int arg0) {\n  int slot;\n  unsigned count;\n  unsigned i;\n  int saved;\n'
              '  slot = arg0 << 4;\n  count = *((volatile unsigned *) (slot + ((unsigned) (&D_800C078C))));\n'
              '  saved = slot;\n  if (count != 0)\n  {\n    i = 0;\n    do\n    {\n      i += 1;\n    }\n'
              '    while (i < count);\n  }\n}\n')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=10,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        # the bare hoist alone is worse; the hoist with the copy sunk into the guard is exact
        return 0 if 'while (0);' in candidate and 'saved = slot;\n    do' in candidate.replace('  saved', 'saved') else 60

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source, name='f'), 10, None) == 0


def test_loop_vector_shape_pass_reaches_the_chain_load_scope_that_matched_a_menu_function(monkeypatch):
    """Identical arms written once plus the chain load behind the branch operand's load: 210 -> 0 in one child."""
    net = setup(monkeypatch)
    source = ('extern void *gState;\nextern int sink(int);\nvoid f(void) {\n  void *entry;\n  int value;\n  void *next;\n'
              '  entry = *((void **) (((char *) gState) + 0x18));\n  if (entry)\n  {\n'
              '    value = *(*((int **) (((char *) entry) + 0x10)));\n    next = entry + 0x28;\n  }\n  else\n  {\n'
              '    value = *(*((int **) (((char *) entry) + 0x10)));\n    next = entry + 0x28;\n  }\n'
              '  if (((*((unsigned char *) (((char *) gState) + 0x2D))) != 0) && (sink(1) == 0))\n  {\n    sink(value);\n  }\n'
              '  sink(next);\n}\n')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=210,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        # the arms alone are worse, the scope on the arms alone is no better; collapsed and scoped is exact
        return 0 if 'do { value = *chain_ptr; } while (0);' in candidate and 'else' not in candidate else 220

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source, name='f'), 210, None) == 0
    assert any('guard_load' in c for c in seen)


def test_loop_vector_shape_pass_reaches_the_halfword_countdown_that_matched_a_menu_function(monkeypatch):
    """The unsigned entry test, the u16 counter and the compound field decrement: 310 -> 0 in one child."""
    net = setup(monkeypatch)
    source = ('extern void *gMenu;\nextern void *gState;\nextern int sink(void *, int);\nvoid f(void) {\n  int i;\n  short t;\n'
              '  unsigned short temp;\n  int next;\n  void *state;\n  void *menu;\n'
              '  t = *((short *) (((char *) gMenu) + 0x7C));\n  if (t == 0)\n  {\n    i = 0;\n    state = gState;\n'
              '    if ((((unsigned) t) + 1) <= ((unsigned char) (*((unsigned char *) (((char *) state) + 5)))))\n    {\n'
              '      do\n      {\n        sink(state, i & 0xFFFF);\n        i += 1;\n      }\n'
              '      while (((unsigned) (i & 0xFFFF)) < ((unsigned char) (*((unsigned char *) (((char *) state) + 5)))));\n    }\n  }\n'
              '  menu = gMenu;\n  temp = *((short *) (((char *) menu) + 0x7C));\n  if (((short) temp) > 0)\n  {\n'
              '    next = temp - 1;\n    *((short *) (((char *) menu) + 0x7C)) = next;\n    if ((next << 0x10) == 0)\n    {\n'
              '      sink(state, 1);\n    }\n  }\n}\n')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=310,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        search.tested += 1
        # each edit alone is no better (314, 310, 455); the three together are exact
        whole = ('(unsigned) t) <' in candidate and 'u16 i;' in candidate and '-= 1;' in candidate)
        return 0 if whole else 314

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source, name='f'), 310, None) == 0
    assert len(seen) >= 3


def test_a_stub_baseline_is_named_before_the_tuning_passes(monkeypatch):
    net = setup(monkeypatch)
    fn = net.function()
    search = deep_permuter.Search(net, 1, 8, Log(), fn)
    assert search.stub_note() == ''
    search.winner_diff = ([(' ', 'nop', 'nop')] * 40 + [('<', 'sw $v0, 0($a0)', '')] * 200, '')
    assert 'it is a stub' in search.stub_note() and '40 of the target' in search.stub_note()
    search.winner_diff = ([('r', 'addu $v0, $a0, $a1', 'addu $v0, $a1, $a0')] * 240, '')
    assert search.stub_note() == ''


# The ROM's entry point: N64 boot code that clears BSS with `addi` and sets $sp in the delay slot of its jump to main.
BOOT_STUB = bytes.fromhex('3C08800D25087050' '3C090004252906F0' 'AD000000AD000004' '21080008' '2129FFF8' '1520FFFB' '00000000'
                          '3C0A8000254A0460' '3C1D8010' '01400008' '27BD2CA0')


def _boot_stub_net(monkeypatch, code):
    from decompnet.app.twins import Twins
    from decompnet.consoles import get as console
    net = setup(monkeypatch)
    net.console = lambda proj: console('n64')
    net.is_rom = lambda proj: True
    net.twins = Twins(net)
    net.forks = None
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=?, size=?, meta=NULL", (code, len(code)))
    return net


def test_deep_search_sets_aside_hand_written_assembly_instead_of_searching_it(monkeypatch):
    net = _boot_stub_net(monkeypatch, BOOT_STUB)
    monkeypatch.setattr(deep_permuter, '_run', lambda *a, **k: pytest.fail('searched hand-written assembly'))
    log = Log()
    result = deep_permuter.run(net, 'game', 'f', 30, 10, log)
    assert not result['matched'] and result['tested'] == 0 and 'addi' in result['reason']
    row = net.function()
    assert row['status'] == 'asm' and 'addi' in json.loads(row['meta'])['asm_code']


def test_deep_search_leaves_a_function_the_user_gave_back_to_the_agents(monkeypatch):
    net = _boot_stub_net(monkeypatch, BOOT_STUB)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET meta='{\"reopened\": true}'")
    assert net.twins.set_aside_function_asm(net.project_row('game'), net.function()) is None
    assert net.function()['status'] == 'open'


def test_neutral_plateau_pass_reaches_a_step_that_pays_off_after_same_score_steps(monkeypatch):
    """A voice-list walk: its call-site constants written again kept 623, and only then did the staged product give
    0; its loop written with `goto` scored 280 at once and led no further than 200."""
    from decompnet.app import draft_repairs

    net = setup(monkeypatch)
    source = 'void f(int a) {\n    g(a);\n}\n'
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=623,best_source=? WHERE name='f'", (source,))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)

    def variants(parent, name, arch, asm='', flags=(), labels=None, **_):
        found = []
        for family, mark in (('goto', '/*g*/'), ('noise', '/*n*/'), ('inline', '/*i*/'), ('product', '/*p*/')):
            if mark not in parent:
                child = parent + mark
                labels[child] = family
                found.append(child)
        return found

    def score(agent, proj, fn, candidate, best, fork):
        search.tested += 1
        if '/*g*/' in candidate:
            return 200 if candidate.count('/*') > 1 else 280  # quick, then nowhere
        if '/*p*/' in candidate:
            return 0 if '/*i*/' in candidate else 700  # the payoff needs the neutral step before it
        return 900 if '/*n*/' in candidate else 623

    monkeypatch.setattr(draft_repairs, 'variants', variants)
    monkeypatch.setattr(search, '_score', score)
    assert search.neutral_plateau_pass(AGENT, proj, dict(fn, best_source=source), 623, None) == 0
    assert search.tested <= 20


def test_neutral_frontier_spreads_over_families_first():
    children = [('a', 1), ('a', 2), ('b', 3), ('a', 4), ('c', 5)]
    assert deep_permuter.Search.neutral_frontier(children, 4) == [('a', 1), ('b', 3), ('c', 5), ('a', 2)]


def test_switch_temporary_restore_gets_an_early_local_trial(monkeypatch):
    from decompnet.app import generation_calls
    from decomprepair.reused_temp_split_repairs import RESTORE_FAMILY, variants

    net = setup(monkeypatch)
    fn, proj = net.function(), net.project_row('game')
    source = '''int f(int which) {
    int value;
    int value_b1;
    switch (which) {
    case 0:
        value = 0;
        break;
    default:
        value_b1 = 1;
        break;
    }
    return value;
}'''
    monkeypatch.setattr(generation_calls, 'library', lambda calls: [
        variants(*arguments) if name == 'decomprepair.reused_temp_split_repairs.variants'
        else [('other', 'another draft')]
        for name, arguments, *_ in calls
    ])
    search = deep_permuter.Search(net, 1, 10, Log(), fn)
    proposals, _ = search._local_proposals(source, proj, fn, [])
    family, candidate = proposals[0]
    assert family == RESTORE_FAMILY
    assert 'value = 1;' in candidate
    assert 'value_b1' not in candidate


@pytest.mark.parametrize('header,expected', [('', 35), ('#define setup() use(counter)', 155)])
def test_counter_reset_pass_uses_header_when_moving_across_setup(monkeypatch, header, expected):
    net = setup(monkeypatch)
    monkeypatch.setattr(net, '_context', lambda *args: {'header': header})
    source = """int f(void) {
    int counter;
    counter = 0;
    setup();
    do {
        observe(counter);
        counter += 1;
    } while (counter < 4);
    return counter;
}"""
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 30, Log(), fn)

    def score(agent, proj, fn, candidate, best, fork):
        search.tested += 1
        return 35 if 'setup();\n    counter = 0;' in candidate else 155

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, proj, dict(fn, best_source=source), 155, None) == expected


def test_loop_vector_shape_pass_pairs_signed_entry_with_halfword_counter(monkeypatch, tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    toolchain = 'gcc-2.7.2-kmc'
    if not toolchains.installed(toolchain):
        pytest.skip('needs the local pinned compiler')
    source = """
typedef unsigned short u16;
extern int count;
extern int next(void);
extern void tick(void), visit(int);
void f(void) {
    int timer;
    int index;
    int *bound;
    if (timer) { tick(); } else { tick(); }
    timer = next();
    if (timer == 0) {
        index = 0;
        bound = &count;
        if (((unsigned) timer) < ((unsigned) (*bound))) {
            do {
                visit(index & 0xff);
                index++;
            } while ((index & 0xffff) < *bound);
        }
    }
}
"""
    expected = (source.replace('int index;', 'u16 index;')
                .replace('((unsigned) timer) < ((unsigned) (*bound))', 'timer < *bound')
                .replace('if (timer) { tick(); } else { tick(); }', 'tick();'))
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': toolchain,
                                                 'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']}})
    original = compile_source(cfg, '', expected)
    baseline = compile_source(cfg, '', source)
    assert original.ok, original.log
    assert baseline.ok, baseline.log
    target = extract_function(original.obj, 'f').code
    assert extract_function(baseline.obj, 'f').code != target
    net = setup(monkeypatch)
    fn = dict(net.function(), best_source=source)
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    seen = []

    def score(agent, proj, fn, candidate, best, fork):
        seen.append(candidate)
        result = compile_source(cfg, '', candidate)
        return 0 if result.ok and extract_function(result.obj, 'f').code == target else best

    monkeypatch.setattr(search, '_score', score)
    assert search.loop_vector_shape_pass(AGENT, net.project_row('game'), fn, 170, None) == 0
    assert expected in seen


def test_reused_temp_split_lane_rejects_a_branch_join_with_old_values(monkeypatch):
    from decompnet.app import generation_calls
    from decomprepair.reused_temp_split_repairs import variants

    net = setup(monkeypatch)
    fn, proj = net.function(), net.project_row('game')
    source = '''int f(int mode, int input) {
    int value;
    value = 1;
    if (mode) {
        value = input;
    } else {
        value = 0;
    }
    return value;
}'''
    monkeypatch.setattr(generation_calls, 'library', lambda calls: [
        variants(*arguments) if name == 'decomprepair.reused_temp_split_repairs.variants' else []
        for name, arguments, *_ in calls
    ])
    search = deep_permuter.Search(net, 1, 10, Log(), fn)
    proposals, _ = search._local_proposals(source, proj, fn, [])
    assert not any(family == 'reused_temp_split' and 'value_b2 = 0;' in candidate
                   for family, candidate in proposals)


def test_masked_phase_merge_and_guard_sink_reach_focused_lane(monkeypatch):
    from decompnet.app import generation_calls
    from decomprepair.integer_lifetime_repairs import masked_phase_reuse

    net = setup(monkeypatch)
    fn, proj = net.function(), net.project_row('game')
    source = '''int f(int input, int color) {
    u32 earlier;
    s32 phase;
    earlier = input;
    sink(earlier);
    phase = color;
    sink((phase & 0xff) << 16);
    phase = input;
    if (color != 255) {
        int copy = input;
        if (phase < 0) sink(copy);
    }
    return 0;
}'''
    monkeypatch.setattr(generation_calls, 'library', lambda calls: [
        masked_phase_reuse(*arguments) if name == 'decomprepair.integer_lifetime_repairs.masked_phase_reuse' else []
        for name, arguments, *_ in calls
    ])
    search = deep_permuter.Search(net, 1, 10, Log(), fn)
    proposals, _ = search._local_proposals(source, proj, fn, [])
    assert any(family == 'merge_integer_lifetimes' and 'earlier = color;' in candidate
               and 'int copy = input;\n        phase = input;' in candidate
               for family, candidate in proposals)


def test_guarded_split_scope_reaches_focused_lane(monkeypatch):
    from decompnet.app import generation_calls
    from decomprepair.reused_temp_split_repairs import guarded_scope_variants

    net = setup(monkeypatch)
    fn, proj = net.function(), net.project_row('game')
    source = '''int f(int *p) {
    int value;
    if (p != 0) {
        value = *p;
        sink(value);
        value = p[1];
        sink(value);
    }
    return 0;
}'''
    monkeypatch.setattr(generation_calls, 'library', lambda calls: [
        guarded_scope_variants(*args) if name == 'decomprepair.reused_temp_split_repairs.guarded_scope_variants' else []
        for name, args, *_ in calls
    ])
    search = deep_permuter.Search(net, 1, 10, Log(), fn)
    proposals, _ = search._local_proposals(source, proj, fn, [])
    assert any(family == 'reused_temp_split' and 'value_b1 = p[1];' in candidate
               and '} while (0);' in candidate for family, candidate in proposals)


def _budget_search(monkeypatch, **attrs):
    net = setup(monkeypatch)
    search = deep_permuter.Search(net, 1, 100, Log())
    search.best = 10
    for key, value in attrs.items():
        setattr(search, key, value)
    return search


def test_a_search_still_improving_at_its_limit_extends_its_budget(monkeypatch):
    search = _budget_search(monkeypatch)
    search.tested = 100
    search.improved_at = (95, time.monotonic())
    search.check()  # extended instead of raising
    assert (search.limit, search.extensions) == (150, 1)
    assert search.deadline - search.started_at == pytest.approx(90)


def test_extensions_need_fresh_improvement_and_are_capped(monkeypatch):
    search = _budget_search(monkeypatch)
    search.tested = 100
    search.improved_at = (95, time.monotonic())
    search.check()
    search.tested = 150  # no improvement since the extension began
    with pytest.raises(deep_permuter.SearchLimit):
        search.check()
    for expected in (2, 3):
        search.improved_at = (search.tested - 1, time.monotonic())
        search.check()
        assert search.extensions == expected
        search.tested = search.limit
    search.improved_at = (search.tested - 1, time.monotonic())
    with pytest.raises(deep_permuter.SearchLimit):  # EXTEND_MAX reached
        search.check()


@pytest.mark.parametrize('attrs', [
    {'improved_at': None},                                   # never improved
    {'improved_at': (10, 0.0)},                              # improved early in the budget
    {'extendable': False, 'improved_at': (99, 1e12)},        # a caller capped the run
    {'best': 0, 'improved_at': (99, 1e12)},                  # matched
])
def test_a_search_that_is_not_improving_late_ends_at_its_limit(monkeypatch, attrs):
    search = _budget_search(monkeypatch, **attrs)
    search.tested = 100
    with pytest.raises(deep_permuter.SearchLimit):
        search.check()


def test_a_run_that_keeps_improving_spends_the_extended_budget(monkeypatch):
    net = setup(monkeypatch)
    monkeypatch.setattr(draft_repairs, 'variants', lambda source, *args, **kwargs: [source + ' '])
    def judge(self, agent, proj, fn, source, best, fork):
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_score=?,best_source=? WHERE id=?', (best-1, source, fn['id']))
        return best-1
    monkeypatch.setattr(Permuter, '_score', judge)
    result = deep_permuter.run(net, 'game', 'f', 1, 2, Log())
    assert result['reason'] == 'Variant limit reached'
    assert result['tested'] > 2  # extended while still improving, then stopped at the cap
