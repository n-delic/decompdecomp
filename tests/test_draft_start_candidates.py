"""Draft candidates from a matched look-alike (app/twin_drafts.py) and a re-typed result (app/draft_return_type.py)."""
import json

from decompnet.app import draft_return_type, twin_drafts
from decompnet.app.twins import rom_print
from decompnet.rom.symbols import Symbol, SymbolTable

DRAFT = '''void f(s32 arg0, M2C_UNK arg1) {
    g(arg0);
}
'''


def test_a_void_draft_whose_exits_set_v0_is_made_again_as_s32():
    assert draft_return_type.retype(DRAFT, 'f', 'tested') == 's32'
    assert draft_return_type.retype(DRAFT, 'f', None) is None
    assert draft_return_type.retype(DRAFT.replace('void f', 's32 f'), 'f', 'void') == 'void'
    context = draft_return_type.context(DRAFT, 'f', 'void f(s32, s32);\ns32 g(s32);\n', 's32')
    assert 'void f(' not in context and context.rstrip().endswith('s32 f(s32 arg0, s32 arg1);') and 's32 g(s32);' in context


def test_the_candidate_is_m2c_run_with_that_declaration_and_stays_off_by_default():
    asm = ('glabel f\n/* 0 80000000 00000000 */  addiu $v0, $a0, 0x1\n/* 4 80000004 00000000 */  jr $ra\n'
           '/* 8 80000008 00000000 */  nop\n')
    seen = []

    def generate(context):
        seen.append(context)
        return 's32 f(s32 arg0, s32 arg1) {\n    return g(arg0);\n}\n'
    [(family, source)] = draft_return_type.candidates(DRAFT, 'f', 'mips', asm, '', generate)
    assert family == 'return_type_m2c' and 'return g(arg0);' in source and seen[0].rstrip().endswith('s32 f(s32 arg0, s32 arg1);')
    assert draft_return_type.candidates(DRAFT, 'f', 'x86', asm, '', generate) == []  # no reader there yet (ROADMAP)
    assert not draft_return_type.enabled({}) and draft_return_type.enabled({'draft_return_type': True})


def _fn(name, vram, code, relocs, source=None):
    return {'name': name, 'vram': vram, 'target': code, 'relocs': json.dumps(relocs), 'matched_source': source}


def test_a_look_alike_s_c_is_moved_with_this_function_s_names():
    code = bytes.fromhex('3c0480103c058010' + '8c8400008ca50000' + '03e0000800851021')
    syms = SymbolTable([Symbol('D_A', 0x80100000, 4, 'object'), Symbol('D_B', 0x80100010, 4, 'object')])
    donor = _fn('donor', 0x80001000, code, [[0, 5, 0x80100000], [8, 6, 0x80100000]],
                'extern s32 D_A;\n\ns32 donor(void) {\n    return D_A + 1;\n}\n')
    target = _fn('f', 0x80002000, code, [[0, 5, 0x80100010], [8, 6, 0x80100010]])
    pa, pb = (rom_print(r, 'mips', True, syms.label) for r in (donor, target))
    [source] = twin_drafts.transplant(donor, target, syms, pa, pb, [], [], 'extern s32 D_A;\n', '', True)
    assert 's32 f(void)' in source and 'D_B + 1' in source and 'extern s32 D_B;' in source
    assert twin_drafts.enabled({}) and not twin_drafts.enabled({'twin_drafts': False})


def test_a_name_that_would_mean_two_things_refuses_the_transplant():
    code = bytes(16)
    syms = SymbolTable([Symbol('D_A', 0x80100000, 4, 'object')])
    donor = _fn('donor', 0x80001000, code, [[0, 5, 0x80100000], [8, 6, 0x80100000]],
                'extern s32 D_A;\n\ns32 donor(void) {\n    return D_A;\n}\n')
    target = _fn('f', 0x80002000, code, [[0, 5, 0x80100010], [8, 6, 0x80100020]])
    pa, pb = (rom_print(r, 'mips', True, syms.label) for r in (donor, target))
    assert twin_drafts.transplant(donor, target, syms, pa, pb, [], [], '', '', True) == []
