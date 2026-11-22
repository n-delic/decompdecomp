"""A loop that branches back to its function's own entry still gets a machine draft (seeds.entry_label)."""
import pytest

from decompnet import seeds

ASM = """nonmatching func_8006AA00, 0x34

glabel func_8006AA00
    /* 6B600 8006AA00 90860000 */  lbu        $a2, 0x0($a0)
    /* 6B604 8006AA04 24840001 */  addiu      $a0, $a0, 0x1
  .L8006AA24:
    /* 6B624 8006AA24 14C0FFF6 */  bnez       $a2, func_8006AA00
    /* 6B628 8006AA28 00001021 */   addu      $v0, $zero, $zero
    /* 6B62C 8006AA2C 0C000000 */  jal        func_8006AA00
    /* 6B630 8006AA30 03E00008 */  jr         $ra
endlabel func_8006AA00
"""


def test_the_entry_gets_a_label_and_the_branches_to_it_use_it():
    out = seeds.entry_label(ASM, 'func_8006AA00')
    assert 'glabel func_8006AA00\nnop\n.Lfunc_8006AA00_entry:\n' in out
    assert 'bnez       $a2, .Lfunc_8006AA00_entry' in out
    assert 'jal        .Lfunc_8006AA00_entry' not in out.replace('jal        func_8006AA00', '')
    assert 'endlabel func_8006AA00' in out and 'nonmatching func_8006AA00, 0x34' in out


def test_asm_without_the_function_label_is_unchanged():
    assert seeds.entry_label('bnez $a2, f\n', 'f') == 'bnez $a2, f\n'


def test_m2c_is_asked_again_with_the_label(monkeypatch):
    seen = []

    def fake(asm, name, *args):
        seen.append(asm)
        if '.Lfunc_8006AA00_entry' not in asm:
            raise seeds.DraftError('Cannot find branch target func_8006AA00')
        return 'draft'

    monkeypatch.setattr(seeds, '_run_m2c', fake)
    assert seeds.run_m2c(ASM, 'func_8006AA00', 'mips-gcc-c') == 'draft' and len(seen) == 2


def test_another_missing_target_is_still_an_error(monkeypatch):
    def fake(*args):
        raise seeds.DraftError('Cannot find branch target elsewhere')

    monkeypatch.setattr(seeds, '_run_m2c', fake)
    with pytest.raises(seeds.DraftError):
        seeds.run_m2c(ASM, 'func_8006AA00', 'mips-gcc-c')
