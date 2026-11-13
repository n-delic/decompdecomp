"""Synthetic contracts for branch/call lowering; private game fixtures never enter the repository."""
import pytest

from decompnet.app import (
    draft_repairs,
    repair_selection,
    residual_followups,
)
from decompnet.app import (
    pointer_snapshot_repairs as snapshots,
)
from decomprepair import branch_scalar_repairs as branches
from decomprepair import call_assignment_repairs as arguments
from decomprepair import call_count_repairs as counts

HEADER = 'typedef int s32; typedef short s16; typedef unsigned short u16; typedef unsigned char u8;'
SOURCE = '''void label(int, int, int, void *, int, u16, int);
void callback(void);
void use(int);
void f(int mode, int n, int x, int h) {
 int y = x; int base = y + 6;
 switch(mode) {
 case 1:
  if(n) { n--; y += h; y -= 18; label(mode,x,y,callback,x,n,0); use((s16)y); }
  else { do { use(base); } while(0); }
  break;
 case 2:
  if(n) { y = x; y += h; y -= 18; n--; label(mode,x,y,callback,x,n,0); use((s16)y); }
  break;
 }
}
'''


def test_branch_scalar_phases_are_split_together_inside_switch_with_unrelated_loop():
    proposals=branches.variants(SOURCE,'f',HEADER,'mips')
    assert proposals and proposals[0][0]==branches.FAMILY
    candidate=proposals[0][1]
    assert '__repair_branch_y_0' in candidate and '__repair_branch_y_1' in candidate
    assert 'int __repair_branch_y_0 = y;' in candidate
    assert 'int __repair_branch_y_1;' in candidate
    assert 'use(base)' in candidate


@pytest.mark.parametrize('suffix',['use(y);','while(mode) { use(y); }'])
def test_branch_split_rejects_value_live_after_arm(suffix):
    text='void f(int x){int y; y=x; if(x){y+=1;use(y);} '+suffix+'}'
    assert not branches.variants(text,'f',HEADER,'mips')


@pytest.mark.parametrize('body',[
    'int y; y=x; if(x){y+=1; use(&y);}',
    'int y; y=x; if(x){int y; y=x;use(y);}',
    'volatile int y; y=x; if(x){y+=1;use(y);}',
    'int y; y=x; if(x){goto inside; inside:y+=1;use(y);}',
])
def test_branch_split_leaves_escaped_shadowed_qualified_and_labeled_regions_alone(body):
    assert not branches.variants('void f(int x){'+body+'}','f',HEADER,'mips')


@pytest.mark.parametrize('arch',['mips','ppc','arm','thumb','x86'])
def test_count_helper_uses_real_prototype_and_omits_constant_arguments(arch):
    proposals=counts.variants(SOURCE,'f',HEADER,arch)
    assert proposals
    candidate=proposals[0][1]
    assert 'static __inline__ int __repair_call_count' in candidate
    assert 'int p5' in candidate  # decrement stays wide; the real callee still receives u16
    assert 'p5--; label(p0, p1, p2, p3, p4, p5, 0); return p5;' in candidate
    assert candidate.count('n = __repair_call_count(')==2
    assert candidate.index('void label(')<candidate.index('static __inline__')<candidate.index('void f(')


@pytest.mark.parametrize('change',[
    lambda s:s.replace('void label(int, int, int, void *, int, u16, int);','void label();'),
    lambda s:s.replace('void label(int, int, int, void *, int, u16, int);','void label(int, ...);'),
    lambda s:s.replace('n--; y += h;', 'n--; use(x); y += h;').replace('n--; label(', 'n--; use(x); label('),
    lambda s:s.replace('label(mode,x,y,callback,x,n,0)','label(mode,n,y,callback,x,n,0)'),
    lambda s:s.replace('n--;','if(x) n--;'),
])
def test_count_helpers_do_not_guess_contracts_or_move_across_effects(change):
    assert not counts.variants(change(SOURCE),'f',HEADER,'mips')


