from decompnet.app.diff_gated_repairs import proposals

FIELDS = (
    ('x', 'x_value', 0x4), ('y', 'y_value', 0x8), ('heading', 'heading_value', 0x10),
    ('frame.speed', 'speed_value', 0x14), ('z', 'z_value', 0xC), ('cleared.number', 'roll_value', 0x18),
)
SOURCE = """
extern float heading;
void f(void *p) {
    void *path;
    union { unsigned int bits; float number; } cleared;
    struct {
        int vector[3];
        unsigned int unused[2];
        volatile float speed;
    } frame;
    float y;
    float z;
    float x_value;
    float y_value;
    float z_value;
    float heading_value;
    float speed_value;
    float roll_value;
    path = *((void **) (((char *) p) + 0x2c));
    if (path) enter(path);
    {
        float x;
        cleared.bits = (unsigned int)p & ~(unsigned int)p;
        prepare((frame.speed = cleared.number, heading = cleared.number, p));
        x = 0.0f;
        y = cleared.number;
        z = 0.0f;
        if (first) {
            frame.speed = 0.0f;
            reset(p);
            *((float *) (((char *) p) + 0x14)) = frame.speed;
        }
        if (second) {
            cleared.bits = (unsigned int)p & ~(unsigned int)p;
            heading = cleared.number;
            frame.speed = cleared.number;
        }
        if (third) {
            frame.speed = 0.0f;
            reset(p);
            *((float *) (((char *) p) + 0x14)) = frame.speed;
        }
        refresh(p);
"""+''.join(
    f'        {local} = *(float *) (((char *) p) + 0x{offset:x});\n'
    f'        {local} = (float) ({local} + {delta});\n'
    for delta, local, offset in FIELDS
)+''.join(
    f'        *((float *) (((char *) p) + 0x{offset:x})) = {local};\n' for _, local, offset in FIELDS
)+"""
    }
}
"""
ROWS = [
    (' ', 'lw $t0, 0x2c($s2)', 'lw $t0, 0x2c($s2)'),
    (' ', 'sw $t0, 0x58($sp)', 'sw $t0, 0x58($sp)'),
    *[('r', 'lwc1 $f16, 0x50($sp)', 'lwc1 $f0, 0x50($sp)'),
      ('r', 'swc1 $f16, 0x14($s2)', 'swc1 $f0, 0x14($s2)')] * 2,
    (' ', 'sw $s7, %lo(heading)($at)', 'sw $s7, %lo(heading)($at)'),
    (' ', 'sw $s7, 0x50($sp)', 'sw $s7, 0x50($sp)'),
]


def test_native_float_spill_reaches_the_kmc_repair_lane():
    candidates = proposals(SOURCE, 'f', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    repairs = [candidate for family, candidate in candidates if family == 'native_float_spill']
    assert len(repairs) == 2
    assert all('float speed_delta;' in candidate
               and 'cleared.bits = (cleared.bits >> 16) << 16;' in candidate
               for candidate in repairs)
    candidates = proposals(SOURCE, 'f', '', 'mips', 'gcc-egcs', ROWS, '')
    assert not any(family == 'native_float_spill' for family, _ in candidates)


SETUP_ROWS = [
    (' ', 'move $s7, $zero', 'move $s7, $zero'),
    ('r', 'move $a0, $s2', 'move $s5, $zero'),
    (' ', 'sw $s7, 0x50($sp)', 'sw $s7, 0x50($sp)'),
    (' ', 'lui $at, %hi(heading)', 'lui $at, %hi(heading)'),
    ('r', 'sw $s7, %lo(heading)($at)', 'sw $s5, %lo(heading)($at)'),
    (' ', 'jal prepare', 'jal prepare'),
    ('r', 'move $s6, $s7', 'move $a0, $s2'),
    (' ', 'lui $v0, %hi(first)', 'lui $v0, %hi(first)'),
    (' ', 'lbu $v0, %lo(first)($v0)', 'lbu $v0, %lo(first)($v0)'),
    ('r', 'move $fp, $s7', 'move $s6, $s5'),
    (' ', 'lwc1 $f16, 0x50($sp)', 'lwc1 $f16, 0x50($sp)'),
    (' ', 'lwc1 $f16, 0x50($sp)', 'lwc1 $f16, 0x50($sp)'),
]


def test_native_setup_copy_reaches_the_kmc_repair_lane():
    native = [code for family, code in proposals(SOURCE, 'f', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
              if family == 'native_float_spill'][1]
    repairs = [code for family, code in proposals(native, 'f', '', 'mips', 'gcc-2.7.2-kmc', SETUP_ROWS, '')
               if family == 'native_float_setup_copy']
    assert len(repairs) == 1
    assert repairs[0].index('prepare(p);') < repairs[0].index('y = cleared.number;')
    candidates = proposals(native, 'f', '', 'mips', 'gcc-egcs', SETUP_ROWS, '')
    assert not any(family == 'native_float_setup_copy' for family, _ in candidates)
