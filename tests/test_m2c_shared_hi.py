"""One `lui` shared by two symbols' accesses is written out before m2c reads it: it named both by the first symbol."""
import pytest

from decompnet import m2c_shared_hi, seeds

STORES = """glabel f
    lui $a2, %hi(D_80100000)
    sw $a0, %lo(D_80100000)($a2)
    sw $a1, %lo(D_80100004)($a2)
    jr $ra
    nop
"""


def test_the_second_symbol_gets_its_own_lui():
    out = m2c_shared_hi.split(STORES)
    assert "sw $a0, %lo(D_80100000)($a2)" in out
    assert "    lui $at, %hi(D_80100004)\n    sw $a1, %lo(D_80100004)($at)\n" in out


def test_a_delay_slot_gets_its_lui_before_the_branch():
    asm = "lui $v0, %hi(D_80100000)\njr $ra\nlw $v1, %lo(D_80100004)($v0)\n"
    assert m2c_shared_hi.split(asm) == (
        "lui $v0, %hi(D_80100000)\n    lui $at, %hi(D_80100004)\njr $ra\nlw $v1, %lo(D_80100004)($at)\n")


def test_an_address_of_another_symbol_and_a_splat_comment_prefix():
    asm = "/* 0 80000000 */  lui $a0, %hi(D_80100000)\n/* 4 80000004 */  addiu $a1, $a0, %lo(D_80100004)\n"
    assert "addiu $a1, $at, %lo(D_80100004)" in m2c_shared_hi.split(asm)


@pytest.mark.parametrize("asm", [
    "lui $a2, %hi(D_80100000)\nsw $a0, %lo(D_80100000)($a2)\njr $ra\nnop\n",
    "lui $a2, %hi(D_80100000)\naddu $a2, $a2, $v0\nlw $v1, %lo(D_80100004)($a2)\n",
    "lui $at, %hi(D_80100000)\nsw $a0, %lo(D_80100004)($at)\n",
])
def test_other_code_is_untouched(asm):
    assert m2c_shared_hi.split(asm) == asm


def test_the_draft_names_both_globals():
    if not seeds.available():
        pytest.skip("m2c is not installed")
    draft = seeds.run_m2c(STORES, "f", "mips-gcc-c")
    assert "D_80100004 = " in draft