ARG_SOURCE='void callee(int,int); void f(int x,int h){int y; y=x+h; y-=18; callee(x,y); use(y);}'


def test_assignment_chain_can_be_embedded_and_extracted_without_losing_grouping():
    proposals=arguments.variants(ARG_SOURCE,'f',HEADER,'mips')
    embedded=next(s for family,s in proposals if family=='embedded_call_assignment')
    assert 'callee(x, (y = ((int)(x+h)) - (18)));' in embedded
    assert 'use(y);' in embedded
    extracted=arguments.variants(embedded,'f',HEADER,'mips')
    assert any(family=='extracted_call_assignment' and 'y = ((int)(x+h)) - (18);' in s for family,s in extracted)


@pytest.mark.parametrize('source',[
 'void f(int x){int y;y=x;callee(y,y);}',
 'void f(int x){int y;y=x;callee(x++,y);}',
 'void f(int x){short y;y=x;callee(x,y);}',
 'void f(int x){int y;y=load();callee(x,y);}',
 'void f(int x){int y;y=x;if(x)callee(x,y);}',
 'void f(int x){int y;y=x;callee(global,y);}',
 'void f(int x){int y;y=x;y=callee(x,y);}',
])
def test_embedding_rejects_dependencies_effects_narrowing_globals_and_unbraced_moves(source):
    assert not arguments.variants(source,'f',HEADER,'mips')


POINTER_SOURCE='''typedef struct {int v;} Obj;
typedef struct {u8 col[4]; Obj *obj;} Widget;
extern Obj *global;
void f(void) { Widget *w; w=make(); w->col[0]=1; w->col[1]=2; w->obj=global; }
'''
ASM='lw $v1, %lo(global_alias + 4)($v1)\naddiu $v0,$zero,1\nsb $v0,0($s0)\nsb $v0,1($s0)\nsw $v1,8($s0)\njal next\n'


def test_typed_snapshot_uses_existing_global_and_stays_after_call():
    proposals=snapshots.variants(POINTER_SOURCE,'f',HEADER,'mips',ASM)
    assert len(proposals)==1
    source=proposals[0][1]
    assert source.index('w=make();')<source.index('__repair_pointer_snapshot = global;')<source.index('w->col[0]')
    assert 'w->obj = __repair_pointer_snapshot;' in source
    assert 'global_alias' not in source  # target spelling is not converted into a guessed source identity


@pytest.mark.parametrize('source,asm,arch',[
 (POINTER_SOURCE,ASM.replace('sb','sh'),'mips'),
 (POINTER_SOURCE,ASM.replace('addiu','jal'),'mips'),
 (POINTER_SOURCE.replace('u8 col','int col'),ASM,'mips'),
 (POINTER_SOURCE.replace('w->col[1]=2;', 'w->col[1]=read();'),ASM,'mips'),
 (POINTER_SOURCE,ASM,'arm'),
])
def test_pointer_snapshots_require_target_byte_order_and_known_byte_fields(source,asm,arch):
    assert not snapshots.variants(source,'f',HEADER,arch,asm)


def test_new_families_are_shared_and_have_bounded_complements():
    expected={branches.FAMILY,counts.FAMILY,snapshots.FAMILY,*arguments.FAMILIES}
    assert expected<=set(draft_repairs.AGENT_FAMILIES)
    labels={}
    draft_repairs.variants(SOURCE,'f','mips',labels=labels,limit=None,header=HEADER)
    assert {branches.FAMILY,counts.FAMILY}<=set(labels.values())
    assert repair_selection.order(['other','embedded_call_assignment'],['inline_call_count'])[0]=='embedded_call_assignment'
    assert 'branch_scalar_lifetime' in residual_followups.FAMILIES['allocation']


