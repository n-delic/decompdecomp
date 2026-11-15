import pytest
from pycparser import CParser

from decompnet.permuter_dependencies import equivalent, protect


def fn(body):
    return CParser().parse('int f(int x, int y) {'+body+'}').ext[0]


@pytest.mark.parametrize('before,after',[
    ('int a; int b; a=x; b=y; return a+b;', 'int a; int b; b=y; a=x; return a+b;'),
    ('int a; int b; a=x; b=a; return a+b;', 'int a; int b; b=a=x; return a+b;'),
    ('int a; a=x; return a;', 'int a; a=x; a=x; return a;'),
    ('int a; a=x-y; return a;', 'int a; a=y; a=x-a; return a;'),
    ('int a; a=x+y; return a;', 'int a; a=x; a=a+y; return a;'),
])
def test_pure_reorders_and_equal_type_chains_are_kept(before,after):
    assert equivalent(fn(before),fn(after))


@pytest.mark.parametrize('before,after',[
    ('int a; int b; a=x; b=a; a=y; return b;', 'int a; int b; a=x; a=y; b=a; return b;'),
    ('int a; a=x-y; return a;', 'int a; a=x; a=y-a; return a;'),
    ('int a; a=x; return a;', 'unsigned a; a=x; return a;'),
    ('int a; int b; a=x; b=y; return a;', 'int a; int b; a=b; b=y; return a;'),
    ('int a; a=x; return a;', 'int a; a=x+(x=3); return a;'),
    ('int a; a=x; return a;', 'int a; a=a=x; return a;'),
    ('int a; a=0; return a;', 'int a; a=x*x; a=0; return a;'),
    ('int a; a=y+y; a=0; return a;', 'int a; a=x+x; a=y+y; a=0; return a;'),
])
def test_changed_dependency_or_conversion_is_declined(before,after):
    assert not equivalent(fn(before),fn(after))


@pytest.mark.parametrize('body',[
    'int *p; p=&x; *p=y; return x;',
    'volatile int a; a=x; return a;',
    'int a; if(x) a=y; return a;',
    'int a; while(x) x--; return x;',
    'return helper(x);',
    'int a; return a;',
])
def test_unproven_memory_control_or_reads_fail_closed(body):
    assert not equivalent(fn(body),fn(body))


def test_guard_rejects_without_forwarding_invalid_candidate_and_preserves_safe_change():
    class Failure(Exception):pass
    def bad(node,*args):node.body.block_items[0].init=None
    node=fn('int a=x; return a;')
    with pytest.raises(Failure):protect(bad,Failure)(node,None,None,None,None)
    def noop(*args):return 'safe'
    assert protect(noop,Failure)(fn('return x;'),None,None,None,None)=='safe'
    with pytest.raises(Failure):protect(noop,Failure,{'x'})(fn('return x;'),None,None,None,None)


def test_native_failure_after_mutation_escapes_native_retry_loop():
    from decompnet.permuter_dependencies import RejectedRewrite
    class NativeFailure(Exception):pass
    def broken(node,*args):
        node.body.block_items[0].init=None
        raise NativeFailure()
    with pytest.raises(RejectedRewrite):
        protect(broken,RejectedRewrite)(fn('int a=x; return a;'),None,None,None,None)


@pytest.mark.parametrize('alias,allowed',[
    ('typedef int s32;',True),('typedef unsigned int s32;',True),
    ('typedef volatile int s32;',False),('typedef short s32;',False),
    ('typedef int *s32;',False),
])
def test_only_proven_unqualified_int_aliases_are_supported(alias,allowed):
    a=CParser().parse(alias+'s32 f(s32 x){s32 a; a=x; return a;}')
    b=CParser().parse(alias+'s32 f(s32 x){s32 a; a=x; a=x; return a;}')
    assert equivalent(a.ext[-1],b.ext[-1],a.ext[:-1])==allowed


def test_expanded_dependencies_have_a_budget_before_tree_comparison():
    node=fn('int a; a=x;'+'a=a+a;'*24+'return a;')
    assert not equivalent(node,node)
