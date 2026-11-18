"""A float argument m2c wrote as its bit pattern (decomprepair's float_bit_argument_repairs) reaches the repairs the
deep pass and autopilot try, and with gcc 2.7.2 the float changes only the argument set-up order."""
import pytest

from decompnet.app import draft_repairs

O32 = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef float f32;\ntypedef signed char s8;\n'
# (shaped like an N64 game's near miss: an integer first argument, then 250.0f's bits)
SOURCE = '''typedef s32 M2C_UNK;
M2C_UNK h(M2C_UNK, M2C_UNK, f32, void *, s32, s32, s32); /* extern */

void g(void *p, s32 a, s32 b) {
    h(0, 0x437A0000, (2.0f * *(f32 *)((s8 *)p + 0x64)) + 4.0f, p, a, 0x20, b);
}
'''


def test_draft_repairs_offer_the_float_literal_under_o32_only():
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'g', 'mips', '', O32, labels, limit=None, header=HEADER)
    floats = [t for t in texts if labels.get(t) == 'float_argument_bits']
    assert floats and 'h(0, 250.0f,' in floats[0] and 'M2C_UNK h(M2C_UNK, f32, f32,' in floats[0]
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'g', 'mips', '', ['-O2'], labels, limit=None, header=HEADER)
    assert not any('h(0, 250.0f,' in t for t in texts)


def test_kmc_schedules_the_float_argument_first(tmp_path):
    import capstone
    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('Pinned KMC compiler is not installed; never download it here.')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': O32}})
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'g', 'mips', '', O32, labels, limit=None, header=HEADER)
    repaired = next(t for t in texts if labels.get(t) == 'float_argument_bits')

    def setup(text):
        import io
        result = compile_source(cfg, HEADER, text)
        assert result.ok, result.log
        code = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text').data()
        md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 + capstone.CS_MODE_BIG_ENDIAN)
        rows = [f'{i.mnemonic} {i.op_str}' for i in md.disasm(code, 0)]
        return [r for r in rows if r in ('lui $a1, 0x437a', 'move $a0, $zero')], len(rows)
    before, size = setup(SOURCE)
    after, repaired_size = setup(repaired)
    # The same instructions and bits, in the other order: m2c's integer puts $a0 first, the float $a1.
    assert size == repaired_size
    assert before == ['move $a0, $zero', 'lui $a1, 0x437a'] and after == ['lui $a1, 0x437a', 'move $a0, $zero']
