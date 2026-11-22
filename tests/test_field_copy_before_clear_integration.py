from decompnet.app.diff_gated_repairs import proposals

FAMILY = "field_copy_before_clear"

SOURCE = '''
void prepare(char *dest, char *input)
{
    *((float *) (((char *) dest) + 12)) = 0.0f;
    *((float *) (((char *) dest) + 20)) = 0.0f;
    *((float *) (((char *) dest) + 16)) = (float) (*((float *) (((char *) input) + 4)));
    send(dest, 7);
}
'''
ROWS = [
    (' ', 'sw $zero, 12($s2)', 'sw $zero, 12($s2)'),
    ('o', '', 'sw $zero, 20($s2)'),
    (' ', 'lwc1 $f0, 4($a1)', 'lwc1 $f0, 4($a1)'),
    (' ', 'move $a0, $s2', 'move $a0, $s2'),
    (' ', 'addiu $a1, $zero, 7', 'addiu $a1, $zero, 7'),
    ('o', 'sw $zero, 20($s2)', ''),
    (' ', 'jal send', 'jal send'),
    (' ', 'swc1 $f0, 16($s2)', 'swc1 $f0, 16($s2)'),
]


def test_deep_permuter_captures_the_copied_field_before_clear_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'prepare', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert candidate.index('float repair_field_snapshot =') < candidate.index('dest) + 20)) = 0.0f;')
    assert '(float) repair_field_snapshot;' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'prepare', '', arch, compiler, ROWS, ''))
