from decompnet.app.diff_gated_repairs import proposals

FAMILY = "saved_float_range_probe"

SOURCE = '''
void adjust(char *record, f32 input)
{
    f32 saved, probe;
    int upper, lower;
    saved = *((f32 *) (((char *) record) + 0x58));
    rebuild(record);
    *((f32 *) (((char *) record) + 0x58)) = saved;
    probe = sample(input);
    upper = 1;
    if (!(probe <= 0.125f)) {
        upper = 0;
    }
    lower = 1;
    if (!(probe >= (-0.125f))) {
        lower = 0;
    }
    report(upper, lower);
}
'''
ROWS = [
    (' ', 'lwc1 $f20, 0x58($s1)', 'lwc1 $f20, 0x58($s1)'),
    (' ', 'jal rebuild', 'jal rebuild'),
    (' ', 'nop', 'nop'),
    (' ', 'swc1 $f20, 0x58($s1)', 'swc1 $f20, 0x58($s1)'),
    (' ', 'jal sample', 'jal sample'),
    (' ', 'nop', 'nop'),
    ('r', 'mov.s $f20, $f0', 'mov.s $f4, $f0'),
    ('r', 'c.le.s $f20, $f2', 'c.le.s $f4, $f2'),
    (' ', 'nop', 'nop'),
    ('r', 'c.le.s $f0, $f20', 'c.le.s $f0, $f4'),
]


def test_deep_permuter_reuses_saved_probe_only_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'saved = sample(input);' in candidate and 'if (!(saved >=' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
