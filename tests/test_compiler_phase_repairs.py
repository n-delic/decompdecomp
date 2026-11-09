"""Homemade sources only; proprietary compiler/game inputs never enter this suite."""
import pytest

from decompnet.app import compiler_phase_repairs as repairs
from decompnet.app import layout_frontiers, search_frontier, targeted_debug_layout

LOOP='void f(void) { unsigned int i; unsigned int x; unsigned int y; i=0; do { x=i+3; y=i+4; use(x,y); i=i+1; } while(i<3); }'
ADDRESS='extern unsigned char table[]; void f(void) { unsigned int a; int b; unsigned short *p; a=2; b=3; p = (unsigned short *) ((table + 8) + ((a + b) * 2)); use(p); }'
# The reader deliberately handles one-word pointer type aliases, as real drafts do.
ADDRESS=ADDRESS.replace('unsigned short','word')


def test_latch_snapshots_are_cfg_dead_not_textually_dead():
    choices=repairs.loop_snapshots(LOOP,'f','','mips')
    assert choices and any('(y = x = i)' in s or '(x = y = i)' in s for _,s in choices)
    assert not any('(i = i)' in s for _,s in choices)
    # A use after the loop and a use before the next iteration's definition both matter.
    live=LOOP.replace('} while(i<3);','} while(i<3); use(x,y);')
    assert not repairs.loop_snapshots(live,'f','','mips')
    live=LOOP.replace('do { x=', 'do { use(x,y); x=')
    assert not repairs.loop_snapshots(live,'f','','mips')


@pytest.mark.parametrize('change',[
    lambda s:s.replace('unsigned int x;','volatile unsigned int x;'),
    lambda s:s.replace('use(x,y);','use(&x,&y);'),
    lambda s:s.replace('i<3','sizeof(x)<3'),
    lambda s:s.replace('i<3','i++<3'),
    lambda s:s.replace('i<3','i<3 && use(x,y)'),
    lambda s:'#define i shared\n'+s,
    lambda s:s.replace('unsigned int x;','unsigned short x;').replace('unsigned int y;','unsigned short y;'),
])
def test_latch_rejects_unknown_effects_and_conversions(change):
    assert not repairs.loop_snapshots(change(LOOP),'f','','mips')


def test_staging_keeps_association_conversion_and_lines():
    source='void f(void){unsigned int a;unsigned int b;unsigned int x;a=3;b=4;x=(a*8)+b;use(x);}'
    results=repairs.scalar_stages(source,'f','','mips')
    assert len(results)==2
    assert any('x = a * 8; x = x + b;' in s for _,s in results)
    assert any('x = a * 8;\nx = x + b;' in s for _,s in results)
    assert not repairs.scalar_stages(source.replace('x=(a*8)+b','x=(a*8)+x'),'f','','mips')
    assert not repairs.scalar_stages(source.replace('unsigned int x','unsigned short x'),'f','','mips')
    assert not repairs.scalar_stages(source.replace('a*8','a*8LL'),'f','','mips')


def test_explicit_word_conversion_and_or_stages_once():
    source='void f(double v){int x;x=((unsigned int)((int)v))|0x80000000U;use(x);}'
    results=repairs.scalar_stages(source,'f','','mips')
    assert results and all(s.count('((int)v)')==1 for _,s in results)
    assert not repairs.scalar_stages(source.replace('((int)v)','((int)read())'),'f','','mips')


def test_address_snapshot_preserves_mixed_unsigned_sum_and_byte_stride():
    results=repairs.address_snapshots(ADDRESS,'f','','mips')
    assert len(results)==1
    text=results[0][1]
    assert 'unsigned int __repair_unscaled_index;' in text
    assert text.index('__repair_unscaled_index = a + b;')<text.index('__repair_byte_table =')<text.index('__repair_byte_table + __repair_unscaled_index * 2')
    assert not repairs.address_snapshots(ADDRESS.replace('unsigned char table','int table'),'f','','mips')
    assert not repairs.address_snapshots(ADDRESS.replace('int b;','short b;'),'f','','mips')
    assert not repairs.address_snapshots(ADDRESS,'f','','unknown')
    assert not repairs.address_snapshots(ADDRESS.replace('use(p)','use(&p)'),'f','','mips')


def test_byte_pointer_order_is_32_bit_and_byte_only():
    source='void f(void){unsigned char *table;void *p;unsigned int i;i=2;p=(void *)(table+i);use(p);}'
    result=repairs.pointer_operands(source,'f','','mips')
    assert len(result)==1 and '(unsigned int)i + (unsigned int)table' in result[0][1]
    assert not repairs.pointer_operands(source.replace('unsigned char *table','int *table'),'f','','mips')
    assert not repairs.pointer_operands(source,'f','','x86')


def test_loop_constant_snapshot_caches_addresses_not_mutable_reads():
    source='extern unsigned char table[];extern const double limit;void f(void){unsigned int i;i=0;do{use(table+i,table+i+1,limit,limit);i=i+1;}while(i<3);}'
    choices=repairs.constant_snapshots(source,'f','','mips')
    assert choices
    assert any('__repair_loop_constant = table;' in s for _,s in choices)
    assert any('__repair_loop_constant = limit;' in s for _,s in choices)
    mutable=source.replace('const double','double').replace('table+i','other+i')
    assert not repairs.constant_snapshots(mutable,'f','','mips')
    volatile=source.replace('const double','const volatile double').replace('table+i','other+i')
    assert not repairs.constant_snapshots(volatile,'f','','mips')


