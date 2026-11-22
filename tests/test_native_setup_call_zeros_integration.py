from decompnet.app.diff_gated_repairs import proposals

SOURCE = """
void reset(void *, int);
void f(void *actor) {
    union { unsigned int bits; float number; } cleared;
    float y;
    if (actor) {
    phase:
        cleared.bits = (unsigned int)actor & ~(unsigned int)actor;
        {
            prepare(actor);
            cleared.bits = (cleared.bits >> 16) << 16;
            y = cleared.number;
            reset(actor, (int) cleared.bits);
            clear(y);
            reset(actor, (int) cleared.bits);
        }
    }
}
"""
ROWS = [
    (' ', 'move $s7, $zero', 'move $s7, $zero'),
    (' ', 'jal reset', 'jal reset'),
    ('r', 'move $a1, $zero', 'move $a1, $s7'),
    (' ', 'jal reset', 'jal reset'),
    ('r', 'move $a1, $zero', 'move $a1, $s7'),
]


def test_native_zero_call_arguments_reach_the_kmc_lane():
    trials = proposals(SOURCE, 'f', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    [code] = [code for family, code in trials if family == 'native_setup_call_zeros']
    assert code.count('reset(actor, 0);') == 2
    assert 'y = cleared.number;' in code
    trials = proposals(SOURCE, 'f', '', 'mips', 'gcc-egcs', ROWS, '')
    assert not any(family == 'native_setup_call_zeros' for family, _ in trials)
