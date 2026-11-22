from decompnet.app.diff_gated_repairs import proposals

FAMILY = "projection_argument_before_store"

SOURCE = '''
extern float heading;
extern float first_bias, second_bias;
float project_x(float angle);
float project_y(float angle);
void project(void *output, void *state)
{
    *((float *) (((char *) output) + 4)) = (float) ((first_bias + (*((float *) (((char *) state) + 12)))) + (project_x(heading) * 0.25f));
    *((float *) (((char *) output) + 8)) = (float) ((second_bias + (*((float *) (((char *) state) + 16)))) + (project_y(heading) * 0.25f));
}
'''
HIGH = 'lui $at, %hi(heading)'
LOW = 'lwc1 $f12, %lo(heading)($at)'
STORE = 'swc1 $f2, 4($s2)'
SECOND = 'swc1 $f2, 8($s2)'
CALL = 'jal project_y'
ROWS = [
    (' ', HIGH, HIGH),
    (' ', 'jal project_x', 'jal project_x'),
    (' ', LOW, LOW),
    (' ', 'mul.s $f0, $f0, $f20', 'mul.s $f0, $f0, $f20'),
    (' ', 'add.s $f2, $f2, $f0', 'add.s $f2, $f2, $f0'),
    ('o', '', STORE),
    (' ', HIGH, HIGH),
    ('o', '', CALL),
    (' ', LOW, LOW),
    ('o', CALL, ''),
    ('o', STORE, ''),
    (' ', SECOND, SECOND),
]


def test_deep_permuter_captures_the_second_projection_argument_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'project', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert candidate.index('repair_projection_argument = heading;') < candidate.index(
        'output) + 4)) = repair_projection_value;')
    assert 'project_y(repair_projection_argument)' in candidate
    assert all(label != FAMILY for label, _ in proposals(SOURCE, 'project', '', 'mips',
                                                          'gcc-egcs', ROWS, ''))
