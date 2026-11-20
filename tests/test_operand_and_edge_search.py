import pytest

from decompnet.app.diff_gated_repairs import proposals

KMC = 'gcc-2.7.2-kmc'


def diff(target, candidate):
    return [('<', line, '') for line in target.split('|') if line] + [
        ('>', '', line) for line in candidate.split('|') if line]


def asm(*lines):
    return '\n'.join(line if line.endswith(':') else
                     f'/* {i*4:06X} {0x80001000+i*4:08X} 00000000 */ {line}' for i,line in enumerate(lines))


def stores(register):
    return (f'sll {register}, $a0, 1|addu {register}, {register}, $a0|sll {register}, {register}, 2|'
            f'lui $at, %hi(A)|addu $at, $at, {register}|sh $s0, %lo(A)($at)|'
            f'lui $at, %hi(B)|addu $at, $at, {register}|sw $s1, %lo(B)($at)|'
            f'lui $at, %hi(C)|addu $at, $at, {register}|sh $s2, %lo(C)($at)')


RETRY_CODE = ('move $a0, $zero','.Lretry:', 'move $v1, $zero','lbu $v0, 0($v1)',
              'addiu $a0, $a0, 1','addiu $v1, $v1, 0x12','bne $a0, $a1, .Lretry','move $a0, $zero')
BYTE_CODE = ('lbu $a2, 7($a0)','j .Lbody','nop','.Lloop:','lhu $v0, 4($a0)', '.Lbody:',
             'lbu $a2, 7($a0)','slt $v0, $v0, $a2','beql $v0, $zero, .Lloop','addiu $a3, $a3, 1')
CLAMP_CODE = ('sltiu $v0, $v1, 14','bnel $v0, $zero, .Lremask','addiu $a0, $zero, 14',
              'sltiu $v0, $v1, 28','beql $v0, $zero, .Lremask','addiu $a0, $zero, 27',
              'j .Lend','nop','.Lremask:','andi $v1, $a0, 0xffff','.Lend:','jr $ra','nop')
DECODER = ('int f(unsigned char *p, unsigned char *q) { int h, l, a, b, total; '
           'h = p[0]; l = p[1]; if (h & 0x80) { a = l | ((h & 0x7f) << 8); } else { a = h; } '
           'total = a; h = q[0]; l = q[1]; if (h & 0x80) { b = l | ((h & 0x7f) << 8); } else { b = h; } '
           'return total + b; }')

CASES = [
    ('float_argument_evaluation_order',
     'void f(float x,float y) { float a,b; a = x - y; b = x + y; call(a,b); }',
     diff('add.s $f14, $f0, $f2|sub.s $f12, $f0, $f2|jal call|nop',
          'sub.s $f12, $f0, $f2|add.s $f14, $f0, $f2|jal call|nop'),
     '', 'b = x + y; a = x - y; call(a,b);'),
    ('in_place_float_divide_square',
     'void f(void) { float x,d,q; x=measure(); d=scale(); q = x/d; use(q*q*127.0f); }',
     diff('div.s $f2, $f2, $f4|mul.s $f2, $f2, $f2','div.s $f6, $f2, $f4|mul.s $f0, $f6, $f6'),
     '', 'x /= d; x *= x; use(x*127.0f);'),
    ('final_indexed_store_offset',
     'void f(int i) { int off, x; off=0; x=scan(off); off = i * 12; '
     '*(u16 *)((u8 *)&A + off) = x; *(u32 *)((u8 *)&B + off) = x; *(u16 *)((u8 *)&C + off) = 1; use(x); }',
     diff(stores('$v0'),stores('$v1')), '', 'int repair_insert_offset = i * 12;'),
    ('loop_guard_sink_initializer',
     'void f(int n) { int off=0; if (n) { ptr=&record; do { read(off); off+=88; } while(next()); } }',
     diff('beqz $a0, .Lend|sw $s1, 0x10($sp)|addiu $s4, $sp, 0x20|move $s1, $zero|addiu $s1, $s1, 88',
          'sw $s1, 0x10($sp)|move $s1, $zero|beqz $a0, .Lend|nop'),
     '', 'ptr=&record; off = 0; do'),
    ('retry_edge_counter_reset',
     'void f(int n,int end) { int off; prepare(); retry: off=0; n=0; do { probe(off); n++; off+=18; } '
     'while (n<2); if (n!=end) goto retry; finish(); }',
     diff('move $a0, $zero|bne $a0, $a1, .Lretry|move $a0, $zero',
          'move $v1, $zero|bne $v1, $a1, .Lretry|move $v1, $zero'), asm(*RETRY_CODE),
     'if (n!=end) { n = 0; goto retry; }'),
    ('ordinary_backedge_byte_read',
     'typedef struct { u8 limit; } State; void f(void *p) { '
     'u8 n = *(volatile u8 *)&((State*)p)->limit; for (;;) { if (n) break; step(); '
     'n = *(volatile u8 *)&((State*)p)->limit; } }',
     diff('lbu $a2, 7($a0)|beql $v0, $zero, .Lloop|addiu $a3, $a3, 1',
          'lbu $a2, 7($a0)|lbu $a2, 7($a0)|beqz $v0, .Lloop|nop'),
     asm(*BYTE_CODE), 'n = *(u8 *)&((State*)p)->limit;'),
    ('shared_remask_clamp',
     'int f(int x) { int masked,value; value=x; masked=(u16)value; '
     'if(masked<14) value=14; if(masked>=28) value=27; masked=(u16)value; return masked; }',
     diff('bnel $v0, $zero, .Lremask|beql $v0, $zero, .Lremask','beqz $v0, .Llow|bnez $v0, .Lhigh'),
     asm(*CLAMP_CODE), 'value = masked < 14 ? 14 : masked >= 28 ? 27 : value;'),
    ('paired_decoder_byte_reuse', DECODER,
     diff('lbu $a3, 1($v0)|sll $v0, $v0, 8|or $a3, $a3, $v0|'
          'lbu $a3, 1($v1)|sll $v0, $v0, 8|or $v0, $a3, $v0|sll $v0, $v0, 17|sra $a3, $v0, 17',
          'lbu $v1, 1($v0)|sll $v0, $v0, 8|or $a3, $v1, $v0|'
          'lbu $a3, 1($v1)|sll $v0, $v0, 8|or $v0, $a3, $v0|sll $v0, $v0, 17|sra $a3, $v0, 17'),
     '', 'return total + l;'),
    ('aligned_wide_zero_clear',
     'extern u64 wide; void f(void) { ((u32*)&wide)[0] = 0; ((u32*)&wide)[1] = 0; }',
     diff('move $t0, $zero|move $t1, $zero|sw $t0, %lo(wide)($at)|sw $t1, %lo(wide+4)($at)',
          'sw $zero, %lo(wide)($at)|sw $zero, %lo(wide+4)($at)'),
     '', 'wide = 0;'),
    ('null_guard_store_constant',
     'void f(void) { void *p; char *q; p = alloc(); if (p != NULL) { q = child(p); '
     '*(u8 *)(q + 0x17) = 100; *(u8 *)(q + 0x18) = 100; } }',
     diff('jal alloc|nop|move $s0, $v0|beqz $s0, .Lend|addiu $a0, $zero, 100|sb $a0, 0x17($v0)|sb $a0, 0x18($v0)',
          'jal alloc|nop|move $s0, $v0|beqz $s0, .Lend|nop|addiu $a0, $zero, 100'),
     '', 'if ((repair_guard_byte = 100, p) != NULL)'),
]


