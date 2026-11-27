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


def test_a_declaration_with_a_pointer_to_an_array_is_a_pointer_to_the_element_not_a_dereference():
    src = "void f(void) {\n    u16 (*sp26C)[];\n    s32 x;\n    x = sp26C;\n}\n"
    out = m2c_types.repair(src)
    assert out == src.replace("u16 (*sp26C)[];", "u16 *sp26C;")
    from decompnet import seeds
    assert seeds.declare_unset_reads(out, "f") == out


def test_pointers_to_arrays_of_unknown_length_become_pointers_to_the_element():
    src = "s32 g(void *, u16 (*)[]);\nvoid f(void) {\n    u16 (*p)[];\n    p = (u16 (*)[]) &sp20[0];\n    t = p[0][0];\n}\n"
    out = m2c_types.repair(src)
    assert "g(void *, u16 *)" in out and "u16 *p;" in out and "(u16 *) &sp20[0]" in out and "t = p[0];" in out


def test_case_labels_outside_any_switch_are_dropped_and_those_inside_stay():
    src = ("void f(int x) {\n    if (x) {\n        switch (x) {\n        case 1:\n            g();\n            break;\n"
           "        }\n        k();\n    } else {\n    default:  /* switch 1 */\n        h();\n    }\n}\n")
    out = m2c_types.repair(src)
    assert "default" not in out and "        case 1:\n" in out and "        h();" in out


def test_a_library_call_keeps_only_the_arguments_the_library_takes():
    assert m2c_types.repair("memcpy(a, b, (n * 2) + 8, var_a3);") == "memcpy(a, b, (n * 2) + 8);"
    assert m2c_types.repair("memcpy(a, b, 8);") == "memcpy(a, b, 8);"


def test_a_function_pointer_called_without_arguments_is_declared_without_parameters():
    out = m2c_types.repair("s32 f(void *a, s32 (*cb)(void *)) {\n    return cb();\n}\n")
    assert "s32 (*cb)()" in out
    field = m2c_types.repair("t = M2C_FIELD(p, f32 *(**)(void *), 0x18)();")
    assert field == "t = M2C_FIELD(p, f32 *(**)(), 0x18)();"
    assert m2c_types.repair("t = cb(1);\ns32 (*cb)(void *);") == "t = cb(1);\ns32 (*cb)(void *);"


def test_a_call_to_a_literal_address_casts_inside_the_parentheses_that_are_called():
    assert m2c_types.repair("(M2C_UNK (*)())0x80000400();") == "((M2C_UNK (*)())0x80000400)();"


def test_a_store_through_a_number_is_not_mistaken_for_a_table_call():
    out = m2c_types.repair("    *(arg3 + temp) = M2C_FIELD(v, u8 *, 3);\n")
    assert "void (*)()" not in out


def test_floats_stored_through_pointers_to_unknown_pointers_become_words_and_float_temporaries():
    src = ("void f(void) {\n    M2C_UNK **p;\n    M2C_UNK *temp_f0;\n    *p = M2C_BITWISE(M2C_UNK **, temp_f0);\n"
           "    x = M2C_BITWISE(f32, temp_f0);\n}\n")
    out = m2c_types.repair(src)
    assert "s32 *p;" in out and "f32 temp_f0;" in out and "M2C_BITWISE(s32, temp_f0)" in out
    plain = "void f(void) {\n    M2C_UNK **p;\n    *p = 0;\n}\n"
    assert m2c_types.repair(plain) == plain  # (only where a float is stored: otherwise m2c's types stand)


def test_the_other_half_of_a_double_no_register_pair_could_hold_prints_as_zero():
    assert m2c_types.repair("x = (f32) ((second half of f64) * y);\nz = second half of f64;") == "x = (f32) (0.0 * y);\nz = 0.0;"


def test_an_address_read_through_two_levels_is_cast_at_each():
    out = m2c_types.repair("    s32 t;\n    **t = 5;\n")
    assert "*(s32 *)*(s32 *)t = 5;" in out


def test_case_labels_stay_in_switches_that_m2c_numbers_in_a_comment():
    src = ("void f(int x, int y) {\n    switch (x) {                    /* switch 1; irregular */\n"
           "    case 0:                         /* switch 1 */\n        switch (y) {                /* switch 2 */\n"
           "        case 3:                     /* switch 2 */\n            g();\n            break;\n        }\n"
           "    case 1:                         /* switch 1 */\n        break;\n    }\n}\n")
    out = m2c_types.repair(src)
    assert "case 0:" in out and "case 3:" in out and "case 1:" in out


def test_a_default_in_the_else_beside_its_switch_stays_the_switchs_default():
    draft = """void f(s32 c, s32 i) {
    if (c != 0) {
        switch (c) {                                /* switch 1 */
        case 1:
            D = 1;
            break;
        case 2:
            D = 2;
            break;
        }
    } else {
    default:
        D = 0x20;
    }
}
"""
    fixed = " ".join(m2c_types.repair(draft).split())
    assert "D = 2; break; default: D = 0x20; break; } } else { D = 0x20; } }" in fixed
    # a `default:` beside no switch is still only dropped
    stray = "void f(s32 c) {\n    if (c) {\n        g();\n    } else {\n    default:\n        D = 0;\n    }\n}\n"
    assert "default" not in m2c_types.repair(stray)
