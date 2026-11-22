from decompnet.app import draft_repairs

SOURCE = '''
void f(void *object) {
    s32 state;
    state = read_byte((char *) object, 0x180);
    switch (state) {
    case 1:
        if (request()) goto clear;
        use();
        break;
    case 2:
        drop();
        break;
    }
clear:
    return;
}
'''

ROWS = [
    'lb $v1, 0x180($s1)',
    'addiu $v0, $zero, 1',
    'beq $v1, $v0, .L1',
    'slti $v0, $v1, 2',
    'bnez $v0, .L3',
    'addiu $v0, $zero, 2',
    'beq $v1, $v0, .L2',
]


def asm(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))



def test_draft_repairs_offer_target_empty_lower_case():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', asm(ROWS), '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'switch_empty_lower_case']
    assert 'case 0: break;\n    case 1:' in candidate
