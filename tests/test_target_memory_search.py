import pytest

from decompnet.app.diff_gated_repairs import proposals

KMC = 'gcc-2.7.2-kmc'


def diff(target,candidate):
    return [('<',line,'') for line in target.split('|') if line] + [
        ('>','',line) for line in candidate.split('|') if line]


def asm(*lines):
    return '\n'.join(line if line.endswith(':') else f'/* {4*i:06X} {0x80001000+4*i:08X} 00000000 */ {line}'
                     for i,line in enumerate(lines))


DIAMOND = ('beqz $a0, .Lelse','nop','lui $a2, %hi(left)','addu $a2, $a2, $v1',
           'lbu $a2, %lo(left)($a2)','j .Ljoin','nop','.Lelse:','lui $a2, %hi(right)',
           'addu $a2, $a2, $v1','lbu $a2, %lo(right)($a2)','.Ljoin:','jal use','nop')
RESET = ('.Lloop:','jal work','addiu $s0, $s0, 1','sltiu $v0, $s0, 3','bnez $v0, .Lloop','nop',
         'jal next','move $s0, $zero')
TRIM = ('andi $v0, $s1, 0xff','beqz $v0, .Lafter','nop','addiu $v1, $zero, 32','.Lloop:',
        'lb $v0, -1($v0)','bne $v0, $v1, .Lafter','andi $v0, $s1, 0xff','addiu $s1, $s1, -1',
        'andi $v0, $s1, 0xff','bnez $v0, .Lloop','nop','.Lafter:','andi $v0, $s1, 0xff','jr $ra','nop')
TIMER = 'lw $v0, 0x14($a0)|blez $v0, .Lend|subu $t7, $v0, $a1|bgtz $t7, .Lend|sw $t7, 0x14($a0)'

