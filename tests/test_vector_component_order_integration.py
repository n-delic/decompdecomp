from decompnet.app import draft_repairs

SOURCE = '''
extern f32 height;
void f(void) {
    f32 vec[3];
    f32 var_s5;
    f32 var_s6;
    f32 var_fp;
    var_s5 = var_s6 = var_fp = 0.0f;
    fill(vec, 3);
    var_s5 += vec[0];
    var_s6 += height;
    var_s6 += vec[2];
    var_fp -= vec[1];
    emit(var_s5, var_fp, var_s6);
}
'''

ROWS = [
    'jal fill',
    'addiu $a0, $sp, 0x20',
    'lwc1 $f0, 0x20($sp)',
    'mtc1 $s5, $f16',
    'add.s $f16, $f16, $f0',
    'mfc1 $s5, $f16',
    'lwc1 $f0, 0x24($sp)',
    'mtc1 $fp, $f16',
    'sub.s $f16, $f16, $f0',
    'mfc1 $fp, $f16',
    'lwc1 $f0, 0x28($sp)',
    'mtc1 $s6, $f16',
    'add.s $f16, $f16, $f0',
    'mfc1 $s6, $f16',
]


def asm(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))



def test_mips_draft_repairs_offer_target_vector_component_order():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', asm(ROWS), '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'vector_component_order']
    assert candidate.index('var_s5 += vec[0];') < candidate.index('var_fp -= vec[1];')
    assert candidate.index('var_fp -= vec[1];') < candidate.index('var_s6 += height;')
    assert candidate.index('var_s6 += height;') < candidate.index('var_s6 += vec[2];')
