"""The focused KMC lane offers the combined address and register repair."""

from decompnet.app import generation_calls

SOURCE = '''void f(u8 *input) {
    s16 mode = 0;
    u8 *p;
    s32 result;
    switch (kind) {
    case 1: mode = 4; break;
    case 2: mode = 6; break;
    }
    {
        s32 first = *(u16 *)(table + 2);
        s32 id = mode;
        do { p = input + (first << 3); } while (0);
        first = mode - first;
        p[0] = first;
        result = call(id, first);
    }
    use(result);
}
'''
ROWS = [('r', 'subu $a1, $s0, $a1', 'subu $a1, $a0, $a1')]


def test_focused_kmc_lane_offers_the_combination_before_widening_alone():
    proposals = generation_calls.serve('decomprepair.kmc_type_repairs.variants',
                                       [SOURCE, 'f', '', ROWS], {}, None)
    assert proposals[0][0] == 'kmc_staged_address_order'
    assert 's32 mode = 0;' in proposals[0][1]
    assert 'result = (unsigned int)input - (0U - (unsigned int)result);' in proposals[0][1]
    assert proposals[1][0] == 'kmc_widen_sign_local'
