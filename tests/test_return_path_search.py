import hashlib
from types import SimpleNamespace

import pytest

from decompnet.app import (
    allocation_targeting,
    deep_permuter,
    loop_weight_repairs,
    return_path_search,
    reviewed_seed_search,
)
from decompnet.app import return_path_hypotheses as hypotheses
from decompnet.arch import get, mips_return_paths
from tests.test_deep_permuter import Log, setup
from tests.test_permute import AGENT
from tests.test_return_contract_repairs import asm

SOURCE='''void f(int a){
 int value;
 int other;
 value=a+1;
 if(value<=0)
 {
   emit(value);
 }
}'''
TARGET=asm('addiu $v0, $a0, 1','bgtz $v0, 0x80001014','move $a1, $v0',
           'li $v0, 3','sw $v0, 0($a0)','jr $ra','nop')


def test_branching_exit_definitions_and_delay_slot_clobber():
    proof=mips_return_paths.read(TARGET)
    assert proof['guarded_exits'][0]['condition']=='> 0'
    assert proof['exits'][0]['definitions']==[0,3] and proof['exits'][0]['ambiguous']
    assert not mips_return_paths.read(TARGET.replace('move $a1, $v0','li $v0, 2'))['guarded_exits']
    assert not mips_return_paths.read(TARGET.replace('jr $ra','jr $t9'))
    assert not mips_return_paths.read(TARGET.replace('0x80001014','missing_label'))
    assert get('x86').source_return_paths is None


def test_likely_delay_slot_executes_only_on_taken_path_and_traps_are_not_returns():
    target=asm('li $v0, 1','beql $a0, $zero, 0x80001014','li $v0, 2',
               'b 0x80001014','nop','jr $ra','nop')
    assert mips_return_paths.read(target)['exits'][0]['definitions']==[0,2]
    trap=asm('beqz $a0, 0x8000100C','nop','break 7','li $v0, 1','jr $ra','nop')
    assert mips_return_paths.read(trap)['exits'][0]['definitions']==[3]
    assert mips_return_paths.read(asm('jal g','li $v0, 3','jr $ra','nop'))['exits'][0]['definitions']==[-1]


def test_direct_caller_consumption_discard_and_unknown_remain_distinct():
    assert mips_return_paths.caller_use(asm('jal f','nop','sw $v0, 0($s0)','jr $ra','nop'),'f')[0]['use']=='consumed'
    assert mips_return_paths.caller_use(asm('jal f','nop','li $v0, 1','jr $ra','nop'),'f')[0]['use']=='discarded'
    assert mips_return_paths.caller_use(asm('jal f','nop','jr $ra','nop'),'f')[0]['use']=='unknown'
    assert mips_return_paths.caller_use(asm('jal g','nop','move $s0, $v0'),'f')==[]


def test_only_final_scalar_guard_is_a_diagnostic_hypothesis():
    proof=mips_return_paths.read(TARGET)
    candidates=hypotheses.variants(SOURCE,'f','','mips',proof)
    assert [f for f,s in candidates]==['path_return','path_return+scalar_scope']
    assert 'if (value > 0) return value;' in candidates[0][1]
    assert '{ int return_value;' in candidates[1][1] and 'emit(return_value)' in candidates[1][1]
    for source in [SOURCE.replace('emit(value);','return;'),SOURCE.replace('int value;','volatile int value;'),
                   SOURCE.replace('}\n}', '}\nemit(a);\n}'),SOURCE.replace('value<=0','value==0')]:
        assert not hypotheses.variants(source,'f','','mips',proof)
    assert not hypotheses.variants(SOURCE,'f','','mips',None)


def test_mapped_middle_quotient_and_estimated_depth_are_prioritized():
    source='int f(int a,int b){\nint x;\nx=a/b;\nx=(a+1)/b;\nx=(a+2)/b;\nreturn x;\n}'
    rows=[('r','move $s4, $a0','move $s5, $a0'),('r','move $s5, $a1','move $s4, $a1')]
    evidence={'allocation':{'values':[dict(pseudo=70,aliases=['s4'],references=10,live_insns=91,legacy_weight=30/91,source_lines=[4]),
                                    dict(pseudo=71,aliases=['s5'],references=7,live_insns=41,legacy_weight=14/41,source_lines=[4])]},
              'value_trace':dict(values=[dict(aliases=['s4'],source_lines=[4]),dict(aliases=['s5'],source_lines=[4])],
                                source_sha256=hashlib.sha256(source.encode()).hexdigest(),header_sha256=hashlib.sha256(b'').hexdigest())}
    proposals=allocation_targeting.proposals(source,'f','',rows,evidence)
    assert proposals and all('do {\nx=(a+1)/b;' in s for f,s in proposals)
    assert all('do {\nx=a/b;' not in s and 'do {\nx=(a+2)/b;' not in s for f,s in proposals)
    assert len(proposals)<=6
    assert not loop_weight_repairs.variants(source,'f',focus_lines=[999])==proposals


