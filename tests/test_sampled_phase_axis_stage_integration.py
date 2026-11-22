from decompnet.app.diff_gated_repairs import proposals

FAMILY = "sampled_phase_axis_stage"

SOURCE = '''
f32 sample(int, int, int);
void adjust(char *record, int input)
{
    f32 carry;
    f32 axis;
    f32 old;
    f32 result;
    int quiet;
    carry = *((f32 *) (((char *) record) + 0x50));
    quiet = 1;
    if (!(carry <= 0.0002f)) {
        quiet = 0;
    }
    if (!quiet) {
        *((f32 *) (((char *) record) + 0x34)) = carry;
    }
    axis = -sample(input, 4, 5);
    if (axis <= 0.0002f) {
        axis = sample(input, 2, 3);
    }
    scale_here:
    old = *((f32 *) (((char *) record) + 0x48));
    result = axis * 32.0f;
    result = ((result - old) * 0.1f) + old;
    report(result);
}
'''
ROWS = [
    ('r', 'lwc1 $f22, 0x50($s1)', 'lwc1 $f2, 0x50($s1)'),
    ('r', 'c.le.s $f22, $f2', 'c.le.s $f2, $f4'),
    ('r', 'swc1 $f22, 0x34($s1)', 'swc1 $f2, 0x34($s1)'),
    (' ', 'addiu $a1, $zero, 4', 'addiu $a1, $zero, 4'),
    (' ', 'jal sample', 'jal sample'),
    (' ', 'addiu $a2, $zero, 5', 'addiu $a2, $zero, 5'),
    ('r', 'neg.s $f26, $f0', 'neg.s $f22, $f0'),
    (' ', 'lui $at, 0x4200', 'lui $at, 0x4200'),
    (' ', 'mtc1 $at, $f6', 'mtc1 $at, $f6'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f26, $f26, $f6', 'mul.s $f2, $f22, $f6'),
    (' ', 'lwc1 $f0, 0x48($s1)', 'lwc1 $f0, 0x48($s1)'),
    ('r', 'sub.s $f2, $f26, $f0', 'sub.s $f2, $f2, $f0'),
]


def test_deep_permuter_reuses_the_guarded_local_for_the_sampled_scale_on_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'carry = -sample(input, 4, 5);' in candidate and 'carry *= 32.0f;' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))


def test_deep_permuter_reuses_the_saved_axis_when_the_scale_is_already_in_place():
    source = SOURCE.replace('if (!(carry <= 0.0002f))', 'if (!(carry <= 0.25f))')
    source = source.replace('    result = axis * 32.0f;\n    result = ((result - old) * 0.1f) + old;',
                            '    axis *= 32.0f;\n    result = ((axis - old) * 0.1f) + old;')
    guarded = [(' ', op, op) for op in [
        'lwc1 $f26, 0x50($s1)', 'lui $at, 0x3e80', 'ori $at, $at, 0', 'mtc1 $at, $f2',
        'nop', 'c.le.s $f26, $f2',
    ]]
    phase = ROWS[3:6] + [(' ', op, op) for op in ['lui $at, 0x3e80', 'ori $at, $at, 0']] + [
        ('r', 'mtc1 $at, $f22', 'mtc1 $at, $f26'),
        ROWS[6],
        ('r', 'c.le.s $f26, $f22', 'c.le.s $f22, $f26'),
    ] + ROWS[7:10] + [
        ('r', 'mul.s $f26, $f26, $f6', 'mul.s $f22, $f22, $f6'),
        ROWS[11],
        ('r', 'sub.s $f2, $f26, $f0', 'sub.s $f2, $f22, $f0'),
    ]
    [candidate] = [text for label, text in proposals(source, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', guarded + phase, '') if label == FAMILY]
    assert 'carry = -sample(input, 4, 5);' in candidate
    assert 'carry = sample(input, 2, 3);' in candidate
    assert 'carry *= 32.0f;' in candidate
    assert FAMILY not in {label for label, _ in proposals(source, 'adjust', '', 'mips', 'ido-5.3', guarded + phase, '')}
