"""Synthetic controls for staged scalar operands and an equivalent switch CFG."""


import ctypes
import shutil
import subprocess

import pytest

from decompnet.app import switch_constant_dispatch as repair

BASE = '''void effect(int);int f(int rank){int i;int result=7;for(i=0;i<3;i++){
int copy;copy=i;
switch(rank){
case 0: if(copy&1){effect(10);}else{effect(11);}result=4;break;
case 1: effect(20);result=2;break;
case 2: effect(30);result=1;break;
case 3: effect(40);result=0;break;
}
effect(50+result);
}return result;}'''


def choices(source=BASE, header=''):
    return repair.variants(source,'f',header,language='c',int_bits=32)


def test_single_consumed_operand_and_forward_graph_proposal():
    result = choices()
    assert len(result) == 1 and result[0][0] == repair.FAMILY
    text = result[0][1]
    assert text.index('__repair_switch_one = 1;') < text.index('copy=i;')
    assert text.index('if (rank == __repair_switch_one)') < text.index('if (rank == 0)')
    assert text.index('__repair_switch_case0:') < text.index('__repair_switch_case1:')
    assert 'switch(rank)' not in text and '__repair_switch_done:;' in text
    assert not choices(text)  # Never stack another generated graph on this one.


@pytest.mark.parametrize('source',[
    BASE.replace('case 3:', 'default:'),
    BASE.replace('result=4;break;', 'result=4;'),
    BASE.replace('case 3:', 'case 4:'),
    BASE.replace('case 1:', 'case 1+0:'),
    BASE.replace('case 3:', 'case 2:'),
    BASE.replace('result=4;', 'if(copy)break;result=4;'),
    BASE.replace('result=4;', 'continue;result=4;'),
    BASE.replace('result=4;', 'return 4;'),
    BASE.replace('result=4;', 'int x;result=4;'),
    BASE.replace('result=4;', 'while(copy){copy--;}result=4;'),
    BASE.replace('result=4;', 'switch(copy){case 1:break;}result=4;'),
    BASE.replace('copy=i;', 'out:copy=i;'),
    BASE.replace('copy=i;', 'goto out;out:copy=i;'),
    BASE.replace('int rank', 'volatile int rank'),
    BASE.replace('int rank', 'int *rank').replace('switch(rank)', 'switch(*rank)'),
    BASE.replace('int rank', 'float rank'),
    BASE.replace('switch(rank)', 'switch(rank++)'),
    BASE.replace('copy=i;', 'escape(&rank);copy=i;'),
    BASE.replace('copy=i;', 'escape(&(rank));copy=i;'),
    BASE.replace('copy=i;', '{int rank;}copy=i;'),
    BASE.replace('copy=i;', '__builtin_foo();copy=i;'),
    BASE.replace('copy=i;', 'asm("nop");copy=i;'),
    BASE.replace('copy=i;', 'consume(({int x=1;x;}));copy=i;'),
    '#include "unknown.h"\n'+BASE,
    '#define rank different\n'+BASE,
    '#define int volatile\n'+BASE,
    '#define effect(x) rank=2\n'+BASE,
    BASE.replace('int rank', 'int &rank'),
    BASE.replace('for(i=0;i<3;i++){', 'for(i=0;i<3;i++){if(i){').replace('}return result;}', '}}return result;}'),
    BASE.replace('int rank', 'void rank'),
])
def test_unsupported_control_types_scope_and_hidden_effects_are_rejected(source):
    assert not choices(source)


def test_scalar_typedef_is_resolved_and_external_selector_is_not_local():
    source = BASE.replace('int rank', 'Count rank')
    assert choices(source,'typedef unsigned int Count;')
    assert not choices(source,'typedef volatile unsigned int Count;')
    assert not choices(source,'typedef struct {int n;} Count;')
    assert not choices(source)
    assert not choices(BASE.replace('int f(int rank)', 'int rank;int f(void)'))
    assert not choices(BASE.replace('int f(int rank)', 'int f(void)').replace('int i;', 'static int rank;int i;'))


@pytest.mark.parametrize('loop', ['for(i=0;i<3;i++)', 'while(i++<3)', 'do'])
def test_braced_loop_forms_and_final_switch_label(loop):
    source = BASE.replace('for(i=0;i<3;i++)',loop).replace('int i;', 'int i=0;')
    if loop == 'do':
        source = source.replace('}return result;', '}while(i++<3);return result;')
    assert choices(source)
    # A shared join still has a valid null statement if no following statement exists.
    assert choices(source.replace('effect(50+result);',''))


def test_executed_results_memory_effects_and_switch_break_paths(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('native C compiler unavailable')
    harness = '''int trace[64];int count;
void effect(int x){trace[count++]=x;}
void reset(void){count=0;}
int entry(int n){reset();return f(n);}
int seen(int n){return trace[n];}
int size(void){return count;}
'''
    libraries = []
    for i, text in enumerate([BASE, choices()[0][1]]):
        path = tmp_path/f'{i}.c'
        library = tmp_path/f'{i}.so'
        path.write_text(text+'\n'+harness)
        subprocess.run([cc,'-shared','-fPIC','-O2',str(path),'-o',str(library)],check=True,
                       capture_output=True,timeout=20)
        libraries.append(ctypes.CDLL(str(library)))
    for rank in [-2147483648,-100,-1,0,1,2,3,4,9,2147483647]:
        outcomes = []
        for native in libraries:
            result = native.entry(rank)
            outcomes.append((result,[native.seen(i) for i in range(native.size())]))
        assert outcomes[0] == outcomes[1]
        assert len(outcomes[0][1]) == (6 if 0 <= rank <= 3 else 3)


def test_unrelated_inner_declaration_cannot_prove_global_selector_type():
    source = BASE.replace('int f(int rank)', 'volatile int rank;int f(void)')
    source = source.replace('int i;', 'int i;{int rank;rank=2;}')
    assert not choices(source)
    assert not choices(BASE, '#define unsigned volatile')
    source = BASE.replace('int f(int rank)', 'int f(void)').replace('int i;', 'int i;int rank=2;')
    assert choices(source)
    source = BASE.replace('int f(int rank)', 'int f(void)').replace('for(i=0;i<3;i++)', 'for(int rank=0;rank<3;rank++)')
    assert not choices(source)
