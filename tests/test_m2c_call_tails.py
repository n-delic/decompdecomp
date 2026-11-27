"""A shared call tail written out per arm, so m2c keeps the stack arguments each arm stores (m2c_call_tails)."""
import pytest

from decompnet import m2c_call_tails, seeds

# two arms of a switch-like branch: each stores the first stack argument and jumps into one shared call
ASM = """glabel func_80000000
addiu      $sp, $sp, -0x28
sw         $ra, 0x20($sp)
beqz       $a0, .L80000028
nop
addiu      $v0, $zero, 0x1
sw         $v0, 0x10($sp)
j          .L80000034
addiu      $v1, $zero, 0x3
.L80000028:
addiu      $v0, $zero, 0x2
sw         $v0, 0x10($sp)
addiu      $v1, $zero, 0x4
.L80000034:
sw         $v1, 0x14($sp)
addiu      $a0, $zero, 0x0
addiu      $a1, $zero, 0x0
addiu      $a2, $zero, 0x0
jal        func_80001000
addiu      $a3, $zero, 0x0
lw         $ra, 0x20($sp)
jr         $ra
addiu      $sp, $sp, 0x28
"""


def test_each_jump_into_a_shared_tail_gets_its_own_call():
    out = m2c_call_tails.write_out(ASM)
    assert out.count("jal        func_80001000") == 2
    # the jump's delay slot runs first, then the tail, then a jump past the call
    arm = out.split("sw         $v0, 0x10($sp)")[1]
    assert arm.split()[:4] == ["addiu", "$v1,", "$zero,", "0x3"]
    assert "j .Lcall_tail_0" in out and ".Lcall_tail_0:\nlw         $ra" in out


def test_tails_without_split_stack_arguments_are_left_alone():
    plain = ASM.replace("sw         $v0, 0x10($sp)\n", "")
    assert m2c_call_tails.write_out(plain) == plain
    branching = ASM.replace("addiu      $a0, $zero, 0x0\n", "beqz       $a1, .L80000028\nnop\n")
    assert m2c_call_tails.write_out(branching) == branching


@pytest.mark.skipif(not seeds.available(), reason="m2c is not installed")
def test_the_draft_keeps_every_arms_stack_arguments():
    draft = seeds.run_m2c(ASM, "func_80000000", "mips-gcc-c", valid=True)
    assert "func_80001000(0, 0, 0, 0, 1, 3)" in draft and "func_80001000(0, 0, 0, 0, 2, 4)" in draft
