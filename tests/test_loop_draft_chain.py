"""A draft m2c writes for a small record loop reaches its compiling, near-target shape through the shared repairs."""
import pytest

from decompnet import seeds, toolchains
from decompnet.app import draft_repairs, initial_draft_repairs
from decompnet.compiler import compile_source
from decompnet.project import compiler_config

DRAFT = seeds.M2C_MACROS + '''
extern s32 gBase;
extern u16 gCount;
extern u16 *gSlot;
extern s32 gRecords;
void func_f(s32 arg0, s32 arg1) {
    s32 temp_a1;
    s32 var_s0;
    s32 var_v1;
    void *temp_v1;

    var_s0 = 0;
    if (gCount != 0) {
        var_v1 = 0 & 0xFFFF;
        do {
            temp_a1 = var_v1 * 0x68;
            temp_v1 = temp_a1 + gBase;
            if (M2C_FIELD(temp_v1, u8 *, 0x61) != 0) {
                M2C_FIELD(temp_v1, u8 *, 0x61) = 0U;
            }
            var_s0 += 1;
            var_v1 = var_s0 & 0xFFFF;
        } while ((u32) (var_s0 & 0xFFFF) < (u16) gCount);
    }
    *((*gSlot * 0xA10) + gRecords) = 0;
}
'''
ASM = '''glabel func_f
/* 0 80000000 27BDFFD8 */  addiu      $sp, $sp, -0x28
/* 4 80000004 AFBF0020 */  sw         $ra, 0x20($sp)
/* 8 80000008 A4400000 */  sh         $zero, 0x0($v0)
'''


def test_the_recovery_chain_proposes_the_store_width_first():
    families = [family for family, _ in initial_draft_repairs.recovery_drafts(
        DRAFT, 'func_f', ASM, 'mips', 'gcc-2.7.2-kmc', '', '', {'failures': []})]
    assert families[0] == 'initial_integer_address_store'


def test_the_loop_and_padding_families_are_proposed_and_registered():
    labels = {}
    typed = DRAFT.replace('*((*gSlot', '*(u16 *)((*gSlot')
    draft_repairs.variants(typed, 'func_f', 'mips', asm=ASM + '/* c 8000000c 0C000000 */  jal g\n', labels=labels, limit=None)
    assert {'loop_index_inline', 'unused_frame_padding', 'field_struct_view'} <= set(labels.values())
    assert {'loop_index_inline', 'unused_frame_padding', 'field_pointer_site'} <= set(draft_repairs.AGENT_FAMILIES)


def test_a_member_form_draft_gets_single_access_pointer_candidates():
    from decomprepair import field_struct_view_repairs as view
    (_, member), = view.variants(DRAFT.replace('*((*gSlot', '*(u16 *)((*gSlot'), 'func_f')
    labels = {}
    draft_repairs.variants(member, 'func_f', 'mips', asm=ASM, labels=labels, limit=None)
    assert 'field_pointer_site' in labels.values()


def test_the_first_candidate_compiles_under_kmc(tmp_path):
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('GCC 2.7.2 KMC is not installed')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                                                  'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']}})
    header = 'typedef signed char s8; typedef unsigned char u8; typedef short s16; typedef unsigned short u16; typedef int s32; typedef unsigned u32;'
    assert not compile_source(cfg, header, DRAFT, trusted=True).ok
    first = next(source for family, source in initial_draft_repairs.recovery_drafts(
        DRAFT, 'func_f', ASM, 'mips', 'gcc-2.7.2-kmc', '', '', {'failures': []}) if family.startswith('initial_integer'))
    assert compile_source(cfg, header, first, trusted=True).ok
