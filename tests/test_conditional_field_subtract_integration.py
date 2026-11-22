from decompnet.app.diff_gated_repairs import proposals

FAMILY = "conditional_saved_field_subtract"

SOURCE = '''
extern f32 balance;
extern u8 active;
void adjust(char *record)
{
    f32 saved;
    f32 value;
    saved = *((f32 *) (((char *) record) + 0x48));
    balance -= saved;
    value = *((f32 *) (((char *) record) + 0x48));
    if (active != 0) {
        value *= 2.0f;
    }
    balance -= value;
}
'''
ROWS = [
    (' ', 'lwc1 $f26, 0x48($s1)', 'lwc1 $f26, 0x48($s1)'),
    (' ', 'lui $at, %hi(balance)', 'lui $at, %hi(balance)'),
    (' ', 'lwc1 $f0, %lo(balance)($at)', 'lwc1 $f0, %lo(balance)($at)'),
    (' ', 'sub.s $f0, $f0, $f26', 'sub.s $f0, $f0, $f26'),
    (' ', 'lbu $v0, %lo(active)($v0)', 'lbu $v0, %lo(active)($v0)'),
    ('r', 'lwc1 $f26, 0x48($s1)', 'lwc1 $f2, 0x48($s1)'),
    (' ', 'bnel $v0, $zero, 0x28', 'bnel $v0, $zero, 0x28'),
    ('r', 'add.s $f26, $f26, $f26', 'add.s $f2, $f2, $f2'),
    (' ', 'lui $at, %hi(balance)', 'lui $at, %hi(balance)'),
    (' ', 'lwc1 $f0, %lo(balance)($at)', 'lwc1 $f0, %lo(balance)($at)'),
    ('r', 'sub.s $f0, $f0, $f26', 'sub.s $f0, $f0, $f2'),
]


def test_deep_permuter_reuses_the_witnessed_field_local_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'saved *= 2.0f;' in candidate and candidate.count('balance -= saved;') == 2
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
