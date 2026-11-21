"""decomprepair 0.5.272's `join_store_split` reaches the repairs the deep pass and autopilot try.

From an N64 switch function (GCC 2.7.2): m2c's shared temporary at a join (`T = g + 1` / `T = g - 1`, `g = T`) held
the update in the call-crossing saved register, 30 points from exact with only `$v0`/`$s1` differing; storing the
update on each arm took it to exact (scored with the judge-worker; no permuter run)."""
from decompnet.app import draft_repairs

SOURCE = '''void f(void) {
    s32 var_s1;
    var_s1 = g_ptr + 0x28;
    switch (gSceneType) {
    case 3:
        if (menuItemIsActive(0x21) & 0xFF) {
            var_s1 = gSceneType; var_s1 += 1;
        } else {
block_12:
            var_s1 = gSceneType - 1;
        }
block_13:
        gSceneType = var_s1;
        func();
        break;
    }
}
'''


def test_join_store_split_is_wired_into_the_repairs():
    assert 'join_store_split' in draft_repairs.AGENT_FAMILIES
    out = draft_repairs.variants(SOURCE, 'f', 'mips', '', ('-G0', '-mips3', '-mgp32', '-mfp32', '-O2'), limit=400)
    assert any('gSceneType = gSceneType + 1;' in v and 'gSceneType = var_s1' not in v for v in out)
