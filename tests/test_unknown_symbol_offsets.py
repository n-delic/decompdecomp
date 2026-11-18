"""Byte offsets m2c added to symbols of unknown type (decomprepair's unknown_symbol_offset_repairs) reach the repairs
the deep pass and autopilot try, and with gcc 2.7.2 give the record stride the game has."""
import pytest

from decompnet import seeds
from decompnet.app import draft_repairs

O32 = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef signed char s8;\ntypedef short s16;\ntypedef unsigned int u32;\n'
# (shaped like an N64 game's near miss: three words of a 16-byte record table, m2c's byte offset)
SOURCE = seeds.M2C_MACROS + '''
extern M2C_UNK D_800C0784;
extern M2C_UNK D_800C0788;

s32 g(s32 arg1, s32 arg2) {
    s32 temp_a2;

    temp_a2 = arg2 * 0x10;
    if (*(&D_800C0784 + temp_a2) != 0) {
        return *(&D_800C0788 + temp_a2) + arg1;
    }
    return 0;
}
'''


def proposals():
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'g', 'mips', '', O32, labels, limit=None, header=HEADER)
    return [t for t in texts if labels.get(t) == 'unknown_symbol_offsets']


def test_draft_repairs_offer_element_and_byte_offsets():
    found = proposals()
    assert any('temp_a2 = arg2 * 0x4;' in t for t in found)
    assert any('(s8 *)&D_800C0784 + (arg2 * 0x10)' in t for t in found)
    assert 'unknown_symbol_offsets' in draft_repairs.AGENT_FAMILIES


def test_kmc_scales_the_index_once(tmp_path):
    import io

    import capstone
    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('Pinned KMC compiler is not installed; never download it here.')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': O32}})

    def shifts(text):
        result = compile_source(cfg, HEADER, text)
        assert result.ok, result.log
        code = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text').data()
        md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 + capstone.CS_MODE_BIG_ENDIAN)
        return [i.op_str.split(', ')[-1] for i in md.disasm(code, 0) if i.mnemonic == 'sll']
    # m2c's draft: 16-byte records scaled by 4 again; each repair: the game's single `sll 4`
    assert shifts(SOURCE) == ['6']
    for text in proposals():
        if 'arg2 * 0x4' in text or '(arg2 * 0x10)' in text:
            assert shifts(text) == ['4'], text
