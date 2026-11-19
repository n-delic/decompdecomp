"""decomprepair 0.5.9's rewrites reach the repairs the deep pass and autopilot try. Each shape is from an N64 game's
function that finished 10,000 deep-permuter variants with no improvement; the rewrite made it exact."""
from decompnet import seeds
from decompnet.app import draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = ('typedef int s32;\ntypedef unsigned int u32;\ntypedef signed char s8;\ntypedef unsigned char u8;\n'
          'typedef short s16;\ntypedef unsigned short u16;\n#define NULL 0\n')


def offered(source, family, asm=''):
    labels = {}
    texts = draft_repairs.variants(source, 'f', 'mips', asm, FLAGS, labels, limit=None, header=HEADER)
    return [t for t in texts if labels.get(t) == family]


def test_twin_constants_from_the_targets_immediates():
    source = seeds.M2C_MACROS + '''
s32 g();
extern s32 D;
void f(void) {
    D = g(0x3C00, 0xC);
}
'''
    asm = 'addiu      $a0, $zero, 0x2C00\naddiu      $a1, $zero, 0xD\n'
    assert any('g(0x2C00, 0xD);' in t for t in offered(source, 'target_constant', asm))


def test_unknown_symbol_reads_take_the_targets_widths():
    source = seeds.M2C_MACROS + '''
extern M2C_UNK D_800CF554;
s16 f(s32 arg0) {
    return *((M2C_UNK *)((s8 *)&D_800CF554 + ((arg0 & 0x3FF) * 2)));
}
'''
    asm = 'lh         $v0, %lo(D_800CF554)($at)\n'
    assert any('(s16 *)((s8 *)&D_800CF554' in t for t in offered(source, 'symbol_access_width', asm))


def test_a_load_before_the_global_store():
    source = seeds.M2C_MACROS + '''
extern s32 D_800E1A68;
extern u8 D_800C7E6C;
void f(void *arg0) {
    D_800E1A68 = 0;
    D_800C7E6C = M2C_FIELD(arg0, u8 *, 0x44);
}
'''
    assert any('0x44);\n    D_800E1A68 = 0;' in t for t in offered(source, 'load_before_store'))


def test_the_callers_parameter_passed_through():
    source = seeds.M2C_MACROS + '''
s32 g();
s32 f(s32 arg0, s32 arg1, s32 arg2) {
    g(arg0, 5, arg1);
    return 1;
}
'''
    assert any('g(arg0, 5, arg2, arg1);' in t for t in offered(source, 'passthrough_argument'))


def test_the_goto_loop_as_a_do_while():
    source = seeds.M2C_MACROS + '''
extern u8 D[];
s32 f(void *arg0, s32 arg1) {
    s32 var_a0;
    u32 var_a2;
    void *var_v1;

    var_a2 = 4;
    var_a0 = 0x3A0;
    var_v1 = M2C_FIELD(arg0, s32 *, 0x6C) + 0x20;
loop_1:
    if (M2C_FIELD(var_v1, u8 *, 0x1FC) == 0) {
        var_a0 += 0xE8;
        goto block_8;
    }
    if (*(D + var_a0) != arg1) {
        var_a0 += 0xE8;
block_8:
        var_a2 += 1;
        var_v1 += 8;
        if (var_a2 >= 0x21U) {
            return 0;
        }
        goto loop_1;
    }
    return 1;
}
'''
    loops = offered(source, 'goto_loop')
    assert loops and 'goto' not in loops[0] and '} while (var_a2 < 0x21U);' in loops[0]
    assert {'target_constant', 'load_before_store', 'passthrough_argument', 'goto_loop'} <= set(draft_repairs.AGENT_FAMILIES)
