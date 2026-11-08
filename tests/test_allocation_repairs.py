"""Homemade allocator-shape examples; no game source or expected game bytes."""


from decompnet.app import allocation_repairs as repairs


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
