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


def test_a_register_read_before_it_is_set_becomes_an_unset_local(monkeypatch):
    class Done:
        returncode, stderr = 0, ''
        stdout = ("void f(void) {\n    s32 x;\n\n    x = M2C_ERROR(/* Read from unset register $t0 */) + 1;\n}\n")
    monkeypatch.setattr(seeds, '_execute_m2c', lambda argv, timeout: Done())
    monkeypatch.setattr(seeds, 'available', lambda: True)
    monkeypatch.setattr(seeds.toolchains, 'venv_bin', lambda *a: '/bin/true')
    out = seeds.run_m2c('glabel f\nnop\n', 'f', 'mips-ido-c', valid=True, strict=True)
    assert 'M2C_ERROR' not in out and 'x = unset_t0 + 1;' in out and 's32 unset_t0;' in out


def test_a_label_with_hex_words_in_its_suffix_is_a_local_label():
    from decompnet.rom.split import _LABEL
    assert _LABEL.match('.L80116830_code_80115DF0_DC550:')
    out = seeds._compilable('void f(void) {\n    .L80116830_code_80115DF0_DC550();\n}\n', 'f',
                            '/* 0 80000000 0C000400 */ nop', '', 'ido-7.1')
    assert 'func_80116830_code_80115DF0_DC550();' in out


def test_subroutine_arguments_m2c_names_are_declared():
    out = seeds.declare_unset_reads('void f(void) {\n    g(subroutine_arg3);\n}\n', 'f')
    assert 's32 subroutine_arg3;' in out


def test_a_float_register_read_before_it_is_set_is_an_unset_float_local(monkeypatch):
    class Done:
        returncode, stderr = 0, ''
        stdout = ("void f(f32 arg0) {\n    f32 y;\n\n"
                  "    y = arg0 * GLUE_F64(M2C_ERROR(/* Read from unset register $f5 */), 0U) + M2C_ERROR(/* Read from unset register $f16 */);\n}\n")
    monkeypatch.setattr(seeds, '_execute_m2c', lambda argv, timeout: Done())
    monkeypatch.setattr(seeds, 'available', lambda: True)
    monkeypatch.setattr(seeds.toolchains, 'venv_bin', lambda *a: '/bin/true')
    out = seeds.run_m2c('glabel f\nnop\n', 'f', 'mips-ido-c', valid=True, strict=True)
    assert 'M2C_ERROR' not in out and 'GLUE' not in out
    assert 'y = arg0 * unset_f5 + unset_f16;' in out and 'f32 unset_f5;' in out and 'f32 unset_f16;' in out


def test_pointer_bits_read_as_float_and_float_bits_read_as_pointer():
    """`M2C_BITWISE(f32, &local)` is a cast IDO and GCC 2.x refuse; the pointer's bits are read as a float through a word,
    and the opposite (a float held in an address) the other way."""
    from decompnet import seeds
    src = ("void func_80000000(s32 *arg0) {\n    f32 temp_fa0;\n    s32 sp1C;\n"
           "    func_80000010(M2C_BITWISE(f32, &sp1C), M2C_BITWISE(f32, arg0), M2C_BITWISE(s32 *, temp_fa0));\n"
           "    M2C_FIELD(*(f32 *)0x2CF8, f32 *, 4) = 0.0f;\n}\n")
    out = seeds.pointer_bits_as_float(src, "func_80000000")
    assert "M2C_BITS_F32(&sp1C)" in out and "M2C_BITS_F32(arg0)" in out
    assert "M2C_PTR_FROM_F32(s32 *, temp_fa0)" in out
    assert "M2C_FIELD(*(u32 *)0x2CF8" in out
    assert "M2C_BITWISE(f32, 1)" not in out


def test_label_before_closing_brace_gets_an_empty_statement():
    from decompnet import seeds
    out = seeds._compilable("void f(void) {\n    if (x) {\n        goto block_2;\n    }\nblock_2:\n}\n", "f")
    assert "block_2:;\n}" in out


