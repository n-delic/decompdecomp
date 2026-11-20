"""The ordinary deep repair router needs both the measured diff and a safe source shape."""

import struct

import pytest

from decompnet.app.diff_gated_repairs import proposals

KMC = 'gcc-2.7.2-kmc'


def diff(left, right):
    a, b = left.split('|'),right.split('|')
    assert len(a)==len(b)
    return [(' ' if x==y else 'r',x,y) for x,y in zip(a,b)]


def asm(lines):
    return '\n'.join(line if line.endswith(':') else f'/* {4*i:06X} {0x80001000+4*i:08X} 00000000 */ {line}'
                     for i,line in enumerate(lines.split('|')))


DIAMOND = ('beqz $a0, .Lelse|nop|jal process|nop|xori $v0, $s0, 32|sltu $v0, $zero, $v0|b .Lend|nop|'
           '.Lelse:|xori $v0, $s0, 32|sltu $v0, $zero, $v0|.Lend:|jr $ra|nop')
FLOAT_PACK = ('trunc.w.s $f0, $f12|mfc1 $a0, $f0|trunc.w.s $f2, $f14|mfc1 $a1, $f2|'
              'sll $v0, $a0, 16|or $v0, $v0, $a1')
SWITCH = ('jal get|nop|mult $v0, $v1|mfhi $v0|sll $t0, $v0, 1|addu $t0, $t0, $v0|sll $t0, $t0, 1|'
          'subu $v1, $a0, $t0|sltiu $at, $v1, 6|beqz $at, .Ldefault|sll $t1, $v1, 2|jr $t2|nop')
IDO = 'lw $t0, 0x28($sp)|sw $zero, 0x14($a0)|jal submit|sw $t0, 0x18($a0)'
SWAP = 'mul.s $f4, $f0, $f12|mul.s $f6, $f2, $f12|sub.s $f0, $f4, $f6|swc1 $f4, 0($a0)|swc1 $f6, 4($a0)'
SHIFT = ('mtc1 $a0, $f0|bgez $a0, .Ldone|srl $v1, $a0, 22|cvt.s.w $f0, $f0|add.s $f0, $f0, $f4|'
         'nop|addu $v0, $a1, $v1')
TABLE = ('lui $v0, %hi(table)|addu $v0, $v0, $a1|lb $v0, %lo(table)($v0)|nop|'
         'lui $v1, %hi(table)|addu $v1, $v1, $a2|lb $v1, %lo(table)($v1)|bnez $a0, .Lloop')
SIGNED = ('subu $v0, $a0, $a2|sltu $at, $a0, $a2|subu $v1, $a1, $a3|subu $v1, $v1, $at|'
          'slt $a0, $v1, $s1|bne $v1, $s1, .Ldifferent|nop|sltu $a0, $v0, $s0')
BIAS = ('cvt.d.w $f0, $f0|bgez $a0, .Ldone|lui $at, %hi(bias)|ldc1 $f2, %lo(bias)($at)|'
        'add.d $f0, $f0, $f2')


