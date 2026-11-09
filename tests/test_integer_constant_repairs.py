from decompnet.app import integer_constant_repairs as repairs

SOURCE='void f(int mode,int x) { if(mode) { x=0; call(1); use(1); } }'


def generate(source=SOURCE,**kwargs):
    return repairs.variants(source,'f',language='c',int_bits=32,**kwargs)


def test_integer_one_is_consumed_and_placements_preserve_existing_effects():
    result=generate()
    assert len(result)==2
    assert all(s.count('int __repair_integer_one;')==1 and s.count('__repair_integer_one = 1;')==1 for _,s in result)
    assert all('call(__repair_integer_one); use(__repair_integer_one);' in s for _,s in result)
    assert 'x=0; __repair_integer_one = 1;' in result[1][1]


def test_unknown_language_macros_and_constant_expression_uses_are_rejected():
    assert not repairs.variants(SOURCE,'f')
    assert not repairs.variants(SOURCE,'f',language='c++',int_bits=32)
    assert not generate(header='#define call(x) #x')
    assert not generate(SOURCE.replace('call(1); use(1);','int a[1]; use(a[1]);'))
    assert not generate(SOURCE.replace('call(1); use(1);','x=sizeof(1); x=sizeof(1);'))


def test_incoming_jump_cannot_bypass_new_initialization():
    source='void f(int mode) { if(mode) goto middle; if(mode) { call(1); middle: use(1); } }'
    assert not generate(source)
