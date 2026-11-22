from decompnet.app.diff_gated_repairs import proposals

FAMILY = "carried_zero_second_projection"

SOURCE = """\
float forward(float angle);
float sideways(float angle);
void project(float sample, float angle, int mode)
{
    float axis;
    float x;
    float z;
    union { u32 bits; f32 number; } zero;
    axis = sample;
    zero.bits = 0;
    x = 0.0f;
    z = zero.number;
    if (mode) {
        x += (axis * forward(angle));
        z = 0.0f + (axis * sideways(angle));
    }
    report(x, z);
}
"""
MATCHED = [(' ', op, op) for op in [
    'jal forward', 'nop', 'mul.s $f0, $f26, $f0', 'mtc1 $s5, $f16', 'nop',
    'add.s $f16, $f16, $f0', 'lwc1 $f12, 4($s1)', 'mfc1 $s5, $f16',
]]
MISMATCHED = [
    (' ', 'jal sideways', 'jal sideways'),
    (' ', 'nop', 'nop'),
    (' ', 'mul.s $f0, $f26, $f0', 'mul.s $f0, $f26, $f0'),
    ('r', 'mtc1 $s6, $f16', 'mtc1 $zero, $f2'),
    (' ', 'nop', 'nop'),
    ('r', 'add.s $f16, $f16, $f0', 'add.s $f0, $f0, $f2'),
    ('r', 'mfc1 $s6, $f16', 'mfc1 $s6, $f0'),
]
ROWS = MATCHED + MISMATCHED


def test_deep_permuter_carries_proven_zero_in_the_second_projection_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'project', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'z += (axis * sideways(angle));' in candidate
    assert 'x += (axis * forward(angle));' in candidate
    assert FAMILY not in {label for label, _ in proposals(SOURCE, 'project', '', 'mips', 'ido-7.1', ROWS, '')}
