import pytest

from decompnet import m2c_delay_slot, seeds

# a loop that steps its pointer in the delay slot of the branch that tests the byte it was on
SCAN = '''glabel skip_to_dot
addiu $sp, $sp, -0x20
sw $ra, 0x1C($sp)
sw $s0, 0x18($sp)
jal fill
addu $s0, $a0, $zero
addiu $v1, $zero, 0x2E
.Lloop:
lbu $v0, 0x0($s0)
beq $v0, $v1, .Ldone
addiu $s0, $s0, 0x1
lbu $v0, 0x0($s0)
bnez $v0, .Lloop
nop
addiu $s0, $s0, 0x1
.Ldone:
jal store
addu $a0, $s0, $zero
lw $ra, 0x1C($sp)
lw $s0, 0x18($sp)
jr $ra
addiu $sp, $sp, 0x20
'''


def test_the_step_is_written_above_the_branch_that_tests_the_old_byte():
    assert m2c_delay_slot.hoist(SCAN) == SCAN.replace('beq $v0, $v1, .Ldone\naddiu $s0, $s0, 0x1\n',
                                                      'addiu $s0, $s0, 0x1\nbeq $v0, $v1, .Ldone\nnop\n')


def test_a_counter_compared_before_its_step_and_splat_comments():
    asm = ('/* 0 80000000 */  slti       $v0, $s0, 0xA\n/* 4 80000004 */  bnez       $v0, .L0\n'
           '/* 8 80000008 */   addiu     $s0, $s0, 0x1\n')
    assert m2c_delay_slot.hoist(asm).split('\n')[1].endswith('addiu     $s0, $s0, 0x1')
    assert m2c_delay_slot.hoist(asm).split('\n')[3] == 'nop'


@pytest.mark.parametrize('code', [
    'lbu $v0, 0($s0)\nbeql $v0, $v1, .L0\naddiu $s0, $s0, 1\n',  # a likely branch runs its slot only when taken
    'lbu $v0, 0($s0)\nbeq $s0, $v1, .L0\naddiu $s0, $s0, 1\n',   # the branch reads the register the slot writes
    'lbu $v0, 0($a0)\nbeq $v0, $v1, .L0\naddiu $s0, $s0, 1\n',   # nothing the condition came from changes
    'lbu $v0, 0($s0)\n.L1:\nbeq $v0, $v1, .L0\naddiu $s0, $s0, 1\n',  # the load is in another block
    'addiu $s0, $s0, 1\nbnez $v0, .L0\naddiu $s0, $s0, 1\n',
    'lbu $v0, 0($s0)\nbnez $v0, .L0\nsw $s0, 0($a1)\n',
])
def test_anything_else_is_left_alone(code):
    assert m2c_delay_slot.hoist(code) == code


@pytest.mark.skipif(not seeds.available(), reason='m2c is not installed')
def test_the_draft_compares_the_byte_the_target_compares():
    draft = seeds.run_m2c(SCAN, 'skip_to_dot', 'mips-gcc-c', valid=True)
    before = draft[:draft.index('var_s0 += 1;', draft.index('loop_1:'))]
    assert '= *var_s0;' in before
    assert 'if (*var_s0 != 0x2E)' not in draft
