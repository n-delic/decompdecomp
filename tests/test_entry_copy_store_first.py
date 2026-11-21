"""decomprepair 0.5.281's `entry_parameter_copy`, `store_value_first` and `field_struct_view_printed` reach the repairs the
deep pass and autopilot try.

From a 636-instruction N64 camera function (KMC GCC 2.7.2), matched by hand from a saved best of 8805: the prologue's
register saves came out in another order until a parameter was copied into a local after the first load (40 to 0),
float-to-short values computed in a temporary before their stores took it from 815 to 90, and the member form of
every field access on the printed spelling a saved source carries from 8535 to 6105 (judge-worker; no permuter run)."""
from decompnet.app import draft_repairs

FLAGS = ('-G0', '-mips3', '-mgp32', '-mfp32', '-O2')
SOURCE = '''void f(void *arg0, s32 arg1) {
    f32 scale;
    do {
        scale = *((f32 *) (((char *) arg0) + 0x4C));
        if (*((s32 *) (((char *) arg0) + 0x100)) & 4) {
            use(arg1, scale);
        }
        rs->a[arg1].h[4] = (s16) ((s32) (*((f32 *) (((char *) arg0) + 0x60)) * 4.0f));
        rs->a[arg1].h[5] = (s16) ((s32) (*((f32 *) (((char *) arg0) + 0x64)) * 4.0f));
        use(arg1 * 2, scale);
    } while (0);
}
'''


def _labels():
    from decomprepair import entry_parameter_copy_repairs, field_struct_view_repairs, store_value_first_repairs
    for module in (entry_parameter_copy_repairs, store_value_first_repairs, field_struct_view_repairs):
        assert set(module.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)
    return draft_repairs.variants(SOURCE, 'f', 'mips', '', FLAGS, limit=2000)


def test_the_three_families_are_wired_into_the_repairs():
    out = _labels()
    assert any('arg1 = arg1_in;' in v and 's32 arg1_in)' in v for v in out)  # entry_parameter_copy
    assert any('tv1 = (s16)' in v and '.h[4] = tv1;' in v for v in out)  # store_value_first
    assert any('->v)' in v and 'typedef struct { f32 v; } Field_f32;' in v for v in out)  # field_struct_view_printed
