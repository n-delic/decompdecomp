"""Different models can work separate queues without changing claims already in progress."""
import json

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def net(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', 'mips', str(tmp_path), json.dumps({'kind': 'rom'}))).lastrowid
        # small: 90%, middle: 50%, large: unscored. All are synthetic bookkeeping rows.
        for name, n, score, attempts in [('small', 10, 100, 2), ('middle', 100, 5000, 1), ('large', 500, None, 0)]:
            db.execute('INSERT INTO functions(project_id,name,n_insns,size,best_score,attempts) VALUES(?,?,?,?,?,?)',
                       (pid, name, n, n * 4, score, attempts))
    return net


def test_models_get_independent_enforced_strategies(net):
    a, _ = net.local_agent('codex-expensive-1')
    b, _ = net.local_agent('codex-cheap-1')
    net.set_selection('game', a.name, {'strategy': 'hardest'})
    net.set_selection('game', b.name, {'strategy': 'closest'})
    assert net.claim_row(a, 'game', strategy='easiest')['name'] == 'large'
    assert net.claim_row(b, 'game')['name'] == 'small'
    assert net.selection('game', a.name)['strategy'] == 'hardest'


def test_threshold_is_strict_includes_unscored_and_never_falls_back(net):
    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'match_below': 50})
    assert net.claim_row(a, 'game')['name'] == 'large'
    b, _ = net.local_agent('b')
    net.set_selection('game', b.name, {'match_below': 50})
    assert net.claim_row(b, 'game') is None  # middle is exactly 50%; small is 90%
    assert 'below 50%' in net.nothing_to_claim(b, net.project_row('game'))
    with pytest.raises(NetworkError, match='outside this agent'):
        net.claim_row(b, 'game', function='small')


def test_untried_and_live_changes_preserve_current_claim(net):
    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'strategy': 'untried'})
    assert net.claim_row(a, 'game')['name'] == 'large'
    net.set_selection('game', a.name, {'strategy': 'closest', 'match_below': 80})
    assert net.claim_row(a, 'game')['name'] == 'large'
    net.release(a, 'large', '', 'game')
    assert net.claim_row(a, 'game')['name'] == 'middle'


def test_policies_survive_database_reopen_and_are_project_scoped(net, tmp_path):
    net.set_selection('game', 'a', {'strategy': 'hardest', 'match_below': 75})
    reopened = RomNetwork(tmp_path / 'app.db', tmp_path)
    assert reopened.selection('game', 'a') == {'strategy': 'hardest', 'match_below': 75.0}
    assert reopened.selection('other', 'a') == {}


@pytest.mark.parametrize('policy', [{'strategy': 'bogus'}, {'strategy': []}, {'match_below': -1},
                                    {'match_below': 101}, {'match_below': 'nan'}, {'match_below': 'bad'}, 'bad'])
def test_invalid_policies_leave_previous_choice_intact(net, policy):
    net.set_selection('game', 'a', {'strategy': 'hardest'})
    with pytest.raises(NetworkError):
        net.set_selection('game', 'a', policy)
    assert net.selection('game', 'a')['strategy'] == 'hardest'


def test_custom_queue_does_not_batch_neighbours_or_use_project_priority(net, monkeypatch):
    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'strategy': 'hardest', 'match_below': 60})
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind': 'rom', 'priority': ['small']}),))
    monkeypatch.setattr(net, '_brief', lambda fn, *args, **kw: fn['name'])
    result=net.claim_many(a, 'game', 4)
    assert result.endswith('large') and 'Requested 4 functions; returned 1' in result
    assert net.store.one('SELECT COUNT(*) AS n FROM claims')['n'] == 1


def test_team_claim_transmits_the_workers_policy(net):
    from decompnet.app.collab import Link

    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'strategy': 'hardest', 'match_below': 60})
    link = object.__new__(Link)
    link.net, link.slug = net, 'game'
    requests = []
    link._req = lambda method, path, body: requests.append(body) or {'function': 'large'}
    assert link.claim(a, None, 'easiest') == 'large'
    assert requests[0]['selection'] == {'strategy': 'hardest', 'match_below': 60.0}


@pytest.mark.parametrize('kind', ['rom', 'fork'])
def test_shared_scheduler_works_for_rom_and_project_mode(net, kind):
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind': kind}),))
    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'strategy': 'hardest'})
    assert net.claim_row(a, 'game')['name'] == 'large'


