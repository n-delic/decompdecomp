from decompnet.app import captured_control_repairs as repair
from decompnet.app import draft_repairs


def proposals(source,family):
    return [text for label,text in repair.variants(source,'f') if label==family]


def test_independent_initializers_swap_with_grouped_integer_declarations():
    source='int f(){int a,b; a=0; /* keep */ b=1; return a+b;}'
    assert any('b=1; /* keep */ a=0;' in text for text in proposals(source,'independent_assignment_order'))
    for bad in ('a=0;b=a;','a=b;b=0;','a=call();b=0;','a=*p;b=0;'):
        assert not proposals('int f(){int a,b;'+bad+'return a+b;}','independent_assignment_order')
    assert not proposals('int f(){int a,b;{int a;a=0;b=1;}return b;}','independent_assignment_order')


def test_pointer_chase_advances_existing_dead_cursor_and_retains_following_loads():
    source='void f(void) {void *cursor; int *chain; cursor=get(); chain=*(int **)((char *)cursor+16); chain=*(int **)chain; use(chain);}'
    result=proposals(source,'pointer_chase_reuse')
    assert len(result)==1 and 'cursor=*(void * *)' in result[0] and 'chain=*(int **)cursor;' in result[0]
    assert not proposals(source.replace('use(chain);','use(cursor);use(chain);'),'pointer_chase_reuse')
    assert not proposals(source.replace('cursor=get();','escape(&cursor);cursor=get();'),'pointer_chase_reuse')


def test_pointer_reuse_in_a_loop_requires_an_unconditional_reset_before_any_read():
    source='void f(void) {void *cursor; int *chain; while(active) {cursor=get(); chain=*(int **)((char *)cursor+16); chain=*(int **)chain; use(chain);}}'
    assert proposals(source,'pointer_chase_reuse')
    assert not proposals(source.replace('while(active)','while(cursor)'),'pointer_chase_reuse')
    assert not proposals(source.replace('cursor=get();','if(active) cursor=get();'),'pointer_chase_reuse')


def test_terminal_boolean_copy_becomes_early_return_with_existing_constant_local():
    source='int f(void *p) {unsigned int flag; {int result=1; flag=(p==0); if(flag != 0) { use(result); } return 1;} }'
    result=proposals(source,'terminal_early_return')
    assert len(result)==1 and 'if (p) return result;' in result[0] and 'flag=(p==0)' not in result[0]
    assert not proposals(source.replace('return 1;}','use(flag);return 1;}'),'terminal_early_return')
    labels={};draft_repairs.variants(source,'f','mips',labels=labels,limit=None)
    assert 'terminal_early_return' in labels.values()


def test_member_store_repetition_crosses_only_adjacent_same_object_stores():
    source='void f(int value){p->owner=value; p->flags |= 4; p->x=0;}'
    result=proposals(source,'member_store_replay')
    assert any('p->flags |= 4;\np->owner=value;' in text for text in result)
    assert not proposals(source.replace('p->flags |= 4;', 'call();'),'member_store_replay')
    assert not proposals(source.replace('p->flags |= 4;', 'other->flags |= 4;'),'member_store_replay')
    assert not repair.variants('void f(){puts("p->owner=value; p->x=0;");}','f')
