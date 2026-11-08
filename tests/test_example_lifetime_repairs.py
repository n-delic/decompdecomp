import shutil
import subprocess

import pytest

from decompnet.app import example_lifetime_repairs as repairs


def test_merge_branch_declarations_preserves_initializer_order():
    source='void f(int flag,int *a,int *b){if(flag){int *src=a;int n=4;while(n--)*src++=0;}else{int n=4;int *src=b;while(n--)*src++=1;}}'
    got=repairs.branches(source,'f')
    assert got and 'int * src;' in got[0][1]
    assert 'src = a;' in got[0][1] and 'src = b;' in got[0][1]
    assert got[0][1].index('src = a;')<got[0][1].index('n = 4;')
    assert not repairs.branches(source.replace('int flag','volatile int flag'),'f')
    shadow=repairs.branches(source.replace('if(flag)','int n; if(flag)'),'f')
    assert all('int n; {\n' in text for _,text in shadow)
    assert all(text.count('int n;')==2 for _,text in shadow)


def test_loop_exit_requires_latch_on_every_path():
    source='typedef unsigned char u8; int f(int flag){u8 i;if(flag){i=0;do{i++;}while(i<3);}else{i=0;do{i++;}while(i<6);}return i;}'
    got=repairs.loop_values(source,'f')
    assert got and got[0][1].count('(__repair_exit_value = i)')==2
    assert 'return __repair_exit_value;' in got[0][1]
    assert not repairs.loop_values(source.replace('i++;','if(flag==2)break;i++;',1),'f')
    assert not repairs.loop_values(source.replace('return i;','i++;return i;'),'f')
    assert not repairs.loop_values(source.replace('else{i=0;do{i++;}while(i<6);}','else{i=2;}'),'f')


def test_float_call_stages_before_integer_cast_without_reassociation():
    source='void sink(int,int,int);void f(float x,float y,int z){sink((int)(x*160.0f+160.0f),(int)((-y)*120.0f+120.0f),z);}'
    got=repairs.float_calls(source,'f')
    assert got and '= x*160.0f+160.0f;' in got[0][1]
    assert 'sink((int)(__repair_float_arg0),(int)(__repair_float_arg1),z)' in got[0][1]
    assert not repairs.float_calls(source.replace(',z);',',z++);'),'f')
    assert not repairs.float_calls(source.replace('x*160.0f','load()*160.0f'),'f')
    assert not repairs.float_calls(source.replace('160.0f','160.0'),'f')


def test_inline_chain_retains_word_conversion_and_no_side_effects():
    source='int f(int i,unsigned short *p){int t;int c;t=p[0];t=i+t;c=(10-t)+t;return c;}'
    got=repairs.scalar_chains(source,'f')
    assert got and got[0][1].count('(int)(p[0])')==2
    assert not repairs.scalar_chains(source.replace('unsigned short *p','volatile unsigned short *p'),'f')
    assert not repairs.scalar_chains(source.replace('c=(10-t)+t','c=g(t)+t'),'f')
    assert not repairs.scalar_chains(source.replace('return c;','return c+t;'),'f')
    assert not repairs.scalar_chains(source.replace('p[0]','p[i++]'),'f')


def test_pointer_update_after_error_return_keeps_other_paths_and_claims_no_alias():
    source='int check(int*,int*); int f(void){int a[4];int b[4];int *p;int ret;p=a;ret=check(p,b);p=b;if(ret!=0)return ret;return *p;}'
    got=repairs.success_pointers(source,'f')
    assert got and 'if(ret!=0)return ret; p=b;' in got[0][1]
    assert not repairs.success_pointers(source.replace('p=b;','global=b;'),'f')
    assert not repairs.success_pointers(source.replace('return *p;','capture(&p);return *p;'),'f')
    assert not repairs.success_pointers(source.replace('int *p;','volatile int *p;'),'f')
    assert not repairs.success_pointers(source.replace('p=b;','p=next();'),'f')


def test_generated_integer_variants_have_same_observable_results(tmp_path):
    compiler=shutil.which('cc')
    if not compiler:pytest.skip('host C compiler unavailable')
    sources=[
        ('typedef unsigned char u8; int f(int flag){u8 i;if(flag){i=0;do{i++;}while(i<3);}else{i=0;do{i++;}while(i<6);}return i;}',repairs.loop_values),
        ('int f(int i){unsigned short values[1]={31};unsigned short *p=values;int t;int c;t=p[0];t=i+t;c=(100-t)+t;return c;}',repairs.scalar_chains),
        ('int f(int flag){int v;int *p=&v;if(flag){int *src=p;int n=4;while(n--)*src=n;}else{int n=4;int *src=p;while(n--)*src=n+1;}return v;}',repairs.branches),
    ]
    for index,(source,generate) in enumerate(sources):
        variants=generate(source,'f');assert variants
        outputs=[]
        for n,text in enumerate([source]+[s for _,s in variants]):
            path=tmp_path/f'{index}-{n}.c';exe=tmp_path/f'{index}-{n}'
            path.write_text('#include <stdio.h>\n'+text+'\nint main(void){int i;for(i=-128;i<=128;i++)printf("%d\\n",f(i));return 0;}\n')
            subprocess.run([compiler,'-std=c89','-pedantic-errors','-O2',str(path),'-o',str(exe)],check=True)
            outputs.append(subprocess.check_output([str(exe)]))
        assert all(x==outputs[0] for x in outputs)


