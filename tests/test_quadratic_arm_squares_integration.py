from decompnet.app.diff_gated_repairs import proposals

FAMILY = "signed_quadratic_arm_squares"

SOURCE = """\
void respond(float input)
{
    float axis;
    float squared;
    axis = input;
    squared = axis * axis;
    if (axis > 0.0f) {
        axis = (squared * 0.25f) + 0.5f;
    } else {
        axis = (squared * (-0.125f)) - 0.5f;
    }
    transmit(axis);
}
"""
OPS = [
    'c.lt.s $f0, $f26', 'nop', 'nop', 'bc1f .Lnegative', 'mul.s $f0, $f26, $f26',
    'lui $at, 0x3e80', 'ori $at, $at, 0', 'mtc1 $at, $f2', 'nop', 'mul.s $f0, $f0, $f2',
    'lui $at, 0x3f00', 'ori $at, $at, 0', 'mtc1 $at, $f2', 'nop', 'j .Ljoin', 'add.s $f26, $f0, $f2',
    'lui $at, 0xbe00', 'ori $at, $at, 0', 'mtc1 $at, $f2', 'nop', 'mul.s $f0, $f0, $f2',
    'lui $at, 0x3f00', 'ori $at, $at, 0', 'mtc1 $at, $f2', 'nop', 'sub.s $f26, $f0, $f2',
]
SWAPS = {4: 'mul.s $f2, $f26, $f26', 7: 'mtc1 $at, $f0', 9: 'mul.s $f0, $f2, $f0',
         18: 'mtc1 $at, $f0', 20: 'mul.s $f0, $f2, $f0'}
ROWS = [('r' if i in SWAPS else ' ', op, SWAPS.get(i, op)) for i, op in enumerate(OPS)]


def test_deep_permuter_inlines_the_square_in_the_two_signed_arms_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'respond', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'axis = ((axis * axis) * 0.25f) + 0.5f;' in candidate
    assert 'axis = ((axis * axis) * (-0.125f)) - 0.5f;' in candidate
    assert FAMILY not in {label for label, _ in proposals(SOURCE, 'respond', '', 'mips', 'ido-7.1', ROWS, '')}
