from decompnet.app.diff_gated_repairs import proposals

FAMILY = 'split_bound_register_roles'

SOURCE = """\
void bound(char *record)
{
    float saved;
    float current;
    saved = 0.25f;
    current = *((f32 *) (((char *) record) + 0x24));
    if ((current < 0.25f) || ((saved = 0.75f, current > 0.75f))) {
        *((f32 *) (((char *) record) + 0x24)) = saved;
    }
}
"""


def scratch_range(value, limit):
    return [
        'lui $at, 0x3e80', 'ori $at, $at, 0', f'mtc1 $at, {limit}',
        f'lwc1 {value}, 0x24($s1)', f'c.lt.s {value}, {limit}', 'nop', 'nop',
        'bc1tl .Llow', f'swc1 {limit}, 0x24($s1)', 'lui $at, 0x3f40', 'ori $at, $at, 0',
        f'mtc1 $at, {limit}', 'nop', f'c.lt.s {limit}, {value}', 'nop', 'nop',
        'bc1f .Lskip', 'move $a0, $s1', f'swc1 {limit}, 0x24($s1)',
    ]


ROWS = [(' ' if left == right else 'r', left, right)
        for left, right in zip(scratch_range('$f0', '$f2'), scratch_range('$f2', '$f0'))]


def test_deep_permuter_splits_a_register_swapped_bound_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'bound', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert 'if (current < 0.25f) {' in candidate
    assert 'else if (current > 0.75f) {' in candidate
    assert FAMILY not in {label for label, _ in proposals(SOURCE, 'bound', '', 'mips', 'ido-7.1', ROWS, '')}
