import pytest

from decompnet.app.diff_gated_repairs import proposals

KMC = 'gcc-2.7.2-kmc'


def difference(target, candidate):
    return [('<', text, '') for text in target.split('|') if text] + [
        ('>', '', text) for text in candidate.split('|') if text]


def assembly(*lines):
    return '\n'.join(line if line.endswith(':') else f'/* {4*i:06X} {0x80001000+4*i:08X} 00000000 */ {line}'
                     for i,line in enumerate(lines))


TAIL = assembly('.Lloop:', 'addiu $v0, $s0, 1', 'addiu $v1, $s1, -3', 'slt $at, $v0, $v1', 'bnez $at, .Lloop', 'nop')

CASES = [
    ('post_call_selector_initializer',
     'int f(int n) { int mode = 0; out = n; n = helper(n); if (n) mode = 1; return mode; }',
     difference('jal helper|nop|move $v1, $zero', 'sw $s0, 0x18($sp)|move $s0, $zero|jal helper|nop'),
     '', 'n = helper(n); mode = 0;'),
    ('field_first_short_circuit',
     'int f(Node *p, Node *q) { if (p != 0) { if (p != q && p->tag == 1 && p->active) { return 1; } } return 0; }',
     difference('lhu $v0, 4($a0)|bnez $v0, .Lnext|nop|beq $a0, $a1, .Lnext', 'xor $v0, $a0, $a1|sltu $v0, $zero, $v0|and $v0, $v0, $v1'),
     '', 'if (p->tag == 1) { if (p != q && p->active)'),
    ('loop_tail_bound',
     'void f(int length) { int i; int bound; i = 0; bound = (int)(unsigned short)length - 3; do { sink(i); i++; } while (i < bound); }',
     difference('addiu $v0, $s0, 1|addiu $v1, $s1, -3|slt $at, $v0, $v1|bnez $at, .Lloop',
                'addiu $s3, $s1, -3|addiu $v0, $s0, 1|slt $at, $v0, $s3|bnez $at, .Lloop'),
     TAIL, 'i++;  bound = (int)(unsigned short)length - 3;'),
    ('delayed_scaled_pointer',
     'void f(int i, Node *n) { char *base; void **p; void *value; base = table; '
     'p = (i * 4) + base; n->flag = 0; value = *p; use(value, p); }',
     difference('sb $zero, 2($a0)|sll $v0, $a1, 2|addu $v0, $v0, $s0|lw $a2, 0($v0)',
                'sll $v0, $a1, 2|addu $v0, $v0, $s0|sb $zero, 2($a0)|lw $a2, 0($v0)'),
     '', 'value = *((void **)((i * 4) + base)); p = (i * 4) + base;'),
    ('guard_local_global_address',
     'extern int limit; int f(int n) { if (n < limit) { limit = n; } return limit; }',
     difference('lui $t0, %hi(limit)|addiu $t0, $t0, %lo(limit)|lw $v0, 0($t0)|bnel $v0, $a0, .Lend|sw $a0, 0($t0)',
                'lui $t1, %hi(limit)|lw $v0, %lo(limit)($t1)|sw $a0, %lo(limit)($t1)'),
     '', 'if (n < (*repair_guard_address)) { (*repair_guard_address) = n; } } return limit;'),
    ('post_copy_store_scope',
     'void f(char *p, float x) { *(u32 *)(p + 8) = a; *(u32 *)(p + 12) = b; *(u32 *)(p + 16) = c; '
     '*(float *)(p + 24) = x; *(u32 *)(p + 28) = 0; }',
     difference('sw $t0, 8($a0)|sw $t1, 12($a0)|sw $t2, 16($a0)|swc1 $f12, 24($a0)',
                'swc1 $f12, 24($a0)|sw $t0, 8($a0)|sw $t1, 12($a0)|sw $t2, 16($a0)'),
     '', 'do { *(float *)(p + 24) = x; } while (0);'),
    ('observed_stack_array_record',
     'void f(void) { float sp20[3]; int sp10[3]; sp10[0] = 7; sp20[0] = 1.0f; helper(sp10, sp20); }',
     [('s','sw $v0, 0x10($sp)','sw $v0, 0x18($sp)'),
      ('s','swc1 $f0, 0x20($sp)','swc1 $f0, 0x1c($sp)')],
     '', 'int sp10[3]; unsigned char gap_1C[4]; float sp20[3];'),
    ('relocated_strided_field_views',
     'extern char A[]; extern char B[]; int f(int i) { return *(u32 *)(A + i * 12) + *(u16 *)(B + i * 12); }',
     difference('sll $t0, $a0, 1|addu $t0, $t0, $a0|sll $t0, $t0, 2|lui $at, %hi(A)|addu $at, $at, $t0|'
                'lw $v0, %lo(A)($at)|lui $at, %hi(B)|addu $at, $at, $t0|lhu $v1, %lo(B)($at)',
                'lw $v0, 0($t1)|lhu $v1, 0($t2)'),
     '', 'struct { u32 value; unsigned char padding[8]; } *)A)[i].value'),
    ('signed_halfword_store_mask',
     'void f(short *p, short *out) { short word; word = *(short *)p; *(short *)out = 1 + (word & 0xffe0); }',
     difference('lh $v0, 0($a0)|andi $v1, $v0, 0xffe0|addu $v1, $v1, $a2|sh $v1, 0($a1)',
                'lhu $v0, 0($a0)|and $v1, $v0, $a2|sh $v1, 0($a1)'),
     '', 'int word; word = *(short *)p; *(short *)out = 1 + ((unsigned short)word & 0xffe0);'),
    ('dead_float_constant_reuse',
     'double f(double x) { double cutoff = 1.0; double scale; if (x > cutoff) { return x; } '
     'scale = 2.0; return helper(x) * scale; }',
     [(' ', 'jal helper', 'jal helper'), ('r','mul.d $f0, $f2, $f4','mul.d $f0, $f4, $f2')],
     '', 'cutoff = 2.0; return helper(x) * cutoff;'),
]


