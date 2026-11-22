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


PROJECTION_SOURCE = '''
void adjust(char *record, float input)
{
    float axis;
    f32 projection;
    f32 value;
    f32 prior;
    f32 result;
    float horizontal = 0.0f;
    float vertical = 0.0f;
    int upper;
    int lower;
    axis = input;
    value = input;
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
    projection = *((f32 *) (((char *) record) + 4));
    horizontal += (projection * angle_sin(axis));
    vertical += (projection * angle_cos(axis));
    prior = *((f32 *) (((char *) record) + 8));
    result = (((value * 0.25f) - prior) * 0.05f) + prior;
    *((f32 *) (((char *) record) + 8)) = (((value * 0.25f) - prior) * 0.05f) + prior;
    report(horizontal, vertical, result);
}
'''
EARLY = ROWS[:24]
LATE = [
    (' ', 'jal angle_cos', 'jal angle_cos'),
    (' ', 'add.s $f12, $f12, $f20', 'add.s $f12, $f12, $f20'),
    ('o', 'nop', ''),
    ('o', 'mul.s $f0, $f26, $f0', ''),
    (' ', 'lui $at, 0x3e80', 'lui $at, 0x3e80'),
    (' ', 'ori $at, $at, 0', 'ori $at, $at, 0'),
    (' ', 'mtc1 $at, $f8', 'mtc1 $at, $f8'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f26, $f30, $f8', 'mul.s $f2, $f30, $f8'),
    (' ', 'lwc1 $f6, 8($s1)', 'lwc1 $f6, 8($s1)'),
    ('o', '', 'mul.s $f0, $f26, $f0'),
    ('r', 'sub.s $f2, $f26, $f6', 'sub.s $f2, $f2, $f6'),
]
PROJECTION_ROWS = EARLY + LATE


def test_deep_permuter_pairs_projection_scale_and_predicate_for_kmc():
    [candidate] = [text for label, text in proposals(PROJECTION_SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', PROJECTION_ROWS, '') if label == 'balanced_projection_scale']
    assert 'repair_guard_fold_bits = upper ^ 1;' in candidate
    assert 'projection = value * 0.25f;' in candidate
    for arch, toolchain in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'ido-5.3'), ('mips', 'gcc-egcs')]:
        assert all(label != 'balanced_projection_scale' for label, _ in proposals(PROJECTION_SOURCE, 'adjust', '', arch, toolchain, PROJECTION_ROWS, ''))


CARRY_SOURCE = '''
void adjust(void *record, float angle, int bypass)
{
    float phase;
    float vertical;
    int upper;
    int lower;
    vertical = 0.0f;
    phase = *((f32 *) (((char *) record) + 8));
    upper = 1;
    if (!(phase <= 0.0002f)) {
        upper = 0;
    }
    lower = 1;
    if (!(phase >= (-0.0002f))) {
        lower = 0;
    }
    if (((lower ^ 1) | (upper ^ 1)) != 0) {
        mark(record);
    }
    project:
    vertical = 0.0f + (phase * cosine(angle));
    report(vertical);
}
'''
CARRY_EARLY = [('r', 'lwc1 $f26, 8($s1)', 'lwc1 $f20, 8($s1)')] + ROWS[:24]
CARRY_LATE = [
    (' ', 'beqz $v0, .Lprojection', 'beqz $v0, .Lprojection'),
    (' ', 'nop', 'nop'),
    (' ', 'jal cosine', 'jal cosine'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f0, $f26, $f0', 'mul.s $f0, $f20, $f0'),
    ('<', 'mtc1 $s6, $f16', ''),
    (' ', 'nop', 'nop'),
    ('r', 'add.s $f16, $f16, $f0', 'add.s $f0, $f0, $f24'),
    ('r', 'mfc1 $s6, $f16', 'mfc1 $s6, $f0'),
]
CARRY_ROWS = CARRY_EARLY + CARRY_LATE


def test_deep_permuter_pairs_the_predicate_with_one_zero_projection_carry():
    family = 'balanced_predicate_projection'
    [candidate] = [text for label, text in proposals(CARRY_SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', CARRY_ROWS, '') if label == family]
    assert 'repair_early_fold | (lower ^ 1)' in candidate
    assert 'vertical += (phase * cosine(angle));' in candidate
    for compiler in ('gcc-egcs', 'ido-5.3'):
        assert all(label != family for label, _ in proposals(CARRY_SOURCE, 'adjust', '', 'mips',
                                                              compiler, CARRY_ROWS, ''))


SUBTRACT_SOURCE = '''
extern f32 lift;
void adjust(void *record, int bypass)
{
    f32 vertical;
    union { u32 bits; f32 number; } zero;
    zero.bits = (u32)record & ~(u32)record;
    vertical = zero.number;
    vertical = zero.number - lift;
    report(vertical);
}
'''
SUBTRACT_ROWS = [
    (' ', 'lui $at, %hi(lift)', 'lui $at, %hi(lift)'),
    (' ', 'lwc1 $f0, %lo(lift)($at)', 'lwc1 $f0, %lo(lift)($at)'),
    ('r', 'mtc1 $s6, $f16', 'mtc1 $fp, $f16'),
    (' ', 'nop', 'nop'),
    ('r', 'sub.s $f16, $f16, $f0', 'sub.s $f0, $f16, $f0'),
    ('r', 'mfc1 $s6, $f16', 'mfc1 $s6, $f0'),
]


def test_deep_permuter_reuses_a_proven_zero_for_global_subtraction():
    family = 'carried_zero_global_subtract'
    [candidate] = [text for label, text in proposals(SUBTRACT_SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', SUBTRACT_ROWS, '') if label == family]
    assert 'vertical -= lift;' in candidate
    assert all(label != family for label, _ in proposals(SUBTRACT_SOURCE, 'adjust', '', 'mips',
                                                          'gcc-egcs', SUBTRACT_ROWS, ''))