def test_denormal_float_literals_become_words_for_ido():
    from decompnet import seeds
    out = seeds.denormal_floats_as_words("f(1e-45f, 4.3e-44f, 1.0f, 2.5e-3f);")
    assert out == "f(M2C_BITS_F32(1U), M2C_BITS_F32(31U), 1.0f, 2.5e-3f);"


def test_null_dereference_arguments_are_zero():
    from decompnet import seeds
    src = "void f(void) {\n    x = g(NULL, *NULL, *((void *)0));\n}\n"
    out = seeds._compilable(src, "f")
    assert "g(NULL, 0, 0)" in out


def test_function_pointer_local_that_is_stepped_becomes_a_word():
    from decompnet import seeds
    out = seeds._compilable("void f(void) {\n    M2C_UNK (*sp24)(M2C_UNK, M2C_UNK);\n    sp24 += 0xC;\n}\n", "f")
    assert "s32 sp24;" in out


def test_ido_float_to_unsigned_fence_becomes_a_truncation():
    from decompnet import seeds
    asm = "\n".join([
        "glabel f", "  cfc1       $t8, $31", "  ctc1       $t9, $31", "  cvt.w.s    $ft1, $fv0", "  cfc1       $t9, $31",
        "  nop", "  andi       $t9, $t9, 0x78", "  beql       $t9, $zero, .L8020D050", "  mfc1       $t9, $ft1",
        "  sub.s      $ft1, $fv0, $ft1", ".L8020D050:", "  nop", "  ctc1       $t8, $31", "  jr $ra"])
    out = seeds.float_unsigned_conversions(asm)
    assert "cfc1" not in out and "ctc1" not in out
    assert "trunc.w.s $ft1, $fv0" in out and "mfc1 $t9, $ft1" in out and "jr $ra" in out
    keep = asm.replace("  jr $ra", "  b .L8020D050")  # a label the rest of the function branches to stays
    assert seeds.float_unsigned_conversions(keep) == keep


def test_ido_double_to_unsigned_fence_becomes_a_double_truncation():
    from decompnet import seeds
    asm = "\n".join([
        "glabel f", "  cfc1       $t3, $31", "  ctc1       $s3, $31", "  cvt.w.d    $ft2, $ft1", "  cfc1       $s3, $31",
        "  nop", "  andi       $s3, $s3, 0x78", "  beql       $s3, $zero, .L80032298", "  mfc1       $s3, $ft2",
        ".L80032298:", "  nop", "  ctc1       $t3, $31", "  jr $ra"])
    out = seeds.float_unsigned_conversions(asm)
    assert "cfc1" not in out and "trunc.w.d $ft2, $ft1" in out and "mfc1 $s3, $ft2" in out


def test_float_dereferenced_as_a_base_and_parenthesised_pointer_words():
    from decompnet import seeds
    src = ("void f(f32 arg0, void *arg1) {\n    f32 temp;\n    temp = M2C_FIELD(arg0, f32 *, 4);\n"
           "    g(M2C_BITWISE(f32, (arg1 + 0x3C)), M2C_BITWISE(void *, (M2C_FIELD(arg1, f32 *, 8) * 2.0f)));\n}\n")
    out = seeds.pointer_bits_as_float(src, "f")
    assert "M2C_FIELD((s32)arg0, f32 *, 4)" in out
    assert "M2C_BITS_F32((arg1 + 0x3C))" in out
    assert "M2C_PTR_FROM_F32(void *, (M2C_FIELD(arg1, f32 *, 8) * 2.0f))" in out


def test_gcc_pointer_from_float_line_unwraps_the_float_word_macros():
    from decompnet.app import compile_error_repair as cer
    src = "a;\n    g(M2C_BITS_F32(&x), M2C_BITWISE(void *, (y - z)));\n"
    once = cer.undo_float_words(src, 2)
    assert "g((&x)," in once
    twice = cer.undo_float_words(once, 2)
    assert "((void *)(s32)((y - z)))" in twice
    assert cer.undo_float_words(twice, 2) is None


