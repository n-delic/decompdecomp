"""Synthetic fixtures only; private game checkpoints stay outside git."""
import json

from decompnet.app import iterative_repairs
from decompnet.app import record_layout_search as repair

SOURCE='''typedef unsigned int u32;
typedef struct { u32 value; } Result;
void f(unsigned int n, unsigned int x, unsigned int y, Result *out) {
 u32 a,b,v;
 do {
 if (n) { a=x; b=y; } else { a=y; b=x; }
 a += b;
 v=a;
 out->value=v;
 n += 1;
 } while(0);
}'''


def test_bounded_compositions_preserve_signature_and_deduplicate():
    choices=repair.variants(SOURCE,'f','','mips')
    assert choices and len(choices)<=repair.LIMIT
    assert len({text for _,text in choices})==len(choices)
    assert all(text.startswith(SOURCE.split('{')[0]) for _,text in choices)
    assert any(text.count('out->value')==2 for _,text in choices)
    assert not repair.variants(SOURCE.replace('u32 a,b,v;','volatile u32 a,b,v;'),'f','','mips')
    assert not repair.variants(SOURCE,'f','','arm')
    assert not repair.variants(SOURCE+' '*repair.MAX_SOURCE,'f','','mips')


def test_composition_uses_real_judge_and_resumes_existing_budget():
    choices=repair.variants(SOURCE,'f','','mips');exact=choices[-1][1];calls=[]
    state={'pending':[dict(source=SOURCE,depth=0,ceiling=100)],'next':[],'seen':[SOURCE]}
    def judge(text):
        calls.append(text)
        return {'score':0 if text==exact else 100,'matched':text==exact,'rows':[]}
    generate=lambda text,report: choices if text==SOURCE else []
    iterative_repairs.advance(state,judge,generate,lambda:len(calls)>=2,arch='mips')
    state=json.loads(json.dumps(state))
    assert iterative_repairs.advance(state,judge,generate,lambda:False,arch='mips')
    assert calls[-1]==exact and len(calls)<=len(choices)+1


def test_synthetic_variants_preserve_outputs(tmp_path):
    import shutil
    import subprocess

    import pytest
    cc=shutil.which('cc')
    if not cc:pytest.skip('host compiler unavailable')
    choices=[SOURCE]+[text for _,text in repair.variants(SOURCE,'f','','mips')]
    program='typedef unsigned int u32;\ntypedef struct { u32 value; } Result;\n'+'\n'.join(text.split('\n',2)[2].replace('void f(',f'void f{i}(',1) for i,text in enumerate(choices))
    calls='\n'.join(f'f{i}(n,x,y,&out); if(out.value!=ref.value) return {i};' for i in range(1,len(choices)))
    program+='\nint main(void){unsigned int n,x,y; Result ref,out; for(n=0;n<2;n++)for(x=0;x<24;x++)for(y=0;y<24;y++){f0(n,x,y,&ref);'+calls+'}return 0;}'
    path=tmp_path/'check.c';path.write_text(program);exe=tmp_path/'check'
    subprocess.run([cc,'-O2',str(path),'-o',str(exe)],check=True,capture_output=True,timeout=30)
    subprocess.run([str(exe)],check=True,capture_output=True,timeout=10)


def test_draft_alternatives_keep_all_originals_and_total_cap():
    inputs=[('raw',SOURCE),('second',SOURCE+'\n'),('third',SOURCE+'\n\n')]
    choices=list(repair.machine_drafts(inputs,'f','','mips'))
    assert [v for v in choices if v[0] in {'raw','second','third'}]==inputs
    assert len(choices)<=len(inputs)+2
    assert choices[0]==inputs[0]
    assert any(kind=='m2c_branch_packing_tail' for kind,_ in choices)


def test_frontier_reserves_distinct_measured_source_layout():
    from decompnet.app.search_frontier import WIDTH, Frontier, source_layout
    baseline=[('r','move $a0, $a1','move $v0, $a1')]*8
    f=Frontier()
    f.offer('void f(){if(x){a();}else{b();} c();}',10,baseline,baseline=baseline,baseline_score=10)
    alternative='void f(){if(x){a();c();}else{b();c();} do{x++;}while(0);}'
    f.offer(alternative,25,baseline,baseline=baseline,baseline_score=10)
    for i in range(12):
        f.offer('void f(){if(x){a();}else{b();} c();} /*'+str(i)+'*/',11+i,baseline,baseline=baseline,baseline_score=10)
    assert alternative in {n['source'] for n in f.nodes}
    assert len(f.nodes)<=WIDTH
    assert source_layout('void f(){ /* goto short */ }')==source_layout('void f(){}')


def test_targeted_lane_resumes_and_does_not_hide_ordinary_search():
    target=['addiu $sp, $sp, -8']+['addu $a0, $a1, $v1']*14+['addiu $sp, $sp, 8']
    rows=[(' ',op,op) for op in target]
    rows[5]=('r',target[5],'addu $v0, $a1, $v1')
    selection={};actual=[];expected=repair.variants(SOURCE,'f','','mips')
    assert repair.applicable(rows,'mips')
    for _ in range((len(expected)+2)//3):
        page=iterative_repairs.targeted_variants(SOURCE,{'rows':rows},{'name':'f','asm':''},'mips',[],tagged=True,selection=selection,limit=3)
        actual.extend(page);selection=json.loads(json.dumps(selection))
    assert actual==expected
    later=iterative_repairs.targeted_variants(SOURCE,{'rows':rows},{'name':'f','asm':''},'mips',[],tagged=True,selection=selection,limit=3)
    assert any(family!=repair.FAMILY for family,_ in later)
