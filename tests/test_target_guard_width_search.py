import pytest

from decompnet.app.diff_gated_repairs import proposals


def paired(a,b):
    a,b = a.split('|'),b.split('|')
    assert len(a)==len(b)
    return [(' ' if x==y else 'r',x,y) for x,y in zip(a,b)]


def asm(text):
    return '\n'.join(line if line.endswith(':') else f'/* {4*i:06X} {0x80001000+4*i:08X} 00000000 */ {line}'
                     for i,line in enumerate(text.split('|')))


NULL = 'lw $v0, 4($a1)|beqz $v0, .Lexit|move $a0, $v0|lh $v1, 2($a0)|lw $v0, 4($a0)'
FLOAT = ('lwc1 $f2, 4($a1)|c.lt.s $f0, $f2|bc1t .Ljoin|nop|lwc1 $f2, 4($a1)|'
         'c.lt.s $f2, $f4|bc1f .Lexit|nop')
LITERAL = 'lui $v0, 0xa000||or $a0, $a0, $v0'
OR = ('andi $v0, $a0, 0xff|sltu $v0, $zero, $v0|andi $v1, $a1, 0xff|sltu $v1, $zero, $v1|'
      'or $v0, $v0, $v1|bnez $v0, .Lend')
CURSOR = ('move $a0, $a1|sb $zero, 508($a0)|sb $zero, 511($a0)|addiu $a0, $a0, 512|'
          'bne $a0, $a2, .Lloop|nop')
FLAG = 'andi $v0, $a0, 1|move $s0, $v0|andi $v0, $v0, 255|bnez $v0, .Lend'
GUARD = 'jal get|nop|beqz $v0, .Lend|li $v1, 100|sh $a1, 4($v0)|sb $v1, 6($v0)|sb $v1, 7($v0)'
SEED = ('li $v0, 1|sltu $at, $v0, $a0|beqz $at, .Lend|li $t0, 1|addiu $t0, $t0, 1|'
        'bne $t0, $a0, .Lloop|nop')
SIGNED = ('bltz $v1, .Ladjust|nop|addiu $a0, $a0, -1|subu $a0, $zero, $a0|sb $a0, 0($a1)')

CASES = [
    ('nonnull_guard_pointer_local',
     'struct S { short a; int b; }; int f(struct S *p) { if (p != 0) { return p->a+p->b; } return 0; }',
     paired(NULL,NULL.replace('lw $v0,','lw $a0,',1).replace('beqz $v0','beqzl $a0').replace('move $a0, $v0','nop')),
     '', 'struct S *repair_body_pointer = p;', 'p->a+p->b','p->a'),
    ('fallthrough_float_field_reload',
     'struct S { float x; }; int f(struct S *p,float a,float b) { if (a<p->x && p->x<b) { return 1; } return 0; }',
     paired(FLOAT,FLOAT.replace('nop|lwc1 $f2, 4($a1)','nop|nop')),
     asm(FLOAT.replace('|c.lt.s $f2','|.Ljoin:|c.lt.s $f2')), 'repair_compare_field = p->x,',
     'p->x<b','p->y<b'),
    ('resolved_high_bit_mask_literal',
     'extern unsigned int mask; unsigned int f(unsigned int x) { return x | (unsigned int)&mask; }',
     paired(LITERAL,'lui $v0, %hi(mask)|addiu $v0, $v0, %lo(mask)|or $a0, $a0, $v0'),
     '', 'x | 0xA0000000U', 'x |','x +'),
    ('normalize_masked_operands_before_or',
     'int f(int x,int y) { if ((x&255)|(y&255)) { return 1; } return 0; }',
     paired(OR,OR.replace('sltu $v0, $zero, $v0','').replace('sltu $v1, $zero, $v1','')),
     '', '(x & 255) != 0', 'y&255','y&127'),
    ('unbiased_record_cursor_backedge',
     'void f(unsigned char *p,unsigned char *end) { do { *(p+508)=0; *(p+511)=0; p+=512; } while (p!=end); }',
     paired(CURSOR,CURSOR.replace('move $a0, $a1','addiu $a0, $a1, 511').replace('508($a0)','-3($a0)')
                         .replace('511($a0)','0($a0)')),
     asm(CURSOR.replace('|sb $zero, 508','|.Lloop:|sb $zero, 508')),
     'if (p!=end) goto repair_record_loop;', '*(p+511)=0;', 'if (*(p+511)) continue;'),
    ('byte_flag_assignment_in_test',
     'int f(int x) { unsigned char flag; flag=x&1; if (flag!=0) { return 1; } return 0; }',
     paired(FLAG,'andi $s0, $a0, 1|nop|andi $v0, $s0, 255|bnez $v0, .Lend'),
     '', 'if ((flag = x&1)!=0)', 'unsigned char flag','unsigned int flag'),
    ('null_guard_repeated_store_immediate',
     'struct S { short c; unsigned char x,y; }; void f(struct S *p,int n) { '
     'if (p) { p->c=n; p->x=100; p->y=100; } }',
     paired(GUARD,GUARD.replace('li $v1, 100','nop')),
     '', 'if ((repair_store_byte = 100, p) != 0)', 'p->y=100','p->y=99'),
    ('entry_seed_and_induction_split',
     'void f(unsigned int n) { unsigned int i; i=1; if (i<n) { do { i++; } while (i<n); } }',
     paired(SEED,SEED.replace('li $t0, 1','nop')),
     '', 'repair_entry_seed = 1;', 'i=1;','i=2;'),
    ('ordinary_local_halfword_read',
     'int f(int y) { unsigned short value=y; return *(volatile unsigned short *)&value; }',
     paired('lhu $v0, 16($sp)|nop|jr $ra','lhu $v0, 16($sp)|andi $v0, $v0, 0xffff|jr $ra'),
     '', '*(unsigned short *)&value', 'unsigned short value','volatile unsigned short value'),
    ('signed_byte_adjustment_local',
     'void f(unsigned int raw,unsigned char *out) { unsigned int v; '
     'if ((signed char)raw<0) { v=raw; v--; v=-v; out[0]=(unsigned char)v; } }',
     paired(SIGNED,SIGNED.replace('addiu $a0, $a0,','addiu $v0, $a0,').replace('subu $a0, $zero, $a0','subu $v0, $zero, $v0')
                         .replace('sb $a0','sb $v0')),
     '', 'signed char repair_adjusted_byte=raw;', 'v=-v;','v+=v;'),
]


def route(source,diff,assembly,*,compiler='gcc-2.7.2-kmc',resolve=None):
    return proposals(source,'f','','mips',compiler,diff,assembly,
                     resolve=resolve if resolve is not None else {'mask':0xa0000000}.get)


@pytest.mark.parametrize('item',CASES,ids=[item[0] for item in CASES])
def test_proposal_requires_diff_and_source(item):
    family,source,diff,assembly,expected,old,new = item
    options = [text for key,text in route(source,diff,assembly) if key==family]
    assert 1<=len(options)<=2 and any(expected in text for text in options)
    assert family not in {key for key,_ in route(source.replace(old,new),diff,assembly)}
    assert family not in {key for key,_ in route(source,[(' ',a,a) for _,a,_ in diff],assembly)}
    assert family not in {key for key,_ in route(source,diff,assembly,compiler='ido-5.3')}


def test_high_mask_requires_resolved_low_half_zero():
    family,source,diff,assembly,*_ = CASES[2]
    for resolver in ({'mask':0xa0000004}.get,{}.get):
        assert family not in {key for key,_ in route(source,diff,assembly,resolve=resolver)}