def test_stack_slot_function_pointer_declaration_goes_and_the_call_is_cast():
    from decompnet import seeds
    out = seeds._compilable("void f(void) {\n    u8 sp[40];\n    M2C_UNK (*sp24)(void *);\n    sp24 = g;\n    sp24(1);\n}\n", "f")
    assert "(*sp24)" not in out.replace("((M2C_UNK (*)())sp24)", "") and "sp24 = (s32)g;" in out
    assert "((M2C_UNK (*)())sp24)(1);" in out


def test_called_address_is_cast_to_a_function_pointer():
    from decompnet.app import compile_error_repair as cer
    assert "((M2C_UNK (*)())(sp4C + sp74))(sp6C);" in cer.call_through_cast("a;\n    (sp4C + sp74)(sp6C);\n", 2)
    assert "arg1((M2C_UNK (*)())arg1)()" not in (cer.call_through_cast("a;\n    *arg1();\n", 2) or "")
    assert "((M2C_UNK (*)())arg1)();" in cer.call_through_cast("a;\n    *arg1();\n", 2)
    assert cer.call_through_cast("a;\n    ((void (*)())x)(1);\n", 2) is None


def test_argument_register_without_a_parameter_is_declared():
    from decompnet import seeds
    out = seeds.declare_written_arguments("s32 f(s32 arg0, s32 arg1) {\n    arg2 = arg1;\n    return arg0;\n}\n", "f")
    assert "    s32 arg2;\n    arg2 = arg1;" in out and "s32 arg0" in out and "s32 arg1" in out
    same = "s32 f(s32 arg0) {\n    s32 arg1;\n    arg1 = arg0;\n    return arg1;\n}\n"
    assert seeds.declare_written_arguments(same, "f") == same


def test_a_switch_through_a_missing_table_is_cut_where_it_jumps():
    from decompnet import seeds
    asm = "glabel f\n  lw $v0, 0x0($v1)\n  jr $v0\n  nop\n  jr $ra\n  nop\n"
    out = seeds.cut_missing_switches(asm)
    assert "jr $ra" in out.splitlines()[2] and out.count("jr $v0") == 0
    assert seeds.cut_missing_switches("  jr $ra\n") == "  jr $ra\n"


def test_ido_void_pointer_steps_and_null_for_numbers():
    from decompnet import seeds
    out = seeds.ido_pointer_steps("void f(void **arg0) {\n    void *var_s1;\n    s32 n;\n    var_s1 += (s32)8;\n"
                                  "    *arg0 += (s32)8;\n    n = NULL;\n    var_s1 = NULL;\n}\n")
    assert "u8 *var_s1;" in out and "*arg0 = (u8 *)*arg0 + 8;" in out
    assert "n = 0;" in out and "var_s1 = NULL;" in out


def test_null_assigned_to_a_number_becomes_zero_on_the_named_line():
    from decompnet.app import compile_error_repair as cer
    assert cer.null_as_zero("a;\n    var = NULL;\n", 2) == "a;\n    var = 0;\n"
    assert cer.null_as_zero("a;\n    var = 1;\n", 2) is None


def test_branch_out_of_the_function_is_drafted_as_a_return():
    asm = ("glabel func_80001FB4\n/* 0 80001FB4 97030004 */  lhu        $v1, 0x4($t8)\n"
           "/* 4 80001FB8 1060FFAF */  beqz       $v1, . + 4 + (-0x51 << 2)\n"
           "/* 8 80001FBC 00191200 */  sll        $v0, $t9, 8\n"
           "/* C 80001FC0 03E00008 */  jr         $ra\n/* 10 80001FC4 00000000 */  nop\n"
           "endlabel func_80001FB4\n")
    cut = seeds.cut_outside_branches(asm)
    assert ". + 4" not in cut and "beqz       $v1, .Loutside_branch" in cut and ".Loutside_branch:\njr $ra" in cut
    assert seeds.cut_outside_branches("  jr $ra\n") == "  jr $ra\n"
    if seeds.available():
        drafted = seeds.run_m2c(asm, "func_80001FB4", "mips-ido-c", strict=True)
        assert drafted and seeds.OUTSIDE_BRANCH_NOTE in drafted


