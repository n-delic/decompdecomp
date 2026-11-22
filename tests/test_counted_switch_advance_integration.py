from decompnet.app.diff_gated_repairs import proposals

FAMILY = "counted_switch_advance"

SOURCE = """
void run(int state, int enabled, void *record)
{
    u8 previous;
    switch (state) {
    case 1:
        if (enabled)
        {
            previous = *((u8 *) (((char *) record) + 12));
            *((u8 *) (((char *) record) + 12)) = (u8) (previous + 1);
            if (((s8) previous) >= 3)
            {
                goto reset_state;
            }
            advance(record);
        }
        else
        {
        reset_state:
            *((s8 *) (((char *) record) + 7)) = 2;
        }
        break;
    case 2:
        stop(record);
        break;
    }
}
"""

SHARED = [
    'lbu $v0, 12($s1)',
    'addiu $v1, $v0, 1',
    'sll $v0, $v0, 0x18',
    'sra $v0, $v0, 0x18',
    'slti $v0, $v0, 3',
]
ROWS = [
    *[(' ', op, op) for op in SHARED],
    ('|', 'bnez $v0, .Ladvance', 'beqz $v0, .Lreset'),
    ('<', 'sb $v1, 12($s1)', ''),
    ('o', 'addiu $v0, $zero, 2', 'nop'),
    ('o', 'j .Lexit', 'j .Lexit2'),
    ('i', 'sb $v0, 7($s1)', 'sb $v1, 12($s1)'),
]


def test_deep_permuter_routes_the_measured_advance_branch():
    options = proposals(SOURCE, 'run', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    [revised] = [text for label, text in options if label == FAMILY]
    assert 'if (((s8) previous) < 3) goto repair_advance_state;' in revised
    assert revised.index('+ 7)) = 2;') < revised.index('repair_advance_state:')
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'ido-5.3'), ('mips', 'gcc-4.4.0-n64')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'run', '', arch, compiler, ROWS, ''))
