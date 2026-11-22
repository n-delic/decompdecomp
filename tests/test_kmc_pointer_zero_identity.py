from decompnet.app import draft_repairs


def test_deep_repair_lane_offers_pointer_zero_identity():
    source = ('extern char *global;\nvoid f(void) { char *p = global; '
              's32 done = 0; if (done) call(); }\n')
    labels = {}
    candidates = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None,
                                         header='typedef unsigned u32;')
    expected = source.replace('done = 0', 'done = (u32)p & ~(u32)p')
    assert expected in candidates
    assert labels[expected] == 'kmc_pointer_zero_identity'
