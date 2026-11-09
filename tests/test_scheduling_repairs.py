"""Captured scheduling shapes, reduced to homemade functions without game data."""
import pytest

from decompnet.app import draft_repairs, scheduling_repairs


def choices(source, family):
    return [s for f,s in scheduling_repairs.variants(source,'f') if f == family]


def test_chained_assignment_reverses_equal_type_unescaped_locals():
    source='void f(){ int i; int found; i = (found = 0); use(i,found); }'
    assert 'found = (i = 0);' in choices(source,'chained_local_order')[0]
    for unsafe in (source.replace('int found','short found'),source.replace('use(i,found)','use(&i,found)'),
                   source.replace('found = 0','found = call()'),source.replace('int i;','volatile int i;'),
                   'void f(){int found; i=(found=0); int i;}'):
        assert not choices(unsafe,'chained_local_order')


@pytest.mark.parametrize('body', ['call();','store=0; call(a, nested(b));','while(mmio) {} call();'])
def test_final_void_call_keeps_prior_work_and_call_arguments(body):
    source='void f(){'+body+'}'
    result=choices(source,'void_tail_call')
    assert result and 'return (void)call(' in result[0]


@pytest.mark.parametrize('source', ['int f(){call();}', 'void f(){if(x) call();}',
                                  'void f(){while(x) call();}', 'void f(){return call();}',
                                  'void f(){if(x){call();}}'])
def test_tail_call_does_not_turn_conditional_or_nonvoid_calls_into_returns(source):
    assert not choices(source,'void_tail_call')


def test_bounds_guards_keep_condition_order_and_body_scope():
    source='void f(){ int x; x=load(); if((x<20)&&(z>=-19)){int n; n=1; store(n);} }\n'
    result=choices(source,'terminal_bounds_guards')[0]
    assert result.index('if (!((x<20))) return;') < result.index('if (!((z>=-19))) return;')
    assert '{int n; n=1; store(n);}' in result


@pytest.mark.parametrize('source', [
    'void f(){if(x<20 && z>0){store();} cleanup();}',
    'void f(){if(x<20 && read()>0){store();}}',
    'void f(){if(x++<20 && z>0){store();}}',
    'void f(){if(x<20 || z>0){store();}}',
    'void f(){if(flag) if(x<20 && z>0){store();}}',
    'int f(){if(x<20 && z>0){store();}}',
])
def test_bounds_guards_reject_nonterminal_or_effectful_conditions(source):
    assert not choices(source,'terminal_bounds_guards')


@pytest.mark.parametrize('arch',['mips','ppc','x86','arm','thumb'])
def test_shared_draft_pipeline_labels_the_new_shape(arch):
    labels={}
    variants=draft_repairs.variants('void f(){call();}', 'f', arch, labels=labels)
    assert any(labels[s]=='void_tail_call' for s in variants)


def test_poll_guard_preserves_volatile_condition_and_combines_with_tail_call():
    source='void f(){while(*(volatile unsigned *)0xA0000000 >= 11) {} store=0; call();}\n'
    result=choices(source,'empty_wait_tail_call')[0]
    assert result.count('*(volatile unsigned *)0xA0000000 >= 11') == 2
    assert 'do {} while (' in result and 'return (void)call();' in result
    assert not choices(source.replace('{}','{work();}'),'empty_wait_guard')
