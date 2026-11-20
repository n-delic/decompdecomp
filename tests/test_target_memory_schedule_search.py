import pytest

from decompnet.app.diff_gated_repairs import proposals


def pair(a,b):
    a,b = a.split('|'),b.split('|')
    assert len(a)==len(b)
    return [(' ' if x==y else 'r',x,y) for x,y in zip(a,b)]


def assembly(text):
    return '\n'.join(line if line.endswith(':') else f'/* {4*i:06X} {0x80001000+4*i:08X} 00000000 */ {line}'
                     for i,line in enumerate(text.split('|')))


KMC = 'gcc-2.7.2-kmc'
IDO = 'ido-5.3'
RELOAD = ('lw $v0, %lo(current)($at)|beqz $v0, .Lskip|sb $zero, 4($v0)|lw $s0, %lo(current)($at)|'
          'lw $v1, 8($s0)|ori $v1, $v1, 32|sw $v1, 8($s0)|lw $s0, %lo(current)($at)|sw $zero, 12($s0)')
ZERO = ('andi $v0, $zero, 0xffff|sh $v0, 2($a0)|nop|sw $zero, 4($a0)|sh $v0, 8($a0)')
MASK = ('lw $t0, 16($sp)|lui $v1, 0xff|ori $v1, $v1, 0xffff|and $a0, $t0, $v1|jal consume|nop')
FLOAT = ('lwc1 $f0, 0($a0)|lwc1 $f2, 4($a0)|add.s $f0, $f0, $f4|add.s $f2, $f2, $f6|'
         'swc1 $f0, 0($a0)|swc1 $f2, 4($a0)')
STORES = ('nop|sh $a1, 4($a0)|addiu $a2, $a2, 1|addiu $a3, $a3, 2|sh $a2, 6($a0)|sh $a3, 8($a0)')
NARROW = ('beqz $a0, .Lend|nop|lw $t0, 0($a1)|lw $t1, 4($a1)|sw $t0, 0($a2)|sw $t1, 4($a2)|'
          'andi $v0, $a3, 0xffff|move $a0, $v0')
SCALE = ('move $t0, $zero|j .Ldone|sll $v0, $t0, 2|sll $v0, $t0, 2|jr $ra|nop')
PACKED = 'lwl $v0, 5($a0)|lwr $v0, 8($a0)|swl $v0, 1($sp)|swr $v0, 4($sp)'
INLINE = ('li $t0, 4|li $t1, 2|li $t2, 32|lui $at, %hi(base)|addiu $a0, $at, %lo(base)|'
          'sb $zero, 0($a0)|addiu $t0, $t0, 2|bne $t0, $t2, .Lloop|nop')
NULL = ('bnez $a0, .Lsuccess|nop|j .Lend|move $v0, $zero|lw $v0, 0($a0)|jr $ra|nop')

