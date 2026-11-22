from decompnet.app.diff_gated_repairs import proposals

FAMILY = "float_clamp_scale_stage"

SOURCE = '''
void adjust(char *record)
{
    f32 axis, old, result;
    axis = sample();
    old = *((f32 *) (((char *) record) + 0x48));
    result = (((axis * 32.0f) - old) * 0.1f) + old;
    *((f32 *) (((char *) record) + 0x48)) = result;
    if (result < (-32.0f)) {
        *((f32 *) (((char *) record) + 0x48)) = -32.0f;
    }
}
'''
ROWS = [
    (' ', 'lui $at, 0x4200', 'lui $at, 0x4200'),
    (' ', 'mtc1 $at, $f6', 'mtc1 $at, $f6'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f26, $f26, $f6', 'mul.s $f0, $f22, $f6'),
    ('r', 'lwc1 $f0, 0x48($s1)', 'lwc1 $f4, 0x48($s1)'),
    ('r', 'sub.s $f2, $f26, $f0', 'sub.s $f0, $f0, $f4'),
]


def test_deep_permuter_tries_the_witnessed_smoothing_scale_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'result = axis * 32.0f;\n    result = ((result - old)' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
