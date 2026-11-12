"""Integers m2c dereferenced without a type (m2c_integer_derefs.py): the C here is made up and small."""

from decomprepair.m2c_integer_derefs import typed_dereferences


def test_a_load_takes_the_width_of_the_variable_it_fills():
    out = typed_dereferences("void f(s32 arg0, s32 arg1) {\n    s16 temp;\n    temp = *(arg0 + arg1);\n}\n", "f")
    assert "temp = *(s16 *)(arg0 + arg1);" in out


def test_a_store_takes_the_width_of_the_variable_it_stores_and_a_float_literal_is_a_float():
    out = typed_dereferences("void f(s32 arg0) {\n    u8 temp;\n    *(arg0 + 4) = temp;\n    *(arg0 + 8) = 1.0f;\n}\n", "f")
    assert "*(u8 *)(arg0 + 4) = temp;" in out and "*(f32 *)(arg0 + 8) = 1.0f;" in out


def test_a_cast_on_the_value_gives_the_width_and_anything_else_is_a_word():
    out = typed_dereferences("void f(s32 arg0, s32 arg1) {\n    s32 t;\n    t = (u8) *(arg0 + 1) + *(arg1 + 2);\n}\n", "f")
    assert "(u8) *(u8 *)(arg0 + 1)" in out and "*(s32 *)(arg1 + 2)" in out


def test_a_dereference_of_a_dereference_gets_both_casts():
    out = typed_dereferences("void f(s32 arg0) {\n    **(arg0 + 4) = 0;\n}\n", "f")
    assert "**" not in out.replace("*(s32 *)*(s32 *)", "")


def test_typed_pointers_arrays_and_addresses_are_left_alone():
    source = ("extern char D_80001000[];\nvoid f(s32 *arg0, s32 arg1) {\n    s32 *p;\n    s32 t;\n"
              "    t = *(arg0 + arg1);\n    t = *(D_80001000 + arg1);\n    t = *(arg1 + &t);\n    t = *p;\n"
              "    t = *((s32 *)arg1);\n}\n")
    assert typed_dereferences(source, "f") == source


def test_a_void_pointer_cannot_be_dereferenced_so_it_is_cast_like_an_integer():
    out = typed_dereferences("void f(void *arg0, s32 arg1) {\n    s32 t;\n    t = *(arg0 + arg1);\n}\n", "f")
    assert "t = *(s32 *)(arg0 + arg1);" in out
    out = typed_dereferences("void f(s32 arg1) {\n    s32 t;\n    t = *(arg1 + M2C_FIELD(arg1, void **, 4));\n}\n", "f")
    assert "*(s32 *)(arg1 + M2C_FIELD" in out


def test_declarations_function_pointer_types_and_multiplication_are_not_dereferences():
    source = ("void f(s32 arg0) {\n    u8 **sp5C;\n    u16 (*sp27C)[];\n    s32 t;\n"
              "    t = arg0 * (arg0 + 1);\n    t = M2C_FIELD(arg0, s32 (**)(void *), 8);\n    t = (arg0 + 1) * 2;\n}\n")
    assert typed_dereferences(source, "f") == source
