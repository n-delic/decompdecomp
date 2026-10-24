"""The PSP's processor, the Allegrex (arch/mips.py, a console's `cpu_variant`): its VFPU, min/max and clz/clo are
words capstone reads as other instructions, so diffs and look-alikes read them with rabbitizer, and the
hand-written-code verdict knows the VFPU and ext/ins/seb; every other MIPS console keeps capstone, unchanged."""

import struct

import capstone

from decompnet import consoles, scoring, similarity
from decompnet.arch import mips
from decompnet.elfobj import FunctionObj, Reloc

LV_Q, VADD_S, VSUB_S, VMUL_S = 0xD8240000, 0x60000000, 0x60800000, 0x64000000  # lv.q C100, 0($at); vadd.s S000...
EXT, SEB, MAX, CLZ = 0x7C823800, 0x7C0A4C20, 0x0085102C, 0x00801016  # ext v0,a0,0,8; seb t1,t2; max v0,a0,a1; clz v0,a0
JAL, BEQZ, JR_RA, NOP = 0x0C000000, 0x10800002, 0x03E00008, 0  # jal (relocated); beqz a0, +2


def le(*words: int) -> bytes:
    return struct.pack(f"<{len(words)}I", *words)


def function(*words: int, cpu: str = "", relocs=()) -> FunctionObj:
    return FunctionObj("f", "mips", False, le(*words), list(relocs), cpu=cpu)


BODY = (LV_Q, VADD_S, EXT, SEB, MAX, CLZ, JAL, BEQZ, NOP, JR_RA, NOP)


def test_allegrex_words_read_as_the_psp_has_them():
    insns = scoring.disassemble(function(*BODY, cpu=mips.ALLEGREX, relocs=[Reloc(24, mips.R_MIPS_26, "draw", 0)]))
    assert [i.mnemonic for i in insns] == ["lv.q", "vadd.s", "ext", "seb", "max", "clz", "jal", "beqz", "nop", "jr",
                                           "nop"]
    assert insns[0].text == "lv.q C100, 0x0($at)" and insns[2].text == "ext $v0, $a0, 0, 8"
    assert insns[6].text == "jal draw"  # the relocation names the call, as on every MIPS console
    assert insns[7].text == "beqz $a0, .L28"  # a branch stays position-relative


def test_other_mips_consoles_keep_capstone():
    reader = capstone.Cs(*mips.CAPSTONE[False])
    reader.skipdata = True
    before = [ins.mnemonic for ins in reader.disasm(le(*BODY), 0)]  # what capstone made of these words, still
    assert [i.mnemonic for i in scoring.disassemble(function(*BODY))] == before
    assert before[1] == "daddi" and not {"lv.q", "max", "clz"} & set(before)


def test_a_diff_sees_one_vfpu_instruction_changed():
    target = function(LV_Q, VADD_S, JR_RA, NOP, cpu=mips.ALLEGREX)
    candidate = FunctionObj("f", "mips", False, le(LV_Q, VMUL_S, JR_RA, NOP))  # (a compiled object: no cpu of its own)
    result = scoring.diff(target, candidate)
    assert not result.bytes_match and result.score > 0
    assert ("|", "vadd.s S000, S000, S000", "vmul.s S000, S000, S000") in result.rows
    assert scoring.diff(target, FunctionObj("f", "mips", False, target.code)).bytes_match


def test_vfpu_code_is_hand_written_on_the_psp_only():
    vfpu = le(VADD_S, JR_RA, NOP)
    assert "VFPU" in mips.asm_reason(vfpu, False, cpu=mips.ALLEGREX)
    assert mips.asm_reason(vfpu, False) is None  # daddi zero, zero, 0 elsewhere: nothing a compiler can't make
    psp = consoles.Console(arch="mips", cpu_variant=mips.ALLEGREX)  # the connector passes the console's variant
    assert "VFPU" in psp.asm_reason(le(LV_Q, JR_RA, NOP))


def test_ext_writes_its_destination_on_the_allegrex():
    """Read as the EE's sq (a store), ext would read v0 before setting it, and compiled code would look hand-written."""
    code = le(EXT, JR_RA, NOP)
    assert mips.asm_reason(code, False, cpu=mips.ALLEGREX) is None
    assert mips.asm_reason(code, False).startswith("reads v0")


def test_look_alikes_tell_vfpu_instructions_apart():
    code = le(VADD_S, VSUB_S)
    assert similarity.mips_mnemonics(code, False, cpu=mips.ALLEGREX) == ["vadd.s", "vsub.s"]
    assert similarity.mips_mnemonics(code, False) == ["daddi", "daddi"]  # one instruction, read as plain MIPS
