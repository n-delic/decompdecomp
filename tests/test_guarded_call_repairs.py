"""Equality facts may simplify call values, never repeat loads or guess branch entry."""
import pytest

from decomprepair import guarded_call_repairs as repair

HEADER = 'typedef unsigned short u16;typedef unsigned char u8;typedef int s32;static void draw(int);'
SOURCE = 'void f(void){u16 x;x=read();if(x==1){draw((u8)((s32)x));}}'


def choices(source=SOURCE, header=HEADER, arch='mips'):
    return [text for _, text in repair.variants(source, 'f', header, arch)]


def test_guard_and_capture_remain_while_call_uses_the_proven_value():
    candidate = choices()[0]
    assert 'x=read();if(x==1)' in candidate
    assert 'draw((u8)((s32)1))' in candidate
    assert candidate.count('read()') == 1
    assert repair.FAMILY == 'guarded_call_value'


def test_short_circuit_comma_capture_and_leading_copy():
    source = 'void f(void){u16 x;u16 saved;if(ready() && ((x=read(),x==1))){saved=x;prepare();draw(saved);draw((u8)saved);}}'
    candidate = choices(source)[0]
    assert 'ready() && ((x=read(),x==1))' in candidate
    assert 'saved=x;prepare();draw(1);draw((u8)1);' in candidate


def test_stable_comparison_operand_is_reused_without_assuming_its_numeric_value():
    source = 'void f(void){u16 x;void *selector;selector=fetch();x=read();if(x==(s32)selector){draw(x);}}'
    candidate = choices(source)[0]
    assert 'draw(((u16)((s32)selector)))' in candidate
    assert candidate.count('fetch()') == 1


def test_pure_captured_pointer_expression_preserves_its_cast_semantics():
    source = 'void f(void){u16 x;u16 saved;void *selector;selector=(void*)(1);if(ready()){other();}else{if((x=read(),x==((s32)selector))){saved=x;draw(saved);}}}'
    candidate = choices(source)[0]
    assert 'draw(((u16)((s32)((void *)((void*)(1))))))' in candidate
    assert 'selector=(void*)(1);' in candidate


def test_labels_in_a_sibling_arm_do_not_bypass_the_guard():
    source = 'void f(void){u16 x;x=read();if(x==1){draw(x);}else{outside:other();}goto outside;}'
    assert 'draw(1)' in choices(source)[0]


@pytest.mark.parametrize('body', [
    'x=2;draw(x);', '(x)=2;draw(x);', '((x))+=2;draw(x);', '++(x);draw(x);',
    'draw(x);x++;', 'if(ready()){x=2;}draw(x);', 'jump:draw(x);',
    'switch(mode()){case 0:draw(x);}', 'int x;draw(x);', 'Other x;draw(x);',
    'for(Other x=0;x<2;x++){draw(x);}', 'void (*x)(int);draw(x);',
    'escape(&(x));draw(x);', 'draw(sizeof(x));', 'draw(__builtin_constant_p(x));',
    'MUTATE(x);draw(x);', 'MUTATE();draw(x);',
])
def test_mutation_escape_shadowing_reflection_and_alternate_entry_are_rejected(body):
    header = HEADER+'\n#define MUTATE(v) ((v)=2)\n#define MUTATE() x=2\n'
    assert not choices(SOURCE.replace('draw((u8)((s32)x));', body), header)


@pytest.mark.parametrize('condition', ['x!=1','x==1 || ready()','!(x==1)','x==1 && (x=2)','x==1, ready()','x==read()','x==100','x==-1','x==1U'])
def test_unknown_or_non_dominating_equality_is_not_a_fact(condition):
    assert not choices(SOURCE.replace('x==1', condition))


def test_only_the_last_conjunct_supplies_a_fact():
    source = 'void f(void){u16 x;u16 y;if(x==1 && (y==2)){draw(x);draw(y);}}'
    candidate = choices(source)[0]
    assert 'draw(x);draw(2)' in candidate


@pytest.mark.parametrize('source', [
    SOURCE.replace('x=read();', 'escape(&(x));x=read();'),
    SOURCE.replace('x=read();', 'ESCAPE(x);x=read();'),
    SOURCE.replace('x=read();', 'CAPTURE();x=read();'),
    SOURCE.replace('u16 x;', 'volatile u16 x;'),
    SOURCE.replace('u16 x;', ''),
    SOURCE.replace('u16 x;', 'u16 x;void nested(void){x=2;}'),
])
def test_local_must_be_unqualified_unescaped_and_not_captured(source):
    header = HEADER+'\n#define ESCAPE(value) publish(&(value))\n#define CAPTURE() publish(&x)\n#define CAPTURE() 0\n'
    assert not choices(source, header)


@pytest.mark.parametrize('header,arch', [(HEADER,'aarch64'), ('','mips'), (HEADER+'\n#define u16 unsigned short','mips'), (HEADER+'\n#define if(v) v','mips'), (HEADER+'\n#if OPTION\n#endif','mips')])
def test_unknown_abi_or_preprocessing_is_not_proof(header, arch):
    assert not choices(header=header, arch=arch)


