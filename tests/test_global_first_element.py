from decompnet.app import draft_repairs


def test_deep_repair_lane_offers_first_element_global():
    source = 'extern s32 table;\nvoid f(void) { call(0, table, 1); }\n'
    labels = {}
    candidates = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    expected = 'extern s32 table[];\nvoid f(void) { call(0, table[0], 1); }\n'
    assert expected in candidates
    assert labels[expected] == 'global_first_element'
