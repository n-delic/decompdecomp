"""Repair families read the target's assembly under the names the C uses."""
from decompnet.app import repair_asm


class Net:
    def rename_tokens(self, proj, token):
        return {'func_8005BB50': 'sprintf', 'func_80001000': 'strcmp', 'D_8006DF60': 'sBusy'}.get(token, token)


ASM = '''jal        func_8005BB50
 jal        func_80001000
 lw         $v0, %lo(D_8006DF60)($v0)
 jal        func_80045398
'''


def test_names_the_source_uses():
    out = repair_asm.current(Net(), {}, ASM, 'void f(void) { sprintf(b); func_80001000(); sBusy = 0; func_80045398(); }')
    assert 'jal        sprintf' in out and '%lo(sBusy)' in out
    assert 'jal        func_80001000' in out and 'jal        func_80045398' in out  # the C's own spelling stays


def test_unchanged_without_names_or_a_reader():
    assert repair_asm.current(Net(), {}, ASM, 'void f(void) {}') == ASM
    assert repair_asm.current(object(), {}, ASM, 'sprintf') == ASM
    assert repair_asm.current(Net(), {}, '', 'sprintf') == ''


def test_renames_are_only_the_renamed_names_and_apply_follows_the_source():
    names = repair_asm.renames(Net(), {}, ASM)
    assert names == {'func_8005BB50': 'sprintf', 'func_80001000': 'strcmp', 'D_8006DF60': 'sBusy'}
    assert 'jal        strcmp' in repair_asm.apply(ASM, names, 'strcmp(a, b);')
    assert repair_asm.apply(ASM, {}, 'strcmp') == ASM
