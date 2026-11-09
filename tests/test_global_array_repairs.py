from decompnet.app import global_array_repairs as arrays

TABLE = '''extern M2C_UNK D_801FAE30;

f32 f(s32 arg0) {
    return *(&D_801FAE30 + (((arg0 + 0x5A) & 0xFF) * 4));
}
'''


def test_scaled_word_access_becomes_a_typed_table_index():
    out = [t for family, t in arrays.variants(TABLE, 'f') if family == 'global_table_index']
    assert len(out) == 3 and 'extern f32 D_801FAE30[];' in out[0]
    assert 'return D_801FAE30[((arg0 + 0x5A) & 0xFF)];' in out[0]


def test_table_with_another_kind_of_use_is_left_alone():
    def indexed(text):
        return [1 for family, _ in arrays.variants(text, 'f') if family == 'global_table_index']
    assert not indexed(TABLE.replace('return', 'g(D_801FAE30); return'))
    assert not indexed(TABLE.replace('* 4)', '* 8)'))


def test_byte_table_signedness_is_flipped():
    source = 'extern char D_8011F2FC[];\nint f(int i) { return D_8011F2FC[i]; }\n'
    assert [t for fam, t in arrays.variants(source, 'f') if fam == 'byte_table_signedness'] == [source.replace('char', 'u8'), source.replace('char', 's8')]


def test_address_only_global_becomes_a_byte_table():
    source = 'extern M2C_UNK D_8011F2F0;\nvoid *f(s32 a) { return ((a & 0xFFFF) * 0x64) + &D_8011F2F0; }\n'
    out = [t for fam, t in arrays.variants(source, 'f') if fam == 'global_address_table']
    assert out[0] == 'extern u8 D_8011F2F0[];\nvoid *f(s32 a) { return ((a & 0xFFFF) * 0x64) + D_8011F2F0; }\n'
    assert not [1 for fam, _ in arrays.variants(source.replace('+ &D', '+ D'), 'f') if fam == 'global_address_table']


def test_scalar_global_tries_other_widths_and_signedness():
    source = 'extern s32 D_41CF00;\nvoid f(void) { D_41CF00 = 1; }\n'
    out = [t for fam, t in arrays.variants(source, 'f') if fam == 'global_scalar_type']
    assert source.replace('s32', 's8') in out and source.replace('s32', 'u32') in out
    assert not arrays.variants('extern s32 D_41CF00;\nvoid f(void) { }\n', 'f')


def test_scalar_globals_of_one_type_are_also_changed_together():
    source = 'extern s32 D_41CF00;\nextern s32 D_425610;\nvoid f(void) { D_41CF00 = D_425610; }\n'
    out = [t for fam, t in arrays.variants(source, 'f') if fam == 'global_scalar_type']
    assert source.replace('s32', 's8') in out


def test_byte_table_read_is_tried_as_a_halfword():
    source = ('extern char D_8011F32E[];\nu16 f(s32 a, s32 b) {\n'
              '    return (*(D_8011F32E + ((a & 0xFFFF) * 0x64)) & 0xFFF) | (b * (D_8011F32E + 4));\n}\n')
    out = [t for fam, t in arrays.variants(source, 'f') if fam == 'table_read_width']
    assert out[0] == source.replace('*(D_8011F32E + ((a', '*(u16 *)(D_8011F32E + ((a')
    assert len(out) == 2  # the product `b * (T + 4)` is not a dereference: one site, u16 then s16


def test_every_byte_table_read_is_widened_at_once_first():
    source = ('extern char D_8011F2F4[];\nextern char D_8011F2F8[];\n'
              'void f(s32 t) {\n    *(D_8011F2F4 + t) = *(D_8011F2F4 + t) | 0x20;\n    *(D_8011F2F8 + t) = *(D_8011F2F8 + t) | 0x20;\n}\n')
    first = [t for fam, t in arrays.variants(source, 'f') if fam == 'table_read_width'][0]
    assert first.count('*(u16 *)(') == 4
