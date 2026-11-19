"""decomprepair 0.5.8's rewrites reach the repairs the deep pass and autopilot try, and the flag search tries GCC with
its scheduling off. Each shape is from an N64 game's function that stayed at m2c's draft through 10,000 deep-permuter
variants; the rewrite made it exact."""
import io

import pytest

from decompnet import seeds
from decompnet.app import draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = ('typedef int s32;\ntypedef unsigned int u32;\ntypedef signed char s8;\ntypedef unsigned char u8;\n'
          'typedef short s16;\ntypedef unsigned short u16;\ntypedef float f32;\n#define NULL 0\n')

EXIT = seeds.M2C_MACROS + '''
s32 g(s32, s32);
s32 h(s32, s32, s32);

s32 f(s32 arg0, s32 arg1, s32 arg2) {
    s32 temp_s0;
    s32 temp_s1;
    s32 temp_v0;
    s32 var_v0;

    temp_s0 = arg1 & 0xFF;
    if ((temp_s0 == 0xFF) || (temp_s1 = arg2 & 0xFF, ((g(temp_s0, temp_s1) & 0xFF) == 0)) || (temp_v0 = h(arg0 & 0x7F, temp_s0, temp_s1), var_v0 = temp_v0, (temp_v0 == -1))) {
        var_v0 = -1;
    }
    return var_v0;
}
'''

OPERAND = seeds.M2C_MACROS + '''
void f(s32 arg0, void *arg1) {
    void *temp_v0;

    temp_v0 = (arg0 * 0x18) + M2C_FIELD(M2C_FIELD(arg1, void **, 0x6C), s32 *, 4);
    M2C_FIELD(arg1, f32 *, 0x24) = (f32) M2C_FIELD(temp_v0, f32 *, 0);
    M2C_FIELD(arg1, f32 *, 0x28) = (f32) M2C_FIELD(temp_v0, f32 *, 4);
}
'''

COMPARISON = seeds.M2C_MACROS + '''
void g(s32, s32);
s8 k(s32, s32);
extern void *D;

void f(s32 arg0, s32 arg1, void *arg2, void *arg3, u16 arg4) {
    s32 var_v0;

    g(arg0, 0);
    g(arg1, 0);
    var_v0 = arg0 != 0;
    if (arg0 == M2C_FIELD(D, s32 *, 0x18)) {
        g(arg0, 9);
        var_v0 = arg0 != 0;
    }
    if ((var_v0 & (arg2 != NULL)) && (arg3 != NULL)) {
        M2C_FIELD(arg2, s8 *, 0x25) = k(M2C_FIELD(D, s32 *, 0x10), arg4);
    }
}
'''


def offered(source, family):
    labels = {}
    texts = draft_repairs.variants(source, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    return [t for t in texts if labels.get(t) == family]


def test_each_rewrite_is_offered():
    copied = [t for t in offered(EXIT, 'constant_exit_return') if 'var_v0' not in t]
    assert copied and 'return -1;\n    }\n    return temp_v0;' in copied[0]
    operand = offered(OPERAND, 'operand_temporary')
    assert operand and '__repair_operand = M2C_FIELD(M2C_FIELD(arg1, void **, 0x6C), s32 *, 4);' in operand[0]
    inlined = [t for t in offered(COMPARISON, 'delay_slot_duplicate') if 'var_v0' not in t]
    assert inlined and 'if (((arg0 != 0) & (arg2 != NULL)) && (arg3 != NULL))' in inlined[0]
    assert 'constant_exit_return' in draft_repairs.AGENT_FAMILIES


def test_the_flag_search_tries_gcc_with_scheduling_off():
    from decompnet.app.engine.compilers import MAX_FLAG_VARIANTS, CompilerMixin
    variants = CompilerMixin().flag_variants('gcc-2.7.2-kmc', FLAGS)
    assert len(variants) <= MAX_FLAG_VARIANTS
    assert [*FLAGS, '-fno-schedule-insns2'] in variants
    assert [*FLAGS, '-fno-schedule-insns', '-fno-schedule-insns2'] in variants
    # (a flag the project already has isn't added twice)
    assert [*FLAGS, '-fno-schedule-insns2'] not in CompilerMixin().flag_variants('gcc-2.7.2-kmc', [*FLAGS, '-fno-schedule-insns2'])[1:]


def _code(tmp_path, text):
    import capstone
    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('Pinned KMC compiler is not installed; never download it here.')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': FLAGS}})
    result = compile_source(cfg, HEADER, text)
    assert result.ok, result.log
    code = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text').data()
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 + capstone.CS_MODE_BIG_ENDIAN)
    return [(i.mnemonic, i.op_str) for i in md.disasm(code, 0)]


def test_kmc_builds_the_shapes_the_game_had(tmp_path):
    # the -1 is loaded straight into the return register, not into another one and copied
    copied = [t for t in offered(EXIT, 'constant_exit_return') if 'var_v0' not in t][0]
    assert ('addiu', '$v1, $zero, -1') in _code(tmp_path, EXIT)
    assert ('addiu', '$v1, $zero, -1') not in _code(tmp_path, copied)
    # the base pointer chain is loaded before the index is computed
    operand = offered(OPERAND, 'operand_temporary')[0]
    assert [m for m, _ in _code(tmp_path, operand)[:2]] == ['lw', 'lw']
    assert [m for m, _ in _code(tmp_path, OPERAND)[:2]] != ['lw', 'lw']
    # the comparison is computed in the condition (sltu, sltu, and), not carried in a saved register (andi)
    inlined = [t for t in offered(COMPARISON, 'delay_slot_duplicate') if 'var_v0' not in t][0]
    assert 'andi' in [m for m, _ in _code(tmp_path, COMPARISON)]
    assert 'andi' not in [m for m, _ in _code(tmp_path, inlined)] and 'and' in [m for m, _ in _code(tmp_path, inlined)]
