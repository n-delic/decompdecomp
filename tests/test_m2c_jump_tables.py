"""A `jr` whose table m2c can't follow gets the table read beside it (m2c_jump_tables.py)."""
from decompnet import m2c_jump_tables as tables

DATA = ".section .rodata\ndlabel jtbl_A\n" + ".word .L1\n" * 7 + "dlabel jtbl_B\n" + ".word .L2\n" * 6 + "\n"

IN_A_REGISTER = """glabel f
lw $v1, 8($a0)
sltiu $v0, $v1, 0x7
beqz $v0, .Lout
addu $a1, $zero, $zero
sll $v0, $v1, 2
addu $v0, $v0, $t2
lw $v0, 0x0($v0)
jr $v0
nop
.Lout:
jr $ra
"""

IN_A_STACK_SLOT = """glabel f
lw $t4, 0x94($sp)
sltiu $v0, $t4, 0x7
beqz $v0, .Lout
andi $v0, $s0, 0xFF
lw $t4, 0x20($sp)
lw $v0, 0x0($t4)
jr $v0
nop
.Lout:
jr $ra
"""


def test_the_table_whose_size_is_the_range_checks_bound_is_read_before_the_jump():
    out = tables.inline_tables(IN_A_REGISTER, DATA)
    assert "lui $v0, %hi(jtbl_A)" in out and "lw $v0, %lo(jtbl_A)($v0)" in out and "jtbl_B" not in out
    assert out.index("move $at, $v1") < out.index("lui $v0, %hi(jtbl_A)") < out.index("jr $v0")
    assert out.rstrip().endswith("jr $ra")  # (the return stays as it was)


def test_an_index_that_was_reused_is_reloaded_from_the_stack_slot_the_check_loaded_it_from():
    out = tables.inline_tables(IN_A_STACK_SLOT, DATA)
    assert "lw $at, 0x94($sp)" in out and "lw $v0, %lo(jtbl_A)($v0)" in out


def test_a_jump_that_already_reads_its_table_and_a_function_without_tables_are_left_alone():
    direct = "lw $v0, %lo(jtbl_A)($at)\njr $v0\n"
    assert tables.inline_tables(direct, DATA) == direct
    assert tables.inline_tables(IN_A_REGISTER, "") == IN_A_REGISTER


def test_two_tables_of_the_same_size_are_not_guessed_between():
    both = DATA.replace(".word .L2\n" * 6, ".word .L2\n" * 7)
    assert tables.inline_tables(IN_A_REGISTER, both) == IN_A_REGISTER
