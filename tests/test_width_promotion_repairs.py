from decompnet.app import width_promotion_repairs as promote

SOURCE = '''u8 f(void *a) {
    u8 *p;
    u8 v;

    v = *p;
    v = (v << 7) + 1;
    return v;
}
'''


def test_narrow_return_and_same_typed_locals_become_int():
    out = [t for _, t in promote.variants(SOURCE, 'f')]
    assert len(out) == 3
    assert out[0] == SOURCE.replace('u8 f(', 'int f(')
    assert out[1] == SOURCE.replace('    u8 v;', '    int v;')
    assert out[2] == SOURCE.replace('u8 f(', 'int f(').replace('    u8 v;', '    int v;')


def test_wide_or_pointer_returns_are_left_alone():
    assert not promote.variants(SOURCE.replace('u8 f(', 's32 f('), 'f')
    assert not promote.variants(SOURCE.replace('u8 f(', 'u8 *f('), 'f')
