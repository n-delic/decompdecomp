import json

from decompnet.app import frozen_repair_cases, region_repairs, repair_sequence_metrics, repair_sequences
from decompnet.app import scratch_role_repairs as scratch
from tests.test_permute import FakeNet
from tests.test_scratch_role_repairs import HEADER, SOURCE


def test_rejections_come_from_actual_guards_and_do_not_change_candidates():
    report=scratch.explain(SOURCE,'f',HEADER,'mips')
    assert report['candidates']==len(scratch.variants(SOURCE,'f',HEADER,'mips'))
    escaped=SOURCE.replace('key = read_key(key);','use(&link); key = read_key(key);')
    report=scratch.explain(escaped,'f',HEADER,'mips')
    assert report['candidates']==0 and report['declined']['escaped_pointer']>0
    assert 'address escapes' in scratch.explanation(escaped,'f',HEADER,'mips')
    assert 'unknown 32-bit ABI' in scratch.explanation(SOURCE,'f',HEADER,'unknown')
    partial=scratch.pointer_phases(SOURCE,'f',HEADER,'mips')[0][1]
    live=partial.replace('return answer != 0;','return answer != repair_scratch;')
    assert scratch.explain(live,'f',HEADER,'mips')['declined']['scratch_still_live']>0


def observe(net,family,*,elapsed=100,before='a',after='b',score=10,exact=False,prefix=()):
    repair_sequences.observe(net,'profile','focus',prefix,family,20,score,before,after,exact=exact,elapsed_ms=elapsed)


def test_sequence_costs_include_unknown_outputs_and_failures_without_fabricating_wins():
    net=FakeNet()
    observe(net,'work',elapsed=15)
    observe(net,'work',elapsed=None,before=None,after=None,score=None)
    observe(net,'work',elapsed=5,after='a',score=20)
    row=repair_sequence_metrics.listing(net,'profile','focus',[])[0]
    assert (row['attempted'],row['identical'],row['unknown_output'],row['failed'])==(3,1,1,1)
    assert (row['elapsed_ms'],row['timed'],row['improved'],row['exact'])==(20,2,1,0)
    assert repair_sequence_metrics.listing(net,'other','focus',[])==[]
    assert repair_sequence_metrics.listing(net,'profile','other',[])==[]
    assert repair_sequence_metrics.listing(net,'profile','focus',['prior'])==[]
    assert '3 fresh trials' in repair_sequence_metrics.brief(net,'profile','focus')


def test_cost_ordering_requires_samples_and_keeps_same_profile_exploration():
    net=FakeNet()
    for _ in range(4):
        observe(net,'fast',elapsed=10)
        observe(net,'slow',elapsed=10000)
        observe(net,'inert',after='a',score=20)
    rows=[dict(family=f,improved=999,exact=999) for f in ['inert','slow','unexplored','fast']]
    ranked=repair_sequence_metrics.rank(net,'profile','focus',[],rows)
    assert [r['family'] for r in ranked]==['fast','slow','unexplored','inert']
    assert {r['family'] for r in ranked}=={r['family'] for r in rows}
    assert repair_sequence_metrics.rank(net,'other','focus',[],rows)==rows
    observe(net,'sparse',elapsed=1,score=0,exact=True)
    sparse=[dict(family='unexplored'),dict(family='sparse')]
    assert repair_sequence_metrics.rank(net,'profile','focus',[],sparse)==sparse


def test_exact_recipe_continuations_use_fresh_costs_not_unmeasured_historical_totals():
    net=FakeNet()
    for family in ['slow','fast']:repair_sequences.learn(net,'profile','focus',['start',family])
    for _ in range(4):
        observe(net,'slow',elapsed=10000,prefix=['start'],score=0,exact=True)
        observe(net,'fast',elapsed=10,prefix=['start'],score=0,exact=True)
    assert repair_sequences.next_families(net,'profile','focus',['start'])[:2]==['fast','slow']


def test_instruction_resolution_handles_insertions_and_same_block_recoveries():
    before=[('r','op0','wrong'),('r','op1','wrong'),(' ','op2','op2')]
    after=[(' ','op0','op0'),('r','op1','wrong'),('>','','extra'),(' ','op2','op2')]
    result=region_repairs.resolution(before,after)
    assert result['removed']==[0] and result['remaining']==[1] and result['introduced']==[]
    text=region_repairs.progress(before,after)
    assert '1 removed' in text and 'next target instructions 2' in text
    assert not region_repairs.resolution(before,[(' ','different','different')])['available']
    assert not region_repairs.resolution([('r',None,'x')],after)['available']


