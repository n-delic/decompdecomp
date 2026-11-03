import json
from types import SimpleNamespace

from decompnet.app import adaptive_repairs, compiler_diagnostics, repair_outcomes, repair_sequences
from tests.test_repair_feedback import fn, match, setup  # noqa: F401
from tests.test_repair_replay import case, obj


def test_background_analysis_saves_evidence_without_adopting_or_reviewing(setup,monkeypatch):  # noqa: F811
    net,_,_,_,fid=setup
    case_id=match(setup)
    original=dict(fn(net,fid))
    net.forks=object()
    monkeypatch.delenv('DECOMP_REPAIR_ANALYSIS',raising=False)
    queue=[]
    monkeypatch.setattr(repair_outcomes.workers,'pool',lambda:SimpleNamespace(submit=queue.append))
    monkeypatch.setattr(repair_outcomes,'analyze',lambda example:{'minimization':{'source':'private reduced C','reverted':2}})
    repair_outcomes.schedule(net.repair_feedback,case_id)
    repair_outcomes.schedule(net.repair_feedback,case_id)
    assert len(queue)==1
    queue.pop()()
    result=net.repair_feedback.detail('demo',case_id)
    assert result['automatic_analysis']['state']=='done'
    assert result['automatic_analysis']['report']['minimization']['reverted']==2
    assert result['status']=='new' and dict(fn(net,fid))==original
    summary=net.repair_feedback.detail('demo',case_id,include_source=False)
    assert 'source' not in summary['automatic_analysis']['report']['minimization']
    assert 'private reduced C' not in ''.join(net.repair_feedback.export('demo'))


def test_disabled_analysis_and_failed_jobs_do_not_lose_matches(setup,monkeypatch):  # noqa: F811
    net,_,_,_,fid=setup
    case_id=match(setup);net.forks=object()
    monkeypatch.delenv('DECOMP_REPAIR_ANALYSIS',raising=False)
    repair_outcomes.configure(net.repair_feedback,False)
    monkeypatch.setattr(repair_outcomes.workers,'pool',lambda:(_ for _ in ()).throw(AssertionError()))
    repair_outcomes.schedule(net.repair_feedback,case_id)
    assert not repair_outcomes.enabled(net.repair_feedback)
    repair_outcomes.configure(net.repair_feedback,True)
    queue=[]
    monkeypatch.setattr(repair_outcomes.workers,'pool',lambda:SimpleNamespace(submit=queue.append))
    monkeypatch.setattr(repair_outcomes,'analyze',lambda _:(_ for _ in ()).throw(ValueError('compile failed')))
    repair_outcomes.schedule(net.repair_feedback,case_id);queue.pop()()
    assert repair_outcomes.result(net.repair_feedback,case_id)['state']=='failed'
    assert fn(net,fid)['status']=='matched'


def test_ablation_only_never_searches_unrelated_families(monkeypatch):
    from decompnet.app import repair_replay
    monkeypatch.setattr(repair_replay.draft_repairs,'variants',lambda *a,**kw:(_ for _ in ()).throw(AssertionError()))
    result=repair_replay.replay(case(),minimize=True,ablation_only=True,trials=4,
                               measure=lambda s:{'object':obj(2 if 'return 2' in s else 1)})
    assert result['minimization']['source']==case()['evidence']['after']['source']
    assert len(result['minimization']['trials'])<=4
    assert result['remaining']==0


def test_phase_comparison_requires_unchanged_outputs_and_complete_dumps():
    a={'output_unchanged':True,'phases':{'greg':{'captured_sha256':'a','register_count':3,'assignments':['old']}}}
    b={'output_unchanged':True,'phases':{'greg':{'captured_sha256':'b','register_count':2,'assignments':['new']}}}
    result=compiler_diagnostics.compare(a,b)
    assert result['comparable'] and result['lead']=='allocation/lifetime'
    assert result['changed'][0]['assignments_changed']
    assert not compiler_diagnostics.compare(a,dict(b,output_unchanged=False))['comparable']
    b['phases']['greg']['truncated']=True
    assert compiler_diagnostics.compare(a,b)['changed']==[]


def test_sequence_shapes_distinguish_spills_without_storing_source():
    register=[('r','move $s0, $a0','move $s1, $a0')]
    stack=[('s','lw $t0, 0x24($sp)','lw $t0, 0x2c($sp)')]
    assert repair_sequences.focus(register)!=repair_sequences.focus(stack)
    renamed=[('r','move $s3, $a1','move $s4, $a1')]
    assert repair_sequences.focus(register)==repair_sequences.focus(renamed)
    assert '$s0' not in repair_sequences.focus(register)


def test_efficiency_has_denominators_unknown_time_and_sparse_labels():
    from tests.test_permute import FakeNet
    net=FakeNet()
    adaptive_repairs.record(net,'profile','register','fast',100,0,elapsed_ms=2000)
    adaptive_repairs.record(net,'profile','register','fast',100,50,elapsed_ms=1000)
    adaptive_repairs.record(net,'other','stack','unknown',100,None)
    rows={r['family']:r for r in adaptive_repairs.efficiency(net)}
    assert rows['fast']['matches_per_compile']==.5
    assert rows['fast']['matches_per_trial_minute']==20
    assert rows['fast']['evidence']=='sparse' and rows['fast']['cpu_seconds'] is None
    assert rows['unknown']['matches_per_trial_minute'] is None
    assert rows['fast']['profile']=='profile'
    assert 'efficiency' in adaptive_repairs.coverage(net)