@pytest.mark.parametrize('family,source,rows,asm,needle', CASES)
def test_followup_requires_measured_gate(family,source,rows,asm,needle):
    options = proposals(source,'f','','mips',KMC,rows,asm)
    assert any(label==family and needle in new for label,new in options), options
    assert len([new for label,new in options if label==family]) <= 2
    assert not proposals(source,'f','','mips',KMC,[(' ',a,a) for _,a,_ in rows],asm)
    assert family not in {label for label,_ in proposals(source,'f','','mips','ido-5.3',rows,asm)}
    assert not proposals(source,'f','','arm',KMC,rows,asm)


def test_tail_bound_accepts_moved_copy_feeding_the_backedge():
    source = CASES[2][1]
    code = ('addiu $v1, $s1, -0x3','beqz $v0, .Lend','nop','lui $s0, 0x8000',
            'addu $s4, $v1, $zero','.Lloop:','jal helper','nop','slt $v0, $a1, $s4',
            'bnez $v0, .Lloop','nop','.Lend:','jr $ra','nop')
    rows = [('o','move $s4, $v1','move $s4, $v1')]
    options = proposals(source,'f','','mips',KMC,rows,assembly(*code))
    assert [family for family,_ in options]==['loop_tail_bound']
    edited = tuple('slt $v0, $a1, $s5' if line=='slt $v0, $a1, $s4' else line for line in code)
    assert not proposals(source,'f','','mips',KMC,rows,assembly(*edited))


def test_scalar_case_requires_saved_register_pressure_and_matching_call():
    family,source,rows,asm,_ = CASES[0]
    for muted in (
        [row for row in rows if not row[2].startswith('sw ')],
        [(marker,left,right.replace('jal helper','jal other')) for marker,left,right in rows],
    ):
        assert family not in {label for label,_ in proposals(source,'f','','mips',KMC,muted,asm)}


def test_strided_evidence_uses_saved_index_across_a_call_only():
    _,source,rows,asm,_ = CASES[7]
    aligned = [(marker,left.replace('$t0','$s0'),right) for marker,left,right in rows]
    index = next(i for i,row in enumerate(aligned) if 'lui $at, %hi(B)' in row[1])
    aligned[index:index] = [('<','jal helper',''),('<','nop','')]
    assert any(family=='relocated_strided_field_views' for family,_ in proposals(source,'f','','mips',KMC,aligned,asm))
    clobbered = [(marker,left.replace('$s0','$t0'),right) for marker,left,right in aligned]
    assert not proposals(source,'f','','mips',KMC,clobbered,asm)
