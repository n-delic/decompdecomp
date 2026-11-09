import pytest

from decompnet.app import equal_value_flow as values


def choices(source, header=''):
    return [text for family,text in values.variants(source,'f',header)
            if family=='cfg_equal_value_alias']


def test_remove_initialized_copy_with_overlapping_original_lifetime():
    source='int f(int x){int value;int saved;value=x&255;saved=value;use(value);return saved*8+saved;}'
    result=choices(source)
    assert result and 'int saved;' not in result[0] and 'saved' not in result[0]
    assert 'value=x&255;use(value);return value*8+value;' in result[0]


def test_copy_survives_calls_branches_and_loop_backedges():
    source='int f(int x){int value;int saved;int i;int total;value=x;saved=value;total=0;for(i=0;i<3;i++){if(x){use(value);}else{other();}total+=saved;}return total;}'
    assert choices(source)
    source='int f(int x){int value;int saved;value=x;if(x){saved=value;use(saved);}return value;}'
    assert choices(source)  # the conditional assignment has no read outside its dominated arm


@pytest.mark.parametrize('body',[
    'if(x){value=x;}saved=value;return saved;',
    'value=x;if(x){saved=value;}return saved;',
    'value=x;while(x--){saved=value;}return saved;',
    'value=x;if(x)goto out;saved=value;out:return saved;',
    'value=x;saved=value;value++;return saved;',
    'value=x;saved=value;if(x){value=3;}return saved;',
    'value=x;saved=value;return use(saved,value++);',
    'value=x;saved=value;return (value=3,saved);',
    'value=x;saved=value;return ++saved;',
    'value=x;saved=value;saved=value;return saved;',
    'value=x;saved=value;escape(&value);return saved;',
    'value=x;saved=value;escape(&(saved));return saved;',
    'value=x;saved=value;{short value;}return saved;',
])
def test_no_missing_initialization_updates_escapes_or_shadows(body):
    assert not choices('int f(int x){int value;int saved;'+body+'}')


def test_types_control_bodies_and_macro_writes_are_boundaries():
    assert not choices('int f(int x){int value;short saved;value=x;saved=value;return saved;}')
    assert not choices('int f(int x){int value;int saved;value=x;if(x)saved=value;return value;}')
    source='int f(int x){int value;int saved;value=x;saved=value;HIDDEN();return saved;}'
    assert not choices(source,'#define HIDDEN() value=2')
    assert not choices(source,'#define HIDDEN() other(value)')
    assert not choices(source.replace('HIDDEN();','asm("nop");'))
    assert not choices(source.replace('int value;','volatile int value;'))
    assert not choices(source.replace('HIDDEN();','__asm("" : "+r"(value));'))
    assert not choices(source.replace('HIDDEN();','_asm { mov value, 3 }'))
    assert not choices(source.replace('HIDDEN();','__builtin_setjmp(buffer);'))


def test_cpp_implicit_reference_escapes_are_not_scalar_value_facts():
    source='int f(int x){int value;int saved;value=x;saved=value;mutate(value);return saved;}'
    assert not choices(source,'void mutate(int &);')
    assert not choices(source,'void mutate(T& out);')
    assert not choices(source,'void mutate(int __attribute__((aligned(4))) &);')
    assert not choices(source,'#include "unknown-parameters.h"')
    assert not choices(source,'#define REF int&\nvoid mutate(REF);')
    assert not choices(source,'import parameters;')
    assert not choices(source.replace('mutate(value);','int &ref=value;mutate(ref);'))
    assert not choices(source.replace('mutate(value);','auto &ref=value;mutate(ref);'))
    assert not choices(source.replace('mutate(value);','int __attribute__((aligned(4))) &ref=value;mutate(ref);'))
    assert not choices(source.replace('mutate(value);','escape(__builtin_addressof(value));'))
    assert not choices(source.replace('mutate(value);','[&](){value=3;}();'))


