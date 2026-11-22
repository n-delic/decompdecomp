from decompnet.app.diff_gated_repairs import proposals

FAMILY = 'saved_quadratic_sign_holder'

SOURCE = """\
float sample(int port, int x, int y);
void track(int port, int mode)
{
    float axis;
    float scratch;
    float held;
    if (mode) {
        axis = sample(port, 4, 5);
        scratch = axis;
        axis = (axis * axis) * 2.5f;
        if (scratch < 0.0f) {
            axis = -axis;
        }
        record(axis);
    } else {
        held = sample(port, 2, 3) * 0.5f;
        record(held);
    }
}
"""

DEMAND = [
    'addiu $a1, $zero, 4', 'jal sample', 'addiu $a2, $zero, 5',
    'mov.s $f26, $f0', 'mul.s $f2, $f26, $f26', 'mov.s $f24, $f26', 'mtc1 $zero, $f0',
    'lui $at, 0x4020', 'ori $at, $at, 0', 'mtc1 $at, $f6', 'c.lt.s $f24, $f0',
    'nop', 'nop', 'bc1f .Lkeep', 'mul.s $f26, $f2, $f6', 'neg.s $f26, $f26', 'nop',
]
DONOR = [
    'addiu $a1, $zero, 2', 'jal sample', 'addiu $a2, $zero, 3',
    'lui $at, 0x3f00', 'ori $at, $at, 0', 'mtc1 $at, $f6', 'nop',
    'mul.s $f24, $f0, $f6', 'nop', 'nop', 'nop', 'nop', 'nop', 'nop', 'nop', 'nop', 'nop',
]
ROWS = [(' ' if '$f24' not in op else 'r', op, op.replace('$f24', '$f2')) for op in DEMAND]
ROWS += [(' ', op, op) for op in DONOR]


def test_deep_permuter_reuses_a_saved_quadratic_sign_local_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'track', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'held = axis;' in candidate
    assert 'if (held < 0.0f)' in candidate
    assert FAMILY not in {label for label, _ in proposals(SOURCE, 'track', '', 'mips', 'ido-7.1', ROWS, '')}
