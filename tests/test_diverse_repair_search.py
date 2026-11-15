import json

from decompnet.app import inert_pair_search as pairs
from decompnet.app import repair_diversity as diversity
from decompnet.app import scan_load_repairs as scans
from decompnet.app.deep_repair_feedback import Feedback


def node(family):
    return dict(parent_source='base',parent_output='parent',path=[family],source=family)


def test_cross_family_plateau_pivots_and_changed_output_resets():
    state={}
    for family in ['a','a','b','b','c','c']:diversity.observe(state,node(family),'parent')
    assert diversity.plateau_count(state)==1
    candidates=[node('unknown'),node('scan_load_reuse')]
    assert diversity.order(state,candidates,0)[0]['path']==['scan_load_reuse']
    diversity.observe(state,node('d'),'new')
    assert diversity.plateau_count(state)==0
    assert json.loads(json.dumps(state))==state
    isolated={}
    for _ in range(10):diversity.observe(isolated,node('a'),'parent')
    assert diversity.plateau_count(isolated)==0


def test_repeated_outputs_do_not_count_as_new_discoveries_and_unknown_has_probe():
    state={}
    for _ in range(8):diversity.observe(state,node('repeated'),'other')
    diversity.observe(state,node('productive'),'unique')
    candidates=[node('repeated'),node('untried'),node('productive')]
    assert diversity.order(state,candidates,0)[0]['path']==['productive']
    assert diversity.order(state,candidates,3)[0]['path']==['untried']
    diversity.observe(state,node('unknown'),None)
    assert all('unknown' not in row['stats'] for row in state['diversity_outputs'].values())


BASE='int f(int a,int b){int x=a;return x+b;}'
FIRST=BASE.replace('int x','unsigned x')
SECOND=BASE.replace('x+b','x-b')
BOTH=FIRST.replace('x+b','x-b')


def test_inert_pairs_use_shared_budget_disjoint_edits_and_persist():
    state={'seen':[]}
    assert pairs.offer(state,BASE,FIRST,'width',10,10,'same','same')==[]
    state=json.loads(json.dumps(state))
    offered=pairs.offer(state,BASE,SECOND,'operator',10,10,'same','same')
    assert offered==[dict(source=BOTH,path=['width','operator','paired_inert'])]
    assert state['paired_trials']==1
    assert pairs.offer(state,BASE,SECOND,'operator',10,10,'same','same')==[]
    state['paired_trials']=pairs.MAX_TRIALS
    assert pairs.offer(state,BASE,FIRST,'another',10,10,'same','same')==[]


def test_pairs_reject_unknown_changed_literals_and_same_family():
    for output in [None,'changed']:
        state={}
        assert pairs.offer(state,BASE,FIRST,'a',10,10,'same',output)==[]
        assert not state
    state={'seen':set()}
    pairs.offer(state,BASE,FIRST,'a',10,10,'same','same')
    assert pairs.offer(state,BASE,SECOND,'a',10,10,'same','same')==[]
    assert pairs.offer(state,BASE,BASE.replace('a;','2;'),'b',10,10,'same','same')==[]
    assert pairs.offer(state,BASE,SECOND,'b',10,10,'same','same')[0]['source']==BOTH


def test_deep_cache_and_section_proofs_do_not_teach():
    feedback=Feedback(BASE,10,[],output='same')
    feedback.record(BASE,FIRST,'a',[],10,[],'same',reused=True)
    assert 'diversity_outputs' not in feedback.state
    feedback.record(BASE,FIRST,'a',[],10,[],'same')
    assert feedback.state['diversity_outputs']
    assert feedback.pairs(BASE,FIRST,'a',10,'same')==[]
    assert feedback.pairs(BASE,SECOND,'b',10,'same')[0]['source']==BOTH
    section=Feedback(BASE,10,[],output='same',granularity='section')
    assert section.pairs(BASE,FIRST,'a',10,'same')==[]


SCAN='int f(void){int a[8];int i,best,index,tmp;i=0;best=99;index=0;for(i=0;i<8;i++){if(a[i]<best){index=i;best=a[i];}}tmp=index;return tmp;}'


def test_scan_capture_and_dead_temporary_reuse():
    variants=scans.variants(SCAN,'f')
    assert any(kind=='scan_load_capture' and '__repair_scan_value=a[i];' in source for kind,source in variants)
    assert any(kind=='scan_load_reuse' and '{tmp=a[i];' in source for kind,source in variants)
    assert all('best=a[i];' not in source for _,source in variants)
    guarded=SCAN.replace('if(a[i]<best)','if(i<8 && a[i]<best)')
    assert any('if(i<8){int __repair_scan_value=a[i];if(' in source for _,source in scans.variants(guarded,'f'))


def test_scan_rejects_side_effects_dependency_writes_else_and_volatile():
    for source in [SCAN.replace('a[i]','a[i++]'),SCAN.replace('index=i;','i=i+1;'),
                   SCAN.replace('best=a[i];}','best=a[i];}else{index=0;}'),
                   SCAN.replace('int a[8]','volatile int a[8]'),
                   SCAN.replace('a[i]<best','i || a[i]<best')]:
        assert scans.variants(source,'f')==[]


def test_disabled_pair_search_and_macro_boundaries():
    state={'paired_search':False}
    assert pairs.offer(state,BASE,FIRST,'a',10,10,'same','same')==[]
    macro='#define VALUE(x) ((x)+1)\n'+BASE
    assert pairs.offer({},macro,macro.replace('((x)+1)','((x)-1)'),'macro',10,10,'same','same')==[]


def test_scan_variants_compile_and_preserve_a_small_scan(tmp_path):
    import shutil
    import subprocess

    import pytest
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host C compiler is unavailable.')
    source=SCAN.replace('i=0;best=99;', 'for(i=0;i<8;i++){a[i]=7-i;}i=0;best=99;')
    for i,(_,candidate) in enumerate(scans.variants(source,'f')):
        path=tmp_path/f'case{i}.c'
        path.write_text(source+'\n'+candidate.replace('int f(void)','int g(void)')+'\nint main(void){return f()!=g();}')
        binary=tmp_path/f'case{i}'
        subprocess.run([cc,'-std=c99','-O2',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=20)
        subprocess.run([str(binary)],check=True,capture_output=True,timeout=10)


def test_iterative_inert_pair_is_compiled_and_needs_exact_judge(monkeypatch):
    from decompnet.app import iterative_repairs
    monkeypatch.setattr(iterative_repairs.paired_local_repairs,'offer',lambda *a:None)
    for exact in [False,True]:
        state=dict(pending=[dict(source=BASE,depth=0,ceiling=10)],next=[],seen=[BASE])
        calls=[]
        def judge(source, calls=calls, exact=exact):
            calls.append(source)
            return dict(score=0 if source==BOTH else 10,matched=source==BOTH and exact,
                        candidate_target='combined' if source==BOTH else 'same')
        def generate(source,report):return [('width',FIRST),('operator',SECOND)] if source==BASE else []
        assert iterative_repairs.advance(state,judge,generate,lambda:False,arch='mips')==exact
        assert BOTH in calls and state['counts']['paired_evaluations']==1
        assert bool(state.get('exact'))==exact


def test_legacy_and_inert_pairs_share_per_parent_limit():
    import hashlib
    key=hashlib.sha256(BASE.encode()).hexdigest()
    state={'paired_parents':{key:dict(trials=2)}}
    assert pairs.offer(state,BASE,FIRST,'a',10,10,'same','same')==[]
    assert pairs.offer(state,BASE,SECOND,'b',10,10,'same','same')==[]
    assert not state.get('paired_trials')
