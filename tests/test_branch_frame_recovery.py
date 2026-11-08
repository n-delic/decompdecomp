"""Homemade branch/packing/frame shapes. No game data is committed."""

import pytest

from decompnet.app import (
    branch_frame_recovery,
    branch_tail_distribution,
    draft_repairs,
    iterative_repairs,
    narrow_store_repairs,
    stack_padding_repairs,
)
from decompnet.arch import mips_stack

HEADER='typedef unsigned int u32; typedef signed int s32; typedef unsigned short u16;'
SOURCE='''typedef struct { u32 color; s32 x,y,z; } Item;
void f(Item *p, s32 mode) {
 u32 color,lo,hi;
 if(mode==0) { color=255; lo=color<<8; hi=color<<16; hi+=1; }
 else if(mode==1) { color=mode; lo=color<<8; hi=color<<16; hi+=2; }
 else { color=0; lo=color<<8; hi=color<<16; hi+=4; }
 lo += hi;
 lo += color << 24;
 p->color=lo;
 p++;
 mode++;
}'''
FRAME=[('s','addiu $sp, $sp, -0x70','addiu $sp, $sp, -0x60'),
       ('s','sw $ra, 0x58($sp)','sw $ra, 0x48($sp)'),
       ('s','lw $ra, 0x58($sp)','lw $ra, 0x48($sp)'),
       ('s','addiu $sp, $sp, 0x70','addiu $sp, $sp, 0x60')]
SWAP=[('r','lw $v0, ($a1)','lw $v0, ($a2)'),('r','sw $v0, ($a2)','sw $v0, ($a1)')]


def tails(s=SOURCE,arch='mips',header=HEADER):return branch_tail_distribution.variants(s,'f',header,arch)


def test_exhaustive_chain_duplicates_whole_tail_but_keeps_loop_updates_shared():
    result=tails()
    assert len(result)==1
    s=result[0][1]
    assert s.count('lo += hi;')==3 and s.count('p->color=lo;')==3
    assert s.count('p++;')==s.count('mode++;')==1
    assert s.index('p++;')>s.rindex('p->color=lo;')
    assert s.count('hi+=1;')==s.count('hi+=2;')==s.count('hi+=4;')==1


@pytest.mark.parametrize('arch',['mips','ppc','x86','arm','thumb'])
def test_source_tail_shared_paths_and_raw_machine_drafts(arch):
    expected=tails(arch=arch)[0][1]
    labels={};draft_repairs.variants(SOURCE,'f',arch,header=HEADER,labels=labels,limit=None)
    assert expected in labels
    assert 'branch_packing_tail' in draft_repairs.AGENT_FAMILIES
    drafts=list(narrow_store_repairs.machine_drafts([('raw',SOURCE),('raw2',SOURCE+'\n')],'f',HEADER,arch))
    assert drafts[0]==('raw',SOURCE) and ('raw2',SOURCE+'\n') in drafts
    assert ('branch_packing_tail',expected) in drafts and len(drafts)<=5


@pytest.mark.parametrize('change',[
 lambda s:s.replace('else { color=0;', 'if(mode==2) { color=0;'),
 lambda s:s.replace('color=255;', 'return; color=255;'),
 lambda s:s.replace('color=255;', 'goto outside; color=255;'),
 lambda s:s.replace('color=255;', 'u32 lo=0; color=255;'),
 lambda s:s.replace('color=255;', 'u32 color,other; color=255;'),
 lambda s:s.replace('lo += hi;', 'lo += call();'),
 lambda s:s.replace('lo += hi;', 'lo += hi++;'),
 lambda s:s.replace('u32 color,lo,hi;', 's32 color,lo,hi;'),
 lambda s:s.replace('color << 24', 'color << 32'),
 lambda s:s.replace('color << 24', 'color << mode'),
 lambda s:s.replace('mode++;','escape(&lo); mode++;'),
 lambda s:s.replace('u32 color;', 'volatile u32 color;'),
 lambda s:s.replace('struct', 'union'),
 lambda s:'#define hi secret\n'+s,
 lambda s:'#define Item hidden\n'+s,
 lambda s:'#define DECL u32 lo=0\n'+s.replace('color=255;', 'DECL; color=255;'),
])
def test_uncertain_or_unsafe_tail_has_no_proposal(change):
    assert not tails(change(SOURCE))


def test_large_shift_types_unknown_records_cpp_and_opaque_macros_rejected():
    assert not tails(arch='unknown')
    assert not tails(header=HEADER+'\n#define u32 odd')
    assert not tails(SOURCE.replace('Item *p', 'Missing *p'))
    assert not tails(SOURCE.replace('void f', 'void C::f'))
    assert not tails(SOURCE.replace('p->color', 'p.color'))


def test_nested_transfers_rejected_but_nested_pure_select_allowed():
    assert not tails(SOURCE.replace('color=255;', 'if(mode) { return; } color=255;'))
    assert tails(SOURCE.replace('color=255;', 'if(mode) { color=2; } else { color=255; }'))


