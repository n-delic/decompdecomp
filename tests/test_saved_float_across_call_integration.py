from decompnet.app import draft_repairs

ACROSS_CALL = '''
void match(char *obj, char *output, int mode) {
    f32 temp_fs1;
    f32 temp_fs4;
    f32 result;
    if (mode) {
        temp_fs1 = read();
        send(temp_fs1);
    } else {
        temp_fs4 = *((f32 *) (obj + 0x3C)) - (*((f32 *) (output + 0x34)) * 1024.0f);
        result = read_again(mode);
        send(temp_fs4 + result);
        send(temp_fs4);
    }
}
'''


def _across_call_assembly(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))


CALL_ROWS = [
    'lwc1 $f2, 0x34($s1)',
    'mul.s $f2, $f2, $f0',
    'lwc1 $f0, 0x3c($s2)',
    'jal read_again',
    'sub.s $f28, $f0, $f2',
]



def test_draft_repairs_pass_assembly_for_saved_float_across_call():
    labels = {}
    asm = _across_call_assembly(CALL_ROWS)
    children = draft_repairs.variants(ACROSS_CALL, 'match', 'mips', asm, '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'saved_float_across_call']
    assert 'f32 temp_fs4;' not in candidate
    assert 'send(temp_fs1 + result);' in candidate
