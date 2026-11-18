"""m2c's masked byte counter loops and byte parameters, then a staged element index (decomprepair 0.5.6), reach
the repairs the deep pass and autopilot try; with gcc 2.7.2 the staging changes only the argument order."""
import pytest

from decompnet import seeds
from decompnet.app import draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2', '-fno-schedule-insns']
HEADER = ('typedef int s32;\ntypedef unsigned int u32;\ntypedef signed char s8;\ntypedef unsigned char u8;\n'
          'typedef short s16;\n#define NULL ((void *)0)\n')
# (shaped like an N64 game's near miss: a u8 counter, an s8 parameter m2c masks, a 12-byte stride)
SOURCE = seeds.M2C_MACROS + '''
void g();

void f(void *arg0, s8 arg1) {
    s32 var_s1;
    s32 var_v0;
    u32 temp_a1;

    if ((arg0 != NULL) && (M2C_FIELD(arg0, s32 (**)(s32, s32, s32), 4) != NULL)) {
        M2C_FIELD(arg0, s8 *, 0x1F) = arg1;
        temp_a1 = arg1 & 0xFF;
        if (temp_a1 != 0) {
            g(arg0, temp_a1);
            return;
        }
        var_s1 = 0;
        if (temp_a1 < (u8) M2C_FIELD(arg0, u8 *, 0x19)) {
            var_v0 = 0 & 0xFF;
            do {
                M2C_FIELD(arg0, s32 (**)(s32, s32, s32), 4)(M2C_FIELD(arg0, s32 *, 0) + (var_v0 * 0xC), 0, M2C_FIELD(arg0, u8 *, 0x18) == var_v0);
                var_s1 += 1;
                var_v0 = var_s1 & 0xFF;
            } while ((u32) (var_s1 & 0xFF) < (u8) M2C_FIELD(arg0, u8 *, 0x19));
        }
    }
}
'''


def proposals(source, family):
    labels = {}
    texts = draft_repairs.variants(source, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    return [t for t in texts if labels.get(t) == family]


def chain():
    loop = next(t for t in proposals(SOURCE, 'masked_counter_loop') if 'u8 arg1' in t)
    return loop, proposals(loop, 'call_index_staging')


def test_draft_repairs_chain_the_loop_and_the_staging():
    loop, staged = chain()
    assert 'for (var_s1 = 0; var_s1 < (u8) M2C_FIELD(arg0, u8 *, 0x19); var_s1++)' in loop
    assert staged and 'temp_index = var_s1 * 0x3;' in staged[0]
    assert {'masked_counter_loop', 'byte_parameter_sign', 'call_index_staging'} <= set(draft_repairs.AGENT_FAMILIES)


def test_kmc_loads_the_base_between_the_index_and_its_scaling(tmp_path):
    import io

    import capstone
    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('Pinned KMC compiler is not installed; never download it here.')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': FLAGS}})

    def order(text):
        result = compile_source(cfg, HEADER, text)
        assert result.ok, result.log
        code = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text').data()
        md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 + capstone.CS_MODE_BIG_ENDIAN)
        rows = [f'{i.mnemonic} {i.op_str}' for i in md.disasm(code, 0)]
        return [r for r in rows if r in ('sll $a0, $a0, 2', 'lw $v1, ($s0)')], len(rows)
    loop, staged = chain()
    (before, size), (after, staged_size) = order(loop), order(staged[0])
    # the game's order: the index `i * 3`, the base's load, then the scaling (same instructions otherwise)
    assert before == ['sll $a0, $a0, 2', 'lw $v1, ($s0)'] and after == ['lw $v1, ($s0)', 'sll $a0, $a0, 2']
    assert size == staged_size