def test_loop_exit_ignores_overwritten_middle_phase_and_handles_switch_goto():
    source=('typedef unsigned char u8;int f(int mode){u8 i;switch(mode){'
            'case 1:i=0;do{i++;}while(i<6);goto end;'
            'case 2:i=0;do{i++;}while(i<3);i=3;do{i++;}while(i<6);break;'
            'default:return -1;}end:return i;}')
    got=repairs.loop_values(source,'f')
    assert got and all(text.count('(__repair_exit_value = i)')==2 for _,text in got)
    assert any('goto end' not in text and 'end:' not in text for _,text in got)
    assert not repairs.loop_values(source.replace('end:return i;','end:i &= 3;return i;'),'f')


def test_float_staging_folds_only_dead_unescaped_copies():
    source=('void sink(int,int,int);void f(float x,float y,int z){float a;float b;'
            'sink(z,z,z);a=x;b=-y;sink((int)(a*160.0f+160.0f),(int)(b*120.0f+120.0f),z);}')
    got=repairs.float_calls(source,'f')
    assert got and 'a=x;' not in got[0][1] and 'b=-y;' not in got[0][1]
    assert '((x)*160.0f+160.0f)' not in got[0][1]  # expression tokens retained, no reassociation
    escaped=source.replace('a=x;','capture(&a);a=x;')
    assert all('a=x;' in text for _,text in repairs.float_calls(escaped,'f'))
    live=source[:-1]+'sink((int)a,z,z);}'
    assert all('a=x;' in text for _,text in repairs.float_calls(live,'f'))


def test_host_compiler_float_and_pointer_observations(tmp_path):
    compiler=shutil.which('cc')
    if not compiler:pytest.skip('host C compiler unavailable')
    programs=[
        ('int result;void sink(int a,int b,int c){result=a+b+c;}void f(float x,float y,int z){float a;float b;a=x;b=-y;sink((int)(a*160.0f+160.0f),(int)(b*120.0f+120.0f),z);}',
         repairs.float_calls,'int main(void){int i;for(i=-100;i<100;i++){f(i/7.0f,i/11.0f,i);printf("%d\\n",result);}return 0;}'),
        ('int status;int check(int *a,int*b){return status;}int f(void){int a[1]={31};int b[1]={42};int *p;int ret;p=a;ret=check(p,b);p=b;if(ret!=0)return ret;return *p;}',
         repairs.success_pointers,'int main(void){for(status=-3;status<4;status++)printf("%d\\n",f());return 0;}'),
    ]
    for index,(source,generate,main) in enumerate(programs):
        generated=generate(source,'f');assert generated
        for optimize in ('-O0','-O2'):
            outputs=[]
            for number,text in enumerate([source]+[s for _,s in generated]):
                path=tmp_path/f'check-{index}-{optimize}-{number}.c';exe=path.with_suffix('')
                path.write_text('#include <stdio.h>\n'+text+'\n'+main+'\n')
                subprocess.run([compiler,'-std=c89','-pedantic-errors',optimize,'-ffp-contract=off',str(path),'-o',str(exe)],check=True)
                outputs.append(subprocess.check_output([str(exe)]))
            assert all(value==outputs[0] for value in outputs)


def test_alias_qualifiers_member_names_and_hidden_index_escape():
    source='typedef unsigned char u8;int f(void){u8 i;i=0;do{i++;}while(i<6);poke(&((i)));return i;}'
    assert not repairs.loop_values(source,'f')
    plain=source.replace('poke(&((i)));','')
    assert not repairs.loop_values(plain,'f','typedef volatile unsigned char u8;')
    field=plain.replace('int f(void)','int f(struct S *s)').replace('return i;','return s->i+i;')
    generated=repairs.loop_values(field,'f')
    assert generated and all('s->i+__repair_exit_value' in text for _,text in generated)
    float_source='struct S{float x;float y;};void g(int,int);void f(int x,int y){g((int)(x),(int)(y));}'
    assert not repairs.float_calls(float_source,'f')