def test_unmapped_layout_fallback_targets_late_dependent_arithmetic():
    source='void f(void){int x;int a;int *out;x=a*3;\nx=x*4;\n*out=x/2;}'
    rows=[('>','','nop')]
    choices=targeted_debug_layout.variants(source,'f','','mips',['-g3'],{'rows':rows})
    assert choices and any('x=a*3; x=x*4; *out=x/2;' in text for _,text in choices)
    assert all(layout_frontiers.equivalent(source,text) for _,text in choices)
    assert not targeted_debug_layout.variants(source,'f','','mips',['-g3','-g0'],{'rows':rows})
    assert not targeted_debug_layout.variants(source,'f','#define __LINE__ 2','mips',['-g3'],{'rows':rows})
    comment=source.replace(';\nx=x*4','; // keep me\nx=x*4')
    assert all('// keep me\nx=x*4' in text for _,text in targeted_debug_layout.variants(comment,'f','','mips',['-g3'],{'rows':rows}))


def test_pipeline_bridge_retains_recovered_tail_without_weakening_judge():
    frame=(' ','addiu $sp, $sp, -0x20','addiu $sp, $sp, -0x20')
    before=[frame]+[('r','addu $v0, $a0, $a1','addu $v1, $a0, $a1')]*12
    after=[frame]+[(' ','addu $v0, $a0, $a1','addu $v0, $a0, $a1')]*12+[('>','','nop'),('>','','nop')]
    assert search_frontier.pipeline_recovery(before,after,'mips')
    assert not search_frontier.pipeline_recovery(before,after+[('>','','nop')],'mips')
    assert not search_frontier.pipeline_recovery(before,after+[('>','','sw $v0, ($a0)')],'mips')
    assert not search_frontier.pipeline_recovery(before,after,'x86')
    frontier=search_frontier.Frontier()
    frontier.offer('best',100,before,arch='mips',baseline=before,baseline_score=100)
    frontier.offer('pipeline',300,after,arch='mips',baseline=before,baseline_score=100)
    assert {'best','pipeline'} <= {n['source'] for n in frontier.nodes}
    assert len(frontier.nodes)<=search_frontier.WIDTH


def test_dispatch_is_registered_and_complements_use_existing_budgets():
    from decompnet.app import draft_repairs, repair_selection, repair_sequence_lane
    assert set(repairs.FAMILIES)<=set(draft_repairs.AGENT_FAMILIES)
    assert set(repairs.FAMILIES)<=repair_sequence_lane.FAMILIES
    labels={}
    candidates=draft_repairs.variants(LOOP,'f','mips','',[],labels,limit=None)
    assert any(labels[c]=='loop_test_snapshot' for c in candidates)
    assert 'targeted_debug_layout' in repair_selection.COMPLEMENTS['staged_scalar_arithmetic']


def test_pipeline_detour_is_actually_expanded_and_stops_only_on_judged_exact():
    from decompnet.app import iterative_repairs as search
    frame=(' ','addiu $sp, $sp, -0x20','addiu $sp, $sp, -0x20')
    before=[frame]+[('r','addu $v0, $a0, $a1','addu $v1, $a0, $a1')]*12
    after=[frame]+[(' ','addu $v0, $a0, $a1','addu $v0, $a0, $a1')]*12+[('>','','nop'),('>','','nop')]
    state={'pending':[dict(source='seed',depth=0,ceiling=100)],'next':[],'seen':['seed']}
    calls=[]
    def evaluate(source):
        calls.append(source)
        return {'score':{'seed':100,'pipeline':300,'exact':0}[source],
                'rows':before if source=='seed' else after,
                'candidate_target':source,'matched':source=='exact'}
    def variants(source, report):
        return {'seed':[('staged_scalar_arithmetic','pipeline')],
                'pipeline':[('targeted_debug_layout','exact')]}.get(source,[])
    assert search.advance(state,evaluate,variants,lambda:len(calls)>8,arch='mips')
    assert calls==['seed','pipeline','exact']


@pytest.mark.parametrize('header', ['#define unsigned OPAQUE', 'typedef unsigned short u8;', 'typedef volatile unsigned char u8;'])
def test_introduced_keywords_and_byte_aliases_need_real_widths(header):
    source=ADDRESS.replace('unsigned char table','u8 table')
    assert not repairs.address_snapshots(source,'f',header,'mips')


def test_synthetic_variants_compile_with_the_pinned_compiler(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('Pinned compiler unavailable; never download during tests')
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc',
        'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}})
    sources=[LOOP,ADDRESS,'void f(void){unsigned int a;unsigned int b;unsigned int x;a=3;b=4;x=(a*8)+b;use(x);}',
             'extern unsigned char table[];extern const double limit;void f(void){unsigned int i;i=0;do{use(table+i,table+i+1,limit,limit);i=i+1;}while(i<3);}']
    families=set()
    for source in sources:
        for family,candidate in repairs.variants(source,'f','','mips'):
            result=compile_source(cfg,'typedef unsigned short word; void use();',candidate)
            assert result.ok,(family,result.log)
            families.add(family)
    assert {'loop_test_snapshot','staged_scalar_arithmetic','unscaled_address_snapshot','loop_constant_snapshot'}<=families


def test_cached_allocation_counts_route_predicates_without_early_constant_evidence():
    import hashlib

    from decompnet.app import compiler_phase_routing
    source='int f(void){return 1;}'
    trace={'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'header_sha256':hashlib.sha256(b'').hexdigest()}
    proposals=[('constant_local',source+' '),('inline_bit_predicate',source+'\n')]
    allocation={'values':[{'references':10,'live_insns':22}]}
    result=compiler_phase_routing.order(source,proposals,trace,allocation=allocation)
    assert result[0][0]=='inline_bit_predicate'
    assert compiler_phase_routing.order(source+' ',proposals,trace,allocation=allocation)==proposals
    assert compiler_phase_routing.order(source,proposals,trace,allocation={'truncated':True,'values':[{}]})==proposals
