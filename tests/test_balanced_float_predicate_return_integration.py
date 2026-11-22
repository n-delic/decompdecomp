from decompnet.app.diff_gated_repairs import proposals

FAMILY = "balanced_float_predicate_return"

SOURCE = '''
void adjust(char *record, float input)
{
    float axis;
    f32 angle_result;
    int upper;
    int lower;
    axis = input;
    upper = 1;
    if (!(axis <= 0.0002f)) {
        upper = 0;
    }
    lower = 1;
    if (!(axis >= (-0.0002f))) {
        lower = 0;
    }
    if (((lower ^ 1) | (upper ^ 1)) != 0) {
        record_event();
    }
    angle_result = next_value(input);
    *((f32 *) (((char *) record) + 0x40)) = (f32) ((*((f32 *) (((char *) record) + 0x40))) + angle_result);
    if (angle_result == 0.0f) {
        notify();
    }
}
'''
ROWS = [
    (' ', 'lui $at, 0x3951', 'lui $at, 0x3951'),
    (' ', 'ori $at, $at, 0xb717', 'ori $at, $at, 0xb717'),
    (' ', 'mtc1 $at, $f0', 'mtc1 $at, $f0'),
    (' ', 'nop', 'nop'),
    (' ', 'c.le.s $f26, $f0', 'c.le.s $f26, $f0'),
    (' ', 'nop', 'nop'),
    (' ', 'nop', 'nop'),
    (' ', 'bc1t .Lfirst', 'bc1t .Lfirst'),
    (' ', 'addiu $v1, $zero, 1', 'addiu $v1, $zero, 1'),
    (' ', 'move $v1, $zero', 'move $v1, $zero'),
    (' ', 'lui $at, 0xb951', 'lui $at, 0xb951'),
    (' ', 'ori $at, $at, 0xb717', 'ori $at, $at, 0xb717'),
    (' ', 'mtc1 $at, $f0', 'mtc1 $at, $f0'),
    (' ', 'nop', 'nop'),
    (' ', 'c.le.s $f0, $f26', 'c.le.s $f0, $f26'),
    ('o', 'addiu $v0, $zero, 1', 'nop'),
    (' ', 'nop', 'nop'),
    (' ', 'bc1t .Lsecond', 'bc1t .Lsecond'),
    ('o', '', 'addiu $v0, $zero, 1'),
    ('o', '', 'move $v0, $zero'),
    ('r', 'xori $v1, $v1, 1', 'xori $v0, $v0, 1'),
    ('o', 'move $v0, $zero', ''),
    ('r', 'xori $v0, $v0, 1', 'xori $v1, $v1, 1'),
    ('r', 'or $v0, $v1, $v0', 'or $v0, $v0, $v1'),
    (' ', 'jal next_value', 'jal next_value'),
    (' ', 'nop', 'nop'),
    ('r', 'lwc1 $f2, 0x40($s2)', 'lwc1 $f4, 0x40($s2)'),
    ('<', 'mov.s $f26, $f0', ''),
    ('r', 'add.s $f2, $f2, $f26', 'add.s $f4, $f4, $f0'),
    ('r', 'mtc1 $zero, $f0', 'mtc1 $zero, $f2'),
    (' ', 'nop', 'nop'),
    ('r', 'c.eq.s $f26, $f0', 'c.eq.s $f0, $f2'),
    (' ', 'nop', 'nop'),
    (' ', 'nop', 'nop'),
    (' ', 'bc1f .Lexit', 'bc1f .Lexit'),
    ('r', 'swc1 $f2, 0x40($s2)', 'swc1 $f4, 0x40($s2)'),
]


def test_deep_permuter_routes_balanced_float_predicate_and_return_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'repair_first_inverted = upper ^ 1;' in candidate and 'axis = next_value(input);' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