def test_statement_expression_cannot_hide_a_jump_around_initialization():
    source='int f(int x){int value;int saved;value=x;consume(({if(x)goto out;0;}));saved=value;out:return saved;}'
    assert not choices(source)


def test_typedefs_must_be_visible_ordinary_integers_without_local_shadowing():
    source='int f(int x){s32 value;s32 saved;value=x;saved=value;use(value);return saved;}'
    assert choices(source,'typedef signed int s32;')
    assert not choices(source)
    assert not choices(source,'typedef struct {volatile int field;} s32;')
    assert not choices(source,'typedef volatile int s32;')
    assert not choices(source,'/* typedef signed int s32; */ typedef struct {int field;} s32;')
    assert not choices(source,'#if OTHER\ntypedef int s32;\n#else\ntypedef volatile int s32;\n#endif')
    assert not choices(source,'typedef int s32;\n#define s32 Other')
    assert not choices(source,'#define int volatile_int')
    assert not choices(source.replace('s32 saved;','typedef unsigned int s32;s32 saved;'),'typedef int s32;')


def test_fields_are_not_renamed_and_standalone_lines_are_removed():
    source='int f(int x) {\n  int value;\n  int saved;\n  value=x;\n  saved=value;\n  obj.saved=3;\n  return saved;\n}\n'
    result=choices(source)
    assert result and 'obj.saved=3;' in result[0]
    assert '  int saved;\n' not in result[0] and '  saved=value;\n' not in result[0]
    assert '  return value;\n' in result[0]


@pytest.mark.parametrize('arch',['mips','ppc','x86','arm'])
def test_shared_pipeline_registers_alias_family(arch):
    from decompnet.app import draft_repairs
    source='int f(int x){int value;int saved;int i;value=x;saved=value;for(i=0;i<2;i++){use(value);use(saved);}return saved;}'
    assert 'cfg_equal_value_alias' in draft_repairs.AGENT_FAMILIES
    labels={}
    generated=draft_repairs.variants(source,'f',arch,labels=labels,limit=None)
    aliases=[text for text in generated if labels[text]=='cfg_equal_value_alias']
    assert aliases and all('saved' not in text for text in aliases)


def test_alias_removal_is_prioritized_for_copy_misses_and_bounded():
    from decompnet.app import repair_ranking, repair_selection
    rows=[('r','move $s1, $a0','move $s2, $a0'),('>','','move $a2, $s3')]
    assert repair_ranking.order(['branch_call','cfg_equal_value_alias'],rows)[0]=='cfg_equal_value_alias'
    assert repair_selection.order(['other','cfg_equal_value_alias'],['loop_weight_scope'])[0]=='cfg_equal_value_alias'
    source='int f(int x){int value;'+''.join(f'int saved{i};' for i in range(8))+'value=x;'
    source+=''.join(f'saved{i}=value;use(saved{i});' for i in range(8))+'return value;}'
    assert 0<len(choices(source))<=4


def test_generated_aliases_preserve_executed_paths_and_call_effects(tmp_path):
    import shutil
    import subprocess
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host C compiler unavailable')
    source='''int calls;
void use(int v){calls+=v;}
int f(int x){int value;int saved;int i;int total;
value=x&255;saved=value;use(value);total=0;
for(i=0;i<4;i++){if(x&1){use(value);}total+=saved*(i+1);}
return total;}
'''
    harness='''int main(void){int x;for(x=-300;x<=300;x++){calls=0;
if(f(x)!=10*(x&255))return 1;
if(calls!=(x&255)*((x&1)?5:1))return 2;}return 0;}
'''
    candidates=choices(source)
    assert candidates
    for index,text in enumerate([source]+candidates):
        path=tmp_path/f'{index}.c';path.write_text(text+harness);binary=tmp_path/f'run{index}'
        subprocess.run([cc,'-O2',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=20)
        subprocess.run([str(binary)],check=True,timeout=5)
