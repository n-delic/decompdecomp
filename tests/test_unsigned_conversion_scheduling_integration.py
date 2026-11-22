from decompnet.app import draft_repairs

SOURCE = """void f(float a, float b) {
    if (a >= 2.1474836e9f) {
        x = (s32) (a - 2.1474836e9f);
        x = x | 0x80000000;
    } else {
        x = (s32) a;
    }
    if (!(b >= 2.1474836e9f)) {
        y = (s32) b;
    } else {
        y = (s32) (b - 2.1474836e9f);
        y = y | 0x80000000;
    }
}"""


def test_app_offers_paired_unsigned_conversion_scheduling():
    family = 'float_unsigned_conversion_scheduling'
    assert family in draft_repairs.AGENT_FAMILIES
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'f', 'mips', flags=['-O2'], labels=labels, limit=None)
    assert any(labels.get(text) == family and 'if (!(a >= 2.1474836e9f))' in text
               and 'do { y = y | 0x80000000; } while (0);' in text for text in texts)
