"""Homemade stores and displayed frame patterns; private game evidence stays outside Git."""
import json
import shutil
import subprocess

import pytest

from decompnet.app import (
    allocation_evidence,
    draft_repairs,
    frame_plateau,
    iterative_repairs,
    repair_ranking,
    search_frontier,
)
from decomprepair import halfword_store_repairs

HEADER='typedef unsigned char u8; typedef unsigned short u16; typedef short s16; typedef int s32;'
SOURCE='''#define H(p,o) (*(u16 *)((u8 *)(p)+(o)))
void f(u8 *node) { u16 off; off=50000; H(node, 8) = off; }
'''


def frame_rows(gap=8,local_gap=0):
    rows=[('s' if gap else ' ','addiu $sp, $sp, -0x60',f'addiu $sp, $sp, -{96-gap}'),
          ('s' if gap else ' ','sw $ra, 0x5c($sp)',f'sw $ra, {92-gap}($sp)'),
          ('s' if gap else ' ','sw $s0, 0x58($sp)',f'sw $s0, {88-gap}($sp)')]
    rows += [(' ','addu $s0, $a0, $zero','addu $s0, $a0, $zero')]*34
    rows += [('s' if local_gap else ' ','sw $t0, 0x14($sp)',f'sw $t0, {20+local_gap}($sp)'),
             ('s' if local_gap else ' ','lw $t0, 0x14($sp)',f'lw $t0, {20+local_gap}($sp)')]
    rows += [(' ','addu $v0, $a1, $zero','addu $v0, $a1, $zero')]*34
    rows += [('s' if gap else ' ','lw $s0, 0x58($sp)',f'lw $s0, {88-gap}($sp)'),
             ('s' if gap else ' ','lw $ra, 0x5c($sp)',f'lw $ra, {92-gap}($sp)'),
             ('s' if gap else ' ','addiu $sp, $sp, 0x60',f'addiu $sp, $sp, {96-gap}')]
    return rows


def test_proven_macro_store_gets_one_real_narrowing():
    sites=halfword_store_repairs.sites(SOURCE,'f',HEADER,'mips')
    assert len(sites)==1 and sites[0]['value']=='off' and sites[0]['lhs']=='H(node, 8)'
    choices=halfword_store_repairs.variants(SOURCE,'f',HEADER,'mips')
    assert len(choices)==1 and choices[0][0]=='halfword_store_normalization'
    text=choices[0][1]
    assert 'H(node, 8) = __repair_store_halfword(off);' in text
    assert text.count('H(node, 8)')==1
    assert not halfword_store_repairs.variants(text,'f',HEADER,'mips')
    labels={}
    draft_repairs.variants(SOURCE,'f','mips',header=HEADER,labels=labels,limit=None)
    assert 'halfword_store_normalization' in labels.values()


def test_width_resolution_for_fields_and_explicit_cast_stores():
    for store in ['*(u16 *)node = off;', '(*(u16 *)(node+8)) = off;']:
        assert halfword_store_repairs.variants(SOURCE.replace('H(node, 8) = off;',store),'f',HEADER,'mips')
    field='typedef struct { u16 half; } Record; void f(Record *p) { u16 off; off=50000; p->half=off; }'
    assert halfword_store_repairs.variants(field,'f',HEADER,'mips')
    for arch in ['mips','ppc','x86','arm','thumb']:
        assert halfword_store_repairs.variants(SOURCE,'f',HEADER,arch)
    assert not halfword_store_repairs.variants(SOURCE,'f',HEADER,'unknown')


@pytest.mark.parametrize('before,after',[
    ('u16 off;', 'volatile u16 off;'), ('u16 off;', 'u32 off;'),
    ('u16 off;', 'u16 off; use(&off);'), ('u16 off;', 'u16 off; use(&(off));'),
    ('= off;', '= off++;'), ('= off;', '= read_value();'),
    ('#define H(p,o) (*(u16 *)((u8 *)(p)+(o)))','#define H(p,o) (touch(), *(u16 *)((u8 *)(p)+(o)))'),
    ('#define H(p,o) (*(u16 *)((u8 *)(p)+(o)))','#define H(p,o) (*(u16 *)((u8 *)(p)+(o)+(o)))'),
    ('#define H(p,o) (*(u16 *)((u8 *)(p)+(o)))','#define H(p,o) (*(u8 *)((u8 *)(p)+(o)))'),
    ('H(node, 8) = off;', 'if(ready) { u8 off; off=7; H(node, 8) = off; }'),
])
def test_unknown_qualified_escaped_or_effectful_shapes_decline(before,after):
    assert not halfword_store_repairs.variants(SOURCE.replace(before,after),'f',HEADER,'mips')


