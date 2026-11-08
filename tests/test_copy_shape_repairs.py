import shutil
import subprocess

import pytest

from decompnet.app import copy_shape_repairs as repairs

COPY = 'typedef unsigned int u32; void f(u32 *a,u32 *b){u32 *s,*d;int i;s=a;d=b;for(i=4;i!=0;--i){*d++=*s++;}s=a+4;d=b+4;for(i=4;i!=0;--i){s--;d--;*s=*d;}}'


FORWARD = COPY.replace('s--;d--;*s=*d;', '*d++=*s++;').replace('s=a+4;d=b+4;', 's=a;d=b;')


@pytest.mark.parametrize('replacement', ['volatile u32 *s,*d;', 'u32 *s; int *d;', 'u32 *s,*d; int *escaped=&i;'])
def test_reject_unknown_or_escaped_storage(replacement):
    assert not repairs.variants(FORWARD.replace('u32 *s,*d;',replacement),'f')


def test_copy_observable_results_and_overlap(tmp_path):
    cc=shutil.which('cc')
    if not cc: pytest.skip('C compiler unavailable')
    for body in (FORWARD,FORWARD.replace('*d++=*s++;','s--;d--;*d=*s;').replace('s=a;d=b;','s=a+4;d=b+4;')):
        candidates=repairs.variants(body,'f');assert candidates
        outputs=[]
        for i,text in enumerate([body]+[x for _,x in candidates]):
            file=tmp_path/f'{i}.c';exe=tmp_path/f'{i}'
            file.write_text('#include <stdio.h>\n'+text+'\nint main(void){u32 x[16];int i;for(i=0;i<16;i++)x[i]=i;f(x,x+1);for(i=0;i<16;i++)printf("%u,",x[i]);return 0;}')
            subprocess.run([cc,'-std=gnu89','-O2',str(file),'-o',str(exe)],check=True,capture_output=True)
            outputs.append(subprocess.check_output([str(exe)]))
        assert len(set(outputs))==1


COMMAND = 'typedef unsigned int u32;typedef struct {u32 *dl;} State;void f(State *o){u32 *p;p=o->dl;o->dl=p+2;p[0]=1;p[1]=2;}'


def test_typed_command_preserves_load_update_store_order():
    got=repairs.variants(COMMAND,'f')
    assert got and got[0][0]=='typed_command_record'
    text=got[0][1]
    assert text.index('o->dl =') < text.index('__repair_command_ptr->w0 =') < text.index('__repair_command_ptr->w1 =')
    assert not repairs.variants(COMMAND.replace('p[0]=1','p[0]=next()'),'f')
    assert not repairs.variants(COMMAND.replace('u32 *dl','void *dl'),'f')


OVERLAP = ('typedef unsigned int u32;void f(u32 *a,u32 *b){u32 *s,*d;int i;'
           'if(((u32)b<(u32)(a+4))&((u32)a<(u32)b))'
           '{s=a+4;d=b+4;for(i=4;i!=0;--i){s--;d--;*d=*s;}}'
           'else{s=a;d=b;for(i=4;i!=0;--i){*d++=*s++;}}'
           'if(((u32)a<(u32)(b+4))&((u32)b<(u32)a))'
           '{s=b+4;d=a+4;for(i=4;i!=0;--i){s--;d--;*d=*s;}}'
           'else{s=b;d=a;for(i=4;i!=0;--i){*d++=*s++;}}}')


def test_overlap_helper_requires_dead_scratch_on_every_exit():
    got=repairs.copy_branches(OVERLAP,'f')
    assert got and '__repair_overlap_copy(b, a, a+4);' in got[0][1]
    assert not repairs.copy_branches(OVERLAP[:-1]+'*s=i;}', 'f')
    assert not repairs.copy_branches(OVERLAP.replace('int i;', 'int i;int *escape=&i;'), 'f')
    assert not repairs.copy_branches(OVERLAP.replace('int i;', 'int i;{int i;}'), 'f')


@pytest.mark.parametrize('source', [OVERLAP,COMMAND])
def test_record_and_overlap_generated_c_behaviour(source,tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('C compiler unavailable')
    candidates=repairs.copy_branches(source,'f') if source==OVERLAP else repairs.variants(source,'f')
    assert candidates
    outputs=[]
    for i,text in enumerate([source]+[s for _,s in candidates]):
        harness=('f(x,x+1);' if source==OVERLAP else 'State state;state.dl=x;f(&state);printf("%ld,",(long)(state.dl-x));')
        path=tmp_path/f'{i}.c';exe=tmp_path/f'{i}'
        path.write_text('#include <stdio.h>\n'+text+'\nint main(void){u32 x[16];int i;for(i=0;i<16;i++)x[i]=i;'+harness+'for(i=0;i<16;i++)printf("%u,",x[i]);return 0;}')
        subprocess.run([cc,'-std=gnu89','-O2',str(path),'-o',str(exe)],check=True,capture_output=True)
        outputs.append(subprocess.check_output([str(exe)]))
    assert len(set(outputs))==1


def test_comma_shadows_and_helper_name_collisions_are_ignored():
    assert not repairs.copy_branches(OVERLAP.replace('int i;', 'int i;{int x,i;}'),'f')
    assert not repairs.variants(FORWARD.replace('int i;', 'int i;{u32 *x,*s;}'),'f')
    assert not repairs.variants(COMMAND.replace('u32 *p;', 'u32 *p;int __repair_command_ptr;'),'f')