def test_summary_export_does_not_include_minimized_source(setup):  # noqa: F811
    net,*_=setup;case_id=match(setup)
    repair_outcomes.prepare(net.repair_feedback)
    with net.store.tx() as db:
        db.execute('INSERT INTO repair_outcome_analysis VALUES(?,?,?,?)',(case_id,'done',json.dumps({'minimization':{'source':'SECRET'}}),0))
    assert 'SECRET' not in ''.join(net.repair_feedback.export('demo'))


def test_interrupted_analysis_does_not_look_running_after_restart(setup):  # noqa: F811
    net,*_=setup;case_id=match(setup)
    repair_outcomes.prepare(net.repair_feedback)
    with net.store.tx() as db:
        db.execute('INSERT INTO repair_outcome_analysis VALUES(?,?,?,?)',(case_id,'running','{}',0))
    result=repair_outcomes.result(net.repair_feedback,case_id)
    assert result['state']=='interrupted'
    assert 'unchanged' in result['report']['skipped']


def test_match_capture_enqueues_the_new_evidence_hash(setup,monkeypatch):  # noqa: F811
    net,*_=setup;seen=[]
    monkeypatch.setattr(repair_outcomes,'schedule',lambda *args:seen.append(args))
    case_id=match(setup)
    assert len(seen)==1 and seen[0][1]==case_id
    assert seen[0][2]==net.repair_feedback.detail('demo',case_id)['evidence']['after']['source_sha256']


def test_busy_captures_are_durable_and_drained_by_one_worker(setup,monkeypatch):  # noqa: F811
    net,*_=setup;net.forks=object();queue=[]
    monkeypatch.delenv('DECOMP_REPAIR_ANALYSIS',raising=False)
    monkeypatch.setattr(repair_outcomes.workers,'pool',lambda:SimpleNamespace(submit=queue.append))
    first=match(setup)
    # A second independent capture can wait while the first worker is scheduled.
    with net.store.tx() as db:
        db.execute('INSERT INTO repair_examples(project_id,function_id,attempt_id,created_at,evidence) SELECT project_id,function_id,attempt_id,created_at,evidence FROM repair_examples WHERE id=?',(first,))
        second=db.execute('SELECT MAX(id) FROM repair_examples').fetchone()[0]
    repair_outcomes.schedule(net.repair_feedback,second)
    assert len(queue)==1 and repair_outcomes.backlog(net.repair_feedback)['pending']==2
    monkeypatch.setattr(repair_outcomes,'analyze',lambda _: {'summary':{}})
    queue.pop()()
    assert repair_outcomes.backlog(net.repair_feedback)['done']==2
    assert not net.store.all('SELECT * FROM repair_analysis_queue')


def test_disabled_analysis_retains_queue_for_resume(setup,monkeypatch):  # noqa: F811
    net,*_=setup;net.forks=object();queue=[]
    monkeypatch.delenv('DECOMP_REPAIR_ANALYSIS',raising=False)
    monkeypatch.setattr(repair_outcomes.workers,'pool',lambda:SimpleNamespace(submit=queue.append))
    repair_outcomes.configure(net.repair_feedback,False)
    case_id=match(setup)
    assert not queue and repair_outcomes.backlog(net.repair_feedback)['pending']==1
    repair_outcomes.configure(net.repair_feedback,True)
    repair_outcomes.resume(net.repair_feedback)
    monkeypatch.setattr(repair_outcomes,'analyze',lambda _: {'skipped':'No failed baseline'})
    queue.pop()()
    assert repair_outcomes.result(net.repair_feedback,case_id)['state']=='skipped'


def test_restart_resumes_durable_interrupted_work(setup,monkeypatch):  # noqa: F811
    net,*_=setup;case_id=match(setup);net.forks=object();queue=[]
    repair_outcomes.prepare(net.repair_feedback)
    with net.store.tx() as db:
        db.execute('INSERT INTO repair_outcome_analysis VALUES(?,?,?,?)',(case_id,'running','{}',0))
        db.execute('INSERT INTO repair_analysis_queue VALUES(?,?,?)',(case_id,None,0))
    monkeypatch.delenv('DECOMP_REPAIR_ANALYSIS',raising=False)
    monkeypatch.setattr(repair_outcomes.workers,'pool',lambda:SimpleNamespace(submit=queue.append))
    monkeypatch.setattr(repair_outcomes,'analyze',lambda _: {'summary':{}})
    assert repair_outcomes.result(net.repair_feedback,case_id)['state']=='pending'
    repair_outcomes.resume(net.repair_feedback);queue.pop()()
    assert repair_outcomes.result(net.repair_feedback,case_id)['state']=='done'
