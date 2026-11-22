from decompnet.app.diff_gated_repairs import proposals

FAMILY = "saved_conditional_smoothing_gain"

SOURCE = """\
f32 sample(s32 port, s32 x, s32 y);
void update(char *record, s32 port, s32 mode, f32 axis)
{
    f32 holder;
    f32 gain;
    f32 current;
    f32 next;
    if (mode) {
        gain = 0.25f;
    } else {
        gain = 0.5f;
    }
    current = *((f32 *) (((char *) record) + 0x18));
    next = ((axis - current) * gain) + current;
    report(next);
    holder = sample(port, 2, 3) * 0.75f;
    report(holder);
}
"""

DEMAND = [
    'lui $at, 0x3e80', 'ori $at, $at, 0', 'mtc1 $at, $f24',
    'j .Lblend', 'nop',
    'lui $at, 0x3f00', 'ori $at, $at, 0', 'mtc1 $at, $f24',
    'lwc1 $f0, 0x18($s0)', 'sub.s $f2, $f26, $f0',
    'mul.s $f2, $f2, $f24', 'add.s $f12, $f2, $f0',
]
DEMAND = [('r' if '$f24' in op else ' ', op, op.replace('$f24', '$f20')) for op in DEMAND]
DONOR = [(' ', op, op) for op in [
    'addiu $a1, $zero, 2', 'jal sample', 'addiu $a2, $zero, 3',
    'lui $at, 0x3f40', 'ori $at, $at, 0', 'mtc1 $at, $f2', 'nop',
    'mul.s $f24, $f0, $f2', *(['nop'] * 10),
]]
ROWS = DEMAND + DONOR


def test_deep_permuter_retains_the_conditional_blend_gain_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'update', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'holder = 0.25f;' in candidate and 'holder = 0.5f;' in candidate
    assert 'next = ((axis - current) * holder) + current;' in candidate
    assert FAMILY not in {label for label, _ in proposals(SOURCE, 'update', '', 'mips', 'ido-7.1', ROWS, '')}