def test_try_cap_releases_at_boundary_and_is_independent_per_worker(net):
    a, _ = net.local_agent('a')
    b, _ = net.local_agent('b')
    net.set_selection('game', a.name, {'max_tries': 2})
    fn = net.claim_row(a, 'game', function='large')
    net._record(fn, a, None, 'invalid C', 1, compiled=False)
    assert net.store.one('SELECT 1 FROM claims WHERE agent_id=?', a.id)
    net._record(fn, a, 400, 'improved C', 1)
    assert not net.store.one('SELECT 1 FROM claims WHERE agent_id=?', a.id)
    assert net._function('large', 'game')['best_source'] == 'improved C'
    assert 'Max tries reached (2/2)' in net.attempt_limit_note(a, 'large', 'game')
    with pytest.raises(NetworkError, match='Max tries reached'):
        net.attempt(a, 'large', 'another C candidate', 'game')
    assert net.claim_row(a, 'game')['name'] != 'large'
    assert net.claim_row(b, 'game', function='large')['name'] == 'large'


def test_last_allowed_try_can_match(net, monkeypatch):
    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'max_tries': 1})
    fn = net.claim_row(a, 'game', function='large')
    monkeypatch.setattr(net, 'on_match', lambda *args: None)
    net._record(fn, a, 0, 'matching C', 1, matched=True)
    assert net._function('large', 'game')['status'] == 'matched'
    assert not net.store.one('SELECT 1 FROM claims WHERE agent_id=?', a.id)


def test_try_cap_survives_restarts_and_can_be_increased(net, tmp_path):
    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'max_tries': 1})
    fn = net.claim_row(a, 'game', function='large')
    net._record(fn, a, 123, 'C', 1)
    reopened = RomNetwork(tmp_path / 'app.db', tmp_path)
    with pytest.raises(NetworkError, match='Max tries reached'):
        reopened.check_attempt_limit(a, reopened._function('large', 'game'))
    reopened.set_selection('game', a.name, {'max_tries': 2})
    reopened.check_attempt_limit(a, reopened._function('large', 'game'))


@pytest.mark.parametrize('value', [0, -1, 1.5, 'bad', 'nan', True, 10001])
def test_try_cap_requires_positive_integer(net, value):
    with pytest.raises(NetworkError, match='whole number'):
        net.set_selection('game', 'a', {'max_tries': value})


def test_project_attempts_cannot_bypass_cap(net, monkeypatch):
    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'max_tries': 1})
    fn = net.claim_row(a, 'game', function='large')
    net._record(fn, a, 123, 'C', 1)
    monkeypatch.setattr(net.forks, '_fn', lambda *args: net._function('large', 'game'))
    with pytest.raises(NetworkError, match='Max tries reached'):
        net.forks.attempt(a, 'game', 'large', 'new C')


def test_all_exhausted_functions_leave_no_work_for_that_worker(net):
    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'max_tries': 1})
    for name in ['small', 'middle', 'large']:
        net._record(net._function(name, 'game'), a, 123, 'C', 1)
    assert net.selection_remaining('game', a) == 0
    assert net.claim_row(a, 'game') is None
    net.set_selection('game', a.name, {})
    assert net.selection_remaining('game', a) == 3


