from decompnet.app.diff_gated_repairs import proposals

SOURCE = """
int reset(void *, int);
void use(void *);
void f(void *p) {
    void *saved;
    union { unsigned int bits; float number; } cleared;
    struct {
        float local;
        unsigned int padding;
        void *room;
    } frame;
    saved = *((void **) (((char *) p) + 0x18));
    frame.room = saved;
    use(frame.room);
    use(frame.room);
    cleared.bits = (unsigned int)p & ~(unsigned int)p;
    frame.local = 0.0f;
    {
        reset(p, 0);
        use(frame.room);
        reset(p, 0);
        use(frame.room);
        reset(p, 0);
        cleared.bits = 0;
        reset(p, 0);
    }
}
"""
ROWS = [
    ('r', 'lw $t0, 0x18($s2)', 'lw $v0, 0x18($s2)'),
    (' ', 'move $s3, $s2', 'move $s3, $s2'),
    ('r', 'sw $t0, 0x50($sp)', 'sw $v0, 0x50($sp)'),
    *[(' ', 'jal reset', 'jal reset'), (' ', 'move $a1, $zero', 'move $a1, $zero')] * 4,
    *[('r', 'lw $t0, 0x50($sp)', 'lw $v0, 0x50($sp)'),
      ('r', 'lw $v0, 0x10($t0)', 'lw $v0, 0x10($v0)')] * 2,
]



def test_natural_pointer_spill_reaches_only_the_kmc_repair_lane():
    candidates = proposals(SOURCE, 'f', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    assert any(family == 'natural_pointer_spill'
               and code.count('reset(p, (int) cleared.bits);') == 4
               and code.count('use(saved);') == 4
               for family, code in candidates)
    candidates = proposals(SOURCE, 'f', '', 'mips', 'gcc-egcs', ROWS, '')
    assert not any(family == 'natural_pointer_spill' for family, _ in candidates)
