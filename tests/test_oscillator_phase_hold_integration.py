from decompnet.app.diff_gated_repairs import proposals

FAMILY = "oscillator_phase_hold"

SOURCE = """
extern f32 phase;
extern f32 axis_z;
void update(f32 *axis_x_out, f32 *record, s32 device)
{
    f32 axis_x;
    f32 carry_x;
    f32 carry_z;
    f32 old;
    f32 sense;
    f32 filtered;
    f32 drive;
    carry_x = 0.0f;
    carry_z = 0.0f;
    drive = wave(phase);
    axis_x = wave(phase + 1.0f);
    axis_z = drive * (wave(phase) * 0.1f);
    *axis_x_out = axis_x;
    phase += 0.01f;
    carry_x += axis_x;
    sense = poll(device, 1, 2);
    old = *record;
    filtered = (((sense * 4.0f) - old) * 0.1f) + old;
    *record = filtered;
    update_position(record);
    carry_z += axis_z;
    axis_x = *record;
    report(carry_x, carry_z, axis_x);
}
"""

OPS = [
    'swc1 $f24, %lo(axis_z)($at)',
    'jal poll',
    'addiu $a2, $zero, 2',
    'mtc1 $s2, $f16',
    'nop',
    'add.s $f16, $f16, $f26',
    'mfc1 $s2, $f16',
    'mtc1 $s3, $f16',
    'nop',
    'add.s $f16, $f16, $f24',
    'mfc1 $s3, $f16',
]
ROWS = [('r', value, '') for value in OPS]


def test_deep_permuter_routes_saved_axes_through_the_measured_mips_path():
    options = proposals(SOURCE, 'update', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    [revised] = [text for label, text in options if label == FAMILY]
    assert 'axis_z = repair_axis_z;' in revised
    assert revised.index('sense = poll(') < revised.index('carry_z += repair_axis_z;')
    assert 'filtered = ((axis_x - old) * 0.1f) + old;' in revised
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'ido-5.3'), ('mips', 'gcc-4.4.0-n64')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'update', '', arch, compiler, ROWS, ''))


PRODUCT_SOURCE = """\
extern f32 phase;
extern f32 axis_z;
f32 wave(f32 phase);
f32 poll(s32 slot, s32 x, s32 y);
void update(f32 *axis_x_out, s32 slot)
{
    f32 holder;
    f32 axis_x;
    f32 input;
    f32 carry_x;
    f32 carry_z;
    f32 product;
    f32 drive;
    drive = wave(phase);
    axis_x = wave(phase + 1.0f);
    carry_x = 0.0f;
    carry_z = 0.0f;
    product = drive * (wave(phase * 3.0f) * 0.1f);
    axis_z = product;
    *axis_x_out = axis_x;
    phase += 0.01f;
    input = poll(slot, 4, 5);
    carry_x += axis_x;
    carry_z += product;
    report(carry_x, carry_z, input);
    holder = poll(slot, 2, 3) * 0.5f;
    report(holder);
}
"""
PRODUCT_OPS = [
    'jal wave', 'nop', 'mul.s $f0, $f0, $f22', 'mul.s $f24, $f20, $f0',
    'swc1 $f24, %lo(axis_z)($at)', 'jal poll', 'addiu $a2, $zero, 5',
    'mtc1 $s2, $f16', 'nop', 'add.s $f16, $f16, $f24', 'mfc1 $s2, $f16',
]
PRODUCT_ROWS = [('r' if '$f24' in op else ' ', op, op.replace('$f24', '$f20')) for op in PRODUCT_OPS]
PRODUCT_ROWS += [(' ', op, op) for op in [
    'addiu $a1, $zero, 2', 'jal poll', 'addiu $a2, $zero, 3',
    'lui $at, 0x3f00', 'ori $at, $at, 0', 'mtc1 $at, $f6', 'nop',
    'mul.s $f24, $f0, $f6', *(['nop'] * 10),
]]


def test_deep_permuter_reuses_the_oscillation_product_holder_for_kmc():
    label = 'saved_oscillator_product_holder'
    [candidate] = [text for family, text in proposals(PRODUCT_SOURCE, 'update', '', 'mips',
                                                     'gcc-2.7.2-kmc', PRODUCT_ROWS, '') if family == label]
    assert 'holder = drive * (wave(phase * 3.0f) * 0.1f);' in candidate
    assert 'axis_z = holder;' in candidate
    assert 'carry_z += holder;' in candidate
    assert label not in {family for family, _ in proposals(PRODUCT_SOURCE, 'update', '', 'mips',
                                                           'ido-7.1', PRODUCT_ROWS, '')}
