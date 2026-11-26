"""A durable deep pass visits all eligible candidates without claiming unfinished work is complete."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, deep_sweep
from decompnet.app.deep_sweep import Sweeps
from decompnet.app.engine import RomNetwork
from decompnet.app.support import Jobs
from decompnet.network import NetworkError
from tests.test_deep_permuter import Log


@pytest.fixture
def sweep(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'db.sqlite', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                         ('game', 'Example', 'mips', str(tmp_path), json.dumps({'platform':'n64',
                          'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}))).lastrowid
        for i, status in enumerate(['open', 'open', 'matched', 'asm']):
            db.execute('INSERT INTO functions(project_id,name,status,size,vram,best_source,best_score) VALUES(?,?,?,?,?,?,?)',
                       (pid, f'f{i}', status, 16+i, 0x80001000+i*32, f'int f{i}(void) {{return 1;}}', 50))
    obj = Sweeps(net, Jobs())
    net.deep_sweeps = obj
    monkeypatch.setattr(deep_sweep, 'POLL_SECONDS', 0)
    monkeypatch.setattr(deep_sweep.random, 'shuffle', lambda batch: None)
    calls = []
    def search(net, slug, name, minutes, variants, log):
        calls.append(name)
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_score=25,best_source=? WHERE name=?',
                       (f'int {name}(void) {{return 2;}}', name))
        return {'tested':3,'matched':False,'reason':'Time limit reached','best_score':25}
    monkeypatch.setattr(deep_permuter, 'run', search)
    return obj, net, calls, tmp_path


@pytest.mark.parametrize('imported', [False, True])
def test_persists_bounded_completion_and_resumes_after_restart(sweep, monkeypatch, imported):
    obj, net, calls, path = sweep
    if imported:
        monkeypatch.setattr(net, 'fork_slug', lambda _: 'game')
        net.forks = SimpleNamespace(get=lambda _: (None,SimpleNamespace(root=path,unit=lambda _:SimpleNamespace(source=None,target=None)),{}))
    with net.store.tx() as db:
        db.execute("UPDATE functions SET n_insns=3 WHERE name='f0'")
    log = Log()
    result = obj.run('game', 1, 10, log)
    assert result['functions_tested'] == 2 and calls == ['f0','f1']
    for group in ('workers', 'recent_workers'):
        counts = {worker['function']: worker['n_insns'] for update in log.updates for worker in update.get(group, [])}
        assert counts == {'f0': 3, 'f1': None}
    assert obj.history('game', net._function('f0','game'))['completed']
    # The pass's own improvements must not make it immediately eligible again.
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 0
    reopened = RomNetwork(path/'db.sqlite', path)
    if imported:
        monkeypatch.setattr(reopened, 'fork_slug', net.fork_slug)
        reopened.forks = net.forks
    again = Sweeps(reopened, Jobs())
    assert again.run('game',1,10,Log())['functions_tested'] == 0
    with reopened.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f0(void){return 3;}' WHERE name='f0'")
    assert again.history('game',reopened._function('f0','game'))['stale']
    assert again.run('game',1,10,Log())['functions_tested'] == 1
    assert calls == ['f0','f1','f0']


def test_budget_or_context_changes_retry_completed_passes(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    obj.run('game',1,10,Log())
    assert obj.run('game',2,10,Log())['functions_tested'] == 2
    old = net._context
    monkeypatch.setattr(net,'_context',lambda *args:dict(old(*args) or {}) | {'header':'typedef int changed;'})
    assert obj.history('game',net._function('f0','game'))['stale']
    assert obj.run('game',2,10,Log())['functions_tested'] == 2


def test_cancelled_function_is_not_complete_and_can_resume(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    log = Log()
    def cancel(*args):
        log.cancelled = True
        log.check_cancel()
    original = deep_permuter.run
    monkeypatch.setattr(deep_permuter,'run',cancel)
    with pytest.raises(RuntimeError,match='cancelled'):
        obj.run('game',1,10,log)
    assert not obj.history('game',net._function('f0','game'))['completed']
    monkeypatch.setattr(deep_permuter,'run',original)
    assert obj.run('game',1,10,Log())['functions_tested'] == 2


def test_claimed_candidates_are_searched_while_unusable_candidates_wait(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    import time
    with net.store.tx() as db:
        db.execute("INSERT INTO claims(function_id,agent_id,expires_at) VALUES(?,?,?)",(net._function('f0','game')['id'],1,time.time()+60))
        db.execute("UPDATE functions SET best_source=NULL,best_score=NULL WHERE name='f1'")
    log=Log()
    def pause(seconds):
        log.cancelled=True
    monkeypatch.setattr(deep_sweep,'POLL_SECONDS',1)
    # Replacing time.sleep globally also intercepts Windows subprocess polling during
    # input fingerprinting, cancelling before the queue reaches its own waiting state.
    monkeypatch.setattr(deep_sweep, 'time', SimpleNamespace(time=time.time, sleep=pause))
    with pytest.raises(RuntimeError,match='cancelled'):
        obj.run('game',1,10,log)
    assert calls == ['f0']
    assert net.store.one('SELECT COUNT(*) n FROM deep_permutation_passes')['n'] == 1
    assert net.store.one('SELECT agent_id FROM claims')['agent_id'] == 1
    assert log.updates[-1]['waiting'] == 1


def test_bad_candidates_are_errors_not_completed_and_changed_inputs_retry(sweep, monkeypatch):
    obj,net,calls,_=sweep
    def bad(*args):
        from decompnet.network import NetworkError
        raise NetworkError('Saved C no longer compiles')
    monkeypatch.setattr(deep_permuter,'run',bad)
    assert obj.run('game',1,10,Log())['functions_tested'] == 0
    row=obj.history('game',net._function('f0','game'))
    assert row['state']=='error' and not row['completed'] and row['completed_at'] is None
    assert obj.status('game')['errors']==2


def test_changed_build_during_search_does_not_finish_the_new_build(sweep, monkeypatch):
    obj,net,calls,_=sweep
    original=deep_permuter.run
    def change(*args):
        result=original(*args)
        # Cancel before another turn; only inspect the completed first invocation.
        with net.store.tx() as db:
            db.execute("UPDATE projects SET config=?",(json.dumps({'platform':'n64','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O3']}}),))
        return result
    monkeypatch.setattr(deep_permuter,'run',change)
    log=Log()
    original_record=obj.record
    def record(fn,scope,state,*args,**kwargs):
        original_record(fn,scope,state,*args,**kwargs)
        if state=='pending':log.cancelled=True
    monkeypatch.setattr(obj,'record',record)
    with pytest.raises(RuntimeError,match='cancelled'):obj.run('game',1,10,log)
    assert not obj.history('game',net._function('f0','game'))['completed']


def test_start_stop_and_restart_require_manual_resume_and_prevent_parallel_sweeps(sweep, monkeypatch):
    import threading
    import time

    obj,net,calls,_=sweep
    entered,finish=threading.Event(),threading.Event()
    def block(*args):
        entered.set()
        finish.wait(2)
        args[-1].check_cancel()
        return {'tested':0,'matched':False}
    monkeypatch.setattr(deep_permuter,'run',block)
    first=obj.start('game',1,10,loop=True)
    assert obj.config('game')['loop'] is True
    assert entered.wait(2)
    assert obj.start('game',1,10)['job'] == first['job']
    assert len(obj.jobs.jobs) == 1
    obj.shutdown()
    assert obj.config('game')['enabled']  # startup clears this without re-running work
    finish.set()
    for _ in range(100):
        if obj.jobs.jobs[first['job']]['state']!='running':break
        time.sleep(.01)
    assert obj.jobs.jobs[first['job']]['state']=='cancelled'
    assert not obj.history('game',net._function('f0','game'))['completed']
    starts=[]
    restarted=Sweeps(net,Jobs())
    monkeypatch.setattr(restarted,'start',lambda *args:starts.append(args))
    restarted.resume()
    assert starts == []
    assert not restarted.config('game')['enabled']
    obj.stop('game')
    assert not obj.config('game')['enabled']


def test_claim_race_is_retried_even_if_other_worker_already_released_it(sweep, monkeypatch):
    from decompnet.network import NetworkError
    obj,net,calls,_=sweep
    original=deep_permuter.run
    first=[True]
    def race(*args):
        if first.pop() if first else False:
            raise NetworkError('This function was claimed meanwhile; try again after it is released.')
        return original(*args)
    monkeypatch.setattr(deep_permuter,'run',race)
    assert obj.run('game',1,10,Log())['functions_tested']==2
    assert obj.history('game',net._function('f0','game'))['completed']


def test_single_function_search_also_records_a_completed_pass(monkeypatch):
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import setup
    net=setup(monkeypatch)
    obj=Sweeps(net,Jobs());net.deep_sweeps=obj
    monkeypatch.setattr(Permuter,'_score',lambda *args:40)
    result=deep_permuter.run(net,'game','f',1,1,Log())
    assert result['reason']=='Variant limit reached'
    assert obj.history('game',net.function())['completed']


def test_improvement_totals_scores_and_percentages_survive_restart_and_repeat(sweep):
    obj, net, _, path = sweep
    with net.store.tx() as db:
        db.execute('UPDATE functions SET n_insns=10')
    obj.run('game', 1, 10, Log())
    status = obj.status('game')
    assert status['improved'] == 2
    row = next(r for r in status['improvements'] if r['function'] == 'f0')
    assert (row['starting_score'], row['new_score']) == (50, 25)
    assert (row['starting_percent_text'], row['new_percent_text']) == ('95.0%', '97.5%')
    fresh = net._function('f0', 'game')
    obj.improvement(fresh, 0)
    # Later poorer results, or scores from unrelated agent work, cannot rewrite these results.
    obj.improvement(fresh, 10)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=0,status='matched' WHERE name='f1'")
        db.execute('DELETE FROM deep_permutation_passes')  # repeat clears completion, not improvements
    reopened = Sweeps(RomNetwork(path / 'db.sqlite', path), Jobs())
    status = reopened.status('game')
    assert status['improved'] == 2
    row = next(r for r in status['improvements'] if r['function'] == 'f0')
    assert (row['starting_score'], row['new_score'], row['new_percent_text']) == (50, 0, '100%')
    row = next(r for r in status['improvements'] if r['function'] == 'f1')
    assert row['new_score'] == 25


def test_cancelled_search_keeps_partial_improvements_and_does_not_round_to_full_match(sweep, monkeypatch):
    obj, net, _, _ = sweep
    with net.store.tx() as db:
        db.execute('UPDATE functions SET n_insns=100')
    run_log = Log()
    def cancel(net, slug, name, minutes, variants, log):
        obj.improvement(net._function(name, slug), 1)
        run_log.cancelled = True
        log.check_cancel()
    monkeypatch.setattr(deep_permuter, 'run', cancel)
    with pytest.raises(RuntimeError, match='cancelled'):
        obj.run('game', 1, 10, run_log)
    row = obj.status('game')['improvements'][0]
    assert row['new_percent_text'] == '99.9%'
    assert not obj.history('game', net._function('f0', 'game'))['completed']


def test_single_search_publishes_improvements_before_completion(monkeypatch):
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import setup
    net = setup(monkeypatch)
    obj = Sweeps(net, Jobs())
    net.deep_sweeps = obj
    monkeypatch.setattr(Permuter, '_score', lambda *args: 20)
    log = Log()
    original = log.progress
    observed = []
    def progress(value):
        observed.append(obj.status('game')['improved'])
        original(value)
    log.progress = progress
    deep_permuter.run(net, 'game', 'f', 1, 1, log)
    assert 1 in observed
    row = obj.status('game')['improvements'][0]
    assert (row['starting_score'], row['new_score']) == (40, 20)


def test_queue_counts_distinguish_current_completions_stale_sources_and_unscored(sweep):
    obj, net, _, _ = sweep
    obj.save('game', {'enabled': False, 'minutes': 1, 'max_variants': 10})
    initial = obj.status('game')
    assert (initial['queue_done'], initial['queue_left'], initial['awaiting_source']) == (0, 2, 0)
    obj.run('game', 1, 10, Log())
    complete = obj.status('game')
    assert (complete['queue_done'], complete['queue_left']) == (2, 0)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='changed' WHERE name='f0'")
        db.execute("UPDATE functions SET best_source=NULL,best_score=NULL WHERE name='f1'")
    obj.queue_cache.clear()  # Cache expires shortly after an external edit.
    changed = obj.status('game')
    assert (changed['queue_done'], changed['queue_left'], changed['awaiting_source']) == (0, 1, 1)


def test_functions_are_searched_on_several_threads_at_once(sweep, monkeypatch):
    """Two threads: each search waits for the other's (a barrier one thread alone could never pass)."""
    import threading

    obj, net, calls, _ = sweep
    meeting = threading.Barrier(2, timeout=10)
    names = {}

    def search(net, slug, name, minutes, variants, log):
        names[name] = threading.current_thread().name
        meeting.wait()
        return {'tested': 3, 'matched': False, 'reason': 'Time limit reached', 'best_score': 25}

    monkeypatch.setattr(deep_permuter, 'run', search)
    result = obj.run('game', 1, 10, Log(), 2)
    assert result['functions_tested'] == 2 and result['tested'] == 6
    assert set(names) == {'f0', 'f1'} and len(set(names.values())) == 2
    assert all(n.startswith('deep-pass') for n in names.values())
    assert all(obj.history('game', net._function(n, 'game'))['completed'] for n in ('f0', 'f1'))


