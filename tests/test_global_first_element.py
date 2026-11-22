from decompnet.app import draft_repairs


def test_deep_repair_lane_offers_first_element_global():
    source = 'extern s32 table;\nvoid f(void) { call(0, table, 1); }\n'
    labels = {}
    candidates = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    expected = 'extern s32 table[];\nvoid f(void) { call(0, table[0], 1); }\n'
    assert expected in candidates
    assert labels[expected] == 'global_first_element'


def test_deep_repair_lane_offers_every_flag_read_as_first_element():
    # Reading a one-byte flag as `flag[0]` keeps its load behind earlier stores through struct pointers
    # (sched1's true_dependence); one KMC GCC 2.7.2 function went 910 -> 0 with this single child.
    source = ('extern u8 flag;\nvoid f(P *p) {\n    p->a->mode = 1;\n    if (flag) { p->b = 2; }\n'
              '    if (flag) { p->c = 3; }\n}\n')
    labels = {}
    candidates = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    expected = source.replace('flag;', 'flag[];').replace('(flag)', '(flag[0])')
    assert expected in candidates
    assert labels[expected] == 'global_first_element'
