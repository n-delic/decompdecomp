"""Targeted instruction evidence reaches MIPS draft repair generation."""

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