def test_unlabelled_branch_inside_the_function_gets_its_own_label():
    # two sequential guarded calls whose first branch the disassembler printed without a label (a re-split function):
    # drafting it as a return nested the second `if` inside the first, which no compiler turns back into these bytes
    asm = ("glabel func_80001000\n"
           "/* 0 80001000 27BDFFE8 */  addiu      $sp, $sp, -0x18\n"
           "/* 4 80001004 AFB00010 */  sw         $s0, 0x10($sp)\n"
           "/* 8 80001008 00808021 */  addu       $s0, $a0, $zero\n"
           "/* C 8000100C 06000004 */  bltz       $s0, . + 4 + (0x4 << 2)\n"
           "/* 10 80001010 AFBF0014 */  sw         $ra, 0x14($sp)\n"
           "/* 14 80001014 0C000800 */  jal        func_80002000\n"
           "/* 18 80001018 02002021 */  addu       $a0, $s0, $zero\n"
           "/* 1C 8000101C 00000000 */  nop\n"
           "/* 20 80001020 3C028040 */  lui        $v0, 0x8040\n"
           "/* 24 80001024 0050102B */  sltu       $v0, $v0, $s0\n"
           "/* 28 80001028 10400003 */  beqz       $v0, . + 4 + (0x3 << 2)\n"
           "/* 2C 8000102C 00000000 */  nop\n"
           "/* 30 80001030 0C000800 */  jal        func_80002000\n"
           "/* 34 80001034 24040001 */  addiu      $a0, $zero, 0x1\n"
           "/* 38 80001038 8FBF0014 */  lw         $ra, 0x14($sp)\n"
           "/* 3C 8000103C 8FB00010 */  lw         $s0, 0x10($sp)\n"
           "/* 40 80001040 03E00008 */  jr         $ra\n"
           "/* 44 80001044 27BD0018 */  addiu      $sp, $sp, 0x18\n"
           "endlabel func_80001000\n")
    cut = seeds.cut_outside_branches(asm)
    assert ". + 4" not in cut and ".Loutside_branch" not in cut
    assert "bltz       $s0, .Lbranch_20" in cut and "beqz       $v0, .Lbranch_38" in cut
    assert ".Lbranch_20:\n/* 20 80001020" in cut and ".Lbranch_38:\n/* 38 80001038" in cut
    # without the disassembler's comments the instructions are counted; a target past the end still returns
    bare = "glabel f\n  beqz $a0, . + 4 + (0x1 << 2)\n  nop\n  addiu $v0, $zero, 1\n  bnez $a0, . + 4 + (0x9 << 2)\n  nop\n"
    cut = seeds.cut_outside_branches(bare)
    assert ".Lbranch_8:\n  addiu $v0" in cut and "bnez $a0, .Loutside_branch" in cut
    if seeds.available():
        drafted = seeds.run_m2c(asm, "func_80001000", "mips-gcc-c", strict=True)
        assert drafted and seeds.OUTSIDE_BRANCH_NOTE not in drafted
        # the second call is not inside the first guard: the guards are siblings
        assert "    }\n    if ((u32) arg0 > 0x80400000U) {" in drafted and "return;" not in drafted


def test_store_to_a_number_becomes_a_word_store():
    from decompnet.app import compile_error_repair as cer
    out = cer.literal_store("void f(void) {\n    0 = (void *) 0;\n    x = 1;\n}", 2)
    assert "    *(s32 *)0 = (s32)((void *) 0);" in out and "x = 1;" in out
    assert cer.literal_store("void f(void) {\n    x = 1;\n}", 2) is None
