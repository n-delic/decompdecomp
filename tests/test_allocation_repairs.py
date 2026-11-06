"""Homemade allocator-shape examples; no game source or expected game bytes."""
from decompnet.app import allocation_evidence as evidence
from decompnet.app import allocation_repairs as repairs
from decompnet.app import draft_repairs, repair_ranking, search_frontier


def test_mixed_pointer_scalar_declarations_and_combined_lifetime_bridge():
    source = 'void f(void) { int count; int *p; int n; int v; p=&v; use(p); use(p); }'
    proposals = repairs.variants(source,'f')
    assert {'spill_declaration_order','pointer_argument_alias','allocation_schedule_bridge'} <= {f for f,_ in proposals}
    alias = next(s for f,s in proposals if f=='pointer_argument_alias')
    assert 'p=&v;\n__repair_argument_pointer = p;' in alias
    assert alias.count('use(__repair_argument_pointer)')==2
    assert 'int * __repair_argument_pointer;' in alias
    assert len(proposals)<=repairs.LIMIT
    labels = {}
    draft_repairs.variants(source,'f','mips',labels=labels,limit=None)
    assert 'pointer_argument_alias' in labels.values()
    assert set(repairs.FAMILIES)<=set(draft_repairs.AGENT_FAMILIES)


def test_pointer_alias_rejects_reassignment_hidden_macro_and_conditional_definition():
    for source,header in [
        ('void f(void){int *p;int v;p=&v;use(p);p=&v;use(p);}',''),
        ('void f(void){int *p;int v;if(v) p=&v;use(p);use(p);}',''),
        ('void f(void){int *p;int v;p=&v;HIDDEN();use(p);use(p);}','#define HIDDEN() p=0'),
        ('void f(void){int *p;int v;p=&v;use(p);use(p);}','#define __repair_argument_pointer global'),
        ('void f(void){volatile int *p;int v;p=&v;use(p);use(p);}',''),
    ]:
        assert not repairs.pointer_aliases(source,'f',header)


def test_byte_fill_keeps_pointer_evaluation_and_representable_constant():
    source = 'void f(char *end) { char *p; do { *(p++) = 1; } while (p < end); }'
    variants = repairs.byte_constants(source,'f')
    assert len(variants)==2
    assert all(s.count('p++')==1 and '= __repair_fill_one;' in s for s in variants)
    assert any('signed char __repair_fill_one = 1;' in s for s in variants)
    assert not repairs.byte_constants(source.replace('char *p','int *p'),'f')
    assert not repairs.byte_constants(source.replace('= 1','= work()'),'f')


def test_initialization_order_keeps_dependencies_comments_and_guards():
    source = 'void f(void){int count;int i;int *p;int v;count=0;i=0; /*keep*/ p=&v;use(p);}'
    variants = repairs.initialization_orders(source,'f')
    assert variants and any('i=0;count=0;' in s for s in variants)
    assert all('/*keep*/' in s for s in variants)
    assert not repairs.initialization_orders('void f(void){int i;int *p;p=&i;i=0;}','f')
    assert not repairs.initialization_orders('void f(void){int a;int b;if(a) a=0;b=0;}','f')


def test_coherent_mapping_frame_spill_and_scheduling_diagnostics():
    rows = [('r','move $s0, $a0','move $s1, $a0'),('r','addu $s0, $s0, $v0','addu $s1, $s1, $v0')]
    assert evidence.diagnose(rows)['register_map']==[('$s0','$s1')]
    assert 'lifetime/split-reuse' in evidence.text(rows)
    assert not evidence.diagnose(rows+[('r','move $s0, $v0','move $s2, $v0')])['register_map']
    stack = [(' ','addiu $sp, $sp, -64','addiu $sp, $sp, -64'),('s','lw $t0, 0x24($sp)','lw $t0, 0x2c($sp)')]
    assert evidence.diagnose(stack,'mips')['spill_offsets']==[(36,44)]
    assert evidence.diagnose(stack,'ppc')['target_frame'] is None
    moved = [('o','li $v0, 1','move $s0, $a0'),('o','move $s0, $a0','li $v0, 1')]
    assert evidence.diagnose(moved)['scheduling']
    assert repair_ranking.order(['constant_local','allocation_initialization_order'],moved)[0]=='allocation_initialization_order'


