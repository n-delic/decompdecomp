from decomprepair import record_slot_repairs as slots

CAST = '''extern u8 D_8011F2F2[];

void f(s32 arg0) {
    *(u16 *)(D_8011F2F2 + (arg0 & 0xFFFF) * 100) |= 0x10;
}
'''


def test_cast_access_becomes_a_typed_record_field():
    (family, out), = slots.variants(CAST, 'f')
    assert family == 'record_slot_table'
    assert 'typedef struct { u16 f0; u8 pad[98]; } Slot_8011F2F2;' in out
    assert 'extern Slot_8011F2F2 D_8011F2F2[];' in out
    assert 'D_8011F2F2[arg0 & 0xFFFF].f0 |= 0x10;' in out


TEMP = '''extern M2C_UNK D_8011F2F4;

void g(u16 a) {
    s32 off;

    off = a * 0x64;
    *(&D_8011F2F4 + off) = *(&D_8011F2F4 + off) | 8;
}
'''


def test_uncast_access_through_a_temporary_tries_three_field_types():
    out = [t for _, t in slots.variants(TEMP, 'g')]
    assert [t.split('{ ')[1].split(' f0')[0] for t in out] == ['u16', 'u8', 'u32']
    assert 'D_8011F2F4[a].f0 = D_8011F2F4[a].f0 | 8;' in out[0]


def test_other_uses_of_the_table_or_small_strides_are_left_alone():
    assert not slots.variants(CAST.replace('|= 0x10;', '|= 0x10; h(D_8011F2F2);'), 'f')
    assert not slots.variants(CAST.replace('* 100', '* 4'), 'f')
    assert not slots.variants(CAST.replace('(arg0 & 0xFFFF)', 'k(arg0)'), 'f')
