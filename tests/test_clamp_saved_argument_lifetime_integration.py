from decompnet.app.diff_gated_repairs import proposals

FAMILY = "clamp_saved_argument_lifetime"

SOURCE = '''
f32 sample(int, int, int);
void forward(void *, void *, f32, f32, s32, s32);
void adjust(char *record, int input)
{
    f32 result;
    f32 old;
    f32 carry;
    result = sample(input, 2, 3) * 0.2f;
    old = *((f32 *) (((char *) record) + 0x50));
    result = ((result - old) * 0.3f) + old;
    *((f32 *) (((char *) record) + 0x50)) = result;
    if (result < (-0.2f)) {
        *((f32 *) (((char *) record) + 0x50)) = -0.2f;
    } else if (result > 0.2f) {
        *((f32 *) (((char *) record) + 0x50)) = 0.2f;
    }
    result = *((f32 *) (((char *) record) + 0x50));
    forward(record, record, old, result, 0, 1);
    carry = sample(input, 0, 1);
    report(carry);
}
'''
ROWS = [
    (' ', 'addiu $a1, $zero, 2', 'addiu $a1, $zero, 2'),
    (' ', 'jal sample', 'jal sample'),
    (' ', 'addiu $a2, $zero, 3', 'addiu $a2, $zero, 3'),
    (' ', 'lui $at, 0x3e4c', 'lui $at, 0x3e4c'),
    (' ', 'ori $at, $at, 0xcccd', 'ori $at, $at, 0xcccd'),
    (' ', 'mtc1 $at, $f6', 'mtc1 $at, $f6'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f24, $f0, $f6', 'mul.s $f2, $f0, $f6'),
    (' ', 'lwc1 $f0, 0x50($s1)', 'lwc1 $f0, 0x50($s1)'),
    ('r', 'sub.s $f2, $f24, $f0', 'sub.s $f2, $f2, $f0'),
    (' ', 'nop', 'nop'),
    (' ', 'nop', 'nop'),
    (' ', 'bc1tl .Lfinish', 'bc1tl .Lfinish'),
    (' ', 'swc1 $f6, 0x50($s1)', 'swc1 $f6, 0x50($s1)'),
    ('r', 'lwc1 $f24, 0x50($s1)', 'lwc1 $f2, 0x50($s1)'),
    ('r', 'mfc1 $a3, $f24', 'mfc1 $a3, $f2'),
    (' ', 'nop', 'nop'),
    (' ', 'jal forward', 'jal forward'),
    (' ', 'move $a0, $s2', 'move $a0, $s2'),
    (' ', 'move $a1, $zero', 'move $a1, $zero'),
    (' ', 'jal sample', 'jal sample'),
    (' ', 'addiu $a2, $zero, 1', 'addiu $a2, $zero, 1'),
    (' ', 'mov.s $f24, $f0', 'mov.s $f24, $f0'),
]


def test_deep_permuter_reuses_the_saved_argument_lifetime_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'carry = sample(input, 2, 3) * 0.2f;' in candidate
    assert 'forward(record, record, old, carry, 0, 1);' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
