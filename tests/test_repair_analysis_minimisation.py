import pytest

from decompnet.app import repair_macro_analysis as macros
from decompnet.app import repair_recipe
from decomprepair import call_preparation_repairs as calls


def test_guards_distinguish_macros_from_budget_and_prototype_absence():
    report = calls.explain('void f(int x){ OUT(x); }','f','#define OUT(x) send(x)','mips')
    assert report['declined']['opaque_macro']==1
    assert 'budget exhaustion' in report['note']
    report = calls.explain('void f(int x){ send(x); }','f','','mips')
    assert report['declined']['missing_explicit_prototype']==1
    assert 'explicit prototype' in calls.explanation('void f(int x){ send(x); }','f')


def test_generated_is_not_claimed_tried():
    header = 'typedef struct { int x; } Packet; void send(int, Packet);'
    source = 'void f(int a, Packet p){ send(a,p); }'
    report = calls.explain(source,'f',header,'mips')
    assert report['candidates']>0
    assert calls.explanation(source,'f',header,'mips')==''


def test_expansion_requires_installed_registry_compiler(monkeypatch):
    monkeypatch.setattr(macros.toolchains,'installed',lambda _:False)
    monkeypatch.setattr(macros.compiler,'compile_source',lambda *a:pytest.fail('must not run'))
    assert not macros.inspect({'toolchain':'gcc-test'},'','void f(){}','f')['available']
    assert not macros.inspect({'toolchain':'gcc-test','cmd':['evil']},'','void f(){}','f')['available']


def test_expansion_uses_same_profile_and_never_returns_source_in_summary(monkeypatch):
    from types import SimpleNamespace

    from decompnet.compiler import CompilerConfig, CompileResult
    monkeypatch.setattr(macros.toolchains,'installed',lambda _:True)
    monkeypatch.setattr(macros.toolchains,'get',lambda _:SimpleNamespace(family='gcc',driver=''))
    monkeypatch.setattr(macros,'compiler_config',lambda root,settings:CompilerConfig(['pinned-gcc','-O2','-c','{src}','-o','{obj}']))
    seen=[]
    def compile(cfg,header,source):
        seen.append((cfg,header,source))
        return CompileResult(True,b'void send(int x);\nvoid f(int x){ send(x); }','')
    monkeypatch.setattr(macros.compiler,'compile_source',compile)
    source='void f(int x){ OUT(x); }'
    result=macros.inspect({'toolchain':'gcc-test'},'#define OUT(x) send(x)',source,'f')
    assert result['available'] and seen[0][2]==source
    assert '-E' in seen[0][0].cmd and '-O2' in seen[0][0].cmd and '-c' not in seen[0][0].cmd
    summary=macros.summary(result,'f','mips')
    assert 'analysis' not in summary and 'source' not in summary
    assert summary['expanded_call_eligibility']['declined'].get('opaque_macro') is None
    assert not macros.inspect({'toolchain':'gcc-test'},'#include "outside.h"',source,'f')['available']
    assert len(seen)==1


def test_recipe_requires_exact_verdict_not_zero_score():
    assert not repair_recipe.minimize('a','b',lambda s:dict(score=0,matched=False,output_fingerprint='x'))['available']
    assert not repair_recipe.minimize('a','b',lambda s:dict(matched=True))['available']


def test_recipe_rejects_extra_data_and_keeps_verified_original():
    after='void f(){ int a=1; int b=2; }'
    before='void f(){ int a=0; int b=0; }'
    def judge(source):
        return dict(matched=True,output_fingerprint='control' if source==after else 'changed-data')
    report=repair_recipe.minimize(before,after,judge,trials=4)
    import hashlib
    assert report['source_sha256']==hashlib.sha256(after.encode()).hexdigest()
    assert not report['retained_verified_reduction']


def test_recipe_finds_smaller_verified_edit_set():
    before='void f(){ int a=0; int b=0; }'
    after='void f(){ int a=1; int b=2; }'
    checked=[]
    def judge(source):
        checked.append(source)
        exact='a=1' in source
        return dict(matched=exact,output_fingerprint='same' if exact else 'different')
    report=repair_recipe.minimize(before,after,judge,trials=8)
    assert report['available'] and report['retained_verified_reduction'] and report['reverted']>=1
    assert 'global minimum' in report['verdict'] and 'source' not in report
    assert any('a=1' in s and 'b=0' in s for s in checked)


@pytest.mark.parametrize('trials,seconds',[(True,1),(0,1),(1,True),(1,0)])
def test_recipe_budget_validation(trials,seconds):
    with pytest.raises(ValueError):
        repair_recipe.minimize('a','b',lambda s:pytest.fail('invalid'),trials=trials,seconds=seconds)


def test_macro_summary_does_not_mislabel_header_qualifiers_as_function_effects():
    report=macros.summary(dict(available=True,analysis='void f(int x){ send(x); }',
                              analysis_header='extern volatile int device; void send(int);',note='analysis only.'),'f','mips')
    assert 'analysis_header' not in report
    assert report['expanded_call_eligibility']['guarded_tokens']=={'source':[],'header':['volatile']}


def test_optional_benchmark_analysis_has_separate_cost_and_no_source_export():
    from decompnet import compile_timing
    from decompnet.app import checkpoint_benchmark
    def factory(case):
        def evaluate(source):
            compile_timing.add('requests',1);compile_timing.add('invocations',1)
            return dict(score=0 if source=='exact' else 1,matched=source=='exact',
                        output_fingerprint='same' if source=='exact' else 'other',compiler_invocations=1,outcome='scored')
        def macro():
            compile_timing.add('invocations',50)  # diagnostic work must not inflate search costs
            return dict(available=False,reason='test adapter')
        return evaluate,dict(macro_analysis=macro)
    report=checkpoint_benchmark.run([dict(id='one',source='start')],factory,trials=1,seconds=1,
                                   minimize_trials=2,generate=lambda *a:[('fix','exact')])
    row=report['checkpoints'][0]
    assert row['compiler_invocations']==3  # uncached baseline is checked twice; optional work is separate
    assert row['recipe_minimisation']['candidate_checks']<=4
    assert row['recipe_minimisation']['available']
    assert row['macro_analysis']['reason']=='test adapter'
    assert 'Recipe minimisation' in checkpoint_benchmark.markdown(report)
    assert 'source' not in row['recipe_minimisation']
