from decompnet.app import draft_repairs

SOURCE = '''
extern f32 height;
void f(int state) {
    union { u32 word; f32 number; } zero;
    f32 buffer[3];
    f32 temp_s6;
    f32 var_s6;
    zero.word = 0;
    var_s6 = zero.number;
    if (state) {
        temp_s6 = 0.0f - height;
        height = new_height();
        fill(buffer, height);
        prepare();
        var_s6 = ((temp_s6 + height) + buffer[2]) + finish();
    }
    emit(var_s6);
}
'''

ROWS = [
    'jal fill',
    'addiu $a0, $sp, 0x20',
    'lwc1 $f0, 0x28($sp)',
    'mtc1 $s6, $f16',
    'nop',
    'add.s $f16, $f16, $f0',
    'mfc1 $s6, $f16',
    'jal prepare',
    'nop',
]


def asm(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))



def test_mips_draft_repairs_offer_phase_correct_vector_accumulator():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', asm(ROWS), '-O2', labels, limit=None)
    choices = [text for text in children if labels[text] == 'vector_accumulator_stage']
    assert any('fill(buffer, height);\n        var_s6 += height;\n        var_s6 += buffer[2];' in text
               and 'var_s6 = zero.number - height;' in text for text in choices)
