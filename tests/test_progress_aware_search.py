import json
from types import SimpleNamespace

from decompnet.app import repair_diversity as diversity
from decompnet.app import repair_replay, scan_donor_ranking
from decomprepair import repair_localisation


def node(family):return dict(source=family,parent_source='base',parent_output='parent',path=[family])


def test_useful_changes_outrank_novel_regressions_without_removing_exploration():
    state={}
    for i in range(3):diversity.observe(state,node('worse'),f'worse{i}',score=100+i,parent_score=10)
    diversity.observe(state,node('useful'),'better',score=5,parent_score=10)
    candidates=[node('worse'),node('unknown'),node('useful')]
    assert diversity.order(state,candidates,0)[0]['path']==['useful']
    assert diversity.order(state,candidates,3)[0]['path']==['unknown']
    assert len(diversity.order(state,candidates,0))==3


def test_changed_but_worse_outputs_do_not_reset_progress_stall():
    state={}
    for i,family in enumerate(['a','a','b','b','c','c']):
        diversity.observe(state,node(family),str(i),score=11+i,parent_score=10)
    assert diversity.plateau_count(state)==1
    row=next(iter(state['diversity_outputs'].values()))
    assert row['streak']==0 and row['progress_streak']==6
    assert diversity.order(state,[node('another'),node('scan_load_reuse')],0)[0]['path']==['scan_load_reuse']
    diversity.observe(state,node('better'),'good',score=9,parent_score=10)
    assert diversity.plateau_count(state)==0
    assert json.loads(json.dumps(state))==state


def test_unknown_or_nonfinite_scores_neither_clear_nor_extend_progress_stall():
    state={}
    for i in range(6):diversity.observe(state,node(str(i)),str(i),score=20,parent_score=10)
    for score in [None,float('nan'),float('inf'),True]:
        diversity.observe(state,node('unknown'),'other',score=score,parent_score=10)
    assert next(iter(state['diversity_outputs'].values()))['progress_streak']==6
    assert diversity.plateau_count(state)==1


def test_regional_recovery_requires_same_target_and_no_new_region_damage():
    before=[('r' if i<2 else ' ',f'add $v0, $a0, {i}',f'add $v1, $a0, {i}') for i in range(32)]
    after=[(' ' if i==0 else mark,a,a if i==0 else b) for i,(mark,a,b) in enumerate(before)]
    n={**node('regional'),'baseline_rows':before}
    state={}
    assert diversity.observe(state,n,'other',score=11,parent_score=10,rows=after) is True
    damaged=list(after);damaged[20]=('r',before[20][1],'different')
    assert diversity.observe({},n,'other',score=11,parent_score=10,rows=damaged) is False
    wrong=[(mark,a+' changed',b) for mark,a,b in after]
    assert diversity.observe({},n,'other',score=11,parent_score=10,rows=wrong) is False


def test_donor_ranking_uses_bound_mapping_and_keeps_old_probes():
    source='first=0;\nsecond=0;\nthird=0;\nfourth=0;\n'
    start=source.index('fourth')
    flow=SimpleNamespace(nodes=[SimpleNamespace(start=start,end=len(source),uses=set(),defines={'fourth'})])
    donors=[SimpleNamespace(name=name) for name in ['first','second','third','fourth']]
    rows=[('r','addu $v0, $a0, $a1','addu $v1, $a0, $a1')]
    mapping=repair_localisation.bind(source,dict(available=True,lines=[dict(line=4)]))
    ranked=scan_donor_ranking.order(source,flow,donors,0,0,rows,mapping,'mips')
    assert [d.name for d in ranked]==['fourth','first','second','third']
    for text,evidence,arch in [(source+' ',mapping,'mips'),(source,None,'mips'),(source,mapping,'arm')]:
        assert scan_donor_ranking.order(text,flow,donors,0,0,rows,evidence,arch)==donors[:3]
    assert scan_donor_ranking.order(source,flow,donors,0,0,[],mapping,'mips')==donors[:3]


