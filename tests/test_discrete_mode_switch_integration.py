from decompnet.app import draft_repairs

SOURCE = '''
void f(void *record) {
    s16 mode;
    mode = *((s16 *) (((char *) record) + 0x7C));
    if ((mode == 0) || (mode == 6)) {
        act();
    }
    finish();
}
'''
ROWS = [
    'lh $v1, 0x7c($s2)',
    'beqz $v1, .L1',
    'addiu $v0, $zero, 6',
    'bne $v1, $v0, .L2',
    'nop',
]


def asm(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))



def test_draft_repairs_offer_two_shared_mode_cases():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', asm(ROWS), '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'discrete_mode_switch']
    assert 'switch (mode)' in candidate
    assert 'case 0: case 6:' in candidate
    assert candidate.count('act();') == 1
