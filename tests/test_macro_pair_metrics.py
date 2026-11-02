from types import SimpleNamespace

from decompnet.app import paired_local_repairs as pairs
from decompnet.app import repair_generation as generation

BASE='int f(int a,int b){ int x = a; return x + b; }'
FIRST=BASE.replace('int x','short x')
SECOND=BASE.replace('x + b','x * b')


def test_simple_unchanged_macros_allow_unaffected_pair_edits():
    prefix='#define READ(p) (*(p))\n'
    candidate=pairs.combine(prefix+BASE,prefix+FIRST,prefix+SECOND)
    assert candidate==prefix+FIRST.replace('x + b','x * b')
    assert pairs.combine(BASE,FIRST,SECOND,'#define READ(p) (*(p))')
    assert pairs.combine(BASE,FIRST,SECOND,'#define HIDDEN(x) update(x)') is None


def test_directives_macro_dependencies_and_effect_crossings_remain_protected():
    assert pairs.combine('#if YES\n'+BASE+'\n#endif', '#if YES\n'+FIRST+'\n#endif','#if YES\n'+SECOND+'\n#endif') is None
    assert pairs.combine(BASE,FIRST,SECOND+'\n#define EMPTY') is None
    assert pairs.combine(BASE,FIRST,SECOND,'#define READ(p) \\\n *(p)') is None
    prefix='#define SIDE(p) touch(p)\n'
    source=prefix+'int f(int a,int b){\n int x = a;\n SIDE(b);\n return x + b;\n}'
    assert pairs.combine(source,source.replace('int x','short x'),source.replace('x + b','x * b')) is None
    source=prefix+'int f(int a,int b){\n int x = SIDE(a);\n return x + b;\n}'
    assert pairs.combine(source,source.replace('int x','short x'),source.replace('x + b','x * b')) is None


def test_cold_warm_generation_measurements_are_source_free_and_scope_specific(monkeypatch):
    clock=[1.];net=SimpleNamespace();runs=[]
    monkeypatch.setattr(generation.time,'monotonic',lambda:clock[0])
    def stage(source):runs.append(source);clock[0]+=.01;return [('one',source+' changed')]
    for _ in range(2):
        with generation.session(net,'private-scope',None,['one'],accept=lambda *a:True) as state:
            generation.call(stage,'private source')
            assert state.metrics['first_eligible_ms'] is not None
    cold,warm=generation.measurements(net)['sessions']
    assert cold['generated_stages']==1 and cold['cache_hits']==0 and round(cold['generation_ms'])==10
    assert warm['generated_stages']==0 and warm['cache_hits']==1 and warm['generation_ms']==0
    assert len(runs)==1 and 'private' not in repr(generation.measurements(net))
    with generation.session(net,'changed-profile',None,['one']):generation.call(stage,'private source')
    assert len(runs)==2


def test_nested_generation_does_not_double_count_time_and_history_is_bounded(monkeypatch):
    clock=[0.];net=SimpleNamespace()
    monkeypatch.setattr(generation.time,'monotonic',lambda:clock[0])
    def child():clock[0]+=.01;return [('one','C')]
    def parent():return generation.call(child)
    with generation.session(net,'scope',None,['one']):generation.call(parent)
    row=generation.measurements(net)['sessions'][0]
    assert row['generated_stages']==2 and round(row['generation_ms'])==10
    for _ in range(70):
        with generation.session(net,'scope',None,[]):pass
    assert generation.measurements(net)['retained']==64


def test_macro_pair_runs_through_iterative_search_with_header_guards():
    from decompnet.app import iterative_repairs
    prefix='#define READ(p) (*(p))\n'
    base,first,second=prefix+BASE,prefix+FIRST,prefix+SECOND
    combined=prefix+FIRST.replace('x + b','x * b')
    rows=[('r','lw $v0, 0($a0)','lw $v1, 0($a0)'),('r','sll $v1, $a1, 2','sll $v0, $a1, 2')]
    def variants(s,r):return [('width',first),('operator',second)] if s==base else []
    def judge(s):return dict(score=0 if s==combined else 10,matched=s==combined,rows=rows)
    state=dict(pending=[dict(source=base,depth=0,ceiling=10)],next=[],seen=[base])
    assert iterative_repairs.advance(state,judge,variants,lambda:False,arch='mips',header='#define OTHER(p) (*(p))')
    state=dict(pending=[dict(source=base,depth=0,ceiling=10)],next=[],seen=[base])
    assert not iterative_repairs.advance(state,judge,variants,lambda:False,arch='mips',header='#define HIDDEN(x) update(x)')
    assert not state.get('paired_trials')
    assert not pairs.coverage('#include "unknown.h"\n'+BASE)['supported']
