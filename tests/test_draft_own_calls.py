"""A draft's calls to itself agree with its definition (seeds.own_call_arguments): m2c passed a register left over
from a loop to a recursive call, and the draft didn't compile."""
from decompnet import seeds

DRAFT = """void func_80000000(s32 arg0, s32 arg1, s32 arg2) {
    u32 temp_a3;

    temp_a3 = arg1 & 0xFFFF;
    if (temp_a3 < arg2) {
        func_80000000(arg0, arg1, temp_a3, temp_a3);
    }
    func_80000000(arg0, arg1 + 1, arg2);
}
"""

ASM = """glabel func_80000000
    andi $a3, $a1, 0xFFFF
.L80000004:
    sltu $v0, $a3, $a2
    beqz $v0, .L80000018
    addu $a2, $a3, $zero
    jal func_80000000
    addu $a0, $a0, $zero
.L80000018:
    jal func_80000000
    addiu $a1, $a1, 1
endlabel func_80000000
"""


def test_a_register_left_over_is_not_an_argument():
    out = seeds.own_call_arguments(DRAFT, "func_80000000", ASM)
    assert "func_80000000(arg0, arg1, temp_a3);" in out
    assert "func_80000000(arg0, arg1 + 1, arg2);" in out
    assert out.startswith("void func_80000000(s32 arg0, s32 arg1, s32 arg2) {")


def test_an_argument_set_for_the_call_becomes_a_parameter():
    asm = ASM.replace("    addu $a0, $a0, $zero\n", "    addu $a3, $zero, $zero\n")
    out = seeds.own_call_arguments(DRAFT.replace("temp_a3, temp_a3)", "temp_a3, 0)"), "func_80000000", asm)
    assert out.startswith("void func_80000000(s32 arg0, s32 arg1, s32 arg2, s32 arg3) {")
    assert "func_80000000(arg0, arg1, temp_a3, 0);" in out
    assert "func_80000000(arg0, arg1 + 1, arg2, arg3);" in out


def test_a_draft_that_agrees_is_untouched():
    same = DRAFT.replace(", temp_a3, temp_a3)", ", temp_a3)")
    assert seeds.own_call_arguments(same, "func_80000000", ASM) == same