def test_mixed_frame_evidence_is_separate_from_strict_old_reader():
    assert mips_stack.frame_growth(FRAME)==16
    assert mips_stack.frame_growth(FRAME+SWAP) is None
    assert mips_stack.repair_layout(FRAME+SWAP)['inferred_growth']==16
    assert mips_stack.repair_layout(SWAP)['pointer_swap']
    assert mips_stack.repair_layout(SWAP)['inferred_growth'] is None
    for rows in [FRAME[:-1]+SWAP, FRAME+[('r','lw $v0, 4($a1)','lw $v0, 8($a2)')],
                 FRAME+[('r','sw $ra, ($sp)','sw $ra, ($a1)')], FRAME+[('r','move $a1, $a2','move $a2, $a1')],
                 FRAME+[('r','lw $a1, ($a2)','lw $a2, ($a3)')], [('bad',)]]:
        assert not mips_stack.repair_layout(rows).get('inferred_growth')


def test_reservation_follows_declarations_preserves_layout_and_has_honest_label():
    s=SOURCE.replace(' u32 color,lo,hi;', ' s32 xyz[3]; typedef struct { s32 v[8]; } Matrix; Matrix m;\n u32 color,lo,hi;')
    out=stack_padding_repairs.measured(s,'f','mips',FRAME+SWAP)
    assert len(out)==1
    child=out[0][1]
    assert child.index('Matrix m;')<child.index('char __repair_stack_padding[16];')<child.index('if(mode')
    assert 'original purpose unknown' in child and 'Matrix m;' in child
    assert not stack_padding_repairs.measured(child,'f','mips',FRAME+SWAP)
    assert not stack_padding_repairs.measured(s,'f','ppc',FRAME+SWAP)


def test_normal_selector_keeps_frame_control_and_targeted_composition_near_front():
    fn=dict(name='f',asm='')
    selected=iterative_repairs.targeted_variants(SOURCE,{'rows':FRAME+SWAP},fn,'mips',[],HEADER,tagged=True,limit=4)
    assert selected[0][0]=='inferred_frame_reservation'
    assert selected[1][0]=='frame_branch_tail_sequence'
    assert '__repair_stack_padding[16]' in selected[1][1] and selected[1][1].count('p->color=lo;')==3
    framed=selected[0][1]
    next_=iterative_repairs.targeted_variants(framed,{'rows':SWAP,'repair_path':['inferred_frame_reservation']},fn,'mips',[],HEADER,tagged=True,limit=4)
    assert next_[0][0]=='branch_packing_tail'
    assert not branch_frame_recovery.variants(SOURCE,'f',HEADER,'mips',[])
    assert {f for f,_ in branch_frame_recovery.variants(SOURCE,'f',HEADER,'mips',FRAME)}=={'inferred_frame_reservation'}


def test_tail_choices_bounded_without_rewriting_other_functions():
    s=SOURCE.replace(' p++;',' p++;\n'+SOURCE[SOURCE.index(' if(mode'):SOURCE.index(' p++;')])
    assert len(tails(s))<=branch_tail_distribution.LIMIT
    prefix='void other(void) { }\n'
    assert all(c.startswith(prefix) for _,c in tails(prefix+SOURCE))


def test_parenthesized_and_pointer_shadow_declarations_are_not_rebound():
    for declaration in ['u32 (lo);','u32 *lo;','u32*lo;','Item*p;']:
        assert not tails(SOURCE.replace('color=255;', declaration+' color=255;'))


def test_measured_hints_do_not_claim_object_or_original_source_recovery():
    from decompnet.app import allocation_evidence
    text=allocation_evidence.text(FRAME+SWAP,'mips')
    assert 'root/derived' in text and 'not proof' in text
    text=allocation_evidence.text(SWAP,'mips')
    assert 'branch-local packing' in text and 'uniform' not in text


def test_new_modules_invalidate_exhausted_candidate_searches():
    from decompnet.claim_stalls import repair_rules
    names=dict(repair_rules())
    for name in ('branch_tail_distribution.py','branch_frame_recovery.py','mips_stack.py'):
        assert len(names[name])==64


def test_optional_frozen_regression_through_normal_search():
    """Private, immutable source/target/context manifests; absent game inputs skip in CI."""
    import os
    from pathlib import Path

    from decompnet.app import frozen_repair_cases, linked_repair_benchmark
    suite=os.environ.get('DECOMPNET_BRANCH_TAIL_SUITE');project=os.environ.get('DECOMPNET_BRANCH_TAIL_PROJECT')
    if not suite or not project:pytest.skip('Private frozen branch-tail cases not supplied')
    cases,_=frozen_repair_cases.load(Path(suite),Path(project),linked_repair_benchmark.evaluator)
    assert len([c for c in cases if c.get('expect_recovery')])>=2
    report=linked_repair_benchmark.suite(Path(project),cases,trials=12,seconds=30)
    for case,result in zip(cases,report['cases']):
        if case.get('expect_recovery'):
            assert result['best_score']==0 and result['first_exact_trial']<=12


def test_generated_c_preserves_runtime_behavior_across_selectors(tmp_path):
    import shutil
    import subprocess
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host C compiler unavailable')
    header=HEADER+'\n#include <stdio.h>\n'
    main='\nint main(void) { Item p={0}; int i; for(i=0;i<10;i++) { f(&p,i); printf("%u\\n",p.color); } return 0; }'
    outputs=[]
    for i,source in enumerate([SOURCE]+[s for _,s in tails()]):
        file=tmp_path/f'case{i}.c';binary=tmp_path/f'case{i}'
        file.write_text(header+source+main)
        subprocess.run([cc,str(file),'-o',str(binary)],check=True,capture_output=True)
        outputs.append(subprocess.check_output([str(binary)]))
    assert outputs and all(o==outputs[0] for o in outputs)
