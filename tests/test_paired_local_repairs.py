

BASE='int f(int a,int b){ int x = a; return x + b; }'


FIRST=BASE.replace('int x','unsigned x')


SECOND=BASE.replace('x + b','x - b')


BOTH=FIRST.replace('x + b','x - b')


ROWS=[('r','lw $v0, 0($a0)','lw $v1, 0($a0)'),('r','sll $v1, $a1, 2','sll $v0, $a1, 2')]


def test_existing_bridge_and_pointer_scope_compose_as_a_shared_regression():
    from decompnet.app import allocation_repairs, pointer_index_scope
    source='void f(S *s,u32 *q){s32 i;u32 *p;s32 value;i=0;p=q;value=1;{ u32 **slot=s->tbl+i; *slot=(u32 *)q; }}'
    parents=[s for family,s in allocation_repairs.variants(source,'f') if family=='allocation_schedule_bridge']
    children=[s for parent in parents for family,s in pointer_index_scope.variants(parent,'f')]
    assert children and all('while (0)' in s and '*(__repair_index_scope_base + __repair_index_scope_offset)' in s for s in children)
