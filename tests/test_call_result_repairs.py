"""Synthetic narrow-return comparisons; candidates still require the ordinary exact judge."""
from decompnet.app import call_result_repairs, pattern_repairs


def test_inline_repeated_narrow_call_comparisons():
    source = '''int f(u8 *a,u8 *b){register u8 x,y;
 x=read(*a);y=read(*b);if((x&0xFF)>(y&0xFF))return 1;
 x=read(*a);y=read(*b);if((x&0xFF)<(y&0xFF))return 0;return 2;}'''
    result = call_result_repairs.variants(source,'f')
    assert len(result)==1
    assert 'register u8 x,y;' not in result[0]
    assert 'if ((u8)read(*a) > (u8)read(*b))' in result[0]
    assert result[0].count('read(')==4
    assert ('call_result_inline',result[0]) in pattern_repairs.variants(source,'f')


def test_reject_extra_use_intervening_statement_and_volatile():
    source='int f(u8*a,u8*b){u8 x,y;x=read(*a);y=read(*b);if(x>y)return 1;return 0;}'
    assert call_result_repairs.variants(source,'f')
    for changed in [source.replace('return 0;','return x;'),source.replace('y=read','side();y=read'),
                    source.replace('u8 x,y','volatile u8 x,y'),source.replace('read(*a)','read(a++)'),
                    source.replace('read(*a)','read(next())')]:
        assert not call_result_repairs.variants(changed,'f')
    other=source+'\nint other(){return 7;}'
    assert call_result_repairs.variants(other,'f')[0].endswith('int other(){return 7;}')
