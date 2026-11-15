import json

from decompnet.app import allocation_evidence, iterative_repairs
from decompnet.app import paired_local_repairs as pairs

BASE='int f(int a,int b){ int x = a; return x + b; }'
FIRST=BASE.replace('int x','unsigned x')
SECOND=BASE.replace('x + b','x - b')
BOTH=FIRST.replace('x + b','x - b')
ROWS=[('r','lw $v0, 0($a0)','lw $v1, 0($a0)'),('r','sll $v1, $a1, 2','sll $v0, $a1, 2')]


def test_combines_only_small_disjoint_literal_preserving_edits():
    assert pairs.combine(BASE,FIRST,SECOND)==BOTH
    assert pairs.combine(BASE,FIRST,FIRST) is None
    assert pairs.combine(BASE,FIRST,BASE.replace('a;','2;')) is None
    assert pairs.combine(BASE,FIRST,BASE+'\n#define BAD 1') is None
    assert pairs.combine(BASE,FIRST,BASE.replace('int x','volatile int x')) is None
    far='int f(){ int x;'+(' x++;'*100)+' return x + b; }'
    assert pairs.combine(far,far.replace('int x','unsigned x'),far.replace('x + b','x - b')) is None


def test_two_nonimproving_siblings_can_match_as_a_pair_and_resume_in_budget():
    state=dict(pending=[dict(source=BASE,depth=0,ceiling=10)],next=[],seen=[BASE])
    calls=[]
    def judge(s):
        calls.append(s)
        return dict(score=0 if s==BOTH else 10,matched=s==BOTH,rows=ROWS,candidate_target=s,candidate_relocs='[]')
    def variants(s,r):return [('width',FIRST),('operator',SECOND)] if s==BASE else []
    assert not iterative_repairs.advance(state,judge,variants,lambda:len(calls)>=3,arch='mips')
    assert state['paired_trials']==1 and state['pending'][0]['source']==BOTH
    restored=json.loads(json.dumps(state))
    assert iterative_repairs.advance(restored,judge,variants,lambda:len(calls)>=4,arch='mips')
    assert calls==[BASE,FIRST,SECOND,BOTH]
    assert restored['winning_path']==['width','operator','paired_local']


def test_pair_candidates_never_make_score_zero_into_an_exact_verdict():
    state=dict(pending=[dict(source=BASE,depth=0,ceiling=10)],next=[],seen=[BASE])
    def variants(s,r):return [('width',FIRST),('operator',SECOND)] if s==BASE else []
    assert not iterative_repairs.advance(state,lambda s:dict(score=0 if s==BOTH else 10,matched=False,rows=ROWS),variants,lambda:False,arch='mips')
    assert not state.get('exact')


def test_error_or_changed_frame_has_no_pair_and_trial_cap_is_persistent():
    for report in [dict(score=None,rows=ROWS),dict(score=30,rows=ROWS),dict(score=10,rows=[])]:
        state=dict(pending=[],seen=[BASE])
        node=dict(parent_source=BASE,source=FIRST,path=['one'],depth=1,ceiling=10,baseline_rows=ROWS)
        pairs.offer(state,node,report,'mips')
        assert not state.get('paired_trials')
    state=dict(pending=[],seen=[BASE],paired_trials=pairs.MAX_TRIALS)
    pairs.offer(state,dict(),dict(),'mips')
    assert not state['pending']


def test_residual_plan_distinguishes_register_and_context_uncertainty():
    p=allocation_evidence.plan(ROWS,'mips')
    assert p['category']=='allocation' and p['changed_rows']==2 and p['first_difference_row']==1
    assert 'cannot be inferred' in p['context']
    assert allocation_evidence.plan([])['families']==[]


def test_equal_score_beam_reserves_different_measured_output(monkeypatch):
    nodes=[]
    for source,score,output,rows in [('numeric',10,'a',ROWS),('equal',10,'b',ROWS),
                                   ('higher',11,'c',[('r','move $s0, $a0','move $s1, $a0')]),
                                   ('highest',12,'d',[('r','move $s0, $a0','move $s2, $a0')])]:
        nodes.append(dict(source=source,depth=1,ceiling=20,score=score,report=dict(rows=rows,candidate_target=output)))
    retained=[]
    monkeypatch.setattr(iterative_repairs,'children',lambda s,n,v,**k:retained.append(n['source']))
    iterative_repairs.expand_wave(dict(next=nodes,pending=[],seen=[],output_diversity=False),lambda *a:[],'mips')
    assert 'equal' not in retained
    retained.clear()
    iterative_repairs.expand_wave(dict(next=nodes,pending=[],seen=[]),lambda *a:[],'mips')
    assert retained[0]=='numeric' and 'equal' in retained and len(retained)<=iterative_repairs.WIDTH