CASES = [
    ('conditional_call_inequality', KMC,
     'int f(int word, int cond) { int temp; if (cond) { process(); temp=word^32; } else { temp=word^32; } '
     'return temp != 0; }',
     diff('jal process|xori $v0, $s0, 32|sltu $v0, $zero, $v0',
          'jal process|xori $v1, $s0, 32|li $v0, 1'), asm(DIAMOND), 'return word != 32;'),
    ('float_to_int_argument_carriers', KMC,
     'int f(int a,int b,float x,float y) { int p,q; p=(int)x; q=(int)y; return (p << 16) | q; }',
     diff(FLOAT_PACK,FLOAT_PACK.replace('$a0','$t0').replace('$a1','$t1')), '', 'return (a << 16) | b;'),
    ('independent_float_product_order', KMC,
     'float f(float a,float b,float c) { float x,y; x=-a*c; y=b*c; return x-y; }',
     diff(SWAP,SWAP.replace('$f4','$f99').replace('$f6','$f4').replace('$f99','$f6')), '',
     'y=b*c; x=-a*c;'),
    ('in_place_modulo_switch_selector', KMC,
     'extern int get(int x); int f(int x) { int v; v=get(x)%6; switch (v) { default: return 0; } }',
     diff(SWITCH,SWITCH.replace('subu $v1','subu $t0').replace('$at, $v1','$at, $t0').replace('$t1, $v1','$t1, $t0')),
     '', 'v = get(x); switch (v %= 6)'),
    ('ido_reload_before_constant_store','ido-5.3',
     'struct State { int clear; int value; }; void f(struct State *p,int arg) { p->clear=0; p->value=arg; submit(); }',
     diff(IDO,'sw $zero, 0x14($a0)|lw $t0, 0x28($sp)|sw $t0, 0x18($a0)|jal submit'),
     '', 'p->value=arg; p->clear=0; submit();'),
    ('reciprocal_in_float_parameter',KMC,
     'void f(float a,float *array) { int i; for (i=0;i<4;i++) { array[i] *= (1.0f / a); } }',
     diff('div.s $f26, $f2, $f26|mul.s $f0, $f0, $f26','div.s $f6, $f2, $f26|mul.s $f0, $f0, $f6'),
     '', 'a = 1.0f / a; for'),
    ('table_index_before_float_conversion',KMC,
     'float f(unsigned int word) { float value; int index; value=(float)(word & 0xffffU); index=word>>22; '
     'return samples[index]+value; }',
     diff(SHIFT,'mtc1 $a0, $f0|bgezl $a0, .Ldone|nop|cvt.s.w $f0, $f0|add.s $f0, $f0, $f4|'
          'srl $v1, $a0, 22|addu $v0, $a1, $v1'), '', 'index = word >> 22; value = (float)'),
    ('one_strided_table_byte_offset',KMC,
     'extern signed char table[][12]; int f(int row,int col) { int i; int sum=0; for (i=0;i<4;i++) { '
     'sum += table[row][col]; sum += table[i][1]; } return sum; }',
     diff(TABLE,'sw $s0, 0($sp)|addiu $s0, %lo(table)($at)|lb $v0, 0($s0)|addiu $s0, $s0, 12|'
          'lui $v1, %hi(table)|addu $v1, $v1, $a2|lb $v1, %lo(table)($v1)|bnez $a1, .Lloop'),
     '', '((signed char *)&table)[(long)row * 12 + (long)col]'),
    ('signed_high_word_bound',KMC,
     'int f(unsigned long long x,long long y,long long bound) { return (x-y)<bound; }',
     diff(SIGNED,SIGNED.replace('|slt $a0','|sltu $a0')), '', '(long long)((x-y))<bound'),
    ('named_unsigned_double_bias',KMC,
     'extern const double bias; double f(unsigned int x) { double v; v=(double)x; return v; }',
     diff(BIAS,BIAS.replace('(bias)','(.rodata+8)')), '', 'v += bias;'),
]


def run(case, *, overrides=None):
    _,compiler,source,rows,assembly,_ = case
    args = dict(source=source,name='f',header='',arch='mips',toolchain=compiler,rows=rows,asm=assembly,
                big_endian=True,read_data=lambda name,size:struct.pack('>d',2.0**32) if (name,size)==('bias',8) else None)
    return proposals(**(args | (overrides or {})))


@pytest.mark.parametrize('case',CASES,ids=[case[0] for case in CASES])
def test_repair_only_triggers_on_its_measured_difference(case):
    family,_,_,rows,_,expected = case
    selected = [new for kind,new in run(case) if kind==family]
    assert 1<=len(selected)<=2
    assert any(expected in new for new in selected)
    assert family not in {kind for kind,_ in run(case,overrides={'rows':[(' ',a,a) for _,a,_ in rows]})}
    assert family not in {kind for kind,_ in run(case,overrides={'toolchain':'gcc-egcs'})}
    assert family not in {kind for kind,_ in run(case,overrides={'arch':'ppc'})}


@pytest.mark.parametrize('index,old,new',[
    (0,'process();','process(&word);'),
    (1,'| q;','| q | a;'),
    (2,'y=b*c;','y=x*c;'),
    (3,'extern int get','extern unsigned get'),
    (4,'p->value=arg;','p->clear=arg;'),
    (5,'i<4','i<0'),
    (6,'word>>22','word>>21'),
    (7,'sum += table[i][1];','table[i][1] = 0;'),
    (8,'long long bound','unsigned long long bound'),
    (9,'extern const','extern'),
])
def test_repair_checks_source_preconditions(index,old,new):
    case = CASES[index]
    assert case[0] not in {kind for kind,_ in run(case,overrides={'source':case[2].replace(old,new)})}


def test_named_bias_requires_the_target_bytes():
    case = CASES[-1]
    for read in (None,lambda *_:b'\x00'*8,lambda *_:struct.pack('>d',2.0**31)):
        assert case[0] not in {kind for kind,_ in run(case,overrides={'read_data':read})}
