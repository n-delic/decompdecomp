from decompnet.app import draft_repairs

SOURCE = """\
void f(char *range, char *actor) {
    f32 center;
    int positive;
    int upper;
    int lower;
    center = *((f32 *)(actor + 0x28));
    positive = 1;
    upper = 1;
    if (!(((*((f32 *) (((char *) range) + 0xF0))) - center) <= 1.5f)) {
        upper = 0;
    }
    if (positive & upper) {
        goto done;
    }
    lower = 1;
    if (!((center - (*((f32 *) (((char *) range) + 0xFC)))) <= 1.5f)) {
        lower = 0;
    }
    use(lower);
done:
    finish();
}
"""


def assembly(*rows):
    return '\n'.join(f'/* {i*4:X} {0x80000000+i*4:08X} 00000000 */ {row}' for i, row in enumerate(rows))


TARGET = assembly(
    'lwc1 $f0, 0x28($s2)',
    'lwc1 $f2, 0xfc($s1)',
    'c.lt.s $f12, $f24',
    'sub.s $f4, $f0, $f2',
    'lwc1 $f2, 0xf0($s1)',
    'bc1t .L2',
    'sub.s $f2, $f2, $f0',
    'c.le.s $f2, $f0',
    'c.le.s $f4, $f0',
)



def test_mips_draft_repairs_offer_opposing_float_bounds():
    labels = {}
    proposed = draft_repairs.variants(SOURCE, 'f', 'mips', TARGET, '-O2', labels, limit=None)
    [candidate] = [text for text in proposed if labels[text] == 'opposing_float_bounds']
    assert candidate.index('repair_lower_delta =') < candidate.index('repair_upper_delta =')
    assert 'if (!(repair_upper_delta <= 1.5f))' in candidate
