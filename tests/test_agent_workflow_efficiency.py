"""Transcript-derived workflows preserve leases, private evidence and exact judging."""
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

from decompnet import candidate_quality
from decompnet.app import (
    agent_evidence,
    agent_tokens,
    deep_permuter,
    efficiency_feedback,
    release_repairs,
    session_budget,
    tools,
)
from decompnet.app.deep_sweep import Sweeps
from decompnet.app.support import Jobs
from tests.test_claims import _game


def game(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')])
    a,_=net.local_agent('worker')
    fn=net._function('f','game')
    net._record(fn,a,10,'int f(){return 1;}',1)
    return net,a,net._function('f','game')


def test_empty_claim_receipt_stops_session_without_reclaiming_and_explicit_claim_recovers(tmp_path,monkeypatch):
    net,a,fn=game(tmp_path)
    budget=session_budget.start(net,a.id,'game',{})
    calls=[]
    monkeypatch.setattr(net,'claim',lambda *args:calls.append(1) or 'Nothing to claim in game right now.')
    monkeypatch.setattr(net,'selection_wait_reason',lambda *args:('unchanged candidates paused','Wait'))
    first=tools.call(net,a,'game','claim_function',{'function':'','strategy':''})
    second=tools.call(net,a,'game','claim_function',{'function':'','strategy':''})
    assert '"state":"waiting"' in first and second==first and len(calls)==1
    assert budget.ready()
    # Explicit selections bypass the idle receipt, and a real claim clears waiting.
    def claim(*args):
        net.claim_row(a,'game','f')
        return 'CLAIMED f'
    monkeypatch.setattr(net,'claim',claim)
    assert 'CLAIMED' in tools.call(net,a,'game','claim_function',{'function':'f','strategy':''})
    assert not budget.waiting and net.store.one('SELECT 1 FROM claims WHERE agent_id=?',a.id)


def test_receipt_invalidates_on_build_edit_and_never_halts_held_work(tmp_path,monkeypatch):
    net,a,fn=game(tmp_path)
    budget=session_budget.start(net,a.id,'game',{})
    calls=[]
    monkeypatch.setattr(net,'claim',lambda *args:calls.append(1) or 'Nothing to claim in game right now.')
    tools._claim(net,a,'game','','')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(){return 2;}' WHERE id=?",(fn['id'],))
    tools._claim(net,a,'game','','')
    assert len(calls)==2
    net.claim_row(a,'game','f')
    tools._claim(net,a,'game','','')
    assert len(calls)==3 and not budget.waiting


def test_saved_baseline_singleflight_across_workers_and_scope_invalidation(tmp_path,monkeypatch):
    net,a,fn=game(tmp_path)
    b,_=net.local_agent('other')
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def measure(net,agent,slug,fn,source,diagnostic=False):
        calls.append(1);entered.set();assert release.wait(3)
        return {'score':30,'source':source,'verified':True,'rows':[]}
    monkeypatch.setattr(agent_evidence,'_baseline',measure)
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(agent_evidence.baseline,net,a,'game',fn,fn['best_source'])
        assert entered.wait(3)
        second=pool.submit(agent_evidence.baseline,net,b,'game',fn,fn['best_source'])
        release.set()
        assert first.result()['score']==second.result()['score']==30
        first.result()['rows'].append(('r','left','right'))
        assert second.result()['rows']==[]
    assert len(calls)==1
    exploratory={'score':45,'source':'int f(){return 2;}'}
    note=agent_evidence.baseline_note(fn,exploratory,net)
    assert 'Saved C rechecked with current inputs: 30' in note and '15 points worse' in note
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=X'01020304' WHERE id=?",(fn['id'],))
    fresh=net._function('f','game')
    assert agent_evidence.baseline(net,a,'game',fresh,fresh['best_source'])['score']==30
    assert len(calls)==2
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(){return 999;}' WHERE id=?",(fn['id'],))
    changed=net._function('f','game')
    assert agent_evidence.baseline(net,a,'game',changed,changed['best_source'])['source']==changed['best_source']
    assert len(calls)==3
    assert net._function('f','game')['best_score']==10  # A diagnostic never promotes or rewrites history.


def test_failed_saved_probe_is_not_a_shared_verdict(tmp_path,monkeypatch):
    net,a,fn=game(tmp_path);calls=[]
    monkeypatch.setattr(agent_evidence,'_baseline',lambda *args,**kwargs:calls.append(1))
    assert agent_evidence.baseline(net,a,'game',fn,fn['best_source']) is None
    assert agent_evidence.baseline(net,a,'game',fn,fn['best_source']) is None
    assert len(calls)==2


def test_advice_and_assembly_focus_skip_only_proven_relocation_noise(tmp_path,monkeypatch):
    net,a,fn=game(tmp_path)
    rows=[('i','lui $v0, %hi(D_80001000)','lui $v0, 0x8000'),
          ('>','','move $a0, $s1'),('r','addu $v1, $a0, $s0','addu $v0, $a0, $s0')]
    assert efficiency_feedback.target_anchor(rows)=='addu $v1, $a0, $s0'
    advice=efficiency_feedback.mismatch(rows)
    assert 'Advisory instruction window' in advice and 'first differing row 1' in advice
    assert rows[0][0]=='i'  # Normalized advice never edits the raw rows or a verdict.
    net._last_diffs[a.id,fn['id']]=(rows,'legend')
    monkeypatch.setattr(net,'_asm_text',lambda *args:'lui $v0, 0x8000\nnop\naddu $v1, $a0, $s0\nnop')
    assert 'Focus at target line 2' in agent_tokens.assembly(net,'game','f',focus='mismatch',agent=a)
    assert len(efficiency_feedback.instruction_window([('r','a'*500,'b'*500)]*100))<1800


def test_plain_language_quality_note_links_only_explicit_compiled_alternative(tmp_path):
    net,a,fn=game(tmp_path)
    net.claim_row(a,'game','f');progress=net.claim_progress(a,fn)
    corrected='int f(){return 2;}'
    net._record(fn,a,50,corrected,1)
    revision=hashlib.sha256(corrected.encode()).hexdigest()[:20]
    candidate_quality.capture(net,fn,a,'The saved version is wrong C: missing counter reset. Start from '+revision,progress)
    item=candidate_quality.records(net,fn)[-1]
    assert item['structured'] and item['corrected_attempt']
    assert candidate_quality.corrected_alternative(net,fn)['source']==corrected
    assert net._function('f','game')['best_source']==fn['best_source']
    assert net._function('f','game')['best_score']==10


def test_cpu_handoff_is_async_bounded_and_deduplicates(tmp_path,monkeypatch):
    net,a,fn=game(tmp_path)
    sweeps=net.deep_sweeps=Sweeps(net,Jobs())
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def repair(net,slug,name,minutes,variants,log):
        assert (minutes,variants,log.maximum_seconds,log.parallel_claims)==(1,6,8,False)
        calls.append(1);entered.set();assert release.wait(3)
        return {'tested':2,'matched':False}
    monkeypatch.setattr(deep_permuter,'run',repair)
    response=release_repairs.handoff(net,'game',fn)
    assert 'CPU handoff queued' in response and entered.wait(3)
    assert 'already queued' in release_repairs.handoff(net,'game',fn)
    release.set()
    job=next(iter(sweeps.jobs.jobs.values()))
    # Join the actual job thread through completion, bounded by its done flag.
    import time
    until=time.monotonic()+3
    while job['state']=='running' and time.monotonic()<until:
        time.sleep(.01)
    assert job['state']=='done' and len(calls)==1 and job['result']['compile_limit']==6
    sweeps.active['game']={'job':'deep'}
    sweeps.jobs.jobs['deep']={'state':'running'}
    assert 'running deep pass' in release_repairs.handoff(net,'game',fn)
    assert len(calls)==1


def test_explicit_pause_grant_invalidates_idle_receipt(tmp_path,monkeypatch):
    from decompnet import claim_stalls
    net,a,fn=game(tmp_path)
    claim_stalls.released(net,fn,exhausted=True,agent=a)
    tools._claim(net,a,'game','','')
    from decompnet.app import work_availability
    assert work_availability.cached(net,a,'game')
    claim_stalls.resume(net,fn,a,single_turn=True)
    assert work_availability.cached(net,a,'game') is None


def test_startup_no_work_does_not_construct_provider_command(tmp_path,monkeypatch):
    from decompnet.app.agents import Manager, Session
    net,a,fn=game(tmp_path)
    manager=object.__new__(Manager)
    budget=session_budget.Budget();budget.waiting=True
    session=Session('1','claude','worker','game','worker',{})
    session.context_budget=budget
    monkeypatch.setattr(manager,'_preflight_tools',lambda token:None)
    monkeypatch.setattr(manager,'prepare_session',lambda *args,**kwargs:None)
    monkeypatch.setattr(manager,'_command',lambda *args:(_ for _ in ()).throw(AssertionError('Provider must not start')))
    assert manager._run_cli(session,'token',tmp_path)
    assert session.rotated


def test_cpu_handoff_rechecks_inputs_at_dispatch_and_skips_rooms(tmp_path,monkeypatch):
    import time
    net,a,fn=game(tmp_path)
    sweeps=net.deep_sweeps=Sweeps(net,Jobs())
    gate=net.__dict__.setdefault('_repair_handoff_gate',threading.BoundedSemaphore(1))
    gate.acquire()
    monkeypatch.setattr(deep_permuter,'run',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Stale inputs must not compile')))
    release_repairs.handoff(net,'game',fn)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(){return 2;}' WHERE id=?",(fn['id'],))
    gate.release()
    job=next(iter(sweeps.jobs.jobs.values()));until=time.monotonic()+3
    while job['state']=='running' and time.monotonic()<until:
        time.sleep(.01)
    assert job['state']=='done' and 'Inputs changed' in job['result']['skipped']
    net.links['game']=object()
    assert release_repairs.handoff(net,'game',fn)==''


def test_old_current_score_is_not_compared_after_build_inputs_change(tmp_path):
    net,a,fn=game(tmp_path)
    measured={'score':45,'source':fn['best_source'],'input_scope':agent_evidence.build_key(net,fn)[0]}
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=X'01020304' WHERE id=?",(fn['id'],))
    text=agent_evidence.baseline_note(net._function('f','game'),measured,net)
    assert 'needs rechecking' in text and 'no current comparison inferred' in text
    assert 'current rechecked score' not in text


def test_shutdown_cancels_queued_cpu_work_and_rejects_new_handoffs(tmp_path,monkeypatch):
    import time
    net,a,fn=game(tmp_path)
    sweeps=net.deep_sweeps=Sweeps(net,Jobs())
    gate=net.__dict__.setdefault('_repair_handoff_gate',threading.BoundedSemaphore(1));gate.acquire()
    monkeypatch.setattr(deep_permuter,'run',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Cancelled job must not compile')))
    release_repairs.handoff(net,'game',fn)
    sweeps.shutdown()
    gate.release()
    job=next(iter(sweeps.jobs.jobs.values()));until=time.monotonic()+3
    while job['state']=='running' and time.monotonic()<until:
        time.sleep(.01)
    assert job['state']=='cancelled'
    assert 'deferred' in release_repairs.handoff(net,'game',fn)


def test_cpu_handoff_limits_the_real_search_deadline_and_claim_policy(tmp_path):
    net,a,fn=game(tmp_path)
    def log(line):
        pass
    log.maximum_seconds=8;log.parallel_claims=False
    search=deep_permuter.Search(net,1,6,log,fn)
    assert search.limit==6 and search.seconds==8 and search.deadline-search.started_at==8
    assert not search.parallel_claims
    net.claim_row(a,'game','f')
    cpu,_=net.local_agent('cpu')
    assert not search._hold(cpu,fn,count_turn=False)
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fn['id'])['agent_id']==a.id