def test_no_pair_work_runs_after_cancellation_or_the_shared_compile_limit():
    state=dict(pending=[dict(source=BASE,depth=0,ceiling=10)],next=[],seen=[BASE])
    calls=[]
    def judge(s):calls.append(s);return dict(score=10,matched=False,rows=ROWS)
    def variants(s,r):return [('width',FIRST),('operator',SECOND)] if s==BASE else []
    iterative_repairs.advance(state,judge,variants,lambda:len(calls)>=3,arch='mips')
    assert state['pending'][0]['source']==BOTH
    before=list(calls)
    iterative_repairs.advance(state,judge,variants,lambda:True,arch='mips')
    assert calls==before and not state.get('counts',{}).get('paired_evaluations')


def test_pair_caps_preserve_unrelated_pending_work():
    names=['long','short','signed','unsigned','float','double']
    source='int f(){ int a; int b; int c; int d; int e; int f; return a+b+c+d+e+f; }'
    state=dict(pending=[dict(source='unrelated')],seen=[source])
    for index,name in enumerate(['a','b','c','d','e','f']):
        node=dict(parent_source=source,source=source.replace('int '+name+';',names[index]+' '+name+';'),
                  path=['family'+name],depth=1,ceiling=10,baseline_rows=ROWS)
        pairs.offer(state,node,dict(score=10,rows=ROWS),'mips')
    assert state['paired_trials']==pairs.PER_PARENT
    assert any(n['source']=='unrelated' for n in state['pending'])


def test_new_pairs_use_the_same_trial_budget_and_report_actual_pair_evaluations():
    import time

    from decompnet.app import repair_benchmark
    def judge(s):return dict(score=0 if s==BOTH else 10,matched=s==BOTH,rows=ROWS,outcome='exact' if s==BOTH else 'miss')
    def variants(s,r):return [('width',FIRST),('operator',SECOND)] if s==BASE else []
    stopped=repair_benchmark.run(BASE,10,judge,variants,trials=2,deadline=time.monotonic()+2,generate_report=True,arch='mips')
    assert stopped['candidate_compiles']==2 and stopped['paired_proposals']==1 and stopped['paired_evaluations']==0
    done=repair_benchmark.run(BASE,10,judge,variants,trials=3,deadline=time.monotonic()+2,generate_report=True,arch='mips')
    assert done['first_exact_trial']==done['candidate_compiles']==3 and done['paired_evaluations']==1


def test_existing_bridge_and_pointer_scope_compose_as_a_shared_regression():
    from decompnet.app import pointer_index_scope
    from decomprepair import allocation_repairs
    source='void f(S *s,u32 *q){s32 i;u32 *p;s32 value;i=0;p=q;value=1;{ u32 **slot=s->tbl+i; *slot=(u32 *)q; }}'
    parents=[s for family,s in allocation_repairs.variants(source,'f') if family=='allocation_schedule_bridge']
    children=[s for parent in parents for family,s in pointer_index_scope.variants(parent,'f')]
    assert children and all('while (0)' in s and '*(__repair_index_scope_base + __repair_index_scope_offset)' in s for s in children)


def test_combined_homemade_c_requires_normal_linked_bytes_with_pinned_compiler(tmp_path):
    import pytest

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    from decompnet.rom.match import RomTarget, link
    from decompnet.rom.symbols import SymbolTable

    if not toolchains.installed('gcc-2.7.2-kmc'):pytest.skip('Needs the already installed pinned compiler; never download it.')
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}})
    base='int f(int a,int b){ int x = a; return x + b; }'
    narrow=base.replace('int x','short x');multiply=base.replace('x + b','x * b')
    combined=pairs.combine(base,narrow,multiply)
    assert combined
    control=compile_source(cfg,'',combined)
    assert control.ok
    function=extract_function(control.obj,'f');assert not function.relocs
    target=RomTarget('f',0x80001000,function.code,[])
    for source in [base,narrow,multiply,combined]:
        built=compile_source(cfg,'',source);assert built.ok
        result=link(built.obj,'f',target,SymbolTable(),lambda *args:None)
        assert result.exact is (source==combined)
        assert not result.problems


def test_paired_search_ablation_preserves_the_same_initial_inputs_and_limits():
    import time

    import pytest

    from decompnet.app import repair_benchmark
    def judge(s):return dict(score=0 if s==BOTH else 10,matched=s==BOTH,rows=ROWS,outcome='matched' if s==BOTH else 'miss')
    def variants(s,r):return [('width',FIRST),('operator',SECOND)] if s==BASE else []
    result=repair_benchmark.run(BASE,10,judge,variants,trials=3,deadline=time.monotonic()+2,generate_report=True,arch='mips',paired_search=False)
    assert result['first_exact_trial'] is None and result['paired_proposals']==0
    assert result['search_policy']['paired_search'] is False
    with pytest.raises(ValueError):repair_benchmark.run(BASE,10,judge,variants,trials=3,deadline=time.monotonic()+2,paired_search='yes')
