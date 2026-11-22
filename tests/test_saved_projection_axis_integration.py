from decompnet.app.diff_gated_repairs import proposals

FAMILY = "saved_projection_axis_reuse"

SOURCE = '''
extern f32 weight;
f32 sine(f32); f32 cosine(f32);
void adjust(char *record, f32 angle)
{
    f32 carry;
    f32 axis;
    f32 x;
    f32 z;
    carry = *((f32 *) (((char *) record) + 0x48));
    weight -= carry;
    x = 0.0f;
    z = 0.0f;
    axis = *((f32 *) (((char *) record) + 0x50));
    x += (axis * sine(angle));
    z += (axis * cosine(angle));
    report(x, z);
}
'''
ROWS = [
    (' ', 'lwc1 $f26, 0x48($s1)', 'lwc1 $f26, 0x48($s1)'),
    (' ', 'lui $at, %hi(weight)', 'lui $at, %hi(weight)'),
    (' ', 'lwc1 $f0, %lo(weight)($at)', 'lwc1 $f0, %lo(weight)($at)'),
    (' ', 'sub.s $f0, $f0, $f26', 'sub.s $f0, $f0, $f26'),
    ('r', 'lwc1 $f26, 0x50($s1)', 'lwc1 $f22, 0x50($s1)'),
    (' ', 'jal sine', 'jal sine'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f0, $f26, $f0', 'mul.s $f0, $f22, $f0'),
    (' ', 'jal cosine', 'jal cosine'),
    (' ', 'nop', 'nop'),
    ('r', 'mul.s $f0, $f26, $f0', 'mul.s $f0, $f22, $f0'),
]


def test_deep_permuter_reuses_the_witnessed_saved_projection_axis_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'x += (carry * sine(angle));' in candidate and 'z += (carry * cosine(angle));' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))


def test_deep_permuter_reuses_a_guarded_load_for_the_same_projection_field():
    source = SOURCE.replace(
        '    carry = *((f32 *) (((char *) record) + 0x48));\n    weight -= carry;',
        '    carry = *((f32 *) (((char *) record) + 0x50));\n'
        '    ok = 1;\n'
        '    if (!(carry <= 0.025f)) {\n'
        '        *((f32 *) (((char *) record) + 0x38)) = carry;\n'
        '    }',
    ).replace('    f32 carry;', '    int ok;\n    f32 carry;')
    source = source.replace('(axis * sine(angle))', 'axis * sine(angle)')
    rows = [
        (' ', 'lwc1 $f26, 0x50($s1)', 'lwc1 $f26, 0x50($s1)'),
        (' ', 'c.le.s $f26, $f2', 'c.le.s $f26, $f2'),
        (' ', 'nop', 'nop'),
        (' ', 'bc1f .Lstore', 'bc1f .Lstore'),
        (' ', 'swc1 $f26, 0x38($s1)', 'swc1 $f26, 0x38($s1)'),
    ] + ROWS[4:]
    [candidate] = [text for label, text in proposals(source, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', rows, '') if label == FAMILY]
    assert 'x += carry * sine(angle);' in candidate
    assert 'z += (carry * cosine(angle));' in candidate
