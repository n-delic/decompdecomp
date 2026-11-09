import pytest

from decompnet.app.layout_repairs import variants


def inputs(old,new,big=True,base=4):
    order='big' if big else 'little';op=(35<<26)|(base<<21)|(2<<16)
    return {'candidate_target':(op|old).to_bytes(4,order).hex(),'candidate_relocs':'[]'}, {'target':(op|new).to_bytes(4,order),'relocs':'[]'}


@pytest.mark.parametrize('big',[True,False])
@pytest.mark.parametrize('old,new',[(0,12),(12,4),(4,16)])
def test_resize_local_padding(old,new,big):
    source=f'struct S {{ char pad[{old}]; int value; }}; int f(struct S *p) {{ return p->value; }}'
    report,target=inputs(old,new,big)
    assert variants(source,report,target,big)==[source.replace(f'pad[{old}]',f'pad[{new}]')]


def test_insert_padding_before_used_field_preserves_comments():
    source='struct S { int value; }; int f(struct S *p) { /* value */ return p->value; }'
    r,t=inputs(0,12)
    result=variants(source,r,t,True)
    assert len(result)==1 and 'char __reuse_pad_value[12];' in result[0] and '/* value */' in result[0]


@pytest.mark.parametrize('source',[
    'struct S { union U u; int value; }; p->value;',
    'struct S { int value:4; }; p->value;',
    'struct S { int value; }; "p->value";',
    'struct S { int value; } __attribute__((packed)); p->value;',
])
def test_complex_or_unused_fields_are_not_guessed(source):
    r,t=inputs(0,12)
    assert variants(source,r,t,True)==[]


def test_stack_relocations_and_conflicting_offsets_are_skipped():
    source='struct S { int value; }; p->value;'
    r,t=inputs(0,12,base=29)
    assert variants(source,r,t,True)==[]
    r,t=inputs(0,12);r['candidate_relocs']='[[0,6,0]]'
    assert variants(source,r,t,True)==[]
    r,t=inputs(0,12);r['candidate_target']*=2;t['target']+=bytes.fromhex(r['candidate_target'][:8])
    assert variants(source,r,t,True)==[]


def test_common_fixed_width_padding_aliases():
    source='struct S { u8 pad[4]; s32 value; }; return p->value;'
    r,t=inputs(4,12)
    assert variants(source,r,t,True)==[source.replace('pad[4]','pad[12]')]


def test_anonymous_typedef_layout():
    source='typedef struct { u8 pad[4]; s32 value; } State; return p->value;'
    r,t=inputs(4,12)
    assert variants(source,r,t,True)==[source.replace('pad[4]','pad[12]')]


def test_layout_and_address_corrections_can_be_combined():
    from decompnet.app.address_repairs import variants as repair
    source='struct S { int value; }; extern struct S *original; int f(void) { return original->value; }'
    r,t=inputs(0,12)
    r['rows']=[('i','lui $v0, %hi(updated)','lui $v0, %hi(original)'),
               ('i','lw $v0, %lo(updated)($v0)','lw $v0, %lo(original)($v0)')]
    candidates=repair(source,r,t,True,{'updated'})
    assert any('updated->value' in c and 'char __reuse_pad_value[12];' in c for c in candidates)
