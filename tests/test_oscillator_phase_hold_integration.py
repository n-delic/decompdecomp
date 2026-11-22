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