def test_strict_frame_pattern_and_priority():
    rows=frame_rows()
    assert frame_plateau.gap(rows,'mips')==8
    assert not frame_plateau.gap(rows,'arm')
    assert allocation_evidence.diagnose(rows,'mips')['frame_only_gap']==8
    assert repair_ranking.order(['stack_padding','local_width','halfword_store_normalization'],rows)[0]=='halfword_store_normalization'
    assert allocation_evidence.plan(rows,'mips')['families'][0]=='halfword_store_normalization'
    for bad in [frame_rows(local_gap=8), frame_rows(0), rows+[('i','lui $v0, %hi(unknown)','lui $v0, %hi(other)')],
                rows+[('r','move $s0, $a0','move $s1, $a0')],rows+[('>','','nop')],
                rows[:-1]+[('s','addiu $sp, $sp, 0x60','addiu $sp, $sp, 80')]]:
        assert not frame_plateau.gap(bad,'mips')


def test_body_correct_alternative_survives_score_regression_and_crowding():
    frontier=search_frontier.Frontier();before=frame_rows(0,32);after=frame_rows()
    frontier.offer('numeric',2,before,arch='mips',baseline=before,baseline_score=2)
    frontier.offer('body-correct',22,after,arch='mips',baseline=before,baseline_score=2)
    for i in range(12):frontier.offer('noise'+str(i),3+i,before,arch='mips',baseline=before,baseline_score=2)
    assert {'numeric','body-correct'}<={n['source'] for n in frontier.nodes}
    assert len(frontier.nodes)<=search_frontier.WIDTH


def test_iterative_retains_body_correct_root_across_budget_resume():
    before,after=frame_rows(0,32),frame_rows()
    state=dict(pending=[dict(source='seed',depth=0,ceiling=2)],next=[],seen=['seed'])
    calls=[]
    def evaluate(source):
        calls.append(source)
        return {'score':{'seed':2,'body':22,'done':0}[source],'matched':source=='done',
                'rows':after if source=='body' else before}
    variants=lambda source,report:{'seed':[('declaration_order','body')],'body':[('halfword_store_normalization','done')]}[source]
    assert not iterative_repairs.advance(state,evaluate,variants,lambda:len(calls)>=2,arch='mips')
    state=json.loads(json.dumps(state))
    assert iterative_repairs.advance(state,evaluate,variants,lambda:False,arch='mips')
    assert calls==['seed','body','done']


def test_compact_guidance_has_real_sites_and_no_compiler_work():
    brief=frame_plateau.brief(SOURCE,'f',HEADER,frame_rows(),'mips')
    assert '8 bytes too small' in brief and 'H(node, 8) = off' in brief
    assert 'halfword_store_normalization' in brief and len(brief)<1200
    assert not frame_plateau.brief(SOURCE,'f',HEADER,frame_rows(0,32),'mips')


