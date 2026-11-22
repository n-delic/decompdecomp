from decompnet.app.diff_gated_repairs import proposals

FAMILY = "balanced_sign_field_clamp"

SOURCE = """\
typedef float f32;
extern f32 heading;
void adjust(void *state, void *record)
{
    f32 axis;
    f32 value;
    f32 bound;
    void *sample;
    axis = 1.0f;
    sample = *((void **) (((char *) state) + 0x20));
    { f32 saved = axis;
    axis = *((f32 *) (((char *) record) + 0x14));
    heading -= axis;
    axis = saved; }
    if ((*((f32 *) (((char *) sample) + 0x18))) <= 0.0f) {
        hint();
    }
    value = *((f32 *) (((char *) record) + 0x24));
    bound = -0.25f;
    if ((value < (-0.25f)) || ((bound = 0.25f, value > 0.25f))) {
        *((f32 *) (((char *) record) + 0x24)) = bound;
    }
}
"""
AXIS = 'lwc1 $f26, 0x14($s1)'
ADDR = 'lui $at, %hi(heading)'
VALUE = 'lwc1 $f0, %lo(heading)($at)'
WRITE = 'swc1 $f0, %lo(heading)($at)'
SUB = 'sub.s $f0, $f0, $f26'
POINTER = 'lw $v0, 0x20($s2)'
SIGN_ROWS = [
    (' ', AXIS, AXIS),
    ('o', '', ADDR),
    ('>', '', VALUE),
    ('o', '', SUB),
    (' ', POINTER, POINTER),
    (' ', ADDR, ADDR),
    ('o', '', WRITE),
    ('i', VALUE, 'lwc1 $f2, 0x18($v0)'),
    ('<', 'lwc1 $f4, 0x18($v0)', ''),
    ('o', SUB, ''),
    ('r', 'mtc1 $zero, $f2', 'mtc1 $zero, $f0'),
    (' ', 'nop', 'nop'),
    ('r', 'c.le.s $f4, $f2', 'c.le.s $f2, $f0'),
    ('o', ADDR, 'nop'),
    ('o', WRITE, ''),
    ('i', 'bc1f .Lother', 'bc1f .Llater'),
    (' ', 'nop', 'nop'),
]
CLAMP_LOAD = 'lwc1 $f0, 0x24($s1)'
HI, LO, MOVE = 'lui $at, 0xbe80', 'ori $at, $at, 0', 'mtc1 $at, $f2'
CLAMP_ROWS = [
    ('o', CLAMP_LOAD, ''),
    (' ', HI, HI), (' ', LO, LO), (' ', MOVE, MOVE),
    ('o', 'nop', CLAMP_LOAD),
    (' ', 'c.lt.s $f0, $f2', 'c.lt.s $f0, $f2'),
    (' ', 'nop', 'nop'), (' ', 'nop', 'nop'),
    (' ', 'bc1tl .Lfinish', 'bc1tl .Lfinish'),
    (' ', 'swc1 $f2, 0x24($s1)', 'swc1 $f2, 0x24($s1)'),
]
ROWS = SIGN_ROWS + CLAMP_ROWS + [(' ', 'nop', 'nop')]


def test_deep_permuter_pairs_the_typed_sign_field_and_clamp_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'adjust', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert '((RepairSignRecord *) sample)->repair_sign_sample' in candidate
    assert 'if (value < (-0.25f))' in candidate
    assert 'else if (value > 0.25f)' in candidate
    assert FAMILY not in {label for label, _ in proposals(SOURCE, 'adjust', '', 'mips', 'ido-7.1', ROWS, '')}
