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


def test_deep_permuter_stages_the_axis_when_a_complete_clamp_reloads_it():
    source = SOURCE[:SOURCE.rindex('}')] + '''
    else if (result > 32.0f) {
        *((f32 *) (((char *) record) + 0x48)) = 32.0f;
    }
    axis = *((f32 *) (((char *) record) + 0x48));
}
'''
    options = proposals(source, 'adjust', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    [candidate] = [text for label, text in options if label == 'float_clamp_axis_stage']
    assert 'axis = axis * 32.0f;\n    old =' in candidate
    assert 'result = ((axis - old)' in candidate


def test_deep_permuter_preserves_the_sign_before_scaling_in_place():
    sign = '''    original = axis;
    axis = axis * axis;
    if (original < 0.0f) {
        axis = -axis;
    }
'''
    source = SOURCE.replace('f32 axis,', 'f32 original, axis,').replace('    old =', sign + '    old =')
    source = source[:source.rindex('}')] + '''
    else if (result > 32.0f) {
        *((f32 *) (((char *) record) + 0x48)) = 32.0f;
    }
    axis = *((f32 *) (((char *) record) + 0x48));
    report(axis, result);
}
'''
    prefix = [
        (' ', 'jal sample', 'jal sample'),
        (' ', 'nop', 'nop'),
        (' ', 'mov.s $f26, $f0', 'mov.s $f26, $f0'),
        ('r', 'mtc1 $zero, $f0', 'mtc1 $zero, $f2'),
        ('o', 'mov.s $f24, $f26', 'nop'),
        ('r', 'c.lt.s $f24, $f0', 'c.lt.s $f0, $f2'),
        (' ', 'nop', 'nop'),
        (' ', 'nop', 'nop'),
        (' ', 'bc1f .Lpositive', 'bc1f .Lpositive'),
        (' ', 'mul.s $f26, $f26, $f26', 'mul.s $f26, $f26, $f26'),
        (' ', 'neg.s $f26, $f26', 'neg.s $f26, $f26'),
    ]

    family = 'float_clamp_signed_square'
    [candidate] = [text for label, text in proposals(source, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', prefix + ROWS, '') if label == family]
    assert 'if (result < 0.0f)' in candidate
    assert 'axis *= 32.0f;\n    old =' in candidate
    for compiler in ('gcc-egcs', 'ido-5.3'):
        assert all(label != family for label, _ in proposals(source, 'adjust', '', 'mips',
                                                              compiler, prefix + ROWS, ''))
