from decompnet.app import local_inline_repairs as inline

SOURCE = '''extern u8 T[];

void f(s32 a) {
    s32 off = (a & 0xFFFF) * 100;

    if (*(f32 *)(T + off) < 1.0f) {
        *(f32 *)(T + off) = 0.0f;
    } else {
        T[off] = 2;
    }
}
'''


def test_temporary_is_inlined_at_each_use_and_at_all():
    out = [text for _, text in inline.variants(SOURCE, 'f')]
    assert len(out) == 4
    assert any('T[(a & 0xFFFF) * 100] = 2;' in t and 'T + off' in t for t in out)
    assert any(t.count('off') == 1 for t in out)  # only the declaration is left
    assert all('s32 off = (a & 0xFFFF) * 100;' in t for t in out)


def test_impure_or_changing_expressions_are_left_alone():
    assert not inline.variants(SOURCE.replace('(a & 0xFFFF) * 100', 'g(a)'), 'f')
    assert not inline.variants(SOURCE.replace('T[off] = 2;', 'T[off] = 2; a = 1;'), 'f')
    assert not inline.variants(SOURCE.replace('T[off] = 2;', 'T[off] = 2; off = 3;'), 'f')
    assert not inline.variants(SOURCE.replace('        T[off] = 2;\n', '').replace('*(f32 *)(T + off) = 0.0f;', 'g();'), 'f')


def test_pointer_temporary_assigned_apart_from_its_declaration():
    # m2c: `u16 *t; t = (u16 *)((u8 *)&D_8011F2F2 + (a & 0xFFFF) * 0x64); *t = *t & 0xFFEF;`
    source = ('extern u8 D_8011F2F2[];\nvoid f(s32 a) {\n    u16 *t;\n\n'
              '    t = (u16 *)((u8 *)&D_8011F2F2 + ((a & 0xFFFF) * 0x64));\n    *t = *t & 0xFFEF;\n}\n')
    out = [text for _, text in inline.variants(source, 'f')]
    both = '*((u16 *)((u8 *)&D_8011F2F2 + ((a & 0xFFFF) * 0x64))) = *((u16 *)((u8 *)&D_8011F2F2 + ((a & 0xFFFF) * 0x64))) & 0xFFEF;'
    assert any(both in text for text in out)
    # a pointer to a local is not a constant address
    assert not inline.variants(source.replace('&D_8011F2F2', '&a'), 'f')


def test_call_temporary_is_inlined_into_the_store_that_reads_it():
    source = ('void f(u8 *p) {\n    f32 t;\n    t = g(*(u16 *)(p + 0x40)) * 2.0f + 1.0f;\n'
              '    *(u8 *)(p + 0xC) = (u32)t;\n}\n')
    out = [text for _, text in inline.variants(source, 'f')]
    assert any('*(u8 *)(p + 0xC) = (u32)(g(*(u16 *)(p + 0x40)) * 2.0f + 1.0f);' in t and 't = g' not in t for t in out)
    # another call in the reading statement could change the order of evaluation
    assert not [t for _, t in inline.variants(source.replace('(u32)t;', 'h(t);'), 'f') if 't = g' not in t]