def test_m2c_and_saved_drafts_get_advice_without_an_unconditional_helper():
    from decompnet.app.source_diagnostics import hints
    notes=hints(SOURCE,'f',arch='mips',rows=[['r','move $a2,$s0','move $a2,$s1']])
    assert any('branch_scalar_lifetime' in n and 'embedded_call_assignment' in n for n in notes)


def test_named_ansi_formals_are_preserved_and_escaped_count_is_rejected():
    text=SOURCE.replace('void label(int, int, int, void *, int, u16, int);',
                        'void label(int a, int b, int c, void *cb, int d, u16 count, int extra);')
    assert counts.variants(text,'f',HEADER,'mips')
    assert not counts.variants(text.replace('switch(mode)', 'escape(&n); switch(mode)'), 'f', HEADER, 'mips')


def test_macro_and_line_sensitive_regions_are_not_rewritten():
    assert not branches.variants(SOURCE,'f',HEADER+'\n#define use(x) hidden(x)','mips')
    assert not arguments.variants('#line 10 "original.c"\n'+ARG_SOURCE,'f',HEADER,'mips')


def test_generation_filters_and_rule_fingerprints_include_new_families():
    from decompnet import claim_stalls
    from decompnet.app import repair_generation
    paths={p for p,_ in claim_stalls.repair_rules()}
    assert {'branch_call_support.py','branch_scalar_repairs.py','call_count_repairs.py',
            'call_assignment_repairs.py','pointer_snapshot_repairs.py'}<=paths
    class Net:pass
    net=Net()
    with repair_generation.session(net, families={'embedded_call_assignment'}, scope='fixture', deadline=None):
        labels={}
        draft_repairs.variants(ARG_SOURCE,'f','mips',labels=labels,limit=None,header=HEADER)
        assert branches.FAMILY not in labels.values()
        assert counts.FAMILY not in labels.values()


def test_compound_phase_after_declaration_initialization_can_be_embedded():
    source='void f(int x,int h){int y=x; y+=h; y-=18; callee(x,y); use(y);}'
    proposals=arguments.variants(source,'f',HEADER,'mips')
    assert any('int y=x;' in s and 'callee(x, (y = ((int)(y + (h))) - (18)))' in s for _,s in proposals)


@pytest.mark.parametrize('prefix',['if(x)', 'while(x)', 'else', 'case 1:'])
def test_embedded_assignment_does_not_remove_an_unbraced_control_body(prefix):
    source='void f(int x,int h){int y; '+prefix+' y=x+h; callee(x,y);}'
    assert not arguments.variants(source,'f',HEADER,'mips')


def test_record_members_are_not_scalar_locals_and_parameter_shadows_are_not_counts():
    source='void f(int x){struct Local {int y;} obj; if(x){y+=1;use(y);}}'
    assert not branches.variants(source,'f',HEADER,'mips')
    source=SOURCE.replace('n--; y += h;', 'float n; n=3; n--; y += h;')
    assert not counts.variants(source,'f',HEADER,'mips')


def test_uncompiled_machine_draft_gets_the_same_advisory_without_renaming():
    from decomprepair.local_flow import hints
    notes=hints(SOURCE,'f')
    assert any('branch-local lifetimes' in n and 'does not rewrite' in n for n in notes)
    assert '__repair_' not in SOURCE


def test_snapshot_reader_is_architecture_owned_and_stops_at_control_boundaries():
    from decompnet import arch
    reader=arch.get('mips').source_pointer_snapshot
    assert reader(ASM)
    for branch in ['beql $v0,$zero,1f','bc1t 1f','b 1f','jr $ra']:
        assert not reader(ASM.replace('addiu $v0,$zero,1',branch))
    assert arch.get('arm').source_pointer_snapshot is None


def test_byte_field_width_comes_from_typedef_evidence_not_its_name():
    header=HEADER.replace('unsigned char u8','unsigned short u8')
    assert not snapshots.variants(POINTER_SOURCE,'f',header,'mips',ASM)
