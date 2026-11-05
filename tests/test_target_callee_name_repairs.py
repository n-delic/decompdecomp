"""Copied overlay names are hypotheses only when one symbolic call target is missing."""
import pytest

from decompnet.app import draft_repairs, local_experiments, recent_capture_repairs
from decompnet.app.callee_name_repairs import FAMILY, variants

OLD='func_80101000_ovl_02'
NEW='func_80102000_overlay_003'
SOURCE=f'extern int {OLD}(void *, int);\nvoid f(void *p){{ {OLD}(p, 7); }}'
ASM=f'jal {NEW}\nnop'


def test_unique_wrong_callee_rewrites_owned_prototype_and_calls_only():
    source=SOURCE+f'\n/* keep {OLD} in documentation */\n'
    choices=variants(source,'f',ASM)
    assert len(choices)==1
    text=choices[0][1]
    assert f'extern int {NEW}(void *, int);' in text
    assert f'{NEW}(p, 7)' in text
    assert f'/* keep {OLD} in documentation */' in text
    assert choices[0][0]==FAMILY
    assert any(label==FAMILY for label,_ in recent_capture_repairs.variants(source,'f','mips',ASM))
    assert not any(label==FAMILY for label,_ in recent_capture_repairs.variants(source,'f','ppc',ASM))
    assert FAMILY in draft_repairs.AGENT_FAMILIES and FAMILY in local_experiments.FAMILIES


def test_already_correct_calls_and_repeated_missing_target_are_preserved():
    source=f'extern int keep(int);\n{SOURCE.replace("{ ","{ keep(1); ")}'
    text=variants(source,'f','jal keep\nnop\n'+ASM)[0][1]
    assert 'keep(1)' in text and f'{NEW}(p, 7)' in text
    twice=SOURCE.replace(f'{OLD}(p, 7);',f'{OLD}(p, 7); {OLD}(p, 9);')
    assert variants(twice,'f',ASM+'\n'+ASM)
    assert not variants(twice,'f',ASM)


@pytest.mark.parametrize('source,asm,header',[
    (SOURCE,ASM+'\njal another\nnop',''),
    (SOURCE.replace(f'{OLD}(p, 7);',f'{OLD}(p, 7); other();'),ASM,''),
    (SOURCE.replace('extern int','static inline int'),ASM,''),
    (SOURCE.replace('extern int','typedef int'),ASM,''),
    (SOURCE.replace(f'{OLD}(p, 7);',f'void *q = {OLD}; {OLD}(p, 7);'),ASM,''),
    (SOURCE.replace('void f(void *p)',f'void f(void *p, int {OLD})'),ASM,''),
    (SOURCE,ASM,f'#define {NEW} other'),
    (SOURCE,ASM,f'#define helper(x) {OLD}(x,7)'),
    (SOURCE,ASM,f'extern void {NEW}(int);'),
    (SOURCE,ASM+'\njalr $t9\nnop',''),
    (SOURCE.replace('extern int '+OLD+'(void *, int);',''),ASM,''),
    (SOURCE,ASM.replace('jal ','j '),''),
])
def test_ambiguous_indirect_macro_or_unowned_bindings_are_not_guessed(source,asm,header):
    assert not variants(source,'f',asm,header)
