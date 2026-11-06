import json

from decompnet.app import adaptive_repairs, efficiency_feedback, progress_rate, session_efficiency
from tests.test_claims import _game
from tests.test_permute import FakeNet


def test_plateau_needs_sustained_low_gain_and_never_counts_exact_or_errors():
    assert progress_rate.plateau([1000]*6+[995]*6)
    assert not progress_rate.plateau([1000]*6+[900]*6)
    assert not progress_rate.plateau([1000]*11+[0])
    assert not progress_rate.plateau([1000]*11+[None])
    assert not progress_rate.plateau([1000]*3)


def test_low_progress_releases_claim_but_preserves_best_source(tmp_path):
    net,_=_game(tmp_path,[('f',100,'open')]);a,_=net.local_agent('a')
    net.claim_row(a,'game','f')
    for i in range(24):
        net._record(net._function('f','game'),a,1000-i%2,'int f(){return 1;}',1)
        result=progress_rate.track(net,a,'game',net._function('f','game'))
        if i<23:assert not result
    assert 'Low-progress handoff' in result
    assert not net.store.one('SELECT 1 FROM claims WHERE agent_id=?',a.id)
    assert net._function('f','game')['best_source']=='int f(){return 1;}'
    assert net._function('f','game')['status']=='open'
    assert 'Next evidence' in net.store.one('SELECT text FROM notes ORDER BY id DESC LIMIT 1')['text']


def test_progress_window_survives_memory_reset_and_invalidates_on_build_change(tmp_path):
    net,_=_game(tmp_path,[('f',100,'open')]);a,_=net.local_agent('a');net.claim_row(a,'game','f')
    for _ in range(4):
        net._record(net._function('f','game'),a,100,'int f(){return 1;}',1)
        progress_rate.track(net,a,'game',net._function('f','game'))
    progress_rate.track(net,a,'game',net._function('f','game'))
    key=f'progress-rate:{a.id}:{net._function("f","game")["id"]}'
    assert json.loads(net.store.one('SELECT value FROM settings WHERE key=?',key)['value'])['count']==4
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps({'compiler':{'toolchain':'changed','flags':[]}}),))
    net._record(net._function('f','game'),a,100,'int f(){return 2;}',1)
    progress_rate.track(net,a,'game',net._function('f','game'))
    assert json.loads(net.store.one('SELECT value FROM settings WHERE key=?',key)['value'])['count']==1


def test_mismatch_feedback_reports_category_and_target_region():
    text=efficiency_feedback.mismatch([(' ','nop','nop'),('r','addu $s0,$a0,$a1','addu $v0,$a0,$a1')])
    assert 'register allocation' in text and 'first differing row 1' in text
    assert 'Target: addu $s0' in text and 'Candidate: addu $v0' in text
    assert not efficiency_feedback.mismatch([(' ','nop','nop')])


def test_continuation_marks_baseline_and_patch_ids(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');net.claim_row(a,'game','f')
    text=efficiency_feedback.continuation(net,a,'game','f',applied=[],base='old')
    assert 'claim=held' in text and 'applied_patch_ids=none (baseline retained)' in text
    assert 'base_revision=old' in text
    assert 'applied_patch_ids=2' in efficiency_feedback.continuation(net,a,'game','f',applied=[2])


def test_repair_ranking_uses_time_without_disabling_exploration():
    net=FakeNet()
    for _ in range(32):
        adaptive_repairs.record(net,'compiler','register','fast',100,90,10)
        adaptive_repairs.record(net,'compiler','register','slow',100,90,1000)
    weights=adaptive_repairs.factors(net,'compiler','register')
    assert weights['fast']>weights['slow']>=adaptive_repairs.MIN_FACTOR
    assert adaptive_repairs.factors(net,'other compiler','register')=={}
    assert adaptive_repairs.factors(net,'compiler','call')=={}


def test_efficiency_preserves_token_categories_and_includes_failed_work():
    def record(id,matches,model='m',tokens=120):
        return dict(id=id,kind='api',model=model,matches=matches,compiles=10,started_at=0,ended_at=60,
                    tool_calls=10,tokens=tokens,input_tokens=10,cache_read_tokens=100,cache_write_tokens=0,output_tokens=10)
    ctx={i:{'cohort':'profile','compiler':'gcc'} for i in range(1,5)}
    groups=session_efficiency.summarize([record(1,2),record(2,0),record(3,1,'other')],ctx)
    assert len(groups)==2
    row=groups[0]
    assert row['compiles_per_match']==10 and row['seconds_per_match']==60
    assert row['tokens_per_match']['cache_read_tokens']==100
    assert row['tokens_per_match']['input_tokens']==10
    missing=session_efficiency.summarize([record(1,2),record(4,0,tokens=0)],ctx)[0]
    assert missing['tokens_per_match'] is None and missing['missing_usage_sessions']==1
    assert session_efficiency.summarize([record(1,0)],ctx)[0]['compiles_per_match'] is None
    assert not session_efficiency.summarize([record(1,2)],{})


def test_workload_measurements_are_project_scoped_and_retained(tmp_path):
    net,_=_game(tmp_path,[('f',100,'open')]);a,_=net.local_agent('a')
    ctx=session_efficiency.context(net,'game',{})
    with net.store.tx() as db:
        db.execute('INSERT INTO settings VALUES(?,?)',('session-efficiency:1',json.dumps(ctx)))
    net._record(net._function('f','game'),a,100,'int f(){return 1;}',1)
    session_efficiency.finish(net,a,'game',1,0)
    saved=json.loads(net.store.one("SELECT value FROM settings WHERE key='session-efficiency:1'")['value'])
    assert saved['workload']['functions']==1
    assert saved['workload']['instructions']==100
    assert saved['workload']['medium']==1


def test_explicit_larger_work_budget_extends_low_gain_minimum(tmp_path):
    net,_=_game(tmp_path,[('f',100,'open')]);a,_=net.local_agent('a')
    net.set_selection('game',a.name,{'stall_compiles':40})
    net.claim_row(a,'game','f')
    for i in range(24):
        net._record(net._function('f','game'),a,1000-i%2,'int f(){return 1;}',1)
        assert not progress_rate.track(net,a,'game',net._function('f','game'))
    assert net.store.one('SELECT 1 FROM claims WHERE agent_id=?',a.id)