CASES = [
    ('volatile_byte_postfix_reload',KMC,
     'extern volatile u8 counter; void f(void) { counter += 1; consume(); }',
     diff('lbu $v0, %lo(counter)($v0)|addiu $v0, $v0, 1|sb $v0, %lo(counter)($at)|'
          'lui $v0, %hi(counter)|jal consume|lbu $v0, %lo(counter)($v0)',
          'lbu $v0, %lo(counter)($v0)|addiu $v0, $v0, 1|sb $v0, %lo(counter)($at)|jal consume|nop'),
     '', 'counter++; consume();'),
    ('one_arm_scaled_offset',KMC,
     'void f(int arg) { int i,off; i=arg&0xff; off=i*12; if (cond) { use(*(&left+off)); } '
     'else { use(*(&right+off)); } }',
     [(' ', 'lbu $a2, %lo(left)($a2)', 'lbu $a2, %lo(left)($a2)'),
      ('r','addu $a2, $a2, $v1','addu $a1, $a1, $v1'),('r','lbu $a2, %lo(right)($a2)','lbu $a2, %lo(right)($a1)')],
     asm(*DIAMOND), 'use(*(&right+((int)(i*12))));'),
    ('split_global_load_decrement',KMC,
     'extern s16 next; void f(void) { int i = start; do { --i; } while (i>0); i = next - 1; consume(i); }',
     diff('addiu $v1, $v1, -1|lh $v1, %lo(next)($v1)|addiu $v1, $v1, -1',
          'addiu $v1, $v1, -1|lh $v0, %lo(next)($v0)|addiu $v1, $v0, -1'),
     '', 'i = next; --i; consume(i);'),
    ('post_call_float_literal',KMC,
     'void f(void) { float length=1000.0f; Object *p; p=make(); if (p) { p->a=length; p->b=length; } }',
     diff('jal make|nop|mtc1 $at, $f0|swc1 $f0, 4($a0)|swc1 $f0, 8($a0)',
          'sdc1 $f20, 16($sp)|mtc1 $at, $f20|jal make|nop|swc1 $f20, 4($a0)|swc1 $f20, 8($a0)'),
     '', '{ float length=1000.0f; p->a=length; p->b=length; }'),
    ('loop_exit_counter_reset',KMC,
     'void f(void) { u32 i; unsigned int next_count; i=0; do { i+=1; work(); } while (i<3U); next_count=0; next(); use(next_count); }',
     diff('bnez $v0, .Lloop|jal next|move $s0, $zero','bnez $v0, .Lloop|jal next|nop'),
     asm(*RESET), 'if (i >= 3U) { next_count = 0; break; }'),
    ('signed_multiply_high_word',KMC,
     's32 f(s32 x) { return (s32)(((s64)x * 0x51eb851f) >> 32); }',
     diff('mult $v0, $v1|sra $v0, $v0, 31|mfhi $a3','mult $v0, $v1|mflo $v0|mfhi $v1|sra $v0, $v0, 31'),
     '', 'return (s32)repair_product.words.high;'),
    ('stepwise_float_accumulator',KMC,
     'float f(float a,float b,float c,float d) { float sum = a*b + b*c + c*d; return sum * d; }',
     [('r','add.s $f0, $f0, $f2','add.s $f4, $f4, $f2'),('r','add.s $f0, $f0, $f6','add.s $f0, $f4, $f6')],
     '', 'sum = a*b; sum += b*c; sum += c*d; sum *= d; return sum;'),
    ('direct_countdown_field','ido-5.3',
     'typedef struct { int timer; } State; void f(State *p,int step) { int cached; cached=p->timer; if (cached>0) { cached-=step; '
     'p->timer=cached; if (cached<=0) done(); } }',
     diff(TIMER,'lw $v1, 0x14($a0)|blez $v1, .Lend|nop|subu $v1, $v1, $a1|sw $v1, 0x14($a0)|bnez $v1, .Lend'),
     '', 'if (p->timer > 0) { p->timer -= step; if (p->timer <= 0)'),
    ('top_tested_byte_trim',KMC,
     'void f(char *src,int arg) { int n,seed,low; char *ptr; n=arg; seed=n&255; clear(seed); low=n&255; '
     'if (seed!=0) { ptr=low+src; for (;;) { low=n&255; if (*(char *)(ptr-1)==32) { n-=1; low=n&255; '
     'ptr=low+src; if (low==0) { low=n&255; } else { continue; } } break; } } finish(low); }',
     diff('beqz $v0, .Lafter|bnez $v0, .Lloop','bnez $v1, .Lafter|bne $v0, $v1, .Lloop'),
     asm(*TRIM), 'while ((unsigned char)n && ((char *)src)[(unsigned char)n - 1] == 32) { --n; }'),
    ('packed_field_opcode_staging',KMC,
     'void f(int x,int y) { int b; b=y&0xfff; out=x|(b|0xed000000U); }',
     diff('andi $v1, $a1, 0xfff|lui $a0, 0xed00|or $v1, $v1, $a0|or $v0, $v0, $v1|sw $v0, 0($t0)',
          'lui $a0, 0xed00|andi $v1, $a1, 0xfff|or $v0, $v1, $a0|or $v0, $a2, $v0|sw $v0, 0($t0)'),
     '', 'b=(y&0xfff) | 0xed000000U; out=x | b;'),
    ('byte_countdown_load_order',KMC,
     'static inline s8 read_byte(char *p, int i) { return *((s8 *) (p + i)); }\n'
     'int f(char *record) {\n'
     '    s32 value;\n'
     '    value = read_byte((char *) record, 12);\n'
     '    *((u8 *) (((char *) record) + 4)) = (u8) ((*((u8 *) (((char *) record) + 4))) - 1);\n'
     '    if (value < read_byte((char *) record, 16)) return value;\n'
     '    return 0;\n}',
     diff('lbu $v0, 4($s1)|lb $v1, 12($s1)|lb $a0, 16($s1)|addiu $v0, $v0, -1|sb $v0, 4($s1)',
          'lb $v0, 12($s1)|lbu $v1, 4($s1)|lb $a0, 16($s1)|addiu $v1, $v1, -1|sb $v1, 4($s1)'),
     '', '(u8) (repair_countdown_byte - 1)'),
]


@pytest.mark.parametrize('family,compiler,source,rows,assembly,expected',CASES)
def test_each_repair_requires_its_measured_diff(family,compiler,source,rows,assembly,expected):
    options = proposals(source,'f','','mips',compiler,rows,assembly,big_endian=True)
    assert any(label==family and expected in new for label,new in options), options
    assert len([new for label,new in options if label==family])<=4
    assert not proposals(source,'f','','mips',compiler,[(' ',a,a) for _,a,_ in rows],assembly,big_endian=True)
    assert not proposals(source,'f','','arm',compiler,rows,assembly,big_endian=True)
    assert not proposals(source,'f','','mips','gcc-4.4.0-n64',rows,assembly,big_endian=True)


def test_multiply_high_requires_big_endian_mips():
    family,compiler,source,rows,assembly,_ = CASES[5]
    assert family not in {kind for kind,_ in proposals(source,'f','','mips',compiler,rows,assembly,big_endian=False)}


def test_arm_recompute_skips_when_both_arms_differ():
    _,compiler,source,rows,assembly,_ = CASES[1]
    both = [('r',left,right.replace('($a2)','($v0)')) if '%lo(left)' in left else (m,left,right)
            for m,left,right in rows]
    assert not proposals(source,'f','','mips',compiler,both,assembly)


def test_countdown_gate_requires_ido_and_same_word_address():
    family,compiler,source,rows,_,_ = CASES[7]
    assert family not in {kind for kind,_ in proposals(source,'f','','mips',KMC,rows,'')}
    wrong = [(m,left.replace('sw $t7, 0x14','sw $t7, 0x18'),right) for m,left,right in rows]
    assert not proposals(source,'f','','mips',compiler,wrong,'')
