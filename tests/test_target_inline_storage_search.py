import pytest

from decompnet.app.diff_gated_repairs import proposals


def diff(a,b):
    a,b = a.split('|'),b.split('|')
    assert len(a)==len(b)
    return [(' ' if x==y else 'r',x,y) for x,y in zip(a,b)]


def asm(text):
    return '\n'.join(line if line.endswith(':') else f'/* {4*i:06X} {0x80001000+4*i:08X} 00000000 */ {line}'
                     for i,line in enumerate(text.split('|')))


RETURN = ('move $s0, $zero|beqz $a0, .Lreturn|move $v0, $s0|beqz $a1, .Lreturn|move $v0, $s0|'
          'sll $v1, $s0, 16|sra $v0, $v1, 16|jr $ra|nop')
ADDRESS = 'sll $v0, $a1, 2|addu $v0, $v0, $a0|lbu $v0, 0($v0)|jr $ra|nop'
FLOAT = 'lwc1 $f20, 4($a0)|jal use|nop|c.lt.s $f20, $f4|bc1t .Ldone|nop'
ENTRY = 'lbu $v0, 0($a0)|blez $v0, .Lexit|nop|addiu $v1, $v1, 1'
COORDS = ('lwc1 $f0, 0($a0)|lwc1 $f2, 0($a1)|sub.s $f0, $f0, $f2|'
          'lwc1 $f4, 4($a0)|lwc1 $f6, 4($a1)|sub.s $f4, $f4, $f6|'
          'lwc1 $f8, 8($a0)|lwc1 $f10, 8($a1)|sub.s $f8, $f8, $f10')
VOLATILE = 'lui $at, %hi(current)|lw $v0, %lo(current)($at)|nop|sw $a0, 0($v0)|lw $v0, %lo(current)($at)|nop'
CALLS = ('beqz $a2, .Lelse|move $a1, $a3|andi $a0, $a3, 0xffff|jal g|nop|'
         'andi $a0, $a3, 0xffff|jal h|nop|andi $a0, $a3, 0xffff|jal g|nop|'
         'andi $a0, $a3, 0xffff|jal h|nop')
SWITCH = ('slti $at, $a0, 3|bnez $at, .Lend|nop|li $v0, 2|beq $a0, $v0, .Lend|nop|'
          'li $v0, 3|beq $a0, $v0, .Lwork|nop|b .Lend|nop|jr $ra|nop|jal call|nop')
MULT = ['li $t0, 0x3333']
EXPANDED_MULT = ['nop']
for register in ('$a0','$a1','$a2'):
    MULT.extend([f'mult {register}, $t0','mflo $v0',*['']*6])
    EXPANDED_MULT.extend([
        f'sll $v0, {register}, 1',f'addu $v0, $v0, {register}','sll $t1, $v0, 4','addu $t1, $t1, $v0',
        'sll $t2, $t1, 4','addu $t2, $t2, $v0','sll $t2, $t2, 4','addu $t2, $t2, $v0',
    ])

FLOAT_STORE = 'lui $t0, 0xc4ea|ori $t0, $t0, 0xaaab|mtc1 $t0, $f0|swc1 $f0, 4($a0)'


