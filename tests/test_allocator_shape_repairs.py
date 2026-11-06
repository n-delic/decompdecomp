import shutil
import subprocess

import pytest

from decompnet.app import allocator_shape_repairs as repairs
from decompnet.app import repair_ranking


def test_index_candidates_keep_store_value_pointer_and_conditional_scope():
    source='void f(void){unsigned int *p; if(flag) p[0]=0xE7000000;}'
    candidates=repairs.indices(source,'f')
    assert len(candidates)==2
    assert all('p[__repair_constant_index]=0xE7000000;' in c for c in candidates)
    assert all('if(flag) {' in c for c in candidates)
    assert not repairs.indices(source.replace('p[0]','next()[0]'),'f')
    assert not repairs.indices(source.replace('0xE7000000','next()'),'f')


def test_balanced_calls_keep_order_and_include_grouped_candidates():
    source='void f(void){int i; i=0;put(1);put(2);put(3);}'
    candidates=repairs.balanced_calls(source,'f')
    assert candidates and len(candidates)<=16
    assert any(c.count('/*__repair_call_balance*/')==2 for c in candidates)
    assert all('if(i)' in c for c in candidates)
    assert not repairs.balanced_calls(source.replace('i=0;',''),'f')
    assert not repairs.balanced_calls(source.replace('int i;','volatile int i;'),'f')
    assert not repairs.balanced_calls(source.replace('i=0;','if(flag)i=0;'),'f')


def test_generated_shapes_keep_runtime_values_and_call_order(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host compiler unavailable')
    source=('int trace; void put(int x){trace=trace*10+x;}\n'
            'int f(int input){int i;unsigned int data[1];unsigned int *p;'
            'p=data;i=0;i=input;put(1);if(input){put(2);}put(3);p[0]=123;return data[0];}')
    candidates=repairs.indices(source,'f')+repairs.balanced_calls(source,'f')
    assert candidates
    for index,candidate in enumerate(candidates):
        path=tmp_path/f'{index}.c';binary=tmp_path/f'{index}'
        path.write_text(candidate+'\nint main(void){trace=0;if(f(0)!=123||trace!=13)return 1;'
                        'trace=0;if(f(1)!=123||trace!=123)return 2;return 0;}')
        subprocess.run([cc,'-std=c99','-O2',str(path),'-o',str(binary)],check=True,capture_output=True)
        subprocess.run([str(binary)],check=True,timeout=5)


def test_family_demotion_is_reset_for_a_new_parent(monkeypatch):
    from decompnet.app import agent_tokens
    monkeypatch.setattr(agent_tokens,'target_scope',lambda *a:'same build')
    assert repair_ranking.effect_scope(None,None,'old')!=repair_ranking.effect_scope(None,None,'changed')
    assert repair_ranking.effect_scope(None,None,'old')==repair_ranking.effect_scope(None,None,'old')


def test_changed_parent_restores_a_demoted_family(monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import agent_tokens
    monkeypatch.setattr(agent_tokens,'target_scope',lambda *a:'same build')
    net=SimpleNamespace()
    old=repair_ranking.effect_scope(net,None,'old source')
    new=repair_ranking.effect_scope(net,None,'new source')
    report={'score':10,'rows':[('r','move $s0, $v0','move $s1, $v0')]}
    for _ in range(2):repair_ranking.remember(net,old,'constant_index_temporary',report,report)
    assert repair_ranking.demote(net,old,['constant_index_temporary','other'])==['other','constant_index_temporary']
    assert repair_ranking.demote(net,new,['constant_index_temporary','other'])==['constant_index_temporary','other']
