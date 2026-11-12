from decomprepair import global_array_repairs as arrays
from decomprepair import guard_shape_repairs as shape
from decomprepair import init_placement_repairs as placement

LOOP = '''void f(void) {
    s32 i = 0;
    a(0);
    b();
    c();
    do {
        d(i);
        i += 1;
    } while (i < 4);
}
'''


def test_initializer_moves_below_leading_calls_but_not_past_a_use():
    out = [t for _, t in placement.variants(LOOP, 'f')]
    assert len(out) == 3
    assert '    s32 i;\n    a(0);\n    i = 0;\n    b();' in out[0]
    assert all(t.index('i = 0;') < t.index('do {') for t in out)
    assert not placement.variants(LOOP.replace('a(0);', 'a(i);'), 'f')
    assert not placement.variants(LOOP.replace('s32 i = 0;', 's32 i = g();'), 'f')


VALUE = '''s32 f(s32 a) {
    u8 buf[4];

    if (D_1 != 0) {
        g(buf);
        return h(a);
    }
    return 0;
}
'''


def test_value_returning_guard_is_flipped_to_the_front():
    (_, out), = [v for v in shape.variants(VALUE, 'f') if 'return 0;\n    }' in v[1]]
    assert out.index('return 0;') < out.index('g(buf);') and out.rstrip().endswith('return h(a);\n}')
    assert not [v for v in shape.variants(VALUE.replace('return 0;', 'return g();'), 'f') if 'return g();\n    }' in v[1]]


def test_word_global_stored_where_its_address_was_meant():
    source = 'extern s32 D_80220ED8;\nvoid f(s32 *p) {\n    *p = D_80220ED8;\n}\n'
    out = [t for fam, t in arrays.variants(source, 'f') if fam == 'global_value_address']
    assert out == [source.replace('= D_80220ED8', '= (s32)&D_80220ED8')]
    assert not [1 for fam, _ in arrays.variants(source.replace('*p = D_80220ED8', 'g(D_80220ED8)'), 'f') if fam == 'global_value_address']
