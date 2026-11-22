from decompnet.app.diff_gated_repairs import proposals

FAMILY = "shared_projection_lifetime"

SOURCE = '''
f32 adjust(char *record, f32 angle, int choose)
{
    f32 seed;
    f32 output;
    f32 horizontal;
    if (choose) goto zero_entry;
    seed = 0.2f;
    shared:
    *((f32 *) (((char *) record) + 0x4C)) = seed;
    horizontal = seed * sine(angle);
    output = seed * cosine(angle);
    emit(horizontal);
    return output;
    zero_entry:
    seed = 0.0f;
    goto shared;
}
'''
ROWS = [
    (' ', 'lui $at, 0x3e4c', 'lui $at, 0x3e4c'),
    (' ', 'ori $at, $at, 0xcccd', 'ori $at, $at, 0xcccd'),
    ('r', 'mtc1 $at, $f26', 'mtc1 $at, $f20'),
    (' ', 'nop', 'nop'),
    ('r', 'swc1 $f26, 0x4c($s1)', 'swc1 $f20, 0x4c($s1)'),
    (' ', 'jal sine', 'jal sine'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f0, $f26, $f0', 'mul.s $f0, $f20, $f0'),
    (' ', 'jal cosine', 'jal cosine'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f26, $f26, $f0', 'mul.s $f26, $f20, $f0'),
    ('r', 'mtc1 $zero, $f12', 'mtc1 $zero, $f4'),
    ('i', 'bnez $v0, 0x10', 'bnez $v0, 0x14'),
    ('r', 'mov.s $f26, $f12', 'mov.s $f20, $f4'),
]


def test_deep_permuter_shares_the_projection_input_with_the_saved_result_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'output = output * cosine(angle);' in candidate
    assert 'output = 0.0f;' in candidate and 'output = 0.2f;' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
