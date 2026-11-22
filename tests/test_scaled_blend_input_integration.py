from decompnet.app.diff_gated_repairs import proposals

FAMILY = "saved_scaled_blend_input"

SOURCE = """\
extern f32 phase;
f32 sample(s32 port, s32 x, s32 y);
void update(char *record, s32 port, s32 input)
{
    f32 holder;
    f32 current_x;
    f32 current_y;
    f32 next_x;
    f32 next_y;
    f32 sampled;
    s32 step;
    holder = *((f32 *) (((char *) record) + 0x20));
    phase -= holder;
    step = input;
    current_x = *((f32 *) (((char *) record) + 0x14));
    next_x = (((((f32) step) * 0.25f) - current_x) * 0.25f) + current_x;
    sampled = sample(port, 2, 3);
    current_y = *((f32 *) (((char *) record) + 0x18));
    next_y = (((sampled * 0.25f) - current_y) * 0.25f) + current_y;
    report(next_x, next_y);
}
"""

DONOR = [(' ', op, op) for op in [
    'lwc1 $f26, 0x20($s0)', 'lui $at, %hi(phase)', 'lwc1 $f0, %lo(phase)($at)', 'sub.s $f0, $f0, $f26',
]]
FACTOR = [(' ', op, op) for op in ['lui $at, 0x3e80', 'ori $at, $at, 0', 'mtc1 $at, $f4']]
TAIL = [(' ', op, op) for op in ['mul.s $f0, $f0, $f4', 'add.s $f0, $f0, $f2']]
CAST = FACTOR + [
    ('r', 'mtc1 $s0, $f26', 'mtc1 $s0, $f0'),
    (' ', 'nop', 'nop'),
    ('r', 'cvt.s.w $f26, $f26', 'cvt.s.w $f0, $f0'),
    ('r', 'mul.s $f26, $f26, $f4', 'mul.s $f0, $f0, $f4'),
    (' ', 'lwc1 $f2, 0x14($s0)', 'lwc1 $f2, 0x14($s0)'),
    ('r', 'sub.s $f0, $f26, $f2', 'sub.s $f0, $f0, $f2'),
] + TAIL
SAMPLE = [(' ', op, op) for op in ['jal sample', 'addiu $a2, $zero, 3']] + FACTOR + [
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f26, $f0, $f4', 'mul.s $f0, $f0, $f4'),
    (' ', 'lwc1 $f2, 0x18($s0)', 'lwc1 $f2, 0x18($s0)'),
    ('r', 'sub.s $f0, $f26, $f2', 'sub.s $f0, $f0, $f2'),
] + TAIL
ROWS = DONOR + CAST + SAMPLE


def test_deep_permuter_stages_the_converted_and_sampled_products_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'update', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'holder = (f32) step;\n    holder *= 0.25f;' in candidate
    assert 'holder = (sampled * 0.25f);' in candidate
    assert FAMILY not in {label for label, _ in proposals(SOURCE, 'update', '', 'mips', 'ido-7.1', ROWS, '')}