CASES = [
    ('inline_shared_signed_halfword_return',
     'static inline int h(int x,int y) { if (!x) return 0; if (!y) return 0; return y; } '
     'int f(int a,int b) { return (short)h(a,b); }',
     diff(RETURN,RETURN.replace('move $v0, $s0','move $v0, $zero')),
     asm(RETURN.replace('|sll','|.Lreturn:|sll')), 'repair_return: return (short)repair_status;',
     '(short)h(a,b)','h(a,b)'),
    ('split_indexed_byte_address',
     'int f(const unsigned char *p,int i) { return *(p + i*4); }',
     diff(ADDRESS,ADDRESS.replace('addu $v0, $v0, $a0','addu $v0, $a0, $v0')),
     '', 'repair_byte_address += i*4;', 'unsigned char *p','unsigned short *p'),
    ('reuse_dead_float_across_call',
     'void use(void); float f(float x,float y) { float a,b; a=x*y; record(a); b=x-y; use(); return b; }',
     diff(FLOAT,FLOAT.replace('$f20','$f22')), '', 'a=x-y; use(); return a;',
     'return b;','return b+a;'),
    ('zero_index_unsigned_byte_entry',
     'void f(unsigned char count) { int i; i=0; if (!count) return; while (i<count) { ++i; } }',
     diff(ENTRY,ENTRY.replace('blez','beqz')), '', 'if (i >= count)', 'unsigned char count','signed char count'),
    ('coordinate_difference_scopes',
     'struct P { float x,y,z; }; float f(struct P *p,struct P *q) { float x,y,z; '
     'x=p->x-q->x; y=p->y-q->y; z=p->z-q->z; return x+y+z; }',
     diff(COORDS,COORDS.replace('sub.s $f0, $f0, $f2|lwc1 $f4, 4($a0)',
                                'lwc1 $f4, 4($a0)|sub.s $f0, $f0, $f2')),
     '', 'do { x=p->x-q->x; } while (0);', 'float x,y,z; };','double x,y,z; };'),
    ('direct_volatile_pointer_object',
     'extern const struct P *current; void f(void) { const struct P *a,*b; '
     'a=*(const struct P *volatile *)&current; use(a); b=*(const struct P *volatile *)&current; use(b); }',
     diff(VOLATILE,'lui $at, %hi(current)|addiu $at, $at, %lo(current)|lw $v0, 0($at)|'
          'sw $a0, 0($v0)|addiu $at, $at, %lo(current)|lw $v0, 0($at)'),
     '', 'extern const struct P *volatile current;', 'use(b);','use(b); use(current);'),
    ('narrow_index_at_calls_in_both_arms',
     'void g(unsigned short); void h(unsigned short); void f(int x,int flag) { int n; '
     'if (flag) { n=(unsigned short)x; g(n); h(n); } else { n=(unsigned short)x; g(n); h(n); } }',
     diff(CALLS,CALLS.replace('beqz $a2, .Lelse|move $a1, $a3','bne $a2, $a1, .Lelse|andi $s0, $a3, 0xffff')
          .replace('andi $a0, $a3, 0xffff','move $a0, $s0')),
     '', 'g( (unsigned short)x);', 'h(unsigned short)','h(unsigned int)'),
    ('explicit_noop_switch_case',
     'void f(int n) { switch (n) { case 2: break; case 3: call(); break; default: break; } }',
     diff(SWITCH,SWITCH.replace('slti $at, $a0, 3','nop')),
     asm(SWITCH.replace('|jr','|.Lend:|jr').replace('|jal call','|.Lwork:|jal call')), 'case 1: default: break;', 'default: break;','default: call(); break;'),
    ('shared_literal_multiplier',
     'int f(int a,int b,int c) { return a*0x3333 + b*0x3333 + c*0x3333; }',
     diff('|'.join(MULT),'|'.join(EXPANDED_MULT)),
     '', 'int repair_multiplier = 0x3333;', 'c*0x3333','c*0x3332'),
    ('typed_float_literal_store',
     'struct P { float x; }; void f(struct P *p) { *(unsigned int *)&p->x=0xC4EAAAAB; }',
     diff(FLOAT_STORE,FLOAT_STORE.replace('mtc1 $t0, $f0','nop').replace('swc1 $f0','sw $t0')),
     '', 'p->x = -1877.33337f;', 'float x','unsigned int x'),
]


def route(source,rows,assembly,compiler='gcc-2.7.2-kmc'):
    return proposals(source,'f','','mips',compiler,rows,assembly,big_endian=True)


@pytest.mark.parametrize('item',CASES,ids=[item[0] for item in CASES])
def test_diff_and_source_gates(item):
    family,source,rows,assembly,expected,old,new = item
    found = [text for key,text in route(source,rows,assembly) if key==family]
    assert 1<=len(found)<=2 and any(expected in text for text in found)
    assert family not in {key for key,_ in route(source.replace(old,new),rows,assembly)}
    assert family not in {key for key,_ in route(source,[(' ',a,a) for _,a,_ in rows],assembly)}
    assert family not in {key for key,_ in route(source,rows,assembly,'ido-5.3')}