def test_the_pass_bookkeeping_never_waits_on_or_dies_of_a_saved_folder_sync(sweep, monkeypatch):
    """A pass's threads waited minutes at a time for saved-folder verification before each turn, and one that failed
    ("you hold another claim") ended the pass: only its start syncs the folder; the app's autosave does the rest."""
    obj, net, calls, _ = sweep
    synced = []

    def sync(slug, **kwargs):
        synced.append(slug)
        if len(synced) > 1:
            raise NetworkError('Could not verify saved C for f1: you hold another claim: f0.')
        return False

    monkeypatch.setattr(net.progress, 'sync', sync)
    result = obj.run('game', 1, 10, Log(), 2)
    assert result['functions_tested'] == 2 and sorted(calls) == ['f0', 'f1']
    assert synced == ['game']  # (the start's: Sweeps.run's recover_room_transport)
    with deep_sweep.looking(net):
        assert all(obj.history('game', net._function(n, 'game'))['state'] == 'completed' for n in ('f0', 'f1'))


def test_one_thread_searches_serially_and_zero_means_what_the_computer_can_spare(sweep, monkeypatch):
    from decompnet.app import workers

    obj, net, calls, _ = sweep
    obj.run('game', 1, 10, Log(), 1)
    assert calls == ['f0', 'f1']
    sizes = []
    real = deep_sweep.Executor
    monkeypatch.setattr(deep_sweep, 'Executor', lambda max_workers=None, **kw: sizes.append(max_workers) or real(max_workers, **kw))
    obj.run('game', 2, 10, Log(), 0)
    obj.run('game', 3, 10, Log(), 5000)  # (no limit)
    assert sizes == [workers.default_workers(), 5000]


def test_the_thread_count_is_saved_validated_and_shown(sweep, monkeypatch):
    from decompnet.app import workers
    from decompnet.network import NetworkError

    obj, net, calls, _ = sweep
    monkeypatch.setattr(obj.jobs, 'start', lambda *args, **kw: 'job')
    for bad in (-1, 1.5, True, '4'):
        with pytest.raises(NetworkError, match='threads'):
            obj.start('game', 1, 10, False, bad)
    status = obj.start('game', 1, 10, False, 40)
    assert status['threads'] == 40 and obj.config('game')['threads'] == 40
    assert status['threads_default'] == workers.default_workers()
    assert obj.config('game') | {'threads': 0} == obj.config('game') | {'threads': 0}
    obj.start('game', 1, 10)
    assert obj.config('game')['threads'] == 0


def test_reset_metrics_preserves_progress_passes_and_evidence(sweep):
    obj, net, _, path = sweep
    obj.run('game', 1, 10, Log())
    fn = net._function('f0', 'game')
    obj.record_recipes(fn['id'], {'rewrite': [1, 0, 0, 1, 0, 25]},
                       [('rewrite', 50, 25, '{}', 1234)])
    with net.store.tx() as db:
        other = db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('other','Other','mips',?,'{}')", (str(path),)).lastrowid
        other_fn = db.execute("INSERT INTO functions(project_id,name) VALUES(?,'other_fn')", (other,)).lastrowid
    obj.record_recipes(other_fn, {'rewrite': [1, 0, 0, 1, 0, 25]}, [])
    obj.record_recipes(fn['id'], {}, [], {('rewrite', 'random rewrites'): [1, 0, 0, 0, 0, 1, 0, 10, 25]})
    obj.record_recipes(other_fn, {}, [], {('rewrite', 'random rewrites'): [1, 0, 0, 0, 0, 1, 0, 10, 25]})
    before = dict(net._function('f0', 'game'))
    result = obj.reset_metrics('game')
    assert result['recipes'] == result['recipe_wins'] == result['improvements'] == result['measurements'] == []
    assert obj.measurements(other)[0]['attempted'] == 1
    assert result['completed'] == 2
    assert result['has_activity'] is False
    assert dict(net._function('f0', 'game')) == before
    assert obj.recipes(other)[0]['tested'] == 1
    assert net.store.one('SELECT COUNT(*) n FROM deep_permutation_recipe_wins')['n'] == 1
    reopened = Sweeps(net, Jobs())
    assert reopened.recipe_wins(fn['project_id']) == []
    assert reopened.status('game')['has_activity'] is False
    reopened.record_recipes(fn['id'], {'rewrite': [1, 0, 0, 1, 0, 5]},
                            [('rewrite', 25, 20, '{}', 1235)])
    assert reopened.recipes(fn['project_id'])[0]['tested'] == 1
    assert reopened.recipe_wins(fn['project_id'])[0]['created_at'] == 1235


