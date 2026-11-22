"""Targeted instruction evidence reaches MIPS draft repair generation."""

from decompnet.app.diff_gated_repairs import proposals
from decompnet.app.draft_repairs import variants


def test_saved_zero_word_for_float_updates_reaches_mips_permuter():
    field = '*((f32 *) (((char *) arg0) + 0x44))'
    source = f"""\
void f(void *arg0, int state) {{
    f32 sp5C;
    sp5C = 0.0f;
    switch (state) {{
    case 5:
        {field} = 0.0f;
        break;
    }}
    {field} = (f32) (({field}) + 0.0f);
}}
"""
    instructions = [
        'addu $s2, $a0, $zero', 'addu $s7, $zero, $zero', 'move $s7, $zero',
        'mtc1 $s7, $f16', 'add.s $f2, $f2, $f16', 'swc1 $f2, 0x44($s2)',
    ]
    assembly = '\n'.join(f'/* {n*4:X} {0x80000000+n*4:08X} 00000000 */ {row}'
                         for n, row in enumerate(instructions))
    labels = {}
    proposed = variants(source, 'f', 'mips', assembly, labels=labels)
    [candidate] = [text for text in proposed if labels[text] == 'stored_float_zero']
    assert f'{field} = repair_saved_zero.number;' in candidate
    assert '+ repair_saved_zero.number' in candidate


def test_measured_kmc_route_tries_the_stack_vector_zero_word():
    source = '''\
typedef struct { float x; float y; float z; } Vector;
void update(void *context) {
    union { unsigned int bits; float number; } zero;
    struct { Vector vector; } frame;
    zero.bits = (unsigned int)context & ~(unsigned int)context;
    fill(&frame.vector.x);
    frame.vector.y = zero.number;
    consume(&frame.vector.x);
    emit(frame.vector.z);
}
'''
    rows = [
        (' ', 'jal fill', 'jal fill'),
        (' ', 'addiu $s0, $sp, 0x20', 'addiu $s0, $sp, 0x20'),
        (' ', 'move $a0, $s0', 'move $a0, $s0'),
        (' ', 'jal consume', 'jal consume'),
        ('r', 'sw $zero, 0x24($sp)', 'sw $s7, 0x24($sp)'),
    ]
    family = 'stack_vector_zero_word'
    [candidate] = [text for label, text in proposals(source, 'update', '', 'mips',
                                                    'gcc-2.7.2-kmc', rows, '') if label == family]
    assert 'frame.vector.words[1] = 0;' in candidate
    assert 'consume(&frame.vector.value.x);' in candidate
    assert family not in {label for label, _ in proposals(source, 'update', '', 'mips', 'ido-7.1', rows, '')}
