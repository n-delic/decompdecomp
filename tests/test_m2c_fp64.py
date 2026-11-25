"""Doubles in odd registers (a 64-bit FPU) are renamed so m2c never prints `(second half of f64)` (m2c_fp64.py)."""
import re

from decompnet import m2c_fp64

DOUBLE_IN_ODD = """glabel f
/* 0 80000000 44851000 */  mtc1       $a1, $f2
/* 4 80000004 D421FBA8 */  ldc1       $f1, %lo(D_800AFBA8)($at)
/* 8 80000008 46001021 */  cvt.d.s    $f0, $f2
/* c 8000000C 46210002 */  mul.d      $f0, $f0, $f1
/* 10 80000010 46200020 */  cvt.s.d    $f0, $f0
/* 14 80000014 03E00008 */  jr         $ra
/* 18 80000018 E4800004 */  swc1       $f0, 0x4($a0)
"""


def registers(text):
    return re.findall(r'\$f\d+', text)


def test_a_double_in_an_odd_register_moves_to_a_pair_nobody_else_uses():
    out = m2c_fp64.pair_free(DOUBLE_IN_ODD)
    load = next(line for line in out.splitlines() if 'ldc1' in line)
    number = int(re.search(r'\$f(\d+)', load)[1])
    assert number % 2 == 0 and number not in (0, 2, 12, 14)
    assert 'mul.d      $f0, $f0, $f%d' % number in out
    assert '$f2' in out  # the single and the even double that were fine keep their names


def test_a_single_beside_an_even_double_is_renamed_so_the_double_keeps_its_pair_to_itself():
    asm = ("lwc1 $f1, 0x638($a0)\ncvt.d.s $f0, $f1\nlwc1 $f2, 0($a1)\nsub.s $f0, $f1, $f2\n"
           "swc1 $f0, 0($a0)\ncvt.d.s $f0, $f0\n")
    out = m2c_fp64.pair_free(asm)
    assert out != asm and out.splitlines()[1].startswith('cvt.d.s $f0, $f')
    assert '$f1' not in out


def test_code_without_a_clash_is_returned_unchanged():
    single = "lwc1 $f0, 0($a0)\nlwc1 $f2, 4($a0)\nadd.s $f0, $f0, $f2\n"
    assert m2c_fp64.pair_free(single) is single
    even = "ldc1 $f2, 0($a0)\nmul.d $f0, $f2, $f2\nsdc1 $f0, 8($a0)\n"
    assert m2c_fp64.pair_free(even) is even


def test_two_words_of_a_32_bit_double_stay_a_pair():
    pair = "lwc1 $f1, 0($a0)\nlwc1 $f0, 4($a0)\nadd.d $f0, $f0, $f0\nsdc1 $f0, 8($a0)\n"
    assert m2c_fp64.pair_free(pair) == pair


def test_adjacent_singles_that_no_double_reads_are_split_so_m2c_does_not_fuse_them():
    vector = "lwc1 $f5, 0($a1)\nlwc1 $f4, 4($a1)\nmul.s $f5, $f5, $f4\nswc1 $f5, 0($a0)\n"
    out = m2c_fp64.pair_free(vector)
    assert out != vector
    assert not ({5, 4} <= {int(n) for n in re.findall(r'\$f(\d+)', out)})


def test_unset_stack_and_saved_register_reads_are_declared_locals():
    from decompnet import seeds
    src = ("void f(void *arg0) {\n    s32 sp20;\n\n    sp20 = 0;\n    g(arg0, sp20, sp24, unksp28);\n"
           "    if (saved_reg_s0 != 0) { return; }\n}\n")
    out = seeds.declare_unset_reads(src, "f")
    assert "    s32 sp24;\n" in out and "    s32 unksp28;\n" in out and "    s32 saved_reg_s0;\n" in out
    assert out.count("sp20;") == 2  # the declared slot is not declared again
    assert seeds.declare_unset_reads(out, "f") == out
    assert seeds.declare_unset_reads("int x;\n", "f") == "int x;\n"
