import shutil
import subprocess

import pytest

from decompnet.app import capture_followup_repairs as repairs
from decompnet.app import draft_repairs, macro_repairs, stack_repairs


def family(source, name, kind, header='', callees=''):
    return [s for f,s in repairs.variants(source,name,header,callees) if f==kind]


def test_scalar_declaration_swaps_reach_later_pairs():
    source = 'int f(void){'+' '.join('int v'+str(i)+';' for i in range(10))+'return 0;}'
    swaps = [s for f,s in stack_repairs.variants(source,'f') if f=='local_declaration_order']
    assert any('int v8; int v7;' in s for s in swaps)
    assert len(swaps)<=12


def test_typed_clamp_uses_field_type_and_retains_comparison():
    header = 'typedef struct {short limit;} Info; typedef struct {Info *info;} Obj;'
    source = 'int f(Obj *p){int m;m=0;if(m<p->info->limit)m=p->info->limit;return m;}'
    result = family(source,'f','typed_clamp_load',header)
    assert len(result)==1
    assert 'short __repair_field;' in result[0] and '__repair_field = p->info->limit;' in result[0]
    assert 'm < __repair_field' in result[0]
    assert not family(source,'f','typed_clamp_load')
    assert not family(source,'f','typed_clamp_load',header.replace('short limit','volatile short limit'))
    assert not family(source.replace('m=0;','escape(&m);m=0;'),'f','typed_clamp_load',header)


def test_typed_pointer_staging_uses_existing_member_type():
    header = 'typedef struct {int x;} Node; typedef struct {Node *list;} Obj;'
    source = 'int f(Obj *o,int i){Node *node;node=&o->list[i];return node->x;}'
    result = family(source,'f','typed_pointer_load',header)
    assert result and 'Node *__repair_list = o->list;' in result[0]
    assert not family(source,'f','typed_pointer_load',header.replace('Node *list','void *list'))


def test_increment_order_and_call_timing_are_bounded_and_local():
    source = 'int f(int *src,int *out){int i;for(i=0;i<3;i++,src++){*out++=read(src);src++;}return 0;}'
    assert family(source,'f','loop_update_order')
    result = family(source,'f','call_postincrement')
    assert result and '*out = read(src++); out++;' in result[0]
    assert not family(source.replace('int i;','int i;escape(&src);'),'f','call_postincrement')
    assert not family('int f(int *src){*global++=read(src);src++;return 0;}','f','call_postincrement')


def test_known_callee_constant_writes_resize_object_not_unrelated_padding():
    source = 'void fill(short *p);void f(void){short value;value=0;fill(&value);use(value);}'
    callee = 'void fill(short *p){p[0]=1;p[1]=2;p[2]=3;p[3]=4;}'
    result = family(source,'f','callee_buffer_extent',callees=callee)
    assert len(result)==1 and 'short value[4];value[0]=0;fill(value);use(value[0]);' in result[0]
    for body in ['p[0]=1;p[3]=4;', 'p[n]=1;', 'p[0]=1;p[1]=other(p);', 'p[0]=1;p[33]=2;']:
        assert not family(source,'f','callee_buffer_extent',callees='void fill(short *p){'+body+'}')
    assert not family(source.replace('use(value)','use(sizeof(value))'),'f','callee_buffer_extent',callees=callee)
    assert not family(source.replace('value=0;','escape(&value);value=0;'),'f','callee_buffer_extent',callees=callee)
    assert not family(source,'f','callee_buffer_extent',callees=callee.replace('short *p','int *p'))


def test_known_signature_prepends_only_unambiguous_type_proven_parameters():
    source = 'int helper(int x);int f(short *arg0,float arg1){return helper(*arg0);}'
    callee = 'int helper(void *object,float mix,short value){return value;}'
    result = family(source,'f','known_call_signature',callees=callee)
    assert result and 'helper(arg0, arg1, *arg0)' in result[0]
    assert 'helper(void* object, float mix, short value)' in result[0] or 'helper(void * object, float mix, short value)' in result[0]
    assert not family(source,'f','known_call_signature')
    assert not family(source.replace('float arg1','float arg1,float other'),'f','known_call_signature',callees=callee)
    assert not family(source,'f','known_call_signature',callees=callee+'\n'+callee)


def test_macro_adapter_preserves_directives_and_excludes_dependent_statements():
    source = 'int f(int x){\n#define ALIAS x\nint a;int b;a=1;b=2;return ALIAS+a+b;}'
    labels={}
    result = draft_repairs.variants(source,'f','mips',labels=labels,limit=None)
    assert result and all('#define ALIAS x' in s and 'return ALIAS+a+b;' in s for s in result)
    assert 'protected' in macro_repairs.diagnostic(source)
    assert not draft_repairs.variants(source.replace('#define ALIAS x','#if X\n#define ALIAS x\n#endif'),'f','mips')
    def move(masked):
        return [('unsafe',masked.replace('a=1;','').replace('return ALIAS','a=1;return ALIAS'))]
    assert not macro_repairs.variants(source.replace('b=2;','use(ALIAS);b=2;'),'f',move)
    assert 'skipped' in macro_repairs.diagnostic(source.replace('#define ALIAS x','#define ALIAS volatile int'))


def test_new_families_reach_shared_draft_generator():
    header = 'typedef struct {short limit;} Obj;'
    source = 'int f(Obj *p){int m;m=0;if(m<p->limit)m=p->limit;return m;}'
    labels={}
    proposals=draft_repairs.variants(source,'f','mips',header=header,labels=labels,limit=None)
    assert any(labels[s]=='typed_clamp_load' for s in proposals)
    assert set(repairs.FAMILIES)<=set(draft_repairs.AGENT_FAMILIES)


def test_generated_representative_candidates_compile_as_c89(tmp_path):
    cc=shutil.which('clang')
    if cc is None:pytest.skip('needs clang for syntax verification')
    header='typedef struct {int x;} Node; typedef struct {int unused;short limit;Node *list;} Obj; int read(int *p); void use(int v);'
    cases=[('int f(Obj *p){int m;m=0;if(m<p->limit)m=p->limit;return m;}','typed_clamp_load',''),
           ('int f(int *src,int *out){*out++=read(src);src++;return 0;}','call_postincrement',''),
           ('int f(Obj *o,int i){Node *node;node=&o->list[i];return node->x;}','typed_pointer_load',''),
           ('int f(int *src){int i;for(i=0;i<3;i++,src++){}return i;}','loop_update_order',''),
           ('void fill(short *p);int f(void){short value;value=0;fill(&value);return value;}','callee_buffer_extent','void fill(short *p){p[0]=1;p[1]=2;}'),
           ('int helper(int x);int f(short *arg0,float arg1){return helper(*arg0);}','known_call_signature','int helper(void *object,float mix,short value){return value;}')]
    for index,(source,kind,callee) in enumerate(cases):
        generated=family(source,'f',kind,header,callee)
        assert generated
        path=tmp_path/f'case{index}.c';path.write_text(header+generated[0])
        subprocess.run([cc,'-std=c89','-Werror=declaration-after-statement','-fsyntax-only',str(path)],check=True,capture_output=True)
