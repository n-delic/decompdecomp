import shutil
import subprocess
import time

import pytest

from decompnet.app import draft_repairs, repair_benchmark, repair_ranking
from decompnet.app import snapshot_branch_repairs as repair

HEADER = 'typedef unsigned int u32; typedef unsigned short u16; typedef unsigned char u8;'
SOURCE = '''typedef struct State { u16 x; u16 y; u16 mode; } State;
static inline u16 get_x(State *p) { return p->x; }
static inline u16 get_y(State *p) { return p->y; }
State *state; u32 result;
void f(void) {
    State *p;
    u32 a;
    u32 b;
    u8 saved_a;
    u8 saved_b;
    p=state;
    a=get_x(p);
    saved_a=(u8)a;
    b=get_y(p);
    p->mode=1;
    saved_b=(u8)b;
    if ((((u16)a)==2) && (((u16)b)==4)) {
        p->x=5; p->y=2;
    } else {
        if (p->x==3 && p->y==5) { p->x=5; p->y=0; }
    }
    result=(p->x<<16)|p->y;
    p->x=saved_a; p->y=saved_b;
}'''
ROWS = [('<','andi $v0, $v0, 0xffff',''),('<','andi $v1, $a1, 0xffff','')]


def test_complete_recipe_is_bounded_and_integrated_without_intermediate_wins():
    choices=repair.variants(SOURCE,'f',HEADER,'mips')
    assert len(choices)==1 and choices[0][0]==repair.FAMILY
    candidate=choices[0][1]
    assert 'unsigned short a;' in candidate and 'unsigned short b;' in candidate
    assert 'a = (unsigned short)(p->x);' in candidate
    assert 'if ((unsigned short)a != 2) goto' in candidate
    assert 'if ((unsigned short)b != 4) goto' in candidate
    assert candidate.count('saved_a=(u8)a;')==1 and 'p->x=saved_a;' in candidate
    assert not repair.variants(candidate,'f',HEADER,'mips')
    labels={}
    generated=draft_repairs.variants(SOURCE,'f','mips',header=HEADER,labels=labels,limit=None)
    assert candidate in generated and labels[candidate]==repair.FAMILY
    assert repair.FAMILY in draft_repairs.AGENT_FAMILIES
    assert repair.variants(SOURCE.replace('u32 a;', 'u16 a;').replace('u32 b;', 'u16 b;'),'f',HEADER,'mips')


@pytest.mark.parametrize('before,after',[
    ('u32 a;', 'volatile u32 a;'),
    ('u32 a;', 'u32 a; u32 *escape=&a;'),
    ('saved_a=(u8)a;', 'saved_a=(u8)a; result=a;'),
    ('saved_a=(u8)a;', 'saved_a=(u8)a; a++;'),
    ('return p->x;', 'p->mode++; return p->x;'),
    ('static inline u16 get_x', 'static inline u32 get_x'),
    ('a=get_x(p);', 'a=get_x(p); a=get_x(p);'),
    ('p=state;', 'p=state; SIDE_EFFECT();'),
    ('((u16)b)==4', '((u16)b)==70000'),
    ('u32 a;', 'u32 a; { u32 a; a=0; }'),
])
def test_unsafe_or_unsupported_shapes_are_not_proposed(before,after):
    header=HEADER+'\n#define SIDE_EFFECT() inspect(a)\n'
    assert not repair.variants(SOURCE.replace(before,after),'f',header,'mips')


def test_missing_mask_diagnosis_prioritizes_without_dropping_other_families():
    assert repair.missing_masks(ROWS)==2
    families=['declaration_order',repair.FAMILY,'local_width']
    ordered=repair_ranking.order(families,ROWS)
    assert ordered[0]==repair.FAMILY and set(ordered)==set(families)
    diagnosis=repair_benchmark.diagnostics({'rows':ROWS},'mips')
    assert diagnosis['missing_halfword_masks']==2
    assert repair.FAMILY in diagnosis['normalization']['preferred_families']
    assert not repair.diagnosis([(' ','andi $v0, $v0, 0xffff','andi $v0, $v0, 0xffff')])


def test_exact_link_verdict_stops_even_with_nonzero_advisory_score():
    calls=[]
    def evaluate(source):
        calls.append(source)
        return {'score':300 if source=='before' else 20,'matched':source!='before','rows':ROWS,'outcome':'exact' if source!='before' else 'miss'}
    report=repair_benchmark.run('before',300,evaluate,lambda _: [(repair.FAMILY,'exact'),('other','never')],
                                trials=8,deadline=time.monotonic()+5,arch='mips')
    assert report['first_exact_trial']==1 and calls==['before','exact']
    assert report['reason']=='control_output_recovered'


def test_rewrite_preserves_boundary_values_and_fallback_effects(tmp_path):
    cc=shutil.which('clang') or shutil.which('cc')
    if not cc:pytest.skip('needs a host C compiler for synthetic semantic replay')
    candidate=repair.variants(SOURCE,'f',HEADER,'mips')[0][1]
    harness='''
int main(void) {
    unsigned values[]={0,1,2,3,4,5,255,256,32767,32768,65535};
    State s; unsigned i,j;
    state=&s;
    for(i=0;i<11;i++) for(j=0;j<11;j++) {
        unsigned a=values[i],b=values[j],x=a,y=b;
        s.x=a;s.y=b;s.mode=0;
        if(a==2 && b==4) {x=5;y=2;}
        else if(a==3 && b==5) {x=5;y=0;}
        f();
        if(result!=((x<<16)|y) || s.x!=(a&255) || s.y!=(b&255) || s.mode!=1) return 1;
    }
    return 0;
}'''
    path=tmp_path/'case.c';exe=tmp_path/'case'
    path.write_text(HEADER+'\n'+candidate+harness)
    subprocess.run([cc,'-std=gnu99','-O2',str(path),'-o',str(exe)],check=True,capture_output=True)
    subprocess.run([str(exe)],check=True)


def test_mask_recovery_retains_one_bounded_worse_sibling_without_adopting_it():
    from decompnet.app import iterative_repairs, search_frontier
    frame=(' ','addiu $sp, $sp, -0x20','addiu $sp, $sp, -0x20')
    before=[frame,*ROWS,(' ','jr $ra','jr $ra')]
    after=[frame,('r','andi $v0, $v0, 0xffff','andi $a0, $v1, 0xffff'),
           ('r','andi $v1, $a1, 0xffff','andi $v1, $v0, 0xffff'),(' ','jr $ra','jr $ra')]
    assert search_frontier.mask_recovery(before,after,'mips')
    node={'ceiling':300,'baseline_rows':before,'path':['local_width']}
    assert iterative_repairs.retention_evidence(node,{'score':705,'rows':after},'mips')['eligible']
    assert not iterative_repairs.retention_evidence(node,{'score':1201,'rows':after},'mips')['eligible']
    frontier=search_frontier.Frontier()
    frontier.offer('winner',300,before,arch='mips')
    for i in range(10):frontier.offer('other'+str(i),301+i,before,arch='mips',baseline=before,baseline_score=300)
    frontier.offer('masks',705,after,arch='mips',baseline=before,baseline_score=300)
    assert len(frontier.nodes)<=search_frontier.WIDTH
    assert frontier.nodes[0]['source']=='winner' and any(n['source']=='masks' for n in frontier.nodes)
    grown=[('r',frame[1],'addiu $sp, $sp, -0x28'),*after[1:]]
    assert not search_frontier.mask_recovery(before,grown,'mips')
    assert not search_frontier.mask_recovery(before,after,'arm')