def test_generated_pair_is_tested_even_when_single_edit_is_worse_and_never_adopted(monkeypatch):
    net=setup(monkeypatch);net.is_rom=lambda _:True
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,asm=?',(SOURCE,TARGET))
    calls=[]
    def judge(*args):
        source=args[-1];calls.append(source)
        return (True,None,'') if 'return_value' in source else (False,SimpleNamespace(score=999,rows=[]),'')
    net._judge_with=judge
    search=deep_permuter.Search(net,1,12,Log(),net.function())
    original=net.function()['best_source']
    result=reviewed_seed_search.run(search,AGENT,net.project_row('game'),net.function(),generated_only=True)
    assert result['generated_roots']==2 and result['reviewed_roots']==0
    assert len(calls)==2 and result['diagnostic_exact'][0]['diagnostic_only']
    assert net.function()['best_source']==original and net.function()['status']=='open'
    assert 'score 999' in return_path_search.history(net,net.project_row('game'),net.function())
    # A second pass reuses the unchanged failed single-edit diagnostic, but rejudges exact results.
    second=reviewed_seed_search.run(deep_permuter.Search(net,1,12,Log(),net.function()),AGENT,net.project_row('game'),net.function(),generated_only=True)
    assert second['cached_diagnostics']==1 and len(calls)==3


def test_discovery_does_not_load_reviewed_roots(monkeypatch):
    net=setup(monkeypatch)
    monkeypatch.setattr('decompnet.app.reviewed_search_seeds.selected',lambda *a:pytest.fail('discovery must not read reviewed roots'))
    result=reviewed_seed_search.run(deep_permuter.Search(net,1,12,Log(),net.function()),AGENT,net.project_row('game'),net.function(),generated_only=True)
    assert not result['diagnostic_exact'] and result['generated_roots']==0


def test_caller_call_delay_slot_can_consume_the_previous_result():
    caller=asm('jal f','nop','jal g','move $a0, $v0','jr $ra','nop')
    assert mips_return_paths.caller_use(caller,'f')[0]['use']=='consumed'


def test_all_mapped_quotient_sites_are_visited_before_deeper_scopes():
    source='int f(int a,int b){\nint x;\nx=a/b;\nx=(a+1)/b;\nx=(a+2)/b;\nreturn x;\n}'
    choices=loop_weight_repairs.variants(source,'f',focus_lines=[3,4,5],depths=(2,1,3))
    assert len(choices)==9
    assert all(c.count('do {')==2 for _,c in choices[:3])
    assert all(c.count('do {')==1 for _,c in choices[3:6])
    assert 'x=(a+2)/b;' in choices[2][1]


def test_partial_return_hypotheses_preserve_observed_void_side_effects(tmp_path):
    import shutil
    import subprocess
    compiler=shutil.which('cc')
    if not compiler:pytest.skip('native C compiler unavailable')
    candidates=hypotheses.variants(SOURCE,'f','','mips',mips_return_paths.read(TARGET))
    for number,(_,candidate) in enumerate(candidates):
        program='#include <assert.h>\nint sum; void emit(int n){sum+=n;}\n'
        program+=SOURCE.replace('void f(', 'void original(')+'\n'+candidate
        program+='\nint main(void){int i,expected;for(i=-100;i<100;i++){sum=0;original(i);expected=sum;sum=0;f(i);assert(sum==expected);}return 0;}\n'
        source=tmp_path/f'case{number}.c';binary=tmp_path/f'case{number}'
        source.write_text(program)
        subprocess.run([compiler,'-O2',str(source),'-o',str(binary)],check=True,capture_output=True)
        subprocess.run([str(binary)],check=True)


def test_discovery_benchmark_counts_verification_without_seed_or_rebuild(tmp_path,monkeypatch):
    import json

    from decompnet.app import reviewed_seed_benchmark
    from tests.test_claims import _game
    net,pid=_game(tmp_path,[('f',10,'open')])
    with net.store.tx() as db:
        db.execute("UPDATE projects SET arch='mips',config=?",(json.dumps({'kind':'rom','platform':'n64','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}),))
        db.execute('UPDATE functions SET best_source=?,best_score=40,asm=?',(SOURCE,TARGET))
    monkeypatch.setattr('decompnet.app.boundary_repairs.blocked',lambda *a:set())
    net._asm_text=lambda fn,proj:fn['asm'];net.xrefs=lambda _: {'called_by':{}}
    calls=[]
    def judge(*args):
        source=args[-1];calls.append(source)
        return (True,None,'') if 'return_value' in source else (False,SimpleNamespace(score=40,rows=[]),'')
    net._judge_with=judge
    (tmp_path/'.reviewed-search-replay').touch()
    report=reviewed_seed_benchmark.run(net,'game','f',SOURCE,None,'',tmp_path,Log(),
        rebuild=lambda:pytest.fail('diagnostic-only exact must not rebuild or adopt'))
    assert report['mode']=='discovery' and report['seed_score'] is None
    assert report['generated_roots']==2 and report['reviewed_roots']==0
    assert report['diagnostic_verified'] and report['verification_compiles']==1
    assert report['total_judge_or_diagnostic_compiles']==len(calls)==4
    assert report['rebuild_seconds'] is None and report['model_tokens']==0 and not report['matched']
