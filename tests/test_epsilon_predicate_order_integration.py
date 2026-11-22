from decompnet.app import draft_repairs

SOURCE = '''
void f(f32 magnitude) {
    int var_v1_3;
    int var_v0_4;
    var_v1_3 = 1;
    if (!(magnitude <= 0.0002f)) {
        var_v1_3 = 0;
    }
    var_v0_4 = 1;
    if (!(magnitude >= (-0.0002f))) {
        var_v0_4 = 0;
    }
    if (((var_v1_3 ^ 1) | (var_v0_4 ^ 1)) == 0) {
        ready();
    }
}
'''

ROWS = [
    'c.le.s $f26, $f22',
    'bc1f .L1',
    'nop',
    'c.le.s $f20, $f26',
    'bc1t .L2',
    'xori $v1, $v1, 1',
    'move $v0, $zero',
    'xori $v0, $v0, 1',
    'or $v0, $v1, $v0',
]


def asm(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))



def test_mips_draft_repairs_offer_target_float_predicate_order():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', asm(ROWS), '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'epsilon_predicate_order']
    assert '(var_v0_4 ^ 1) | (var_v1_3 ^ 1)' in candidate
