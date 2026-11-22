from decompnet.app.diff_gated_repairs import proposals

FAMILY = "smoothing_call_before_read"

HEADER = 'f32 sample(int, int, int);\n'
SOURCE = '''
void adjust(char *record, int input)
{
    f32 old, result;
    old = *((f32 *) (((char *) record) + 0x50));
    result = (((sample(input, 2, 3) * 0.2f) - old) * 0.3f) + old;
    *((f32 *) (((char *) record) + 0x50)) = result;
    if (result < (-0.2f)) {
        *((f32 *) (((char *) record) + 0x50)) = -0.2f;
    }
}
'''
ROWS = [
    ('>', '', 'lwc1 $f20, 0x50($s1)'),
    (' ', 'jal sample', 'jal sample'),
    (' ', 'addiu $a2, $zero, 3', 'addiu $a2, $zero, 3'),
    (' ', 'lui $at, 0x3e4c', 'lui $at, 0x3e4c'),
    (' ', 'ori $at, $at, 0xcccd', 'ori $at, $at, 0xcccd'),
    ('r', 'mtc1 $at, $f6', 'mtc1 $at, $f4'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f24, $f0, $f6', 'mul.s $f0, $f0, $f4'),
    ('o', 'lwc1 $f0, 0x50($s1)', ''),
    ('r', 'sub.s $f2, $f24, $f0', 'sub.s $f0, $f0, $f20'),
]


def test_deep_permuter_routes_the_witnessed_sample_order_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', HEADER, 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'result = sample(input, 2, 3) * 0.2f;\n    old =' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', HEADER, arch, compiler, ROWS, ''))
