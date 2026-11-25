"""Drafts that IDO and m2c's own output kept from compiling: byte-pointer arithmetic, `.L` calls, float register saves."""
from decompnet import seeds

ASM = """glabel f
/* 0 80000000 27BDFFD0 */  addiu      $sp, $sp, -0x30
/* 4 80000004 E7B50018 */  swc1       $fs0f, 0x18($sp)
/* 8 80000008 E7B4001C */  swc1       $fs0, 0x1C($sp)
/* C 8000000C F7B40020 */  sdc1       $f20, 0x20($sp)
/* 10 80000010 E7A40004 */  swc1       $ft0, 0x4($sp)
"""


def test_float_register_saves_are_dropped_and_other_stores_stay():
    out = seeds.drop_float_saves(ASM)
    assert 'fs0' not in out and '$f20' not in out
    assert 'swc1       $ft0, 0x4($sp)' in out and 'addiu' in out


def test_ido_gets_byte_pointers_where_a_void_pointer_takes_arithmetic():
    draft = "void f(void *arg0) {\n    g(arg0 + 4);\n}\n"
    assert 'u8 *arg0' in seeds._compilable(draft, 'f', '', '', 'ido-7.1')
    assert 'void *arg0' in seeds._compilable(draft, 'f', '', '', 'gcc-2.7.2-kmc')


def test_a_call_to_a_local_label_is_named_like_a_function():
    out = seeds._compilable("M2C_UNK .L8033193C_raw_slot_09(M2C_UNK *);\nvoid f(void) {\n    .L8033193C_raw_slot_09();\n}\n",
                            'f', '/* 0 80000000 0C000400 */ nop', '', 'ido-7.1')
    assert '.L8033' not in out and 'func_8033193C_raw_slot_09();' in out


def test_a_store_printed_as_a_call_through_a_cast_is_a_store():
    out = seeds._compilable("void f(void) {\n    ((void (*)()) *(s32 *))(a + 4) = 1;\n}\n", 'f', '', '', 'ido-7.1')
    assert '*(s32 *)(a + 4) = 1;' in out and '(void (*)())' not in out
