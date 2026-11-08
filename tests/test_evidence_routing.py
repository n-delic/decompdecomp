
from decompnet.app import expression_locals


def test_c89_initializer_does_not_get_an_assignment_before_its_declaration():
    source='void f(){s32 value=*(s32 *)(base+4); send(value);}'
    out=expression_locals.variants(source,'f')
    assert not any('address_temporary'==f for f,_ in out)
    assert expression_locals.declaration_prefix(' const s32 *value = ')
    assert expression_locals.declaration_prefix(' s32*value = ')
    assert not expression_locals.declaration_prefix(' value = ')
    macro='void f(){s32 value=M2C_FIELD(base+4,s32 *,0);send(value);}'
    out=expression_locals.variants(macro,'f')
    assert out and all('s32 value=M2C_FIELD((__repair_addr =' in text for family,text in out if family=='address_temporary')
