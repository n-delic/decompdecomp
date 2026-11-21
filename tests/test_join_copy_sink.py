"""decomprepair 0.5.189's `join_copy_sink` and the byte-cursor walk reach the repairs the deep pass and autopilot try.

Both come from one N64 function (GCC 2.7.2): the app's untouched m2c draft scored 255 and the stock deep pass stopped at
195 with neither family in reach. The walk's indexed loop took it to 15, the copy sunk past the guard to exact; in a
scratch app, a fresh import and the stock search matched it (scratch_run.py). The library's own tests hold the shapes."""
from decompnet.app import draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = ('typedef int s32;\ntypedef unsigned int u32;\ntypedef short s16;\ntypedef unsigned short u16;\n'
          '#define NULL ((void *)0)\n#define M2C_FIELD(expr, type_ptr, offset) (*(type_ptr)((s8 *)(expr) + (offset)))\n'
          'typedef signed char s8;\nextern s32 D_800C4470;\nextern s32 D_800C4474;\nextern void *D_80108788;\n'
          'extern s32 D_8010A500;\ns32 func_8000A894();\ns32 func_8005B5A8();\ns32 func_8007092C();\n')
BODY = '''void f(void) {
    char *var_s0;
    char *var_s1;
    s32 var_s2;
    s32 var_s3;
    s32 var_v0;
    u32 temp_a0;

    var_s3 = 0;
    if (D_80108788 != NULL) {
        var_v0 = 0;
        if (M2C_FIELD(D_80108788, void **, 0x70) != NULL) {
%s
            goto block_9;
        }
    } else {
block_9:
        var_v0 = var_s3;
    }
    if (var_v0 == 0) {
        func_8005B5A8();
    }
}
'''
WALK = '''            var_s2 = 0;
            var_s1 = &D_800C4474;
            var_s0 = &D_800C4470;
            do {
                temp_a0 = M2C_FIELD(M2C_FIELD(D_80108788, void **, 0x70), u32 *, 0x30);
                if (((temp_a0 >> 4) == *(s32 *)var_s0) && ((temp_a0 >> 0x14) == *(s32 *)var_s1)) {
                    var_s3 = 1;
                    func_8007092C(D_8010A500, 1);
                }
                var_s1 += 4;
                var_s2 += 1;
                var_s0 += 4;
            } while (var_s2 == 0);'''
INDEXED = '''            for (var_s2 = 0; var_s2 < 1U; var_s2++) {
                temp_a0 = M2C_FIELD(M2C_FIELD(D_80108788, void **, 0x70), u32 *, 0x30);
                if (((temp_a0 >> 4) == ((s32 *)&D_800C4470)[var_s2]) && ((temp_a0 >> 0x14) == ((s32 *)&D_800C4474)[var_s2])) {
                    var_s3 = 1;
                    func_8007092C(D_8010A500, 1);
                }
            }'''


def offered(source):
    labels = {}
    out = draft_repairs.variants(HEADER + source, 'f', 'mips', '', FLAGS, labels, limit=None)
    return out, labels


def test_the_byte_cursor_walk_is_offered_as_an_indexed_loop():
    out, labels = offered(BODY % WALK)
    assert any('((s32 *)&D_800C4470)[var_s2]' in text and labels.get(text) == 'pointer_walk_index' for text in out)


def test_the_join_copy_is_offered_past_the_guard_for_the_agents_and_the_deep_pass():
    assert 'join_copy_sink' in draft_repairs.AGENT_FAMILIES
    out, labels = offered(BODY % INDEXED)
    sunk = [text for text in out if labels.get(text) == 'join_copy_sink']
    assert sunk and 'goto' not in sunk[0]
    assert 'if (D_80108788 != NULL && M2C_FIELD(D_80108788, void **, 0x70) != NULL) {' in sunk[0]
    assert sunk[0].count('var_v0 = var_s3;') == 2
