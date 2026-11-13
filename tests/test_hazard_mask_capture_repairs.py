import pytest

from decompnet.app import hazard_line_repairs as lines
from decompnet.arch.mips_hazards import rows_witness
from decomprepair import known_bit_repairs as bits
from decomprepair import post_call_capture_repairs as captures

ROWS=[('i','beq $t0, $zero, 0x18','beq $t0, $zero, 0x14'),(' ','nop','nop'),('i','j f+0x18','j f+0x14')]


def test_one_word_hazard_witness_is_advisory_and_bounded():
    assert rows_witness(ROWS)
    assert not rows_witness([('i','beq $t0, $zero, 0x28','beq $t0, $zero, 0x14'),ROWS[1]])
    assert not rows_witness([('i','beq $t0, $zero, 0x18','beq $t1, $zero, 0x14'),ROWS[1]])
    assert not rows_witness([('i','jal f+0x18','jal f+0x14'),ROWS[1]])
    assert not rows_witness(ROWS[:1])
    assert not rows_witness([('i',None,'nop')])


def test_line_repair_retains_evaluation_and_assignment_conversion():
    source='int f(float x){int v; v=x+2; return v;}'
    found=lines.variants(source,'f',arch='mips',rows=ROWS)
    assert any('int __repair_hazard_value;\n__repair_hazard_value = x+2;\nv = __repair_hazard_value;' in s for _,s in found)
    assert not lines.variants(source,'f',arch='ppc',rows=ROWS)
    assert not lines.variants(source,'f',arch='mips',rows=[])
    assert not lines.variants(source.replace('x+2','__LINE__+2'),'f',arch='mips',rows=ROWS)
    assert not lines.variants(source,'f','#define S __COUNTER__',arch='mips',rows=ROWS)


MASK='unsigned int f(void){int x; unsigned int t=(unsigned short)x; int b=(t+2)*2; return b << 8;}'


@pytest.mark.parametrize('arch',['mips','ppc','arm','thumb','x86'])
def test_known_even_mask_is_range_proven_and_shared(arch):
    result=bits.variants(MASK,'f',arch=arch)
    assert any('(b & 0xfffffffeu) << 8' in s for _,s in result)
    masked=MASK.replace('b << 8','(b & 0xfffffffeu) << 8')
    assert any('((unsigned int)b)' in s for _,s in bits.variants(masked,'f',arch=arch))


@pytest.mark.parametrize('old,new',[
    ('(t+2)*2','(t+2)*3'),('unsigned short','short'),('b << 8','++b; return b << 8'),
    ('b << 8','sink(&b); return b << 8'),('b << 8','b=1; return b << 8'),
    ('b << 8','b << 32'),('int x;','volatile int x;'),
])
def test_known_mask_declines_unsupported_values_and_mutations(old,new):
    assert not bits.variants(MASK.replace(old,new),'f',arch='mips')


CAPTURE='void f(void){int x; unsigned int t=(unsigned short)x; emit(t, 2); t &= 31; use(t);}'


def test_capture_moves_conversion_after_call_and_converts_original_argument():
    found=captures.variants(CAPTURE,'f',arch='mips')
    assert any('unsigned int t; emit((unsigned short)x, 2);\nt = (unsigned short)x;' in s for _,s in found)


@pytest.mark.parametrize('old,new',[
    ('emit(t, 2)','emit(t, ++x)'),('emit(t, 2)','emit(t, change())'),
    ('emit(t, 2)','emit(t, &x)'),('emit(t, 2)','emit(t, t)'),
    ('emit(t, 2)','if(x) emit(t, 2)'),('emit(t, 2)','x=3; emit(t, 2)'),
    ('int x;','volatile int x;'),('emit(t, 2)','sink(&t); emit(t, 2)'),
])
def test_capture_does_not_cross_mutations_control_aliases_or_opaque_args(old,new):
    assert not captures.variants(CAPTURE.replace(old,new),'f',arch='mips')


def test_capture_and_mask_decline_opaque_macros():
    assert not captures.variants(CAPTURE,'f','#define emit(a,b) (++x)',arch='mips')
    assert not bits.variants(MASK.replace('return b','HIDDEN; return b'),'f','#define HIDDEN ++b',arch='mips')


def test_shared_search_exposes_new_families():
    from decompnet.app import draft_repairs, iterative_repairs
    assert bits.FAMILY in draft_repairs.AGENT_FAMILIES
    assert captures.FAMILY in draft_repairs.AGENT_FAMILIES
    pool=iterative_repairs.targeted_pool('int f(float x){int v;v=x;return v;}',{'rows':ROWS},{'name':'f','asm':''},'mips',[])
    assert any(family==lines.FAMILY for family,_ in pool)


def test_unconditional_macro_definitions_remain_opaque_in_shared_generator():
    from decompnet.app import draft_repairs
    s=CAPTURE.replace('int x;', 'int x;\n#define UNUSED 3\n')
    labels={}
    choices=draft_repairs.variants(s,'f','mips',labels=labels,header='',limit=None)
    assert any(labels[c]==captures.FAMILY and '#define UNUSED 3' in c for c in choices)
    assert not captures.variants(s.replace('#define UNUSED 3','#if X\n#endif'),'f',arch='mips')


def test_macro_continuations_and_hidden_escapes_are_not_rewritten():
    from decomprepair.repair_source_view import read
    s=CAPTURE.replace('int x;', 'int x;\n#define HIDDEN(a) \\\n { a++; }\n')
    p=read(s,'f')
    assert p and 'a++' not in p.clean
    assert not captures.variants(s.replace('emit(t, 2)','HIDDEN(x); emit(t, 2)'),'f',arch='mips')
    source='int f(float x){int v;escape(&(v));v=x;return v;}'
    assert all('__repair_hazard_value' not in s for _,s in lines.variants(source,'f',arch='mips',rows=ROWS))


def test_conditional_mask_does_not_establish_a_dominating_bound():
    s=MASK.replace('int b=', 'if(x) t &= 31; int b=')
    assert not bits.variants(s,'f',arch='mips')
    s=MASK.replace('int b=', 't &= 31; int b=')
    assert bits.variants(s,'f',arch='mips')


def test_pinned_private_repair_precursors_when_supplied():
    """Repeat the actual historical recovery gate without shipping game code to CI."""
    import os
    from pathlib import Path

    from decompnet.app import frozen_repair_cases, linked_repair_benchmark, repair_benchmark
    location=os.environ.get('DECOMPNET_HAZARD_REPAIR_SUITE')
    if not location:pytest.skip('Supply the private frozen precursor manifest and copied project.')
    root=Path(os.environ['DECOMPNET_HAZARD_REPAIR_PROJECT'])
    cases,_=frozen_repair_cases.load(Path(location),root,linked_repair_benchmark.evaluator)
    results=linked_repair_benchmark.suite(root,cases,trials=32,seconds=30)
    assert results['candidate_compiles']<=32*len(cases)
    assert all(row['first_exact_trial'] is not None for row in results['cases'])
    before=os.environ.get('DECOMPNET_HAZARD_REPAIR_BASELINE')
    if before:
        import json
        # Baseline report uses the same sources, target, compiler and budget guards.
        prior=json.loads(Path(before).read_text())
        for old,new in zip(prior,results['cases'],strict=True):
            repair_benchmark.compare(old,dict(results,cases=[new]))
