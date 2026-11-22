"""The Deep repair lane folds a two-step local and copies it back after a copy (decomp-repair 0.5.358)."""
from decompnet.app import draft_repairs


def test_lane_offers_fold_with_copy_back():
    # A gcc 2.7.2 race function closed its last two rows only with both edits together.
    source = '''extern s32 D_800C469C;
void f(void *arg0, s32 k) {
    void *base;
    void *entry;
    void *cur;
    base = &D_800C469C;
    entry = (void *) (*((s8 *) arg0 + 0xE) * 0xE8);
    entry = (void *) (((char *) entry) + ((s32) base));
    switch (k) {
    default:
        cur = entry;
        use(cur);
    }
}
'''
    labels = {}
    proposals = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    assert any(labels.get(candidate) == 'copy_back_set'
               and 'entry = (void *) (((char *) ((void *) (*((s8 *) arg0 + 0xE) * 0xE8))) + ((s32) base));' in candidate
               and 'entry = cur;' in candidate for candidate in proposals)
