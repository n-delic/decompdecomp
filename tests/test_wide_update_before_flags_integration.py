from decompnet.app.diff_gated_repairs import proposals

FAMILY = "wide_update_before_flags"

SOURCE = '''
extern double offset;
void adjust(char *state)
{
    *((unsigned char *) (((char *) state) + 12)) = 0U;
    *((signed char *) (((char *) state) + 8)) = 1;
    *((float *) (((char *) state) + 4)) = (float) ( ((double) (*((float *) (((char *) state) + 4)))) + offset );
}
'''
LOAD = 'lwc1 $f0, 4($s1)'
FIRST = 'sb $zero, 12($s1)'
SECOND = 'sb $v0, 8($s1)'
WIDE = ['lui $at, %hi(offset)', 'ldc1 $f2, %lo(offset)($at)', 'cvt.d.s $f0, $f0', 'add.d $f0, $f0, $f2']
WRITE = ['cvt.s.d $f0, $f0', 'swc1 $f0, 4($s1)']
SET = 'addiu $v0, $zero, 1'
ROWS = [
    (' ', SET, SET),
    (' ', LOAD, LOAD),
    ('o', '', FIRST),
    ('o', '', SECOND),
    *((' ', line, line) for line in WIDE),
    ('o', FIRST, ''),
    ('o', SECOND, ''),
    *((' ', line, line) for line in WRITE),
]


def test_deep_permuter_stages_the_witnessed_wide_update_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert candidate.index('double repair_wide_update =') < candidate.index('state) + 12)) = 0U;')
    assert '(float) repair_wide_update;' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
