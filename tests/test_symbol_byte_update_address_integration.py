from decompnet.app import draft_repairs

SOURCE = '''
extern u8 flags;
void f(void) {
    prepare();
    flags |= 0x21;
    finish();
}
'''
ROWS = [
    'lui $v1, %hi(flags)',
    'addiu $v1, $v1, %lo(flags)',
    'lbu $v0, ($v1)',
    'ori $v0, $v0, 0x21',
    'sb $v0, ($v1)',
]


def asm(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))



def test_draft_repairs_offer_one_address_for_a_compound_byte_update():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', asm(ROWS), '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'symbol_byte_update_address']
    assert '__base_flags = &flags;' in candidate
    assert '(*__base_flags) |= 0x21;' in candidate