@pytest.mark.parametrize('family,source,rows,assembly,needle',CASES)
def test_every_repair_requires_a_relevant_diff(family,source,rows,assembly,needle):
    options = proposals(source,'f','','mips',KMC,rows,assembly,flags=['-mgp32'],resolve={'wide':8}.get)
    assert any(label==family and needle in new for label,new in options), options
    assert len([new for label,new in options if label==family]) <= 4
    assert not proposals(source,'f','','mips',KMC,[(' ',a,a) for _,a,_ in rows],assembly,flags=['-mgp32'],resolve={'wide':8}.get)
    assert family not in {label for label,_ in proposals(source,'f','','arm',KMC,rows,assembly,flags=['-mgp32'],resolve={'wide':8}.get)}
    assert family not in {label for label,_ in proposals(source,'f','','mips','ido-5.3',rows,assembly,flags=['-mgp32'],resolve={'wide':8}.get)}


def test_wide_clear_requires_o32_and_a_resolved_aligned_symbol():
    family,source,rows,assembly,_ = CASES[8]
    for flags,resolver in ((['-mgp32'],None),(['-mgp32'],{'wide':12}.get),(['-mgp64'],{'wide':8}.get)):
        assert family not in {label for label,_ in
                              proposals(source,'f','','mips',KMC,rows,assembly,flags=flags,resolve=resolver)}
    killed = diff('move $t0, $zero|move $t1, $zero|xor $t0, $a1, $a2|'
                  'sw $t0, %lo(wide)($at)|sw $t1, %lo(wide+4)($at)',
                  'sw $zero, %lo(wide)($at)|sw $zero, %lo(wide+4)($at)')
    assert not proposals(source,'f','','mips',KMC,killed,assembly,flags=['-mgp32'],resolve={'wide':8}.get)


def test_float_argument_diff_keeps_outgoing_stack_slots_fixed():
    source = ('void f(float x,float y) { float a,b; a=x-y; b=x+y; state &= 1; '
              'if (test(1,2,3,4,5,6,a,b)) work(); }')
    rows = diff('add.s $f2, $f0, $f4|sub.s $f0, $f0, $f4|swc1 $f2, 0x1c($sp)|jal test|swc1 $f0, 0x18($sp)',
                'sub.s $f0, $f0, $f4|add.s $f2, $f0, $f4|swc1 $f2, 0x1c($sp)|jal test|swc1 $f0, 0x18($sp)')
    [(_, changed)] = proposals(source,'f','','mips',KMC,rows,'')
    assert 'b=x+y; a=x-y;' in changed and 'test(1,2,3,4,5,6,a,b)' in changed
