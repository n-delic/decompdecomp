from decompnet.app.diff_gated_repairs import proposals


def test_measured_float_order_reaches_the_app_repair_lane():
    source = """
static inline float adjust(float *field, float delta) { return *field + delta; }
void f(void *p) {
    float first;
    float second;
    float third;
    float x;
    float y;
    float z;
    x = 1.0f;
    y = 2.0f;
    z = 3.0f;
    first = adjust((float *) (((char *) p) + 0x4), x);
    second = adjust((float *) (((char *) p) + 0x8), y);
    third = adjust((float *) (((char *) p) + 0xC), z);
    *((float *) (((char *) p) + 0x4)) = first;
    *((float *) (((char *) p) + 0x8)) = second;
    *((float *) (((char *) p) + 0xC)) = third;
}
"""
    rows = [('i', 'swc1 $f4, 0x4($s2)', 'swc1 $f4, 0x8($s2)')]
    options = proposals(source, 'f', '', 'mips', 'gcc-2.7.2-kmc', rows, '')
    assert any(family == 'measured_staged_float_order'
               and candidate.index('third = adjust') < candidate.index('second = adjust')
               and candidate.index(' = third;') < candidate.index(' = first;')
               for family, candidate in options)


def test_global_float_schedule_reaches_the_kmc_repair_lane():
    source = """
extern float bias;
static inline float adjust(float *field, float delta) { return *field + delta; }
void f(void *p, float x) {
    union { unsigned int bits; float number; } reset;
    float first;
    float second;
    float third;
    float fourth;
    reset.bits = 0;
    first = adjust((float *) (((char *) p) + 0x4), bias);
    second = adjust((float *) (((char *) p) + 0x8), x);
    third = adjust((float *) (((char *) p) + 0xC), x);
    fourth = adjust((float *) (((char *) p) + 0x10), reset.number);
    *((float *) (((char *) p) + 0x8)) = second;
    *((float *) (((char *) p) + 0x4)) = first;
    *((float *) (((char *) p) + 0xC)) = third;
    *((float *) (((char *) p) + 0x10)) = fourth;
}
"""
    rows = [
        ('i', 'lwc1 $f6, 0x4($s2)', 'lwc1 $f2, %lo(bias)($at)'),
        ('i', 'lwc1 $f2, %lo(bias)($at)', 'lwc1 $f6, 0x4($s2)'),
        ('i', 'swc1 $f4, 0x4($s2)', 'swc1 $f4, 0x8($s2)'),
        ('i', 'swc1 $f8, 0x8($s2)', 'swc1 $f8, 0x4($s2)'),
        (' ', 'swc1 $f6, 0xc($s2)', 'swc1 $f6, 0xc($s2)'),
        (' ', 'swc1 $f4, 0x10($s2)', 'swc1 $f4, 0x10($s2)'),
    ]
    options = proposals(source, 'f', '', 'mips', 'gcc-2.7.2-kmc', rows, '')
    assert any(family == 'measured_staged_float_order'
               and 'first = (float)' in candidate and 'fourth = (float)' in candidate
               and candidate.index(' = third;') < candidate.index(' = second;') < candidate.index(' = fourth;')
               for family, candidate in options)
    options = proposals(source, 'f', '', 'mips', 'gcc-egcs', rows, '')
    assert not any(family == 'measured_staged_float_order' for family, _ in options)
