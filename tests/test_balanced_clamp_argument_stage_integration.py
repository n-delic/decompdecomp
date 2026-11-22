from decompnet.app.diff_gated_repairs import proposals

FAMILY = "balanced_clamp_argument_stage"

PREDICATES = '''
    first = 1;
    if (!(axis <= 0.0002f)) {
        first = (unsigned int) 0;
    }
    second = 1;
    if (!(axis >= (-0.0002f))) {
        second = 0;
    }
    if (((second ^ 1) | (first ^ 1)) != 0) record_event();
'''
SOURCE = '''
void func_80000200(void *, void *, f32, f32, s32, s32);
void adjust(char *record, f32 axis)
{
    int first;
    int second;
    int other_first;
    int other_second;
    f32 result;
''' + PREDICATES + PREDICATES.replace('first', 'other_first').replace('second', 'other_second') + '''
    result = axis;
    *((f32 *) (((char *) record) + 0x50)) = result;
    if (result < (-0.2f)) {
        *((f32 *) (((char *) record) + 0x50)) = -0.2f;
    } else if (result > 0.2f) {
        *((f32 *) (((char *) record) + 0x50)) = 0.2f;
    }
    func_80000200(record, record, axis, *((f32 *) (((char *) record) + 0x50)), 0, 1);
}
'''
PREDICATE_ROWS = [
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
    (' ', 'mtc1 $at, $f2', 'mtc1 $at, $f0'),
    (' ', 'nop', 'nop'),
    (' ', 'c.le.s $f2, $f26', 'c.le.s $f0, $f26'),
    ('o', 'addiu $v0, $zero, 1', 'nop'),
    (' ', 'nop', 'nop'),
    (' ', 'bc1t .Lsecond', 'bc1t .Lsecond'),
    ('o', '', 'addiu $v0, $zero, 1'),
    ('o', '', 'move $v0, $zero'),
    ('r', 'xori $v1, $v1, 1', 'xori $v0, $v0, 1'),
    ('o', 'move $v0, $zero', ''),
    ('r', 'xori $v0, $v0, 1', 'xori $v1, $v1, 1'),
    ('r', 'or $v0, $v1, $v0', 'or $v0, $v0, $v1'),
]
ARGUMENT_ROWS = [
    (' ', 'bc1tl .Lfinish', 'bc1tl .Lfinish'),
    (' ', 'swc1 $f6, 0x50($s1)', 'swc1 $f6, 0x50($s1)'),
    ('<', 'lwc1 $f24, 0x50($s1)', ''),
    (' ', 'lw $a1, 0x30($s1)', 'lw $a1, 0x30($s1)'),
    ('|', 'mfc1 $a3, $f24', 'lw $a3, 0x50($s1)'),
    ('o', 'nop', ''),
    (' ', 'jal func_80000100+0x100', 'jal func_80000100+0x100'),
    (' ', 'move $a0, $s2', 'move $a0, $s2'),
]
ROWS = PREDICATE_ROWS * 2 + ARGUMENT_ROWS


def test_deep_permuter_routes_the_balanced_post_clamp_argument_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'func_80000200(record, record, axis, result, 0, 1);' in candidate
    assert '= first ^ 1;' in candidate and '= other_first ^ 1;' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
