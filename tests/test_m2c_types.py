"""m2c's pointer and void spellings a C compiler refuses are made compilable (m2c_types.py)."""
from decompnet import m2c_types, seeds


def test_a_void_dereference_is_a_word_or_the_type_the_address_already_has():
    assert m2c_types.repair("while (*(void *)0xA4600010 & 3) {}") == "while (*(s32 *)0xA4600010 & 3) {}"
    both = "if (*(u8 *)0x42FF1C) { f(*(void *)0x42FF1C); }"
    assert "*(u8 *)0x42FF1C) { f(*(u8 *)0x42FF1C)" in m2c_types.repair(both)
    assert m2c_types.repair("M2C_FIELD(p, void **, 4) = 0;") == "M2C_FIELD(p, M2C_UNK **, 4) = 0;"


def test_a_sum_of_numbers_that_is_dereferenced_is_read_through_a_pointer():
    out = m2c_types.repair("x = (M2C_UNK *) *(0x43A7C8 + (i * 0x10));")
    assert out == "x = (M2C_UNK *) *(s32 *)(0x43A7C8 + (i * 0x10));"
    assert m2c_types.repair("y = *((a * 4) + M2C_FIELD(p, s32 *, 0x2C));").startswith("y = *(s32 *)((a * 4) +")
    assert m2c_types.repair("z = a * (b + c);") == "z = a * (b + c);"  # a product is not a dereference


def test_a_local_that_holds_an_address_is_read_as_the_type_it_is_assigned_to():
    src = "void f(s32 arg1) {\n    u8 temp;\n    u32 p;\n    temp = *p;\n    arg1 = *(arg1 + p);\n}\n"
    out = m2c_types.repair(src)
    assert "temp = *(u8 *)p;" in out and "arg1 = *(s32 *)(arg1 + p);" in out
    pointer = "void f(u32 *p) {\n    s32 a;\n    a = *p;\n}\n"
    assert m2c_types.repair(pointer) == pointer


def test_a_call_through_a_table_entry_is_a_call_through_a_function_pointer():
    out = m2c_types.repair("    *(&D_800B31A0 + t)(a, b);\n    g(a);\n")
    assert out == "    ((void (*)()) *(&D_800B31A0 + t))(a, b);\n    g(a);\n"


def test_two_added_addresses_are_added_as_numbers():
    assert m2c_types.repair("if (a < (u32) (&D_1 + &D_2)) {}") == "if (a < (u32) ((u32) &D_1 + (u32) &D_2)) {}"


def test_the_frame_buffer_and_indexed_slots_are_declared():
    src = "void f(void) {\n    g(&sp10[0], sp);\n}\n"
    out = seeds.declare_unset_reads(src, "f")
    assert "    u8 sp[256];\n" in out and "    s32 sp10[16];\n" in out


def test_a_prototype_that_disagrees_with_a_call_becomes_a_declaration_without_parameters():
    """m2c prints one prototype per callee and the draft calls it with another count: `f(f(x), 1)`."""
    src = ("void *func_1(void *, M2C_UNK);               /* extern */\n"
           "M2C_UNK func_2(f32 *, M2C_UNK);              /* extern */\n"
           "s32 func_3(M2C_UNK *);                       /* extern */\n"
           "s32 func_4(void);                            /* extern */\n"
           "void g(void *a) {\n    x = func_1(func_1(a), 1);\n    func_2(&y, 3);\n    if (func_3() != 4) {}\n    func_4();\n}\n")
    out = m2c_types.repair(src)
    assert "void *func_1();" in out and "s32 func_3();" in out
    assert "M2C_UNK func_2(f32 *, M2C_UNK);" in out  # the calls agree: untouched
    assert "s32 func_4(void);" in out
    assert m2c_types.repair(out) == out


def test_numbers_and_addresses_in_arithmetic_are_cast_to_numbers():
    assert m2c_types.repair("return (arg1 - temp) / 688;") == "return ((s32) arg1 - (s32) temp) / 688;"
    assert m2c_types.repair("t = var - &D_80140000;") == "t = var - (s32) &D_80140000;"
    assert m2c_types.repair("s24 sp40;") == "s32 sp40;"
    assert m2c_types.repair("x = a - b;") == "x = a - b;"


def test_a_word_read_from_memory_that_is_dereferenced_and_cast_values_are_read_through_a_pointer():
    assert m2c_types.repair("t = *(*(s32 *)0x430350 + i);") == "t = *(s32 *)(*(s32 *)0x430350 + i);"
    assert m2c_types.repair("y = (f32) *((i * 8) + p);") == "y = (f32) *(s32 *)((i * 8) + p);"
    assert m2c_types.repair("*(M2C_FIELD(p, s32 *, 0x10) + (a - b)) = 0xFF;").startswith("*(s32 *)(M2C_FIELD(")


def test_a_declaration_with_a_pointer_to_an_array_is_not_a_dereference():
    src = "void f(void) {\n    u16 (*sp26C)[];\n    s32 x;\n    x = sp26C;\n}\n"
    assert m2c_types.repair(src) == src
    from decompnet import seeds
    assert seeds.declare_unset_reads(src, "f") == src
