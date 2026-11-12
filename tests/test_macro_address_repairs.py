import pytest

from decompnet.app import draft_repairs
from decomprepair import macro_address_repairs as repair

FLAGS=['-mgp32']
MACRO='#define LOAD(p,o) (*(char**)((char*)(p)+(o)))\n'
SOURCE=MACRO+'void f(char *a,int k){ LOAD(LOAD(a,140),k); }'


def variants(source=SOURCE,header='',flags=FLAGS,arch='mips'):
    return repair.variants(source,'f',arch,flags,header)


def test_outer_address_changes_but_inner_load_and_directive_stay_intact():
    rows=variants()
    assert len(rows)==1
    family,text=rows[0]
    assert family=='macro_byte_address_order'
    assert text.startswith(MACRO)
    assert '*(char**)(k + (int)(LOAD(a,140)))' in text
    assert text.count('LOAD(a,140)')==1


@pytest.mark.parametrize('body',[
    'LOAD(LOAD(a++,140),k)', 'LOAD(LOAD(read_pointer(),140),k)',
    'LOAD(LOAD(a,140),k++)', 'LOAD(LOAD(a,140),read_index())',
    'LOAD(LOAD(a,140+4),k)', 'LOAD(LOAD(a,k),k)',
    'LOAD(a,k)', 'LOAD(LOAD(a,140),k+1)',
])
def test_rejects_side_effects_unknown_calls_and_unproven_inner_loads(body):
    assert not variants(SOURCE.replace('LOAD(LOAD(a,140),k)',body))


@pytest.mark.parametrize('source',[
    SOURCE.replace('int k','short k'),SOURCE.replace('int k','float k'),
    SOURCE.replace('char *a','void *a'),SOURCE.replace('char *a','char **a'),
    SOURCE.replace('int k','volatile int k'),
    SOURCE.replace('int k','int k[2]'),SOURCE.replace('char *a','char *a[2]'),
    SOURCE.replace('LOAD(LOAD(a,140),k);','{ unsigned char k=1; LOAD(LOAD(a,140),k); }'),
])
def test_requires_known_word_index_and_byte_pointer_declarations(source):
    assert not variants(source)


@pytest.mark.parametrize('flags,arch',[
    ([], 'mips'),(['-mgp64'],'mips'),(['-mgp32','-mabi=n32'],'mips'),
    (['-m32','-m64'],'x86'),(['-m32'],'unknown'),
])
def test_rejects_unknown_or_wide_abis(flags,arch):
    assert not variants(flags=flags,arch=arch)


@pytest.mark.parametrize('directive',[
    '#undef LOAD\n','#if CONFIG\n','#include "local.h"\n',
    '#define LOAD(p,o) (*(char**)((char*)(p)+(o)))\n',
    '#define OPAQUE(p) p ## suffix\n',
])
def test_rejects_ambiguous_directive_state(directive):
    assert not variants(MACRO+directive+SOURCE[len(MACRO):])


def test_rejects_definition_after_function_and_header_redefinition():
    assert not variants(SOURCE[len(MACRO):]+MACRO)
    assert not variants(header=MACRO)
    assert not variants(header='#define int long long\n')


def test_rejects_duplicate_macro_parameter_reads_and_nonbyte_address_scaling():
    assert not variants(SOURCE.replace('(p)+(o)','(p)+(o)+(o)'))
    assert not variants(SOURCE.replace('((char*)(p)','((short*)(p)'))


def test_alias_widths_and_multiline_source_are_preserved():
    source=SOURCE.replace('char *a,int k','u8 *a,u32 k').replace('(char*)(p)','(u8*)(p)')
    assert variants(source,'typedef unsigned char u8; typedef unsigned int u32;')
    assert not variants(source,'typedef unsigned short u8; typedef unsigned int u32;')
    source=SOURCE.replace('LOAD(LOAD(a,140),k)','LOAD(\n LOAD(a,140),\n k)')
    text=variants(source)[0][1]
    assert source.count('\n')==text.count('\n')
    assert text.startswith(MACRO)


def test_supports_scalar_loads_and_limits_work_to_the_requested_function():
    source='#define WORD(p,o) (*(int*)((char*)(p)+(o)))\n'+SOURCE
    source=source.replace('LOAD(LOAD(a,140),k);','WORD(LOAD(a,140),k);')
    neighbor='\nvoid other(char *a,int k){ LOAD(LOAD(a,140),k); }'
    rows=variants(source+neighbor)
    assert len(rows)==1 and '*(int*)(k + (int)(LOAD(a,140)))' in rows[0][1]
    assert rows[0][1].endswith(neighbor)


def test_shared_route_emits_the_guarded_candidate_before_generic_trials():
    expected=variants()[0][1];labels={}
    rows=draft_repairs.variants(SOURCE,'f','mips',flags=FLAGS,labels=labels,limit=None)
    assert expected in rows
    assert labels[expected]=='macro_byte_address_order'
    assert 'macro_byte_address_order' in draft_repairs.AGENT_FAMILIES


@pytest.mark.parametrize('arch,flags', [('mips',['-mgp32']),('ppc',['-m32']),('x86',['-m32']),('arm',['-mabi=32']),('thumb',['-mabi=32'])])
def test_shared_32bit_architecture_metadata(arch,flags):
    assert variants(arch=arch,flags=flags)


def test_address_mismatch_ranking_prefers_the_narrow_macro_candidate():
    from decompnet.app import repair_ranking
    rows=[('r','addu $v0, $a0, $v1','addu $v0, $v1, $a0')]
    families=['arithmetic_operand_order','macro_byte_address_order','integer_address_order']
    ranked=repair_ranking.order(families,rows)
    assert ranked.index('macro_byte_address_order')<ranked.index('integer_address_order')


def test_postfix_expression_keeps_the_dereferenced_value_parenthesized():
    source=SOURCE.replace('LOAD(LOAD(a,140),k);','LOAD(LOAD(a,140),k)[1];')
    text=variants(source)[0][1]
    assert '(*(char**)(k + (int)(LOAD(a,140))))[1]' in text
