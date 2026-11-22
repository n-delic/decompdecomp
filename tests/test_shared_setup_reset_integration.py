from decompnet.app.diff_gated_repairs import proposals


def test_shared_setup_zero_reaches_the_kmc_repair_lane():
    source = """
extern float heading;
void f(void *p) {
    union { unsigned int bits; float number; } reset;
    struct { volatile float speed; } frame;
    float depth;
    float vertical;
    reset.bits = (unsigned int)p & ~(unsigned int)p;
    frame.speed = reset.number;
    heading = 0.0f;
    {
        float horizontal = 0.0f;
        depth = reset.number;
        prepare(p);
        vertical = 0.0f;
        if (active) record(horizontal, depth, vertical);
    }
}
"""
    rows = [
        (' ', 'move $s7, $zero', 'move $s7, $zero'),
        (' ', 'sw $s7, 0x3c($sp)', 'sw $s7, 0x3c($sp)'),
        ('r', 'sw $s7, %lo(heading)($at)', 'sw $zero, %lo(heading)($at)'),
        (' ', 'jal prepare', 'jal prepare'),
        ('r', 'move $s6, $s7', 'move $s5, $zero'),
    ]
    options = proposals(source, 'f', '', 'mips', 'gcc-2.7.2-kmc', rows, '')
    assert any(family == 'shared_setup_reset'
               and candidate.index('heading = reset.number;') < candidate.index('prepare(p);')
               and candidate.index('prepare(p);') < candidate.index('depth = reset.number;')
               for family, candidate in options)
    options = proposals(source, 'f', '', 'mips', 'gcc-egcs', rows, '')
    assert not any(family == 'shared_setup_reset' for family, _ in options)
