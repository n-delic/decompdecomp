import shutil
import subprocess

import pytest

from decompnet.app import branch_pointer_repairs as repairs
from decompnet.app import draft_repairs

SOURCE='''
void use(int *);
void work(int mode, int flag) {
int *result; int *name; int value; int other; int x; int y;
switch(mode) {
case 1:
 if(flag) { result=&value; name=&other; *result=1; *name=2; }
 else { result=&other; name=&value; *result=3; *name=4; }
 break;
case 2:
 if(flag) { result=&value; name=&other; *result=5; *name=6; }
 else { result=&other; name=&value; *result=7; *name=8; }
 break;
default: return;
}
use(result); use(name);
}
'''


def test_distributes_suffix_and_retains_mixed_pointer_choices():
    distributed=repairs.distribute(SOURCE,'work')
    assert distributed and distributed.count('use(result)')==4
    options=repairs.localize(distributed,'work')
    assert len(options)==3
    assert any(c.count('int *name;')==4 and c.count('int *result;')==1 for c in options)
    assert any(c.count('int *result;')==4 and c.count('int *name;')==1 for c in options)
    assert any(c.count('int *result;')==4 and c.count('int *name;')==4 for c in options)
    variants=repairs.variants(SOURCE,'work')
    assert set(repairs.FAMILIES)=={f for f,_ in variants}
    assert len(variants)<=repairs.LIMIT
    for arch in ('mips','ppc','x86','arm','thumb'):
        assert all(s in draft_repairs.variants(SOURCE,'work',arch,limit=None) for _,s in variants)


def test_unsupported_control_and_uninitialized_or_escaped_pointer_stay_unchanged():
    assert repairs.distribute(SOURCE.replace(' break;','',1),'work') is None
    assert repairs.distribute(SOURCE.replace('default: return;',''),'work') is None
    uncertain=repairs.distribute(SOURCE.replace('result=&value;','if(mode) result=&value;',1),'work')
    assert all(s.count('int *result;')==1 for s in repairs.localize(uncertain,'work'))
    assert not repairs.variants(SOURCE.replace('int *name;','volatile int *name;'),'work')
    assert not repairs.variants(SOURCE.replace('switch(mode)','goto there; there: switch(mode)'),'work')
    distributed=repairs.distribute(SOURCE,'work')
    escaped=distributed.replace('use(result);','escape(&result); use(result);')
    assert all(s.count('int *result;')==1 for s in repairs.localize(escaped,'work'))


def test_statement_macros_keep_calls_and_reject_side_effect_arguments():
    source=SOURCE.replace('result=&value; name=&other; *result=1; *name=2;','SET(value,other)')
    prefix='#define SET(a,b) result=&a; name=&b; *result=1; *name=2;\n'
    expanded=repairs.expand_statements(prefix+source,'work')
    assert expanded and 'SET(value,other)' not in expanded
    assert repairs.variants(prefix+source,'work')
    assert repairs.expand_statements(prefix+source.replace('SET(value,other)','SET(next(),other)'),'work') is None


def test_all_candidates_preserve_execution_order_with_host_compiler(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host compiler required for synthetic execution check.')
    prefix='#include <stdio.h>\nvoid use(int *p) { printf("%d,",*p); }\n'
    suffix='\nint main(void) { int i,j;for(i=0;i<4;i++)for(j=0;j<2;j++)work(i,j);return 0; }'
    outputs=[]
    for index,source in enumerate([SOURCE]+[s for _,s in repairs.variants(SOURCE,'work')]):
        path=tmp_path/f'{index}.c';binary=tmp_path/f'{index}'
        path.write_text(prefix+source+suffix)
        subprocess.run([cc,'-std=c89','-Werror',str(path),'-o',str(binary)],check=True,capture_output=True)
        outputs.append(subprocess.check_output([str(binary)]))
    assert outputs and all(o==outputs[0] for o in outputs)


def test_repeated_call_shortfall_is_advisory_and_prioritizes_mixed_lifetimes():
    from decompnet.app import allocation_evidence, repair_ranking
    rows=[(' ','move $a0, $s0','move $a0, $s0')]*20+[(' ','jal helper','jal helper')]*2+[('<','jal helper','')]*2
    report=allocation_evidence.diagnose(rows)
    assert report['possible_tail_merging']
    assert 'or missing logic' in allocation_evidence.text(rows)
    assert repair_ranking.order(['call_arity','branch_pointer_lifetimes'],rows)[0]=='branch_pointer_lifetimes'


def test_macro_strings_and_unbraced_guard_invocations_are_not_expanded():
    source=SOURCE.replace('result=&value; name=&other; *result=1; *name=2;','SET(value,other)')
    string='#define SET(a,b) log("a"); result=&a;\n'
    assert repairs.expand_statements(string+source,'work')==string+source
    guarded='#define SET(a,b) result=&a; name=&b;\n'+source.replace('SET(value,other)','if(flag) SET(value,other)')
    assert repairs.expand_statements(guarded,'work')==guarded


def test_tail_distribution_rejects_names_shadowed_in_a_branch():
    source=SOURCE.replace('result=&value;','int other; result=&value;',1)
    assert repairs.distribute(source,'work') is None
