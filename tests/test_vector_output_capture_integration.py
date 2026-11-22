from decompnet.app import draft_repairs

SOURCE = '''
void f(void *context) {
    struct { f32 vector[3]; } frame;
    f32 x;
    f32 y;
    f32 z;
    load(frame.vector, 3);
    *((float *) context) = 0.0f;
    x = frame.vector[0];
    y = frame.vector[1];
    z = frame.vector[2];
    use(x, y, z);
}
'''
ROWS = [
    'jal load',
    'addiu $a0, $sp, 0x20',
    'lwc1 $f0, 0x20($sp)',
    'lwc1 $f2, 0x24($sp)',
    'lwc1 $f4, 0x28($sp)',
    'lw $v1, 0x68($s2)',
    'sw $zero, 0x34($v1)',
    'add.s $f0, $f0, $f2',
]


def asm(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))



def test_mips_draft_repairs_offer_capturing_vector_output():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', asm(ROWS), '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'vector_output_capture']
    assert candidate.index('repair_vector_2 =') < candidate.index('*((float *) context) =')
    assert 'y = repair_vector_1;' in candidate