@pytest.mark.parametrize('kind', ['deep-permuter', 'deep-permutation-pass'])
def test_reset_metrics_rejects_active_searches(sweep, kind):
    obj, net, _, _ = sweep
    obj.jobs.jobs['active'] = {'state': 'running', 'kind': kind}
    with pytest.raises(NetworkError, match='Stop active deep'):
        obj.reset_metrics('game')


@pytest.mark.parametrize('threads', [1, 2])
def test_random_queue_uses_only_eligible_functions_once(sweep, monkeypatch, threads):
    obj, net, calls, _ = sweep
    shuffled = []
    def reverse(batch):
        shuffled.append([fn['name'] for fn in batch])
        batch.reverse()
    monkeypatch.setattr(deep_sweep.random, 'shuffle', reverse)
    obj.run('game', 1, 10, Log(), threads)
    assert shuffled[0] == ['f0', 'f1']
    assert sorted(calls) == ['f0', 'f1']
    if threads == 1:
        assert calls == ['f1', 'f0']
    calls.clear()
    obj.run('game', 1, 10, Log(), threads)
    assert calls == []  # Completed passes remain skipped despite random selection.


def test_restart_reshuffles_interrupted_functions(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    selected = []
    log = Log()
    def interrupted(*args):
        selected.append(args[2])
        log.cancelled = True
        log.check_cancel()
    monkeypatch.setattr(deep_permuter, 'run', interrupted)
    with pytest.raises(RuntimeError, match='cancelled'):
        obj.run('game', 1, 10, log)
    log.cancelled = False
    monkeypatch.setattr(deep_sweep.random, 'shuffle', lambda batch: batch.reverse())
    with pytest.raises(RuntimeError, match='cancelled'):
        obj.run('game', 1, 10, log)
    assert selected == ['f0', 'f1']


def test_measurements_accumulate_by_family_and_phase_without_rewriting_old_totals(sweep):
    obj, net, _, _ = sweep
    fn = net._function('f0', 'game')
    obj.record_recipes(fn['id'], {'rewrite': [5, 1, 0, 1, 0, 10]}, [],
                       {('rewrite', 'random rewrites'): [3, 1, 0, 1, 0, 1, 0, 100, 10]})
    obj.record_recipes(fn['id'], {'rewrite': [2, 0, 0, 1, 1, 5]}, [],
                       {('rewrite', 'random rewrites'): [1, 0, 1, 0, 0, 0, 0, 50, 0],
                        ('rewrite', 'local repairs after random improvement'): [1, 0, 0, 0, 0, 1, 1, 20, 5]})
    rows = {r['phase']: r for r in obj.measurements(fn['project_id'])}
    random = rows['random rewrites']
    assert (random['attempted'], random['compile_failed'], random['unscored'], random['unchanged'],
            random['improved'], random['elapsed_ms'], random['score_gain']) == (4, 1, 1, 1, 1, 150, 10)
    assert rows['local repairs after random improvement']['matched'] == 1
    assert obj.recipes(fn['project_id'])[0]['tested'] == 7


def test_project_sweeps_do_not_enable_early_rotation(sweep,monkeypatch):
    obj,net,calls,_=sweep
    def search(net,slug,name,minutes,variants,log):
        assert not getattr(log,'adaptive_budget',False)
        calls.append((name,minutes))
        return {'tested':32,'matched':False,'reason':'Time limit reached'}
    monkeypatch.setattr(deep_permuter,'run',search)
    assert obj.run('game',2,100,Log())['functions_tested']==2
    assert calls==[('f0',2),('f1',2)]
    assert net.store.one('SELECT state FROM deep_permutation_passes LIMIT 1')['state']=='completed'


def test_room_client_can_start_a_deep_pass(sweep, monkeypatch):
    obj, net, *_ = sweep
    net.links['game'] = object()
    monkeypatch.setattr(obj.jobs, 'start', lambda *args, **kwargs: 'room-pass')
    assert obj.start('game', minutes=1, max_variants=10, threads=2,compiler_profiles=2)['job'] == 'room-pass'


def test_function_runner_credentials_survive_other_workers_starting(sweep):
    _, net, _, _ = sweep
    background, background_token = net.local_agent('permuter')
    first, first_token = net.local_agent(f'permuter-{net._function("f0", "game")["id"]}')
    second, second_token = net.local_agent(f'permuter-{net._function("f1", "game")["id"]}')
    assert len({background.id, first.id, second.id}) == 3
    for agent, token in ((background, background_token), (first, first_token), (second, second_token)):
        assert net.authenticate(token).id == agent.id


def test_room_deep_summary_updates_only_its_runner_and_function(sweep):
    _, net, _, _ = sweep
    agent, _ = net.local_agent('permuter-1')
    other, _ = net.local_agent('permuter-2')
    net.add_note(agent, 'game', 'Keep the signed comparison.', 'f0')
    net.add_note(agent, 'game', 'Deep permuter: score 50 → 40', 'f0')
    net.add_note(agent, 'game', 'Deep permuter: score 40 → 20', 'f0')
    net.add_note(other, 'game', 'Deep permuter: score 90 → 80', 'f0')
    net.add_note(agent, 'game', 'Deep permuter: score 50 → 30', 'f1')
    rows = net.store.all('SELECT text FROM notes ORDER BY id')
    assert [r['text'] for r in rows] == [
        'Keep the signed comparison.', 'Deep permuter: score 40 → 20',
        'Deep permuter: score 90 → 80', 'Deep permuter: score 50 → 30']


def test_start_repeat_clears_only_this_projects_open_passes_and_preserves_improvements(sweep,monkeypatch):
    obj,net,*_=sweep
    own=net.project_row('game')['id']
    with net.store.tx() as db:
        other=db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',('other','Other','/tmp','{}')).lastrowid
        db.execute("INSERT INTO functions(project_id,name,status) VALUES(?,?,'open')",(other,'elsewhere'))
        for fn in db.execute('SELECT id FROM functions').fetchall():
            db.execute("INSERT INTO deep_permutation_passes(function_id,scope,state,minutes,max_variants) VALUES(?,?,'completed',1,10)",(fn['id'],'saved'))
        first=db.execute("SELECT id FROM functions WHERE project_id=? AND name='f0'",(own,)).fetchone()['id']
        db.execute('INSERT INTO deep_permutation_improvements(function_id,starting_score,new_score,updated_at) VALUES(?,50,25,1)',(first,))
    started=[]
    monkeypatch.setattr(obj.jobs,'start',lambda *args,**kwargs:started.append(args[0]) or 'repeat-job')
    result=obj.start('game',minutes=1,max_variants=10,repeat=True,threads=1)
    remaining=net.store.all('SELECT f.name FROM deep_permutation_passes d JOIN functions f ON f.id=d.function_id ORDER BY f.name')
    assert {r['name'] for r in remaining}=={'f2','f3','elsewhere'}
    assert started==['deep-permutation-pass'] and result['job']=='repeat-job'
    assert net.store.one('SELECT new_score FROM deep_permutation_improvements WHERE function_id=?',first)['new_score']==25
    assert net._function('f0','game')['best_score']==50


def test_activity_visibility_tracks_new_runs_and_discards_reset_job(sweep, monkeypatch):
    obj, net, _, _ = sweep
    assert obj.status('game')['has_activity'] is False
    obj.active['game'] = {'job': 'old'}
    obj.jobs.jobs['old'] = {'state': 'done', 'progress': {'recent_workers': [{'function': 'f0'}]}}
    assert obj.status('game')['has_activity'] is True
    status = obj.reset_metrics('game')
    assert status['has_activity'] is False and status['progress'] == {}
    assert 'game' not in obj.active
    reopened = Sweeps(net, Jobs())
    assert reopened.status('game')['has_activity'] is False
    monkeypatch.setattr(reopened.jobs, 'start', lambda *args, **kwargs: 'new')
    reopened.jobs.jobs['new'] = {'state': 'running'}
    assert reopened.start('game')['has_activity'] is True
    assert Sweeps(net, Jobs()).status('game')['has_activity'] is True


def test_attention_lists_only_current_unresolved_failures(sweep):
    obj, net, _, _ = sweep
    cfg = obj.config('game')
    proj = net.project_row('game')
    for name in ('f0', 'f1', 'f2', 'f3'):
        fn = net._function(name, 'game')
        scope = deep_sweep.signature(net, proj, fn, cfg['minutes'], cfg['max_variants'])
        obj.record(fn, scope, 'error', cfg['minutes'], cfg['max_variants'], error='invalid API token')
    status = obj.status('game')
    assert status['errors'] == 2
    assert [issue['function'] for issue in status['attention']] == ['f0', 'f1']
    assert status['attention'][0]['error'] == 'invalid API token'
    assert status['attention'][0]['kind'] == 'search'
    assert 'internal search worker' in status['attention'][0]['action']
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name='f0'")
        db.execute("UPDATE functions SET best_source='int f1(void) {return 4;}' WHERE name='f1'")
    assert obj.status('game')['attention'] == []
    assert obj.status('game')['errors'] == 0
    # Historical error records remain available, but no longer raise a warning.
    assert net.store.one("SELECT COUNT(*) n FROM deep_permutation_passes WHERE state='error'")['n'] == 4


def test_attention_omits_unscored_candidates(sweep):
    obj, net, _, _ = sweep
    cfg, proj = obj.config('game'), net.project_row('game')
    fn = net._function('f0', 'game')
    scope = deep_sweep.signature(net, proj, fn, cfg['minutes'], cfg['max_variants'])
    obj.record(fn, scope, 'error', cfg['minutes'], cfg['max_variants'],
               error='The saved C does not compile in the current context; fix its declarations before deep search.')
    assert 'compile its saved C' in obj.status('game')['attention'][0]['action']
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=NULL WHERE name='f0'")
    assert obj.status('game')['attention'] == []


def test_queue_separates_rotated_errors_and_removed_functions(sweep):
    obj, net, calls, _ = sweep
    obj.save('game', {'enabled':False, 'minutes':1, 'max_variants':10})
    proj = net.project_row('game')
    for name, state in [('f0','rotated'), ('f1','error')]:
        fn = net._function(name, 'game')
        obj.record(fn, deep_sweep.signature(net, proj, fn, 1, 10), state, 1, 10, error='compile failed' if state == 'error' else '')
    status = obj.status('game')
    assert (status['queue_left'], status['queue_rotated'], status['queue_blocked']) == (1, 0, 1)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET gone_mark='removed' WHERE name='f0'")
    obj.queue_cache.clear()
    status = obj.status('game')
    assert (status['queue_left'], status['queue_rotated'], status['queue_blocked']) == (0, 0, 1)
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 0
    assert calls == []


def test_profile_count_is_remembered_forwarded_and_invalidates_completion(sweep, monkeypatch):
    obj, net, _, _ = sweep
    counts = []
    def search(net, slug, name, minutes, variants, log, compiler_profiles=1):
        counts.append(compiler_profiles)
        return {'tested':2, 'matched':False, 'reason':'Time limit reached', 'best_score':50,
                'compiler_profiles':compiler_profiles}
    monkeypatch.setattr(deep_permuter, 'run', search)
    obj.save('game', {'enabled':False, 'minutes':1, 'max_variants':10, 'compiler_profiles':1})
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 2
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 0
    obj.save('game', {**obj.config('game'), 'compiler_profiles':2})
    assert obj.config('game')['compiler_profiles'] == 2
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 2
    assert counts == [1, 1, 2, 2]


def test_status_reuses_scope_checks_until_header_or_pass_changes(sweep, monkeypatch):
    obj, net, _, _ = sweep
    obj.save('game', {'enabled': False, 'minutes': 1, 'max_variants': 10})
    obj.run('game', 1, 10, Log())
    calls = []
    original = deep_sweep.signature
    def measured(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(deep_sweep, 'signature', measured)
    obj.status('game')
    first = len(calls)
    obj.status('game')
    assert len(calls) == first
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,unit,header,created_at) VALUES(1,1,'','/* declaration edit */',0)")
    obj.status('game')
    assert len(calls) > first

def test_queue_batches_state_and_loads_fresh_function_at_dispatch(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    reads=[]
    original=net.store.all
    def read(sql,*args):
        reads.append(sql)
        return original(sql,*args)
    monkeypatch.setattr(net.store,'all',read)
    def shuffle(batch):
        assert all(set(fn) == {'id','name','size'} for fn in batch)
        # A concurrent worker removes this candidate after the queue snapshot.
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched' WHERE name='f0'")
    monkeypatch.setattr(deep_sweep.random,'shuffle',shuffle)
    obj.run('game',1,10,Log())
    assert calls == ['f1']
    assert any('LEFT JOIN deep_permutation_passes' in sql and 'json_each' in sql for sql in reads)


def test_cached_queue_and_attention_refresh_when_profile_count_changes(sweep):
    obj, net, _, _ = sweep
    cfg={'enabled':False,'minutes':1,'max_variants':10,'compiler_profiles':1}
    obj.save('game',cfg)
    proj=net.project_row('game')
    for name,state in [('f0','completed'),('f1','error')]:
        fn=net._function(name,'game')
        obj.record(fn,deep_sweep.signature(net,proj,fn,1,10),state,1,10,error='compile failed' if state=='error' else '')
    first=obj.status('game')
    assert first['queue_done']==1 and first['queue_blocked']==1 and len(first['attention'])==1
    obj.save('game',cfg | {'compiler_profiles':2})
    changed=obj.status('game')
    assert changed['queue_done']==0 and changed['queue_blocked']==0 and changed['queue_left']==2
    assert changed['attention']==[]


def test_shared_preparation_preserves_scope_and_reads_context_once(sweep, monkeypatch):
    obj, net, _, _ = sweep
    proj = net.project_row('game')
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(1,'',1,'void f0(void); int f1(void);')")
    functions = [net._function(name, 'game') for name in ('f0', 'f1')]
    expected = [deep_sweep.signature(net, proj, fn, 1, 10) for fn in functions]
    contexts = []
    original = net._context
    def context(*args):
        contexts.append(args)
        return original(*args)
    monkeypatch.setattr(net, '_context', context)
    shared = {'contexts': {}}
    actual = [deep_sweep.signature(net, proj, fn, 1, 10, 1, shared) for fn in functions]
    assert actual == expected and len(contexts) == 1


def test_search_starts_before_full_preparation_and_reports_checked_total(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    with net.store.tx() as db:
        for i in range(32):
            db.execute("INSERT INTO functions(project_id,name,status,size,vram,best_source,best_score) VALUES(1,?,'open',16,?,'int x(void){return 1;}',50)",
                       (f'extra{i}', 0x80002000+i*32))
    log = Log()
    seen = []
    def search(net, slug, name, minutes, variants, function_log):
        prep = log.updates[-1]['preparation']
        seen.append(dict(prep))
        return {'tested': 0, 'matched': False, 'reason': 'Time limit reached', 'best_score': 50}
    monkeypatch.setattr(deep_permuter, 'run', search)
    result = obj.run('game', 1, 10, log, 1)
    assert result['functions_tested'] == 34
    assert seen[0]['checked'] < seen[0]['total'] == 34
    assert all('preparation' in update for update in log.updates)
    preparations = [update['preparation'] for update in log.updates]
    assert all(p['eligible'] == len(p['candidates']) <= p['checked'] for p in preparations)
    full = next(p for p in preparations if p['done'] and p['eligible'] == 34)
    assert len(set(full['candidates'])) == 34
    assert {f'extra{i}' for i in range(32)} <= set(full['candidates'])
    assert preparations[-1]['done']


def test_loop_doubles_both_budgets_and_stops_on_cancel(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    original = deep_permuter.run
    log = Log()
    budgets = []
    def search(*args, **options):
        if len(calls) == 6:
            log.cancelled = True
            log.check_cancel()
        budgets.append((*args[3:5], options.get('expanded_budget', False)))
        return original(*args)
    monkeypatch.setattr(deep_permuter, 'run', search)
    with pytest.raises(RuntimeError, match='cancelled'):
        obj.run('game', 60, 300_000, log, loop=True)
    assert calls == ['f0', 'f1'] * 3
    assert budgets == [(60, 300_000, False)] * 2 + [(120, 600_000, True)] * 2 + [(240, 1_200_000, True)] * 2
    assert obj.history('game', net._function('f1', 'game'))['completed']


def test_waiting_turn_goes_behind_other_candidates(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    original = deep_permuter.run
    attempts = []
    def search(*args):
        name = args[2]
        attempts.append(name)
        if len(attempts) == 1:
            raise NetworkError('Another worker holds the claim.')
        return original(*args)
    monkeypatch.setattr(deep_permuter, 'run', search)
    obj.run('game', 1, 10, Log())
    assert attempts == ['f0', 'f1', 'f0']


def test_free_workers_refill_before_slow_turn_finishes(sweep, monkeypatch):
    import threading
    obj, net, calls, _ = sweep
    # Changed inputs need a second turn. The slower function must not gate it.
    slow_started, refilled = threading.Event(), threading.Event()
    outer_log = Log()
    counts = {}
    def search(net, slug, name, minutes, variants, log):
        counts[name] = counts.get(name, 0) + 1
        if name == 'f0':
            slow_started.set()
            assert refilled.wait(3), 'free worker was left idle behind the slow function'
        else:
            assert slow_started.wait(3)
            if counts[name] == 2:
                refilled.set()
                outer_log.cancelled = True
        return {'tested': 1, 'matched': False, 'best_score': 50}
    # A new cycle may begin only once all other turns are finished, so use an input change
    # to make a completed function eligible again while the other worker stays occupied.
    original_completed = obj.completed
    def completed(slug, before, minutes, variants, result, scope):
        original_completed(slug, before, minutes, variants, result, scope)
        if before['name'] == 'f1' and counts['f1'] == 1:
            with net.store.tx() as db:
                db.execute("UPDATE functions SET best_source=best_source || ' ' WHERE name='f1'")
    monkeypatch.setattr(obj, 'completed', completed)
    monkeypatch.setattr(deep_permuter, 'run', search)
    with pytest.raises(RuntimeError, match='cancelled'):
        obj.run('game', 1, 10, outer_log, threads=2)
    assert refilled.is_set()


def test_loop_flag_validation(sweep):
    obj, *_ = sweep
    with pytest.raises(NetworkError, match='true or false'):
        obj.start('game', loop='yes')


def test_old_host_outage_errors_are_retryable_not_attention(sweep):
    obj,net,*_=sweep
    proj=net.project_row('game')
    fn=net.store.one('SELECT * FROM functions WHERE project_id=? LIMIT 1',proj['id'])
    obj.record(fn,'old-scope','error',1,10,error="the room's host isn't reachable right now: can't reach the host (ReadTimeout)")
    obj.recover_room_transport('game')
    row=net.store.one('SELECT state,error FROM deep_permutation_passes WHERE function_id=?',fn['id'])
    assert row['state']=='pending' and row['error']==''


def test_room_status_is_read_only_even_with_old_transport_errors_and_a_writer(sweep,monkeypatch):
    import sqlite3
    obj,net,_,_=sweep
    cfg=obj.config('game');proj=net.project_row('game')
    for name,state in [('f0','error'),('f1','rotated')]:
        fn=net._function(name,'game')
        obj.record(fn,deep_sweep.signature(net,proj,fn,cfg['minutes'],cfg['max_variants']),state,
                   cfg['minutes'],cfg['max_variants'],error="the room's host isn't reachable right now")
    def forbidden(*args,**kwargs):pytest.fail('local status must not contact the host or synchronize progress')
    net.links['game']=SimpleNamespace(_req=forbidden)
    monkeypatch.setattr(net.progress,'sync',forbidden)
    # A different process is writing. WAL snapshot reads must still work without repairing passes.
    net.store.conn.execute('PRAGMA busy_timeout=100')
    writer=sqlite3.connect(net.store.path)
    writer.execute('BEGIN IMMEDIATE')
    try:
        with net.progress.view():status=obj.status('game')
    finally:
        writer.rollback();writer.close()
    assert status['queue_left']==2 and status['queue_blocked']==0 and status['attention']==[]
    assert [r['state'] for r in net.store.all('SELECT state FROM deep_permutation_passes ORDER BY function_id')]==['error','rotated']
    # Actual search recovery remains durable and only runs when work is requested.
    monkeypatch.undo()
    obj.recover_room_transport('game')
    assert {r['state'] for r in net.store.all('SELECT state FROM deep_permutation_passes')}=={'pending'}


def test_status_scope_queries_skip_unneeded_archived_source(sweep,monkeypatch):
    obj,net,_,_=sweep
    obj.run('game',1,10,Log())
    reads=[];original=net.store.all
    def read(sql,*args):
        if 'JOIN deep_permutation_passes' in sql:
            reads.append(sql)
        return original(sql,*args)
    monkeypatch.setattr(net.store,'all',read)
    obj.status('game')
    assert reads and all('f.*' not in sql and 'matched_source' not in sql for sql in reads)


def test_compact_status_preserves_entry_specific_compiler_scope(sweep):
    obj,net,_,_=sweep
    cfg=obj.config('game')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET rom_offset=4096 WHERE name='f0'")
        project=json.loads(db.execute("SELECT config FROM projects WHERE slug='game'").fetchone()['config'])
        project['function_compilers']={'4096:2147487744':{'vram':0x80001000,'toolchain':'gcc-2.8.1','flags':['-O1']}}
        db.execute("UPDATE projects SET config=? WHERE slug='game'",(json.dumps(project),))
    proj=net.project_row('game');fn=net._function('f0','game')
    obj.record(fn,deep_sweep.signature(net,proj,fn,cfg['minutes'],cfg['max_variants']),'completed',cfg['minutes'],cfg['max_variants'])
    assert obj.status('game')['queue_done']==1


def test_function_threads_are_persisted_and_dispatched(sweep, monkeypatch):
    obj, net, calls, path = sweep
    obj.save('game', {'threads':2, 'threads_per_function':3, 'compiler_profiles':1, 'minutes':1, 'max_variants':10})
    seen = []
    def search(net, slug, name, minutes, variants, log, **kwargs):
        seen.append(kwargs)
        return {'tested':2, 'matched':False, 'reason':'Time limit reached', 'best_score':50}
    monkeypatch.setattr(deep_permuter, 'run', search)
    result = obj.run('game', 1, 10, Log(), threads=2)
    assert result['functions_tested'] == 2
    assert seen == [{'compiler_profiles':1, 'threads_per_function':3}]*2
    assert obj.status('game')['threads_per_function'] == 3
    reopened = Sweeps(net, Jobs())
    assert reopened.config('game')['threads_per_function'] == 3


@pytest.mark.parametrize('value', [0, True, 257, '2'])
def test_invalid_function_threads_do_not_start_or_save(sweep, value):
    obj, net, calls, path = sweep
    with pytest.raises(NetworkError, match='threads per function'):
        obj.start('game', threads_per_function=value)
    assert not obj.active and obj.config('game')['threads_per_function'] == 1


def test_controls_and_start_stop_do_not_wait_for_saved_statistics(sweep, monkeypatch):
    import threading
    import time
    obj, net, calls, path = sweep
    clock = [100]
    monkeypatch.setattr(deep_sweep, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(obj, 'run', slow)
    monkeypatch.setattr(obj, 'status', lambda *a: pytest.fail('Controls must not scan statistics'))
    monkeypatch.setattr(obj, 'attention', lambda *a: pytest.fail('Controls must not load attention history'))
    try:
        result = obj.start('game', threads=2, threads_per_function=3)
        assert entered.wait(2)
        assert result['control_only'] and result['job']
        assert result['threads_per_function'] == 3
        current = obj.control('game')
        assert current['job'] == result['job'] and current['state'] == 'running'
        assert obj.start('game')['job'] == result['job']
        assert len(obj.jobs.jobs) == 1
        clock[0] = 112
        assert obj.control('game')['runtime_seconds'] == 12
        stopped = obj.stop('game')
        assert not stopped['enabled']
        assert stopped['runtime_seconds'] == 12
        clock[0] = 200
        assert obj.control('game')['runtime_seconds'] == 12
        assert Sweeps(net, Jobs()).control('game')['runtime_seconds'] == 12
    finally:
        release.set()


@pytest.mark.parametrize('fails', [False, True])
def test_runtime_continues_from_saved_total_and_freezes_on_exit(sweep, monkeypatch, fails):
    import time

    obj, net, _, _ = sweep
    obj.save('game', obj.config('game') | {'runtime_seconds': 40})
    clock = [200]
    monkeypatch.setattr(deep_sweep, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=time.time))

    def run(*args):
        clock[0] = 207
        assert obj.control('game')['runtime_seconds'] == 47
        if fails:
            raise NetworkError('Search failed.')

    monkeypatch.setattr(obj, 'run', run)
    if fails:
        with pytest.raises(NetworkError, match='Search failed'):
            obj.timed_run('game', 5, 10000, Log(), 1, False)
    else:
        obj.timed_run('game', 5, 10000, Log(), 1, False)
    clock[0] = 300
    assert obj.status('game')['runtime_seconds'] == 47
    assert Sweeps(net, Jobs()).control('game')['runtime_seconds'] == 47


def test_maximum_throughput_controls_persist_for_returning_project(sweep, monkeypatch):
    import time
    obj,net,_,_=sweep
    seen=[]
    monkeypatch.setattr(obj,'run',lambda *args:seen.append(args))
    started=obj.start('game',1,10,threads=2,maximum_throughput=True,compile_slots=10)
    for _ in range(100):
        if obj.jobs.jobs[started['job']]['state']!='running':break
        time.sleep(.01)
    assert seen
    saved=deep_sweep.Sweeps(net,obj.jobs).config('game')
    assert saved['maximum_throughput'] is True and saved['compile_slots']==10


def test_imported_deep_pass_retains_existing_source_before_search(sweep, monkeypatch):
    obj, net, calls, path = sweep
    monkeypatch.setattr(net, 'fork_slug', lambda _:'game')
    net.forks = SimpleNamespace(get=lambda _:(None,SimpleNamespace(root=path,unit=lambda _:SimpleNamespace(source=None,target=None)),{}),
                                _current_source=lambda fp,fn:f'int {fn["name"]}(void) {{return 3;}}')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source=NULL WHERE status='open'")
    assert obj.status('game')['ready'] == 2
    obj.run('game', 1, 3, Log(), threads=1)
    assert calls == ['f0','f1']
    assert net.store.one("SELECT best_source FROM functions WHERE name='f0'")['best_source']


def test_imported_source_seed_does_not_replace_saved_or_unscored_candidates(sweep, monkeypatch):
    _, net, _, _ = sweep
    monkeypatch.setattr(net, 'fork_slug', lambda _:'game')
    net.forks = SimpleNamespace(get=lambda _:pytest.fail('existing or unscored source must not be read'))
    proj=net.project_row('game')
    fn=net._function('f0','game')
    assert deep_permuter.prepare_source(net,proj,fn)['best_source']==fn['best_source']
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source=NULL,best_score=NULL WHERE name='f0'")
    fn=net._function('f0','game')
    assert deep_permuter.prepare_source(net,proj,fn)['best_source'] is None


@pytest.mark.parametrize("threads", [1, 2])
def test_one_function_bug_does_not_stop_the_pass(sweep, monkeypatch, caplog, threads):
    obj, net, calls, _ = sweep
    original = deep_permuter.run

    def search(net, slug, name, *args, **kwargs):
        if name == 'f0':
            calls.append(name)
            raise TypeError("object of type 'NoneType' has no len()")
        return original(net, slug, name, *args, **kwargs)

    monkeypatch.setattr(deep_permuter, 'run', search)
    result = obj.run('game', 1, 10, Log(), threads)
    assert sorted(calls) == ['f0', 'f1']
    assert result['functions_tested'] == 1
    row = net.store.one('SELECT state,error FROM deep_permutation_passes WHERE function_id=?', net._function('f0', 'game')['id'])
    assert row['state'] == 'error'
    assert 'TypeError' in row['error']
    assert 'continuing with other functions' in caplog.text
    assert obj.history('game', net._function('f1', 'game'))['completed']


def test_deep_search_tries_matched_twin_and_rejudges_improved_partial(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'db.sqlite', tmp_path)
    body = bytes.fromhex('2402000103e0000800000000')  # homemade return-one target
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,root,config,big_endian) VALUES(?,?,?,?,?,1)',
                         ('game', 'Example', 'mips', str(tmp_path), json.dumps({
                             'kind': 'rom', 'platform': 'n64', 'big_endian': True,
                             'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}))).lastrowid
        for index, name in enumerate(('donor', 'target', 'other')):
            db.execute('INSERT INTO functions(project_id,name,status,size,vram,target,relocs,best_source,best_score,matched_source) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (pid, name, 'matched' if not index else 'open', len(body), 0x80001000+index*32, body, '[]',
                        f'int {name}(void) {{return 2;}}', 50, 'int donor(void) {return 1;}' if not index else None))
    monkeypatch.setattr(net, '_context', lambda *_: {'header': '', 'version': 1})
    judged = []

    def judge(agent, proj, fn, source, **kwargs):
        judged.append((fn['name'], source))
        exact = 'return 1;' in source
        score = 0 if exact else 10
        with net.store.tx() as db:
            db.execute('UPDATE functions SET status=?,best_source=?,best_score=?,matched_source=? WHERE id=?',
                       ('matched' if exact else 'open', source, score, source if exact else None, fn['id']))
        return {'matched': exact, 'score': score}

    monkeypatch.setattr(net, 'try_quietly', judge)
    result = deep_permuter.run(net, 'game', 'target', 1, 10, Log())
    assert result['matched'] and result['tested'] == 1
    assert judged[0][0] == 'target' and 'int target(void)' in judged[0][1]
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='asm' WHERE name='donor'")
        db.execute("UPDATE functions SET status='open',matched_source=NULL,best_score=50,best_source='int target(void) {return 2;}' WHERE name='target'")

    def search(*args, **kwargs):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_score=10,best_source='int target(void) {return 3;}' WHERE name='target'")
        return {'matched': False, 'tested': 1}

    monkeypatch.setattr(deep_permuter, '_run', search)
    result = deep_permuter.run(net, 'game', 'target', 1, 10, Log())
    assert result['tested'] == 2
    assert judged[-1][0] == 'other' and 'int other(void) {return 3;}' in judged[-1][1]
    assert net._function('other', 'game')['best_score'] == 10


def test_rejudges_saved_candidates_before_search(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    fn = net._function('f0', 'game')
    agent = net.attribution_agent('autopilot')
    bad = 'int f0(void) { return 2; }'
    good = 'int f0(void) { return 3; }'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=NULL,best_score=1 WHERE id=?', (fn['id'],))
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'','')", (fn['project_id'],))
        db.executemany('INSERT INTO attempts(function_id,agent_id,source,compiled,score) VALUES(?,?,?,1,?)',
                       [(fn['id'], agent.id, bad, 1), (fn['id'], agent.id, bad, 2), (fn['id'], agent.id, good, 3)])
    def change_config(**changes):
        with net.store.tx() as db:
            project = db.execute('SELECT config FROM projects WHERE id=?', (fn['project_id'],)).fetchone()
            config = json.loads(project['config'])
            config.update(changes)
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(config), fn['project_id']))

    def shuffle(batch):
        change_config(compiler={'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O1']})
        batch.reverse()  # Scored f1 would otherwise fill the queue first.

    monkeypatch.setattr(deep_sweep.random, 'shuffle', shuffle)
    tried = []

    def compile_source(proj, header, source, fn):
        assert net.cfg(proj)['compiler']['flags'] == ['-O1']
        tried.append(source)
        change_config(spend={'tokens': len(tried)}, auto_push={'next_check': len(tried)},
                      score_commits={'last_check': len(tried)})
        return SimpleNamespace(ok=source == good, log='undeclared name', obj=b'object')

    monkeypatch.setattr(net, '_compile', compile_source)
    monkeypatch.setattr(net, '_evaluate', lambda *args: (object(), False, []))
    monkeypatch.setattr(net, '_score_candidate', lambda *args: SimpleNamespace(score=36, bytes_match=False))
    monkeypatch.setattr(net, 'can_draft', lambda proj: True)
    monkeypatch.setattr(net, '_autopilot_one', lambda *args, **kw: pytest.fail('History yielded a baseline'))
    original = deep_permuter.run

    def search(net, slug, name, *args):
        if name == 'f0':
            current = net._function(name, slug)
            assert current['best_score'] == 36
            assert current['best_source'] == good
        return original(net, slug, name, *args)

    monkeypatch.setattr(deep_permuter, 'run', search)
    obj.run('game', 1, 10, Log())
    assert tried == [bad, good]
    assert calls == ['f0', 'f1']
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id'])


def test_failed_recovery_uses_drafts_and_waits_for_new_inputs(sweep, monkeypatch):
    obj, net, _, _ = sweep
    from decompnet.app.deep_source_recovery import prepare

    proj = net.project_row('game')
    fn = net._function('f0', 'game')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=NULL,best_score=NULL WHERE id=?', (fn['id'],))
    monkeypatch.setattr(net, 'can_draft', lambda proj: True)
    calls = []

    def draft(agent, proj, fid, *, retry_draft):
        assert retry_draft
        calls.append(fid)
        if len(calls) == 1:
            return 'fail'
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_source=?,best_score=80 WHERE id=?', ('int f0(void) { return 4; }', fid))
        return 'far'

    monkeypatch.setattr(net, '_autopilot_one', draft)
    assert prepare(net, proj, fn, Log())['best_source'] is None
    assert prepare(net, proj, fn, Log())['best_source'] is None
    assert calls == [fn['id']]
    assert obj.status('game')['attention'][0]['function'] == 'f0'
    with net.store.tx() as db:
        db.execute("INSERT INTO settings(key,value) VALUES(?, '1')", (f'machine-draft-generation:{fn["id"]}',))
    assert prepare(net, proj, fn, Log())['best_score'] == 80
    assert len(calls) == 2


def test_recovery_sets_code_no_compiler_makes_aside_instead_of_drafting_it(sweep, monkeypatch):
    _, net, _, _ = sweep
    from decompnet.app.deep_source_recovery import prepare

    proj = net.project_row('game')
    fn = net._function('f0', 'game')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=NULL,best_score=NULL WHERE id=?', (fn['id'],))
    net.twins = SimpleNamespace(set_aside_function_asm=lambda proj, fn: None)
    monkeypatch.setattr(net, 'can_draft', lambda proj: True)
    monkeypatch.setattr(net, '_autopilot_one', lambda *a, **k: 'fail')
    assert prepare(net, proj, fn, Log())['status'] == 'open'

    def set_aside(proj, fn):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='asm' WHERE id=?", (fn['id'],))
        return 'uses the cache instruction (cache)'

    net.twins = SimpleNamespace(set_aside_function_asm=set_aside)
    monkeypatch.setattr(net, '_autopilot_one', lambda *a, **k: pytest.fail('assembly gets no machine draft'))
    with net.store.tx() as db:
        db.execute("DELETE FROM settings WHERE key LIKE 'deep-source-recovery:%'")
    assert prepare(net, proj, fn, Log())['status'] == 'asm'


def test_claimed_function_defers_source_recovery(sweep, monkeypatch):
    _, net, _, _ = sweep
    from decompnet.app.deep_source_recovery import prepare

    proj = net.project_row('game')
    fn = net._function('f0', 'game')
    agent = net.attribution_agent('helper')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=NULL,best_score=NULL WHERE id=?', (fn['id'],))
        db.execute("INSERT INTO claims VALUES(?,?,0,unixepoch()+600)", (fn['id'], agent.id))
    monkeypatch.setattr(net, 'can_draft', lambda proj: pytest.fail('The helper owns this function'))
    assert prepare(net, proj, fn, Log())['best_source'] is None
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?', fn['id'])['agent_id'] == agent.id


def test_sweep_recovers_exact_candidates_before_search(sweep, monkeypatch):
    from decompnet.app import match_reconciliation

    obj, net, calls, _ = sweep
    monkeypatch.setattr(net, 'is_rom', lambda proj: True)

    def recover(engine, slug, seconds, attempts, log, *, automatic):
        assert engine is net and slug == 'game' and automatic
        assert seconds is None and attempts is None
        assert calls == []
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',best_score=0 WHERE name='f0'")
        return {'matched': 1, 'attempts': 1}

    monkeypatch.setattr(match_reconciliation, 'run', recover)
    assert obj.run('game', 1, 10, Log())['matched'] == 1
    assert calls == ['f1']


def _record_pass(obj, net, name, state, result, completed_at):
    cfg, proj = obj.config('game'), net.project_row('game')
    fn = net._function(name, 'game')
    obj.record(fn, deep_sweep.signature(net, proj, fn, cfg['minutes'], cfg['max_variants']), state,
               cfg['minutes'], cfg['max_variants'], result)
    with net.store.tx() as db:
        db.execute('UPDATE deep_permutation_passes SET completed_at=? WHERE function_id=?', (completed_at, fn['id']))
    return fn['id']


def test_passes_made_without_the_randomizer_are_repeated_once_it_is_installed(sweep, monkeypatch):
    """Without decomp-permuter a deep pass only ran the simple rewrites yet counted as finished; installing it
    sends those passes (recorded as such, or older than the install) back to pending, and keeps the others."""
    from decompnet.app import permute
    obj, net, _, _ = sweep
    flagged = _record_pass(obj, net, 'f0', 'completed', {'randomizer': False}, 500.0)
    older = _record_pass(obj, net, 'f1', 'completed', {}, 500.0)
    newer = _record_pass(obj, net, 'f2', 'completed', {}, 2000.0)
    full = _record_pass(obj, net, 'f3', 'completed', {'randomizer': True}, 500.0)
    monkeypatch.setattr(permute, 'installed_at', lambda: None)
    assert obj.reopen_without_randomizer('game') == 0
    monkeypatch.setattr(permute, 'installed_at', lambda: 1000.0)
    assert obj.reopen_without_randomizer('game') == 2
    states = {r['function_id']: r['state'] for r in net.store.all('SELECT function_id,state FROM deep_permutation_passes')}
    assert states == {flagged: 'pending', older: 'pending', newer: 'completed', full: 'completed'}
    assert obj.reopen_without_randomizer('game') == 0


def test_deep_search_result_says_when_the_randomizer_was_missing(monkeypatch):
    from decompnet.app import permute
    monkeypatch.setattr(permute, 'ensure_available', lambda report=None: False)
    monkeypatch.setattr(deep_permuter, '_full_run', lambda *a, **k: {'matched': False, 'tested': 3})
    lines = []
    result = deep_permuter.run(None, 'game', 'f0', 1, 10, lines.append)
    assert result['randomizer'] is False and 'not installed' in result['notice']
    assert any('not installed' in line for line in lines)
    monkeypatch.setattr(permute, 'ensure_available', lambda report=None: True)
    result = deep_permuter.run(None, 'game', 'f0', 1, 10, lines.append)
    assert result['randomizer'] is True and 'notice' not in result


def test_missing_randomizer_is_installed_once_and_a_failure_is_reported(monkeypatch):
    from decompnet.app import permute
    monkeypatch.setenv('DECOMP_AUTO_TOOLS', 'on')
    state = {'installed': False, 'calls': 0}
    monkeypatch.setattr(permute, 'available', lambda: state['installed'])
    monkeypatch.setitem(permute._auto, 'failed', 0.0)

    def ensure(tid, log=None):
        state['calls'] += 1
        if state['calls'] == 1:
            raise OSError('offline')
        state['installed'] = True
    monkeypatch.setattr(permute.toolchains, 'ensure', ensure)
    monkeypatch.setattr(permute.toolchains, 'get', lambda tid: SimpleNamespace(supported=True))
    said = []
    assert permute.ensure_available(said.append) is False
    assert any('could not be installed' in line for line in said)
    assert permute.ensure_available(said.append) is False and state['calls'] == 1  # backed off, not retried per function
    monkeypatch.setitem(permute._auto, 'failed', 0.0)
    assert permute.ensure_available(said.append) is True and state['calls'] == 2
    assert permute.ensure_available(said.append) is True and state['calls'] == 2


@pytest.mark.parametrize('legacy', [False, True])
def test_new_focused_rule_retries_only_the_matching_completed_pass(sweep, monkeypatch, legacy):
    from decompnet import claim_stalls
    from decompnet.app import targeted_retry
    from tests.test_residual_trials import BYTE_GUARD, BYTE_MASK_ROWS, assembly

    obj, net, calls, _ = sweep
    rules = [('unrelated_repairs.py', 'unchanged')]
    monkeypatch.setattr(claim_stalls, 'repair_rules', lambda: list(rules))
    asm = assembly(*(left for _, left, _ in BYTE_MASK_ROWS))
    with net.store.tx() as db:
        for name in ('f0', 'f1'):
            db.execute("UPDATE functions SET best_score=10,best_source=?,asm=? WHERE name=?",
                       (BYTE_GUARD.replace('f0', name), asm if name == 'f0' or not legacy else '', name))
    def search(net, slug, name, minutes, variants, log):
        calls.append(name)
        fn = net._function(name, slug)
        return {'tested': 5, 'matched': False, 'best_score': 10, 'reason': 'Variant limit reached',
                'targeted_retry': targeted_retry.snapshot(fn['best_source'], BYTE_MASK_ROWS)}

    monkeypatch.setattr(deep_permuter, 'run', search)
    proj = net.project_row('game')
    for name in ('f0', 'f1'):
        fn = net._function(name, 'game')
        rows = BYTE_MASK_ROWS if name == 'f0' else BYTE_MASK_ROWS + [('i', 'nop', 'move $a0, $a1')]
        evidence = None if legacy else targeted_retry.snapshot(fn['best_source'], rows)
        obj.record(fn, deep_sweep.signature(net, proj, fn, 1, 10), 'completed', 1, 10,
                   {'targeted_retry': evidence})
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 0
    rules.append(('last_byte_mask_repairs.py', 'new'))
    assert obj.history('game', net._function('f0', 'game'))['stale']
    assert obj.history('game', net._function('f1', 'game'))['completed']
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 1
    assert calls == ['f0']
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 0


def test_cpu_ranking_reopens_completed_sweep_and_reaches_search(sweep, monkeypatch):
    obj, net, _, _ = sweep
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 2
    obj.save('game', obj.config('game') | {'rank_candidates': True})
    assert obj.history('game', net._function('f0', 'game'))['stale']
    assert obj.control('game')['rank_candidates'] is True
    calls = []

    def search(net, slug, name, minutes, variants, log, *, rank_candidates):
        calls.append((name, rank_candidates))
        return {'tested': 1, 'matched': False, 'rank_candidates': rank_candidates}

    monkeypatch.setattr(deep_permuter, 'run', search)
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 2
    assert calls == [('f0', True), ('f1', True)]
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 0
    with pytest.raises(NetworkError, match='CPU constant ranking'):
        obj.start('game', rank_candidates='yes')


def rows_and_details():
    rows = [{'id': i, 'name': f'f{i}', 'size': size} for i, size in enumerate([400, 40, 4000, 80, 16, 800], start=1)]
    details = {1: {'best_score': 300, 'n_insns': 100, 'vram': 0x6000}, 2: {'best_score': 5, 'n_insns': 10, 'vram': 0x1000},
               3: {'best_score': 2000, 'n_insns': 1000, 'vram': 0x3000}, 4: {'best_score': None, 'n_insns': 20, 'vram': 0x2000},
               5: {'best_score': 0, 'n_insns': 4, 'vram': None}, 6: {'best_score': 40, 'n_insns': None, 'vram': 0x4000}}
    return rows, details


@pytest.mark.parametrize('how, expected', [
    ('lowest_score', [5, 2, 6, 1, 3, 4]),        # (no score yet: last)
    ('highest_score', [3, 1, 6, 2, 5, 4]),
    ('smallest', [5, 2, 4, 1, 6, 3]),            # (a function with no instruction count falls back on its bytes / 4)
    ('largest', [3, 6, 1, 4, 2, 5]),
    ('address', [2, 4, 3, 6, 1, 5]),             # (no address: last)
    ('closest', [5, 6, 2, 3, 1, 4]),             # (score per instruction; no score: last)
    ('random', [1, 2, 3, 4, 5, 6]),              # (the shuffled order, as given)
])
def test_a_pass_takes_the_open_functions_in_the_order_chosen(how, expected):
    rows, details = rows_and_details()
    assert [row['id'] for row in deep_sweep.order_functions(rows, details, how)] == expected
    assert set(deep_sweep.FUNCTION_ORDERS) >= {'smart', how}


def test_the_smart_order_is_the_one_a_pass_always_used():
    rows, details = rows_and_details()
    assert deep_sweep.order_functions(rows, details, 'smart') == deep_sweep.closeness_order(rows, details)


def test_a_pass_starts_at_750_candidates_looping_and_remembers_which_functions_to_search_first(sweep, monkeypatch):
    obj, net, calls, path = sweep
    assert deep_sweep.DEFAULT_VARIANTS == 750 and obj.config('game')['max_variants'] == 750
    # (every later loop doubles it, so no function is held to the first budget)
    assert obj.config('game')['loop'] is True
    assert obj.config('game')['function_order'] == 'smart'
    with pytest.raises(NetworkError, match='which functions to search first'):
        obj.start('game', function_order='newest')
    assert not obj.active
    obj.start('game', function_order='largest')
    assert obj.config('game')['function_order'] == 'largest'
    status = obj.status('game')
    assert status['function_order'] == 'largest' and status['threads_default_maximum'] >= status['threads_default'] - 2


def test_functions_are_searched_in_the_order_the_project_chose(sweep, monkeypatch):
    obj, net, calls, path = sweep
    with net.store.tx() as db:
        for name, size, score in (('f0', 64, 90), ('f1', 8, 10)):
            db.execute('UPDATE functions SET size=?,best_score=?,n_insns=? WHERE name=?', (size, score, size // 4, name))
    obj.start('game', function_order='largest')
    for _ in range(300):
        if calls == ['f0', 'f1']:break
        import time
        time.sleep(.02)
    assert calls[:2] == ['f0', 'f1']


def test_stalled_passes_count_passes_without_improvement_and_reset_on_one(sweep):
    obj, net, calls, path = sweep
    proj = net.project_row('game')
    before = dict(net._function('f0', 'game'))
    scope = deep_sweep.signature(net, proj, net._function('f0', 'game'), 1, 10)
    assert obj.stalled('game', before) == 0
    for expected in (1, 2, 3):
        obj.completed('game', dict(net._function('f0', 'game')), 1, 10, {'best_score': 50}, deep_sweep.candidate_scope.key(net, proj, before))
        assert obj.stalled('game', before) == expected
        assert deep_permuter.stalled_passes(net, proj, before) == expected
    # an error record keeps the count it replaced
    obj.record(net._function('f0', 'game'), scope, 'error', 1, 10, error='compile failed')
    assert obj.stalled('game', before) == 3
    # a pass that lowers the score starts the count again
    pre = dict(net._function('f0', 'game'))
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=10 WHERE name='f0'")
    obj.completed('game', pre, 1, 10, {'best_score': 10}, deep_sweep.candidate_scope.key(net, proj, before))
    assert obj.stalled('game', before) == 0


def test_the_pair_search_widens_with_stalled_passes_and_is_capped():
    assert [deep_permuter.escalation(n) for n in (0, 1, 2, 4, 9, None)] == [1, 1.5, 2, 3, 3, 1]
    search = deep_permuter.Search(SimpleNamespace(), 1, 10, Log())
    assert search.pair_scale == 1.0
    search.escalate(0)
    assert search.pair_scale == 1.0
    search.escalate(2)
    assert search.pair_scale == 2.0 and search.seconds == 60 and search.limit == 10


def test_status_sends_the_first_rewrite_rows_and_the_export_all_of_them(sweep, monkeypatch):
    """A long pass has tens of thousands of rewrite families; the window asked for them all every few seconds (12 MB, 5 s)."""
    from decompnet.app import deep_sweep
    obj, net, _, _ = sweep
    monkeypatch.setattr(deep_sweep, 'REWRITE_ROWS_SENT', 3)
    fn = net._function('f0', 'game')
    for i in range(5):
        obj.record_recipes(fn['id'], {f'rewrite_{i}': [i + 1, 0, 0, i, 0, 0]}, [])
    shown, full = obj.status('game'), obj.status('game', full=True)
    assert len(shown['recipes']) == 3 and shown['recipes_total'] == 5
    assert [r['family'] for r in shown['recipes']] == [r['family'] for r in full['recipes']][:3]
    assert len(full['recipes']) == 5
    obj.record_recipes(fn['id'], {'rewrite_late': [1, 0, 0, 9, 0, 0]}, [])
    assert obj.status('game')['recipes_total'] == 5  # cached for a few seconds: the window does not need the last trial
    obj.reset_metrics('game')
    assert obj.status('game')['recipes_total'] == 0  # a reset shows at once


def test_a_missing_package_stops_the_pass_and_keeps_the_queue_instead_of_marking_errors(sweep, monkeypatch):
    obj,net,calls,_=sweep
    def broken(*args):
        raise RuntimeError("No module named 'decomprepair'")
    monkeypatch.setattr(deep_permuter,'run',broken)
    with pytest.raises(Exception, match='could not load part of itself'):
        obj.run('game',1,10,Log())
    assert obj.status('game')['errors']==0
    assert obj.history('game',net._function('f0','game'))['state']=='pending'


def test_import_errors_from_earlier_passes_are_requeued(sweep):
    obj,net,*_=sweep
    fn=net.store.one("SELECT * FROM functions WHERE name='f0'")
    obj.record(fn,'old-scope','error',1,10,error="RuntimeError: No module named 'decomprepair'")
    obj.recover_room_transport('game')
    assert net.store.one('SELECT state,error FROM deep_permutation_passes WHERE function_id=?',fn['id'])['state']=='pending'
