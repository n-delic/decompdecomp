from decompnet.app.diff_gated_repairs import proposals

SOURCE = """void f(void) {
    f32 saved;
    f32 value;
    f32 bound;
    saved = value;
    if ((value >= 0.0f) && (((s32) value) < bound)) {
        x = a - (value * ax);
        y = b - (value * ay);
        z = c - (((int) value) * az);
    }
}"""
ROWS = [
    ('i', 'swc1 $f0, 0x10($sp)', 'swc1 $f4, 0x20($sp)'),
    ('>', '', 'trunc.w.s $f0, $f4'),
    ('>', '', 'mfc1 $v0, $f0'),
    ('>', '', 'mtc1 $v0, $f0'),
    ('>', '', 'cvt.s.w $f0, $f0'),
    (' ', 'c.lt.s $f0, $f24', 'c.lt.s $f0, $f24'),
    ('r', 'mul.s $f2, $f0, $f12', 'mul.s $f2, $f4, $f12'),
    ('r', 'mul.s $f4, $f0, $f6', 'mul.s $f4, $f4, $f6'),
    (' ', 'mul.s $f0, $f0, $f8', 'mul.s $f0, $f0, $f8'),
]


def test_app_offers_the_pair_when_the_target_keeps_the_float():
    family = 'untruncated_float_compare_and_product'
    texts = [text for label, text in proposals(SOURCE, 'f', '', 'mips',
                                              'gcc-2.7.2-kmc', ROWS, '') if label == family]
    text, = texts
    assert '(saved < bound)' in text
    assert 'z = c - (saved * az);' in text
