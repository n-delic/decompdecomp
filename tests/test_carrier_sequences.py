"""Handmade fixtures; no game source or ROM in the test suite."""
import json

from decompnet.app import carrier_sequence_repairs as repair
from decompnet.app import iterative_repairs

SOURCE='''void f(int input, unsigned short *left, unsigned short *right, short *out) {
 int product; int prev; int next;
 short d; unsigned short *p; unsigned short *q;
 product=input*3; input=product+7; prev=input-2; next=input+2;
 do { p=left; d=*p; q=right+next; d-=*q; } while(0);
 out[0]=d; out[1]=input; out[2]=prev; out[3]=next;
}'''


def rows():
    target=['addiu $sp, $sp, -8']+['addu $a0, $a1, $v1']*13+['lhu $t0, ($a2)','lhu $t1, 2($a2)']+['addiu $sp, $sp, 8']
    result=[(' ',s,s) for s in target]
    result[14]=('o',target[14],target[15]);result[15]=('o',target[15],target[14])
    return result


def test_composes_from_earlier_scope_before_narrowing_and_carriers():
    choices=repair.variants(SOURCE,'f','','mips')
    assert choices and len(choices)<=repair.LIMIT
    assert all('while(0)' not in text and 'd = *p - *q;' in text for _,text in choices)
    assert any('prev=product+7' in text.replace(' ','') and 'input=prev+2' in text.replace(' ','') for _,text in choices)
    assert all(text.startswith('void f(int input, unsigned short *left, unsigned short *right, short *out)') for _,text in choices)
    assert not repair.variants(SOURCE.replace('q=right+next;','q=right+next; *out=9;'),'f','','mips')
    assert not repair.variants(SOURCE.replace('short d;','volatile short d;'),'f','','mips')


def test_targeted_prefix_resumes_and_leaves_broad_search_available():
    expected=repair.variants(SOURCE,'f','','mips');selection={};result=[]
    assert repair.applicable(rows(),'mips')
    for _ in range((len(expected)+2)//3):
        page=iterative_repairs.targeted_variants(SOURCE,{'rows':rows()},{'name':'f','asm':''},'mips',[],tagged=True,selection=selection,limit=3)
        result.extend(page);selection=json.loads(json.dumps(selection))
    assert result==expected
    assert iterative_repairs.targeted_variants(SOURCE,{'rows':rows()},{'name':'f','asm':''},'mips',[],tagged=True,selection=selection,limit=3)
    assert not repair.applicable([], 'mips')
    assert not repair.applicable(rows()+[('>','','nop')], 'mips')


def test_drafts_keep_originals_and_cap_alternatives_across_all_inputs():
    sources=[('raw',SOURCE),('second',SOURCE+'\n'),('third',SOURCE+'\n\n')]
    generated=list(repair.machine_drafts(sources,'f','','mips'))
    assert [item for item in generated if item[0] in {'raw','second','third'}]==sources
    assert len(generated)<=len(sources)+3
    assert generated[0]==sources[0]
    assert any(kind=='m2c_narrow_load_subtraction' for kind,_ in generated)


def test_ordinary_judge_controls_sequence_success_and_budget():
    choices=repair.variants(SOURCE,'f','','mips');exact=choices[-1][1];calls=[]
    state={'pending':[dict(source=SOURCE,depth=0,ceiling=240)],'next':[],'seen':[SOURCE]}
    def judge(source):
        calls.append(source)
        return {'score':0 if source==exact else 240,'matched':source==exact,'rows':rows()}
    def variants(source, report):
        return choices if source==SOURCE else []
    # Stop/resume is server-owned; no change to numeric adoption or match criteria.
    iterative_repairs.advance(state,judge,variants,lambda:len(calls)>=3,arch='mips')
    state=json.loads(json.dumps(state))
    assert iterative_repairs.advance(state,judge,variants,lambda:False,arch='mips')
    assert calls[-1]==exact and len(calls)<=len(choices)+1


def test_homemade_transfer_preserves_observable_results(tmp_path):
    import shutil
    import subprocess

    import pytest
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host C compiler unavailable')
    choices=[SOURCE]+[text for _,text in repair.variants(SOURCE,'f','','mips')]
    programs=[]
    for i,text in enumerate(choices):programs.append(text.replace('void f(',f'void f{i}(',1))
    tests='\n'.join(f'f{i}(n,a,b,out); for(j=0;j<4;j++) if(out[j]!=ref[j]) return {i+1};' for i in range(1,len(choices)))
    source='\n'.join(programs)+'''\nint main(void) {
 unsigned short a[128],b[128];short ref[4],out[4];int i,j,n;
 for(i=0;i<128;i++){a[i]=(unsigned short)(65000+i*3);b[i]=(unsigned short)(i*7);}
 for(n=0;n<16;n++){f0(n,a,b,ref);
'''+tests+'\n}return 0;}\n'
    path=tmp_path/'transfer.c';path.write_text(source)
    exe=tmp_path/'transfer'
    subprocess.run([cc,'-O2',str(path),'-o',str(exe)],check=True,capture_output=True,timeout=30)
    subprocess.run([str(exe)],check=True,capture_output=True,timeout=10)
