from decompnet.app import draft_repairs

SOURCE = '''
f32 compute(void);
void f(void *object, int condition) {
    f32 amount;
    f32 result;
    amount = read_amount();
    if (condition) {
        result = 0.0f;
        adjust(object);
    } else {
        prepare(object);
        result = compute();
    }
    apply(object, amount, result);
}
'''

ROWS = [
    'jal compute',
    'mov.s $f12, $f24',
    'mfc1 $a1, $f24',
    'mfc1 $a2, $f0',
    'move $a0, $s2',
    'jal apply',
    'nop',
]


def asm(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))



def test_mips_draft_repairs_offer_forwarded_float_argument():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', asm(ROWS), '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'float_forwarded_argument']
    assert 'f32 compute(f32);' in candidate
    assert 'result = compute(amount);' in candidate
    assert 'apply(object, amount, 0.0f);' in candidate