@pytest.mark.parametrize('setup', [
    'if(ready()) selector=(void*)1;',
    'if(ready()){selector=(void*)1;}',
    'selector=(void*)1;selector=fetch();',
    'selector=(void*)1;entry:other();',
    'selector=(void*)1;while(ready()){selector=fetch();}',
])
def test_captured_rhs_constant_requires_unchanged_dominating_assignment(setup):
    source = 'void f(void){u16 x;void *selector;'+setup+'x=read();if(x==(s32)selector){draw(x);}}'
    results = choices(source)
    assert results
    assert 'draw(((u16)((s32)selector)))' in results[0]


def test_rhs_mutation_or_escape_invalidates_reuse():
    source = 'void f(void){u16 x;void *selector;selector=fetch();if(x==(s32)selector){selector=fetch();draw(x);}}'
    assert not choices(source)
    assert not choices(source.replace('selector=fetch();draw(x);','escape(&selector);draw(x);'))


def test_grouped_proposal_combines_independent_proven_call_sites():
    source = 'void f(void){u16 x;u16 y;if(x==1){draw(x);}if(y==2){draw(y);}}'
    candidates = choices(source)
    assert 'draw(1)' in candidates[0] and 'draw(2)' in candidates[0]
    assert len(candidates) == 3


def test_condition_write_does_not_reuse_an_older_pointer_capture():
    source = 'void f(void){u16 x;void *selector;selector=(void*)1;if((selector=fetch(),x==(s32)selector)){draw(x);}}'
    candidate = choices(source)[0]
    assert 'draw(((u16)((s32)selector)))' in candidate


def test_an_earlier_macro_cannot_secretly_escape_a_comparison_dependency():
    source = 'void f(void){u16 x;void *selector;selector=fetch();ESCAPE(selector);if(x==(s32)selector){draw(x);}}'
    assert not choices(source, HEADER+'\n#define ESCAPE(value) publish(&(value))\n')


def test_source_lines_and_unrelated_calls_are_preserved():
    source = SOURCE.replace(';',';\n')
    candidate = choices(source)[0]
    assert candidate.count('\n') == source.count('\n')


def test_shared_pipeline_exposes_family_for_agents_and_deterministic_search():
    from decompnet.app import draft_repairs
    labels = {}
    candidates = draft_repairs.variants(SOURCE,'f','mips',header=HEADER,labels=labels,limit=None)
    assert repair.FAMILY in draft_repairs.AGENT_FAMILIES
    assert any(labels.get(text) == repair.FAMILY and 'draw((u8)((s32)1))' in text for text in candidates)


@pytest.mark.parametrize('arch',['mips','ppc','x86','arm','thumb'])
def test_supported_32_bit_targets_share_the_same_source_proof(arch):
    assert choices(arch=arch)


def test_synthetic_execution_keeps_reads_calls_and_branch_behaviour(tmp_path):
    import shutil
    import subprocess
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('host C compiler not installed')
    source = 'void f(void){u16 x;u16 saved;if(ready() && ((x=read(),x==1))){saved=x;prepare();draw(saved);draw((u8)saved);}}'
    prefix = HEADER+'''\n#include <stdio.h>
static int value, reads, events;
static int ready(void){events=events*10+2;return 1;}
static u16 read(void){reads++;return (u16)value;}
static void prepare(void){events=events*10+3;}
static void draw(int x){events=events*10+x;}
'''
    suffix = '\nint main(void){int i;int values[]={0,1,257};for(i=0;i<3;i++){value=values[i];reads=events=0;f();printf("%d %d\\n",reads,events);}return 0;}'
    outputs = []
    for index, text in enumerate([source, choices(source)[0]]):
        file = tmp_path/f'case{index}.c'
        exe = tmp_path/f'case{index}'
        file.write_text(prefix+text+suffix)
        subprocess.run([cc,'-std=c99',str(file),'-o',str(exe)],check=True,capture_output=True,timeout=15)
        outputs.append(subprocess.check_output([str(exe)],text=True,timeout=5))
    assert outputs == ['1 2\n1 2311\n1 2\n']*2


@pytest.mark.parametrize('extra', [
    'void mutate(u16 &value);',
    'typedef u16 &Ref;void mutate(Ref value);',
    'void mutate(Unknown value);',
])
def test_unknown_or_reference_parameters_can_mutate_a_local_without_visible_address(extra):
    source = SOURCE.replace('draw((u8)((s32)x));','mutate(x);draw(x);')
    assert not choices(source, HEADER+extra)


def test_overloaded_or_reflected_calls_are_not_c_value_substitutions():
    assert not choices(header=HEADER+'void draw(u16);')
    assert not choices(SOURCE.replace('x=read();','auto &alias=x;x=read();'))
    assert not choices(SOURCE.replace('x=read();','auto change=[&](){x=2;};x=read();'))


def test_plain_pointer_comparison_dependency_is_still_passed_by_value():
    source = 'void f(void){u16 x;void *selector;selector=fetch();if(x==(s32)selector){use(selector);draw(x);}}'
    assert choices(source, HEADER+'void use(void *);')


def test_unknown_constructor_syntax_is_not_an_unescaped_c_local():
    source = SOURCE.replace('x=read();','Opaque capture=x;x=read();')
    assert not choices(source)


def test_commented_prototypes_do_not_prove_argument_passing():
    source = SOURCE.replace('draw((u8)((s32)x));','modify(x);draw(x);')
    assert not choices(source, HEADER+'/* void modify(int); */')


def test_missing_optional_c_parser_cannot_crash_shared_search(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'pycparser', None)
    assert not choices()
