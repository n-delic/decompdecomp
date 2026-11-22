from decompnet.app.diff_gated_repairs import proposals

FAMILY = "float_delta_load_order"

SOURCE = """
void adjust(char *record)
{
    f32 old;
    f32 delta;
    old = *((f32 *) (((char *) record) + 8));
    delta = ((*((f32 *) (((char *) record) + 12))) - old) * 0.25f;
    use(delta);
}
"""

ROWS = [
    ('i', 'lwc1 $f0, 12($s1)', 'lwc1 $f2, 8($s1)'),
    ('i', 'lwc1 $f2, 8($s1)', 'lwc1 $f0, 12($s1)'),
    (' ', 'sub.s $f0, $f0, $f2', 'sub.s $f0, $f0, $f2'),
]


def test_deep_permuter_routes_the_measured_float_difference_load_order():
    options = proposals(SOURCE, 'adjust', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    [revised] = [text for label, text in options if label == FAMILY]
    assert revised.index('repair_delta_input =') < revised.index('old =')
    assert 'delta = ((repair_delta_input) - old) * 0.25f;' in revised
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'ido-5.3'), ('mips', 'gcc-4.4.0-n64')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
