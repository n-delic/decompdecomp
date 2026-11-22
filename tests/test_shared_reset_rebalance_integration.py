from decompnet.app.diff_gated_repairs import proposals

SOURCE = """
extern float heading;
int prepare(void *);
int state(void *, int);

void f(void *p) {
    union { unsigned int bits; float number; } cleared;
    struct {
        volatile float speed;
    } frame;
    float y;
    float z;
    cleared.bits = (unsigned int)p & ~(unsigned int)p;
    frame.speed = cleared.number;
    heading = cleared.number;
    {
        float x = 0.0f;
        prepare(p);
        z = cleared.number;
        y = 0.0f;
        state(p, (int) cleared.bits);
        state(p, (int) cleared.bits);
        state(p, (int) cleared.bits);
        state(p, (int) cleared.bits);
        cleared.bits = 0;
        draw(p, cleared.number);
        heading = 0.0f;
        frame.speed = 0.0f;
        draw(p, x + y + z);
    }
}
"""
ROWS = [
    (' ', 'jal prepare', 'jal prepare'),
    ('r', 'move $s6, $s7', 'move $s5, $zero'),
    *[(' ', 'jal state', 'jal state'), ('r', 'move $a1, $zero', 'move $a1, $s7')] * 4,
    ('r', 'sw $s7, %lo(heading)($at)', 'sw $zero, %lo(heading)($at)'),
    ('r', 'sw $s7, 0x5c($sp)', 'sw $zero, 0x5c($sp)'),
]


def test_shared_reset_rebalance_reaches_the_kmc_repair_lane():
    choices = proposals(SOURCE, 'f', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    assert any(family == 'shared_reset_rebalance'
               and candidate.count('state(p, 0);') == 2
               and 'y = cleared.number;' in candidate
               for family, candidate in choices)
    choices = proposals(SOURCE, 'f', '', 'mips', 'gcc-egcs', ROWS, '')
    assert not any(family == 'shared_reset_rebalance' for family, _ in choices)
