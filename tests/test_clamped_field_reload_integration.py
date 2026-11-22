from decompnet.app.diff_gated_repairs import proposals

FAMILY = "saved_clamped_field_reload"

SOURCE = '''
extern f32 balance;
void adjust(char *record, f32 input)
{
    f32 axis, old, result;
    axis = input;
    old = *((f32 *) (((char *) record) + 0x48));
    result = ((axis - old) * 0.8f) + old;
    *((f32 *) (((char *) record) + 0x48)) = result;
    if (result < (-82.8f)) {
        *((f32 *) (((char *) record) + 0x48)) = -82.8f;
    } else if (result > 82.8f) {
        *((f32 *) (((char *) record) + 0x48)) = 82.8f;
    }
    balance -= *((f32 *) (((char *) record) + 0x48));
    observe(axis);
}
'''
ROWS = [
    (' ', 'bc1tl .Lfinish', 'bc1tl .Lfinish'),
    (' ', 'swc1 $f6, 0x48($s1)', 'swc1 $f6, 0x48($s1)'),
    ('<', 'lwc1 $f26, 0x48($s1)', ''),
    (' ', 'lui $at, %hi(balance)', 'lui $at, %hi(balance)'),
    (' ', 'lwc1 $f0, %lo(balance)($at)', 'lwc1 $f0, %lo(balance)($at)'),
    ('>', '', 'lwc1 $f2, 0x48($s1)'),
    ('r', 'sub.s $f0, $f0, $f26', 'sub.s $f0, $f0, $f2'),
    (' ', 'swc1 $f0, %lo(balance)($at)', 'swc1 $f0, %lo(balance)($at)'),
]


def test_deep_permuter_reloads_clamped_field_into_saved_axis_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'balance -= axis;\n    axis = repair_saved_axis;' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