def test_compiled_store_preserves_every_unsigned_halfword_and_other_fields(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('No host compiler')
    candidate=halfword_store_repairs.variants(SOURCE.replace('void f(u8 *node)','void f(u8 *node, u16 input)').replace('off=50000','off=input'),'f',HEADER,'mips')[0][1]
    support='''int main(void) { unsigned int n; union { unsigned short align; unsigned char b[16]; } x;
    for(n=0;n<65536;n++) { int i; for(i=0;i<16;i++)x.b[i]=0xA5; f(x.b,(u16)n);
      if(*(u16 *)(x.b+8)!=(u16)n)return 1;
      for(i=0;i<16;i++)if(i!=8 && i!=9 && x.b[i]!=0xA5)return 2;
    } return 0; }'''
    path=tmp_path/'test.c';path.write_text(HEADER+'\n'+candidate+support)
    binary=tmp_path/'test'
    subprocess.run([cc,'-O2','-std=gnu89',str(path),'-o',str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True,timeout=10)


@pytest.mark.parametrize('imported',[False,True])
def test_deep_short_lane_matches_before_broad_generation(monkeypatch,imported):
    from decompnet.app import deep_permuter
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import Log, setup
    from tests.test_permute import AGENT

    net=setup(monkeypatch,imported)
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=22 WHERE id=1',(SOURCE,))
    net._context=lambda *a:{'header':HEADER,'version':1}
    net.forks.permuter_header=lambda *a:HEADER
    net._last_diffs={(AGENT.id,1):(frame_rows(),'')}
    monkeypatch.setattr(draft_repairs,'variants',lambda *a,**k:pytest.fail('Do not generate a broad pool before the frame lane'))
    seen=[]
    def judge(self,agent,proj,fn,source,best,fork):
        seen.append(source)
        assert 'H(node, 8) = __repair_store_halfword(off);' in source
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source=?,best_score=0,status='matched' WHERE id=1",(source,))
        return 0
    monkeypatch.setattr(Permuter,'_score',judge)
    search=deep_permuter.Search(net,1,1,Log(),net.function())
    result=search.local_repairs(AGENT,net.project_row('game'),net.function(),22,'fork' if imported else None)
    assert result[0]==0 and len(seen)==1 and search.tested==1


def test_iterative_frame_page_precedes_broad_pool_and_resumes(monkeypatch):
    fn={'name':'f','asm':''};selection={}
    monkeypatch.setattr(iterative_repairs,'targeted_pool',lambda *a,**k:[('other','ordinary')])
    first=iterative_repairs.targeted_variants(SOURCE,{'rows':frame_rows()},fn,'mips',[],HEADER,tagged=True,selection=selection,limit=1)
    assert len(first)==1 and first[0][0]=='halfword_store_normalization'
    second=iterative_repairs.targeted_variants(SOURCE,{'rows':frame_rows()},fn,'mips',[],HEADER,tagged=True,selection=selection,limit=1)
    assert second==[('other','ordinary')]
    omitted=iterative_repairs.targeted_variants(SOURCE,{'rows':frame_rows()},fn,'mips',[],HEADER,tagged=True,selection={},omit_families=('halfword_store_normalization',))
    assert omitted==[('other','ordinary')]


def test_search_notes_keep_numeric_and_body_roots_and_expire_with_inputs(monkeypatch):
    import hashlib

    from decompnet.app import repair_ranking, search_evidence
    from tests.test_permute import AGENT, FakeNet

    net=FakeNet();proj=net.project_row('game')
    with net.store.tx() as db:db.execute('UPDATE functions SET name=?,best_source=?,best_score=2 WHERE id=1',('f',SOURCE))
    net._context=lambda *a:{'header':HEADER,'version':1}
    fn=net.function()
    monkeypatch.setattr(search_evidence.experiment_evidence,'build_scope',lambda *a:'inputs-v1')
    rows=[frame_rows(0,32)]
    monkeypatch.setattr(repair_ranking,'evidence',lambda *a:rows[0])
    search_evidence.remember(net,AGENT,proj,fn,SOURCE,2)
    rows[0]=frame_rows()
    alternative=SOURCE.replace('off=50000','off=40000')
    search_evidence.remember(net,AGENT,proj,fn,alternative,22)
    rows[0]=frame_rows(0,32)
    for i in range(12):search_evidence.remember(net,AGENT,proj,fn,SOURCE+' '+str(i),3+i)
    pool=net._search_evidence[1,'inputs-v1']
    assert len(pool)==8 and hashlib.sha256(SOURCE.encode()).hexdigest() in pool
    assert hashlib.sha256(alternative.encode()).hexdigest() in pool
    # Keep this test on the bounded source-history portion of the brief.
    monkeypatch.setattr(search_evidence.region_repairs,'summary',lambda *a:'')
    monkeypatch.setattr('decompnet.app.allocation_search.brief',lambda *a:'')
    text=search_evidence.brief(net,proj,fn)
    assert 'Historical root SHA256' in text and 'H(node, 8) = off' in text
    assert 'halfword_store_normalization' in text
    pool[hashlib.sha256(SOURCE.encode()).hexdigest()]['score']=0
    assert 'Historical root SHA256' not in search_evidence.brief(net,proj,fn)
    monkeypatch.setattr(search_evidence.experiment_evidence,'build_scope',lambda *a:'inputs-v2')
    assert 'Historical root SHA256' not in search_evidence.brief(net,proj,fn)
