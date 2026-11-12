"""Homemade source shapes; no extracted game source or target bytes."""
import hashlib

import pytest

from decompnet.app import allocation_targeting, draft_repairs, repair_ranking, search_frontier
from decomprepair import narrow_store_repairs as repairs

HEADER='typedef signed short s16; typedef signed int s32; typedef unsigned short u16;'
SOURCE='''typedef struct { s16 x; s16 y; u16 w; u16 h; } Box;
typedef struct { s32 id; } Entry;
extern Box bounds;
void f(Box *p, Entry *entry) {
 s32 x = bounds.x;
 s32 w;
 s32 half;
 s32 y;
 s32 height;
 w = (s16)bounds.w;
 half = w / 2;
 y = bounds.y;
 height = (s16)bounds.h;
 entry->id = 2;
 p->x = x;
 p->y = (height = y + height + 1);
 p->w = w;
 p->h = height / 2;
 p->x = half;
 p->x = x + p->x;
 p->x = p->x + 1;
}'''
# The assignment's value must really be dead; use the previous height independently.
DEAD=SOURCE.replace('p->h = height / 2;', 'p->h = 4;')


def candidates(source=DEAD, arch='mips'):
    return repairs.variants(source,'f',HEADER,arch)


def test_dead_capture_hoists_only_over_independent_stores_and_keeps_conversion():
    out=[s for f,s in candidates() if f==repairs.FAMILIES[0]]
    assert out
    assert 's16 __repair_next_field = y + height + 1;' in out[0]
    assert out[0].index('s16 __repair_next_field')<out[0].index('entry->id = 2;')
    assert 'p->y = __repair_next_field;' in out[0]
    assert 'p->h = 4;' in out[0]
    assert not [s for f,s in candidates(SOURCE) if f==repairs.FAMILIES[0]]


@pytest.mark.parametrize('tail', ['while (y--) { use(height); }', 'goto again; again: use(height);',
                                  'switch (y) { case 2: use(height); break; }'])
def test_cfg_read_after_assignment_is_not_dead(tail):
    source=DEAD.replace(' p->w = w;',tail+'\n p->w = w;')
    assert not [s for f,s in candidates(source) if f==repairs.FAMILIES[0]]


@pytest.mark.parametrize('change',[
 lambda s:s.replace('s32 height;', 'volatile s32 height;'),
 lambda s:s.replace(' height = (s16)bounds.h;', ' escape(&height); height = (s16)bounds.h;'),
 lambda s:s.replace('y + height + 1', 'load() + height'),
 lambda s:s.replace('s16 y;', 'volatile s16 y;'),
 lambda s:s.replace('typedef struct { s16 x;', 'typedef union { s16 x;'),
 lambda s:'#define height other\n'+s,
 lambda s:'#define y read_y()\n'+s,
 lambda s:s.replace('s32 y;', 'float y;'),
])
def test_opaque_qualified_or_escaped_inputs_have_no_capture(change):
    assert not [s for f,s in candidates(change(DEAD)) if f==repairs.FAMILIES[0]]


def test_unknown_arch_and_relevant_type_macros_are_rejected():
    assert not candidates(arch='unknown')
    assert not repairs.variants(DEAD,'f',HEADER+'\n#define s16 weird','mips')


def test_capture_does_not_cross_calls_or_scalar_writes():
    for stmt in ['emit();','y = 3;']:
        source=DEAD.replace(' entry->id = 2;',stmt+'\n entry->id = 2;')
        out=[s for f,s in candidates(source) if f==repairs.FAMILIES[0]][0]
        assert out.index(stmt)<out.index('s16 __repair_next_field')


def test_fold_preserves_intermediate_narrowing_and_proves_signed_add_bounds():
    out=[s for f,s in candidates() if f==repairs.FAMILIES[1]]
    assert out and 'p->x = (s16)(x + half) + 1;' in out[0]
    for source in [DEAD.replace('s32 x = bounds.x;', 's32 x = unknown();'),
                   DEAD.replace('half = w / 2;', 'half = w / 2; half = unknown();'),
                   DEAD.replace('half = w / 2;', 'half = w / 2; half <<= 2;'),
                   DEAD.replace('s32 x = bounds.x;', 's32 x = 2147483647;'),
                   DEAD.replace('p->x = x + p->x;', 'p->x = load() + p->x;')]:
        assert not [s for f,s in candidates(source) if f==repairs.FAMILIES[1]]
    # Signed halfword wrap is intentionally still an explicit conversion in generated C.
    narrow=lambda v:((v+32768)%65536)-32768
    for x in [-32768,-1,0,32767]:
        for w in [-32768,-1,0,32767]:
            half=int(w/2)
            before=narrow(narrow(x+narrow(half))+1)
            after=narrow(narrow(x+half)+1)
            assert before==after


def test_pair_and_shared_generation_are_bounded_and_draft_original_is_retained():
    out=candidates()
    assert out[0][0]=='narrow_store_sequence'
    assert '__repair_next_field' in out[0][1] and 'p->x = (s16)(x + half) + 1;' in out[0][1]
    drafts=list(repairs.machine_drafts([('raw',DEAD),('other',DEAD+'\n')],'f',HEADER,'mips'))
    assert drafts[0]==('raw',DEAD) and ('other',DEAD+'\n') in drafts
    assert len(drafts)<=5
    labels={}
    generated=draft_repairs.variants(DEAD,'f','mips','',labels=labels,header=HEADER,limit=None)
    assert all(f in draft_repairs.AGENT_FAMILIES for f in repairs.FAMILIES)
    assert out[0][1] in generated and labels[out[0][1]]=='narrow_store_sequence'


