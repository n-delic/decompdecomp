"""m2c's delay-slot duplicates, char symbols' widths from the target and M2C_FIELD records (decomprepair 0.5.7)
reach the repairs the deep pass and autopilot try."""
import pytest

from decompnet import seeds
from decompnet.app import draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned int u32;\ntypedef signed char s8;\ntypedef unsigned char u8;\ntypedef short s16;\ntypedef unsigned short u16;\n'
# (shaped like an N64 game's near miss: a duplicated `arg0 * 2`, a word stored through `char D[]`, a slot's fields)
SOURCE = seeds.M2C_MACROS + '''
s32 g();
void h();
extern char D_8010D784[];
extern char D_80117388[];
extern u8 D_801002EA;

void f(s32 arg0, u8 id) {
    s32 var_v0;
    s32 temp_v0;
    void *temp_a0;

    var_v0 = arg0 * 2;
    if (g() & 0xFF) {
        h(arg0);
        var_v0 = arg0 * 2;
    }
    temp_v0 = (((var_v0 + arg0) * 4) + arg0) << 5;
    *(D_8010D784 + temp_v0) = arg0;
    temp_a0 = (id * 4) + D_80117388;
    if (M2C_FIELD(temp_a0, u16 *, 2) == 0) {
        M2C_FIELD(temp_a0, u16 *, 2) = 1U;
        if (D_801002EA != 0xFF) {
            M2C_FIELD(temp_a0, u8 *, 1) = 0xFF;
        } else {
            M2C_FIELD(temp_a0, u8 *, 0) = 0xFFU;
        }
    }
}
'''
ASM = 'sw         $s0, %lo(D_8010D784)($at)\n'


def offered(source, family):
    labels = {}
    texts = draft_repairs.variants(source, 'f', 'mips', ASM, FLAGS, labels, limit=None, header=HEADER)
    return [t for t in texts if labels.get(t) == family]


def test_each_step_is_offered():
    moved, = offered(SOURCE, 'delay_slot_duplicate')
    assert moved.count('var_v0 = arg0 * 2;') == 1 and '        h(arg0);\n    }\n    var_v0 = arg0 * 2;' in moved
    wide, = offered(SOURCE, 'symbol_access_width')
    assert '*(s32 *)(D_8010D784 + temp_v0) = arg0;' in wide
    record = offered(SOURCE, 'inferred_record_layout')
    assert record and '((__repair_record_temp_a0*)temp_a0)->field_2 = 1U;' in record[0]
    assert {'delay_slot_duplicate', 'symbol_access_width'} <= set(draft_repairs.AGENT_FAMILIES)


def test_kmc_no_longer_keeps_the_duplicate_across_the_call(tmp_path):
    import io

    import capstone
    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('Pinned KMC compiler is not installed; never download it here.')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': FLAGS}})

    def saved(text):
        result = compile_source(cfg, HEADER, text)
        assert result.ok, result.log
        code = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text').data()
        md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 + capstone.CS_MODE_BIG_ENDIAN)
        return {i.op_str.split(',')[0] for i in md.disasm(code, 0) if i.mnemonic == 'sw' and '($sp)' in i.op_str}
    # m2c's copy before the call lives in a saved register; computed after the `if`, it needs none
    moved, = offered(SOURCE, 'delay_slot_duplicate')
    assert len(saved(moved)) < len(saved(SOURCE))