def test_parallel_submissions_cannot_both_use_the_last_try(net, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    a, _ = net.local_agent('a')
    net.set_selection('game', a.name, {'max_tries': 1})
    fn = net.claim_row(a, 'game', function='large')
    entered, proceed = threading.Event(), threading.Event()
    compiled = []
    def candidate(*args):
        compiled.append(1)
        entered.set()
        assert proceed.wait(5)
        net._record(fn, a, 123, 'C', 1)
        return {'text': 'diff', 'score': 123, 'matched': False}
    monkeypatch.setattr(net, '_attempt_unlimited', candidate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(net.attempt, a, 'large', 'C', 'game')
        assert entered.wait(5)
        second = pool.submit(net.attempt, a, 'large', 'C2', 'game')
        proceed.set()
        assert first.result()['score'] == 123
        with pytest.raises(NetworkError, match='Max tries reached'):
            second.result()
    assert len(compiled) == 1


def test_explicit_strategy_overrides_sdk_deferral_but_automatic_preserves_it(net):
    with net.store.tx() as db:
        db.execute('UPDATE functions SET meta=? WHERE name=?',
                   (json.dumps({'naming': {'method': 'sdk_signature', 'name': 'sdkExample'}}), 'large'))
    automatic, _ = net.local_agent('automatic')
    explicit, _ = net.local_agent('explicit')
    assert net.claim_row(automatic, 'game', strategy='hardest')['name'] == 'middle'
    net.set_selection('game', explicit.name, {'strategy': 'hardest', 'max_tries': 10})
    assert net.claim_row(explicit, 'game')['name'] == 'large'


@pytest.mark.parametrize('kind', ['rom', 'fork'])
def test_least_tried_sorts_actual_attempt_counts(net, kind):
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind': kind}),))
        db.execute("UPDATE functions SET attempts=9 WHERE name='large'")
    agent, _ = net.local_agent('least-worker')
    net.set_selection('game', agent.name, {'strategy': 'least_tried'})
    assert net.claim_row(agent, 'game')['name'] == 'middle'


def test_worker_stall_override_is_independent_persistent_and_resettable(net,tmp_path):
    from decompnet.network import move_on_brief
    a,_=net.local_agent('patient');b,_=net.local_agent('automatic')
    fn=net._function('large','game')
    net.set_selection('game',a.name,{'stall_compiles':30,'max_tries':80})
    assert net.stall_status(fn['id'],a.id)['after']==30
    assert net.stall_status(fn['id'],b.id)['after']==10
    assert 'move on after 30' in move_on_brief(fn,net.agent_stall_budget(fn,a))
    for _ in range(35):
        net._record(fn,a,100,'int large(){return 1;}',1)
    state=net.stall_status(fn['id'],a.id)
    assert state['no_better']>=30 and state['after']==30
    reopened=RomNetwork(tmp_path / 'app.db',tmp_path)
    assert reopened.selection('game',a.name)['stall_compiles']==30
    net._record(fn,a,90,'int large(){return 2;}',1)
    assert net.stall_status(fn['id'],a.id)['no_better']==0
    net.set_selection('game',a.name,{})
    assert net.stall_status(fn['id'],a.id)['after']==10


@pytest.mark.parametrize('value',[True,False,0,-1,1.5,1001,'invalid',float('inf'),float('nan')])
def test_stall_budget_validation(net,value):
    with pytest.raises(NetworkError,match='No-improvement'):
        net.set_selection('game','a',{'stall_compiles':value})


@pytest.mark.parametrize('kind', ['rom', 'fork'])
def test_oldest_checked_puts_never_checked_first(net, kind):
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind':kind}),))
    first, _ = net.local_agent('first')
    with net.store.tx() as db:
        for fn, stamp in [(1, 200), (2, 100)]:
            db.execute('INSERT INTO attempts(function_id,agent_id,compiled,score,created_at) VALUES(?,?,0,NULL,?)',
                       (fn, first.id, stamp))
    a, _ = net.local_agent('oldest')
    net.set_selection('game', a.name, {'strategy':'oldest_checked'})
    assert net.claim_row(a, 'game')['name'] == 'large'
    b, _ = net.local_agent('oldest-2')
    net.set_selection('game', b.name, {'strategy':'oldest_checked'})
    assert net.claim_row(b, 'game')['name'] == 'middle'


@pytest.mark.parametrize('kind', ['rom', 'fork'])
def test_model_filter_uses_session_ids_across_workers_efforts_and_named_claims(net, tmp_path, kind):
    from decompnet.app.agents import SCHEMA
    with net.store.tx() as db:
        for statement in SCHEMA:
            db.execute(statement)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind':kind}),))
    old, _ = net.local_agent('old-worker')
    new, _ = net.local_agent('new-worker')
    with net.store.tx() as db:
        db.execute("INSERT INTO helpers(id,project,kind,model,name,label,state) VALUES(1,'game','codex','model-a#high',?,'new','stopped')", (new.name,))
        for model, start, end in [('model-a#low',10,20), ('model-b',21,30)]:
            db.execute("INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at,ended_at) VALUES(2,'game','codex',?,?,?,?)", (model,old.name,start,end))
        for fn, stamp in [(1,15),(2,25)]:
            db.execute('INSERT INTO attempts(function_id,agent_id,compiled,score,created_at) VALUES(?,?,0,NULL,?)',(fn,old.id,stamp))
    net.set_selection('game',new.name,{'strategy':'oldest_checked','model_untried':True})
    with pytest.raises(NetworkError,match='already been checked'):
        net.claim_row(new,'game',function='small')
    assert net.selection_remaining('game',new) == 2
    assert net.claim_row(new,'game')['name'] == 'large'
    # Changing queue settings never replaces the current lease.
    assert net.claim_row(new,'game')['name'] == 'large'
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
        db.execute("UPDATE functions SET status='asm' WHERE id=3")
    assert net.claim_row(new,'game')['name'] == 'middle'  # checked only by model-b
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
        db.execute("UPDATE functions SET status='asm' WHERE id=2")
    assert net.claim_row(new,'game') is None  # never falls back to model-a's previous work


def test_model_filter_requires_explicit_identity(net):
    a, _ = net.local_agent('unknown')
    net.set_selection('game',a.name,{'model_untried':True})
    with pytest.raises(NetworkError,match='explicit model'):
        net.claim_row(a,'game')
    with pytest.raises(NetworkError):
        net.validate_selection({'model_untried':'yes'})


def test_assembly_metadata_policy_is_boolean_persisted_and_agent_scoped(net):
    net.set_selection('game', 'reader', {'assembly_metadata': False})
    assert net.selection('game', 'reader')['assembly_metadata'] is False
    assert 'assembly_metadata' not in net.selection('game', 'other')
    for value in ('false', 0, None):
        with pytest.raises(NetworkError, match='Assembly byte'):
            net.validate_selection({'assembly_metadata': value})
