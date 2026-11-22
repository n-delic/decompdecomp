from decompnet.app.diff_gated_repairs import proposals

SOURCE = """extern void *commands;
void f(void) {
    *((s32 *) (((char *) commands) + 0)) = 0xDB080000;
    scale = tint;
    *((s32 *) (((char *) commands) + 4)) = scale;
    red = r;
    commands = commands + 8;
    blue = b;
    green = g;
}"""
ROWS = [
    ('r', 'sw $v0, ($a2)', 'sw $v0, ($v1)'),
    ('>', '', 'lw $v0, %lo(commands)($v0)'),
    ('r', 'sw $a0, 4($a2)', 'sw $a0, 4($v0)'),
    ('r', 'lbu $v0, %lo(r)($v0)', 'lbu $v1, %lo(r)($v1)'),
    ('i', 'lbu $v1, %lo(g)($v1)', 'lbu $a0, %lo(b)($a0)'),
    ('i', 'lbu $a0, %lo(b)($a0)', 'lbu $a1, %lo(g)($a1)'),
    ('r', 'addiu $a2, $a2, 8', 'addiu $v0, $v0, 8'),
    ('r', 'sw $a2, %lo(commands)($at)', 'sw $v0, %lo(commands)($at)'),
    ('r', 'sb $v0, %lo(red)($at)', 'sb $v1, %lo(red)($at)'),
    ('i', 'sb $v1, %lo(green)($at)', 'sb $a0, %lo(blue)($at)'),
    ('i', 'sb $a0, %lo(blue)($at)', 'sb $a1, %lo(green)($at)'),
]


def test_deep_permuter_offers_measured_command_cursor():
    texts = [text for family, text in proposals(SOURCE, 'f', '', 'mips',
                                              'gcc-2.7.2-kmc', ROWS, '')
             if family == 'measured_captured_command_cursor']
    text, = texts
    assert text.count('(char *) repair_command_cursor') == 2
    assert text.index('green = g;') < text.index('blue = b;')