def test_hard_cohort_is_selected_from_captured_facts_in_input_order():
    def case(i,score,count,target):
        return dict(id=i,evidence=dict(target_sha256=target,instructions=count,before=dict(score=score,source_sha256=str(i)),after=dict(source_sha256='after')))
    cases=[case(1,5,300,'easy'),case(2,60,20,'small'),case(3,60,300,'same'),case(4,70,300,'same'),case(5,80,400,'other')]
    selected=repair_replay.select_cases(cases,limit=8,min_failed_score=50,min_instructions=50,distinct=True)
    assert [c['id'] for c in selected]==[3,5]
    assert repair_replay.cohort_manifest(selected)==[dict(id=3,before='3',after='after'),dict(id=5,before='5',after='after')]
    assert repair_replay.select_cases(cases,ids=[5],limit=1)==[cases[-1]]


def test_deep_and_iterative_feedback_receive_region_evidence(monkeypatch):
    from decompnet.app import deep_repair_feedback, iterative_repairs
    seen=[]
    def observe(state,node,output,**kwargs):seen.append((node,kwargs))
    monkeypatch.setattr(deep_repair_feedback.repair_effects,'observe',observe)
    before=[('r','old','candidate')];after=[(' ','old','old')]
    feedback=deep_repair_feedback.Feedback('base',10,before,output='before')
    feedback.record('base','candidate','family',[],5,after,'after')
    assert seen[-1][0]['baseline_rows']==before and seen[-1][1]['rows']==after
    feedback.record('base','cached','family',[],5,after,'after',reused=True)
    assert len(seen)==1
    state=dict(pending=[dict(source='seed',depth=0,ceiling=10)],next=[],seen=['seed'])
    iterative_repairs.advance(state,lambda s:dict(score=10,matched=False,rows=after,candidate_target='out'),lambda s,r:[],lambda:False)
    assert seen[-1][1]['rows']==after


def test_replay_freezes_cohort_before_measurement(monkeypatch,tmp_path):
    import sys
    example=dict(id=1,evidence=dict(instructions=100,before=dict(score=50,source_sha256='before'),after=dict(source_sha256='after')))
    source=tmp_path/'private.json';output=tmp_path/'results.json'
    source.write_text(json.dumps(dict(examples=[example])))
    def replay(case,**kwargs):
        assert json.loads(output.read_text())['cohort']==[dict(id=1,before='before',after='after')]
        return dict(id=1,skipped='synthetic test')
    monkeypatch.setattr(repair_replay,'replay',replay)
    monkeypatch.setattr(sys,'argv',['replay',str(source),'--output',str(output),'--min-failed-score','50','--distinct-functions'])
    repair_replay.main()
    assert json.loads(output.read_text())['selection']['min_failed_score']==50


def test_mapped_scan_candidates_keep_originals_and_compile(tmp_path):
    import shutil
    import subprocess

    import pytest

    from decompnet.app import scan_load_repairs
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host C compiler unavailable.')
    source='''int f(void){
int a[8];int i,best,index,first,second,third,fourth;
for(i=0;i<8;i++){a[i]=7-i;}best=99;index=0;
for(i=0;i<8;i++){if(a[i]<best){index=i;best=a[i];}}
first=1;
second=2;
third=3;
fourth=index;
return first+second+third+fourth;
}'''
    mapping=repair_localisation.bind(source,dict(available=True,lines=[dict(line=8)]))
    rows=[('r','addu $v0, $a0, $a1','addu $v1, $a0, $a1')]
    original=scan_load_repairs.variants(source,'f')
    guided=scan_load_repairs.variants(source,'f',rows=rows,locations=mapping)
    assert set(original)<=set(guided)
    assert any('{fourth=a[i];' in candidate for _,candidate in guided)
    assert not any('{fourth=a[i];' in candidate for _,candidate in original)
    for i,(_,candidate) in enumerate(guided):
        path=tmp_path/f'case{i}.c';binary=tmp_path/f'case{i}'
        path.write_text(source+'\n'+candidate.replace('int f(void)','int g(void)')+'\nint main(void){return f()!=g();}')
        subprocess.run([cc,'-O2',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=20)
        subprocess.run([str(binary)],check=True,capture_output=True,timeout=10)