def test_recovery_focus_validates_target_identity_and_prefers_dense_surviving_window():
    before=[('r' if i in [0,1,64,65,66] else ' ',f'op{i}','wrong' if i in [0,1,64,65,66] else f'op{i}') for i in range(96)]
    after=[('r' if i in [1,64,65,66] else ' ',f'op{i}','wrong' if i in [1,64,65,66] else f'op{i}') for i in range(96)]
    evidence=region_repairs.resolution(before,after)
    assert any(r[1]=='op64' for r in region_repairs.focus(after,evidence))
    assert all(r[1]!='op1' for r in region_repairs.focus(after,evidence))
    assert region_repairs.focus(after,{**evidence,'target_sha256':'stale'})==region_repairs.focus(after)


def test_holdout_selection_is_saved_before_outcomes_and_excludes_development(tmp_path):
    for name in ['development','a','b','c']:(tmp_path/(name+'.c')).write_text('int '+name+'(void){return 1;}')
    (tmp_path/'header.h').write_text('')
    manifest=tmp_path/'cases.json'
    manifest.write_text(json.dumps(dict(cases=[dict(name=n,before=n+'.c',header='header.h') for n in ['development','a','b','c']])))
    def evaluator(*args):return None,dict(compiler='fixed'),None,None,None
    cases,frozen=frozen_repair_cases.load(manifest,tmp_path,evaluator,freeze=True,holdouts=2,development_functions=['development'])
    assert len(cases)==2 and all(c['name']!='development' for c in cases)
    assert frozen['selection']['method']=='distinct-function-name-hash'
    manifest.write_text(json.dumps(frozen))
    assert frozen_repair_cases.load(manifest,tmp_path,evaluator)[0]==cases


def test_unavailable_time_is_neutral_and_invalid_time_is_not_accumulated():
    net=FakeNet()
    for duration in [None,float('inf'),float('nan'),-1,True]:observe(net,'unknown',elapsed=duration,before=None,after=None,score=20)
    row=repair_sequence_metrics.listing(net,'profile','focus',[])[0]
    assert row['elapsed_ms']==0 and row['timed']==0 and row['unknown_output']==5 and row['failed']==0


def test_iterative_survivor_report_is_passed_to_followup_generator():
    from decompnet.app import iterative_repairs
    before=[('r','op0','wrong'),('r','op1','wrong')]
    after=[(' ','op0','op0'),('r','op1','wrong')]
    captured=[];calls=[]
    def evaluate(source):
        calls.append(source)
        return dict(score=20 if source=='before' else 10,matched=False,rows=before if source=='before' else after)
    def generate(source,report):
        if source=='middle':captured.append(report['instruction_resolution'])
        return [('repair','middle')] if source=='before' else []
    state=dict(pending=[dict(source='before',depth=0,ceiling=20)],next=[],seen=['before'])
    iterative_repairs.advance(state,evaluate,generate,lambda:len(calls)>4)
    assert captured and captured[0]['removed']==[0] and captured[0]['remaining']==[1]


def test_explicit_local_tool_returns_guard_reason_without_submitting_a_variant(tmp_path,monkeypatch):
    from decompnet.app import agent_tokens, local_experiments
    from tests.test_claims import _game
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('worker')
    source='int f(unsigned char *object,int key){ unsigned char *link; use(&link); return 0; }'
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(agent,'game','f')
    _,revision=agent_tokens.candidate(net,agent,'game','f')
    monkeypatch.setattr(local_experiments.agent_evidence,'preflight',lambda *args:None)
    monkeypatch.setattr(local_experiments,'available',lambda *args,**kwargs:{})
    monkeypatch.setattr(agent_tokens,'variants',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('No candidate may be submitted')))
    text=local_experiments.run(net,agent,'game','f',revision,['scratch_role_reuse'])
    assert 'Scratch repair declined' in text and 'address escapes' in text and 'not exhaustion' in text


def test_cli_selects_and_freezes_holdouts_without_running_candidate_search(tmp_path,monkeypatch):
    import sys

    from decompnet.app import linked_repair_benchmark as bench
    for name in ['development','a','b','c']:(tmp_path/(name+'.c')).write_text('int '+name+'(void){return 1;}')
    (tmp_path/'header.h').write_text('')
    manifest=tmp_path/'cases.json';output=tmp_path/'frozen.json'
    manifest.write_text(json.dumps(dict(cases=[dict(name=n,before=n+'.c',header='header.h') for n in ['development','a','b','c']])))
    monkeypatch.setattr(bench,'evaluator',lambda *args:(None,dict(compiler='fixed'),None,None,None))
    monkeypatch.setattr(bench,'suite',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('Freeze cannot replay candidates')))
    monkeypatch.setattr(sys,'argv',['replay','--project',str(tmp_path),'--cases',str(manifest),'--freeze',
        '--holdouts','2','--development-functions','development','--output',str(output)])
    bench.main()
    frozen=json.loads(output.read_text())
    assert len(frozen['cases'])==2 and all(c['name']!='development' for c in frozen['cases'])