CASES = [
    ('middle_volatile_field_pointer',KMC,
     'extern void *volatile current; void f(void) { void *p; p=current; if (p) first(p); p=current; '
     '*(unsigned int *)((unsigned char *)p+8)|=32; p=current; finish(p); }',
     pair(RELOAD,RELOAD.replace('lw $v0','lw $v1',1).replace('beqz $v0','beqz $v1').replace('4($v0)','4($v1)')),
     '', 'unsigned int *repair_flags =','void *volatile current','void *current'),
    ('ido_unsigned_halfword_zero_chain',IDO,
     'void f(unsigned char *p) { *(short *)(p+8)=*(int *)(p+4)=*(short *)(p+2)=0; }',
     pair(ZERO,'nop|sh $zero, 2($a0)|lh $v0, 2($a0)|sw $v0, 4($a0)|sh $v0, 8($a0)'),
     '', '*(unsigned short *)(p+8)', 'p+8','p+6'),
    ('stack_load_then_compound_mask',KMC,
     'void f(void) { unsigned int words[4]; init(words); consume((words[0] & 0xFFFFFF)); }',
     pair(MASK,'lui $v1, 0xff|ori $v1, $v1, 0xffff|lw $t0, 16($sp)|and $a0, $t0, $v1|jal consume|nop'),
     '', 'repair_masked_word &= 0xFFFFFF;', '0xFFFFFF','0x7FFFFF'),
    ('paired_float_loads_before_adds',KMC,
     'struct S { float x,y; }; void f(struct S *p) { p->x=p->x+1.0f; p->y=p->y+2.0f; }',
     pair(FLOAT+'|nop','lwc1 $f0, 0($a0)|add.s $f0, $f0, $f4|nop|lwc1 $f2, 4($a0)|'
          'add.s $f2, $f2, $f6|swc1 $f0, 0($a0)|swc1 $f2, 4($a0)'),
     '', 'float repair_loaded_2 = p->y;', 'p->y+2.0f','p->x+2.0f'),
    ('separate_output_store_scopes',KMC,
     'struct S { short a,b,c; }; void f(struct S *p,int x,int y) { '
     'p->a=1; x+=1; y+=2; p->b=x; p->c=y; }',
     pair(STORES,'addiu $a2, $a2, 1|sh $a1, 4($a0)|sh $a2, 6($a0)|addiu $a3, $a3, 2|sh $a3, 8($a0)|nop'),
     '', 'do { p->b=x; p->c=y; } while (0);','p->c=y','p->b=y'),
    ('narrow_after_guarded_record_copy',KMC,
     'struct S { int a,b; }; void f(struct S *p,struct S *q,int count,int yes) { unsigned short n; '
     'n=(unsigned short)count; if (yes) { *p=*q; use(n); } }',
     pair(NARROW,'andi $v0, $a3, 0xffff|beqz $a0, .Lend|nop|lw $t0, 0($a1)|lw $t1, 4($a1)|'
          'sw $t0, 0($a2)|sw $t1, 4($a2)|move $a0, $v0'),
     '', '*p=*q; n=(unsigned short)count;', 'if (yes)','if (n)'),
    ('default_index_through_shared_scale',KMC,
     'unsigned int f(int op) { unsigned int i,out; switch (op) { case 1: i=1; goto scale; '
     'default: out=0; goto done; } scale: out=i<<2; done: return out; }',
     pair(SCALE,SCALE.replace('j .Ldone|sll $v0, $t0, 2','j .Ldone|nop')),assembly(SCALE.replace('|jr','|.Ldone:|jr')),
     'default: i = 0; goto scale;', 'out=i<<2','out=i<<3'),
    ('isolated_unaligned_word_copy',KMC,
     'extern const unsigned char src[16]; void f(void) { unsigned char dst[16]; '
     '*(unsigned int *)(dst+1)=*(const unsigned int *)(src+5); use(dst); }',
     pair(PACKED,'lw $v0, 5($a0)|sw $v0, 1($sp)||'),
     '', '__attribute__((packed))','src[16]','src[6]'),
    ('inline_loop_preheader_parameter_order',KMC,
     'extern unsigned char base; static inline void run(volatile unsigned char *p,int i,int step,int n) '
     '{ for (;i<n;i+=step) p[i]=0; } void f(void) { run(&base,4,2,32); }',
     pair(INLINE,'lui $at, %hi(base)|addiu $a0, $at, %lo(base)|li $t0, 4|li $t1, 2|li $t2, 32|'
          'sb $zero, 0($a0)|addiu $t0, $t0, 2|bne $t0, $t2, .Lloop|nop'),
     assembly(INLINE.replace('|sb','|.Lloop:|sb')), 'run(4, 2, 32, &base);','run(&base,4,2,32)','run(&base,4,2,31)'),
    ('null_success_shared_epilogue',KMC,
     'int f(int *p) { if (!p) return 0; return *p; }',
     pair(NULL,NULL.replace('bnez $a0, .Lsuccess','beql $a0, $zero, .Lend')),
     assembly(NULL.replace('|lw','|.Lsuccess:|lw').replace('|jr','|.Lend:|jr')),
     'repair_return: return repair_result;', 'return 0','return 1'),
]


def route(source,compiler,rows,asm,flags=()):
    return proposals(source,'f','','mips',compiler,rows,asm,big_endian=True,flags=flags)


@pytest.mark.parametrize('item',CASES,ids=[item[0] for item in CASES])
def test_repair_requires_measured_diff_and_safe_source(item):
    family,compiler,source,rows,asm,expected,old,new = item
    found = [text for key,text in route(source,compiler,rows,asm) if key==family]
    assert 1<=len(found)<=2 and any(expected in text for text in found)
    assert family not in {key for key,_ in route(source.replace(old,new),compiler,rows,asm)}
    assert family not in {key for key,_ in route(source,compiler,[(' ',a,a) for _,a,_ in rows],asm)}
    assert family not in {key for key,_ in route(source,KMC if compiler==IDO else IDO,rows,asm)}


def test_packed_copy_respects_strict_aliasing():
    family,compiler,source,rows,asm,*_ = CASES[7]
    assert family not in {key for key,_ in route(source,compiler,rows,asm,('-fstrict-aliasing',))}
