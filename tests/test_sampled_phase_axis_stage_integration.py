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