def test_frontier_preserves_distinct_register_cycles_and_spill_layouts():
    frontier = search_frontier.Frontier()
    for i in range(3):
        frontier.offer('cycle'+str(i),10+i,[('r','move $s0, $a0',f'move $s{i+1}, $a0')],arch='mips')
    assert len({n['allocation'] for n in frontier.nodes})==3
    assert len(frontier.nodes)==3


def test_rewrites_preserve_behavior_with_host_compiler(tmp_path):
    import shutil
    import subprocess

    import pytest
    cc = shutil.which('cc')
    if not cc:pytest.skip('Host compiler needed for synthetic execution checks.')
    prefix = 'static int total; void use(int *p) { total += *p; }\n'
    source = '''int work(void) {
int count; int *p; int i; int value; char *bytes; char storage[8];
value=3;p=&value;count=0;i=0;
use(p); value=4; use(p);
bytes=storage;
do { *(bytes++) = 1; } while(bytes < storage+8);
for(i=0;i<8;i++) count+=storage[i];
return total+count;
}'''
    variants = repairs.variants(source,'work')
    assert set(repairs.FAMILIES)<={f for f,_ in variants}
    for index,text in enumerate([source]+[s for _,s in variants]):
        path,binary = tmp_path/f'case{index}.c',tmp_path/f'case{index}'
        path.write_text(prefix+text+'\nint main(void) { return work()!=15; }')
        subprocess.run([cc,'-std=c89','-Werror',str(path),'-o',str(binary)],check=True,capture_output=True)
        subprocess.run([str(binary)],check=True,capture_output=True)


def test_initialization_replay_keeps_direct_aggregate_context_and_volatile_accesses_elsewhere():
    source = '''typedef struct { int a; int b; } Pair;
void work(int *input) { int count; unsigned int i; Pair *p; Pair value;
count=0;p=&value;i=0;
value.a=*(volatile int *)input; use(p); }
'''
    variants = repairs.initialization_orders(source,'work')
    assert any('count=0;i=0;p=&value;' in s for s in variants)
    assert all('*(volatile int *)input' in s for s in variants)
    assert not repairs.initialization_orders('void work(void){volatile int count;int i;count=0;i=0;}','work')


def test_four_stage_search_can_cross_allocation_plateaus_without_accepting_score_zero():
    from decompnet.app import iterative_repairs
    state = dict(pending=[dict(source='0',depth=0,ceiling=60)],next=[],seen=['0'])
    tested = []
    def evaluate(source):
        tested.append(source)
        return {'score':0 if source=='4' else 60,'matched':source=='4'}
    assert iterative_repairs.advance(state,evaluate,lambda s,r:[str(int(s)+1)],lambda:False)
    assert tested==['0','1','2','3','4']
    state = dict(pending=[dict(source='0',depth=0,ceiling=60)],next=[],seen=['0'])
    assert not iterative_repairs.advance(state,lambda s:{'score':0,'matched':False},lambda *a:[],lambda:False)


def test_scheduling_diagnostics_do_not_change_rows_or_scores():
    rows = [('o','li $v0, 1','move $s0, $a0'),('o','move $s0, $a0','li $v0, 1')]
    before = list(rows)
    assert evidence.diagnose(rows)['scheduling']
    assert rows==before


def test_shared_families_reach_supported_candidate_architectures():
    source = 'void work(void){int count; int *p; int n; int value;p=&value;use(p);use(p);count=0;n=0;}'
    for arch in ('mips','ppc','x86','arm','thumb'):
        labels = {}
        proposals = draft_repairs.variants(source,'work',arch,labels=labels,limit=None)
        # Another existing family may propose identical text and own its deduplicated label.
        assert all(text in proposals for _,text in repairs.variants(source,'work'))


def test_synthetic_candidates_compile_with_installed_kmc(tmp_path):
    import pytest

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):pytest.skip('Pinned KMC compiler is not installed; never download it here.')
    cfg = compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}})
    source = '''void use(int *);
int work(char *end) { int count; int *p; int index; int value; char *bytes;
value=3;p=&value;count=0;index=0;use(p);use(p);
bytes=end-8;do { *(bytes++) = 1; } while(bytes < end);return count+index+value; }
'''
    variants = repairs.variants(source,'work')
    assert set(repairs.FAMILIES)<={f for f,_ in variants}
    for family,text in [('control',source)]+variants:
        result = compile_source(cfg,'',text)
        assert result.ok,(family,result.log)
