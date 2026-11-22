from decompnet.app.diff_gated_repairs import proposals

FAMILY = "balanced_signed_selector_increment"

SOURCE = '''
static inline s8 byte_at(char *p, int offset)
{
    return *((s8 *) (p + offset));
}
void adjust(char *record)
{
    unsigned short mode;
    s16 snapshot;
    s8 count;
    mode = byte_at((char *) record, 0x17e);
    switch (mode) {
    case 0: case 1: break;
    case 2: case 3: act(); break;
    }
    count = byte_at((char *) record, 0x307);
    *((u8 *) (((char *) record) + 0x197)) = 0xfU;
    if (count < 100) {
        *((s8 *) (((char *) record) + 0x307)) = (s8) (count + 1);
    }
}
'''
ROWS = [
    ('r', 'lb $v1, 0x17e($s1)', 'lb $v0, 0x17e($s1)'),
    ('>', '', 'andi $v1, $v0, 0xffff'),
    ('|', 'bltz $v1, 0x210 [bytes 04600043]', 'bltz $v1, 0x210 [bytes 04600042]'),
    (' ', 'slti $v0, $v1, 2', 'slti $v0, $v1, 2'),
    ('|', 'bnez $v0, 0x210 [bytes 14400041]', 'bnez $v0, 0x210 [bytes 14400040]'),
    (' ', 'slti $v0, $v1, 4', 'slti $v0, $v1, 4'),
    ('|', 'beqz $v0, 0x210 [bytes 1040003f]', 'beqz $v0, 0x210 [bytes 1040003e]'),
    (' ', 'nop', 'nop'),
    (' ', 'lb $v1, 0x307($s1)', 'lb $v1, 0x307($s1)'),
    (' ', 'sb $v0, 0x197($s1)', 'sb $v0, 0x197($s1)'),
    ('<', 'move $v0, $v1', ''),
    ('r', 'slti $v1, $v1, 0x64', 'slti $v0, $v1, 0x64'),
    ('r', 'beqz $v1, 0x320', 'beqz $v0, 0x320'),
    ('r', 'addiu $v0, $v0, 1', 'addiu $v0, $v1, 1'),
    (' ', 'sb $v0, 0x307($s1)', 'sb $v0, 0x307($s1)'),
]


def test_deep_permuter_routes_the_balanced_selector_and_increment_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 's8 mode;' in candidate and 'snapshot = count;' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