def test_register_and_scheduling_rows_prioritize_typed_store_repairs():
    rows=[('r','addu $s1, $s2, $s3','addu $s4, $s2, $s3')]
    order=repair_ranking.order(['local_width',*repairs.FAMILIES],rows)
    assert order.index('dead_assignment_narrow_capture')<order.index('local_width')


def test_copy_removal_keeps_bounded_register_only_sibling():
    baseline=[(' ','addiu $sp, $sp, -32','addiu $sp, $sp, -32'),
              ('r','move $s1, $a0','move $s2, $a0'),('>','','move $s3, $s0')]
    alternative=baseline[:2]
    assert search_frontier.allocation_bridge(baseline,alternative,'mips')
    frontier=search_frontier.Frontier()
    frontier.offer('numeric',100,baseline,arch='mips',baseline=baseline,baseline_score=100)
    frontier.offer('bridge',150,alternative,arch='mips',baseline=baseline,baseline_score=100)
    for i in range(20):frontier.offer(str(i),160+i,[('|','nop','changed')]*10,arch='mips',baseline=baseline,baseline_score=100)
    assert {'numeric','bridge'}<={n['source'] for n in frontier.nodes}
    assert len(frontier.nodes)<=search_frontier.WIDTH


def test_tied_allocation_priorities_rank_real_source_captures():
    line=next(i for i,s in enumerate(DEAD.splitlines(),1) if 'p->y = (height' in s)
    rows=[('r','move $s4, $a0','move $s5, $a0'),('r','move $s5, $a1','move $s4, $a1')]
    evidence={'allocation':{'values':[dict(pseudo=p,aliases=[a],references=3,live_insns=42,
                                             legacy_weight=3/42,source_lines=[line]) for p,a in [(70,'s4'),(71,'s5')]]},
              'value_trace':dict(values=[dict(aliases=['s4'],source_lines=[line])],
                  source_sha256=hashlib.sha256(DEAD.encode()).hexdigest(),header_sha256=hashlib.sha256(HEADER.encode()).hexdigest())}
    points=allocation_targeting.targets(DEAD,HEADER,rows,evidence)
    assert points and points[0]['tied_priorities']==1
    proposed=allocation_targeting.proposals(DEAD,'f',HEADER,rows,evidence)
    assert proposed and proposed[0][0] in repairs.FAMILIES
    assert 'tied competing priorities' in allocation_targeting.brief(DEAD,HEADER,rows,evidence)


@pytest.mark.parametrize('arch',['mips','ppc','x86','arm','thumb'])
def test_shared_32_bit_c_path(arch):
    assert candidates(arch=arch)[0][0]=='narrow_store_sequence'


def test_generated_pair_preserves_boundary_values_in_compiled_c(tmp_path):
    import shutil
    import subprocess

    from decompnet import limits
    from decompnet.forks.csource import find

    cc=shutil.which('cc')
    if not cc:pytest.skip('needs a native C compiler')
    transformed=candidates()[0][1]
    a,b=find(DEAD,'f');c,d=find(transformed,'f')
    code=HEADER+'\n#include <string.h>\n'+DEAD[:a].replace('extern Box bounds;','Box bounds;')
    code+=DEAD[a:b].replace('void f(', 'void before(')+'\n'
    code+=transformed[c:d].replace('void f(', 'void after(')+'\n'
    code+='''int main(void) {
 short values[] = {-32768,-32767,-16384,-1,0,1,16383,32766,32767};
 int i,j,k;
 for(i=0;i<9;i++)for(j=0;j<9;j++)for(k=0;k<9;k++) {
   Box a={0}, b={0}; Entry x={0}, y={0};
   bounds.x=values[i]; bounds.y=values[j]; bounds.w=(u16)values[k]; bounds.h=(u16)values[i];
   before(&a,&x); after(&b,&y);
   if(memcmp(&a,&b,sizeof a)||x.id!=y.id)return 1;
 }
 return 0;
}'''
    source=tmp_path/'boundary.c';exe=tmp_path/'boundary';source.write_text(code)
    subprocess.run(limits.capped([cc,'-O2',str(source),'-o',str(exe)]),check=True,capture_output=True,timeout=15)
    subprocess.run([str(exe)],check=True,timeout=5)


def test_new_draft_rules_invalidate_completed_miss_cache(monkeypatch):
    from decompnet.app import draft_reports
    fn={'target':b'abc','relocs':'[]'}
    previous=draft_reports.key('plan','header','data',fn)
    monkeypatch.setattr(draft_reports,'REPAIR_REVISION',draft_reports.REPAIR_REVISION+1)
    assert draft_reports.key('plan','header','data',fn)!=previous


def test_hidden_macro_reads_and_parenthesized_updates_are_not_proved_dead_or_bounded():
    hidden='#define OBSERVE use(height)\n'+DEAD.replace(' p->w = w;', ' OBSERVE; p->w = w;')
    assert not [s for f,s in candidates(hidden) if f==repairs.FAMILIES[0]]
    for extra in ['(half) <<= 20;', '++(half);', 'SHIFT(half);', 'HIDDEN;']:
        text='#define SHIFT(v) ((v) <<= 20)\n#define HIDDEN half = 100000\n'+DEAD.replace('half = w / 2;','half = w / 2; '+extra)
        assert not [s for f,s in candidates(text) if f==repairs.FAMILIES[1]]
