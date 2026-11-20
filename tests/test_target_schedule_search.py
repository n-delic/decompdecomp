"""Exercise measured MIPS routing, with synthetic source and instructions."""

import pytest

from decompnet.app.diff_gated_repairs import proposals


def rows(target, candidate):
    target, candidate = target.split('|'), candidate.split('|')
    assert len(target)==len(candidate)
    return [(' ' if a==b else 'r',a,b) for a,b in zip(target,candidate)]


OFFSET = ('sll $v0, $a0, 2|addu $v0, $v0, $a0|sll $v0, $v0, 2|lw $v1, 4($a1)|'
          'addiu $v0, $v0, -20|addu $v0, $v0, $v1')
CLAMP = 'bc1t .Lfallback|mfc1 $v0, $f0|li $v0, 1|sw $v0, 0($a0)'
ZERO = 'slt $at, $a2, $a1|bnel $at, $zero, .Lend|move $v0, $zero|beqz $a0, .Lend|nop|jr $ra|nop'
SELECT = 'lbu $v0, %lo(table)($a0)|lhu $v1, 4($a1)|beqz $v0, .Lelse|ori $v0, $v1, 0x80|ori $v0, $v1, 0x40'
SIGNED = 'andi $v0, $a0, 0xffff|slt $at, $v0, $a1|bnez $at, .Lloop|nop'
VOLATILE = 'lw $v0, %lo(table)($at)|sll $v1, $a0, 2|addu $v0, $v1, $v0'


CASES = [
    ('offset_initializer_final_shift',
     'int *f(int index,int **bases) { int offset; int *base; offset=(index<<2)+index; '
     'base=bases[1]; offset=(offset<<2)-20; return base+offset; }',
     rows(OFFSET,OFFSET.replace('$v0','$t0').replace('$v1','$v0').replace('$t0','$v1')),
     'offset=offset-20;', 'base=bases[1];', 'base=bases[offset];'),
    ('clamp_fallback_on_store_edge',
     'void f(float x,unsigned int *out) { unsigned int value; value=1; if (x > 0.0f) { '
     'value=(unsigned int)x; } *out=value; }',
     rows(CLAMP,CLAMP.replace('li $v0','li $v1').replace('sw $v0','sw $v1')),
     'else { value = 1; }', 'x > 0.0f', 'value != 0'),
    ('common_zero_result_label',
     'int f(int bound,int node) { int result; if (bound < node) { result=0; goto done; } '
     'if (node != 0) { result=node; goto done; } result=0; done: return result; }',
     rows(ZERO,ZERO.replace('bnel $at, $zero,','bnez $at,')),
     'goto repair_absent;', 'result=0; done:', 'result=1; done:'),
    ('selector_byte_before_halfword',
     'extern unsigned char table[]; int f(int i,unsigned short *src) { int half,b,out; half=src[1]; b=table[i]; '
     'if (b) { out=half|0x80; } else { out=half|0x40; } return out; }',
     rows(SELECT,'lhu $v1, 4($a1)|lbu $v0, %lo(table)($a0)|beqz $v0, .Lelse|ori $a1, $v1, 0x80|ori $a1, $v1, 0x40'),
     '((volatile unsigned char *)table)[i]', 'src[1]', 'src[b]'),
    ('float_output_store_boundary',
     'struct Pair { float x; float y; }; void f(struct Pair *out,float a,float b) { out->x=a; out->y=b; }',
     rows('mul.s $f8, $f0, $f1|swc1 $f8, 0($a0)|swc1 $f4, 4($a0)',
          'swc1 $f2, 0($a0)|mul.s $f2, $f0, $f1|swc1 $f4, 4($a0)'),
     'do {} while (0);','out->y=b','out->x=b'),
    ('signed_masked_loop_bound',
     'extern int bound; void f(unsigned int i) { do { i++; } while ((i & 0xffff) < bound); }',
     rows(SIGNED,SIGNED.replace('slt $at','sltu $at')),
     '(int)(unsigned short)i < bound','extern int bound','extern unsigned int bound'),
    ('previous_record_index_before_scaling',
     'struct Item { int x[3]; }; struct Item *f(struct Item *p,int i) { return &p[i - 1]; }',
     rows('addiu $v0, $a1, -1|sll $v1, $v0, 1|addu $v1, $v1, $v0|sll $v1, $v1, 2|addu $v0, $a0, $v1',
          'sll $v0, $a1, 1|addu $v0, $v0, $a1|sll $v0, $v0, 2|addiu $v1, $v0, -12|addu $v0, $a0, $v1'),
     '(long)(i - 1) * (long)sizeof(*p)', 'i - 1', 'i + 1'),
    ('guarded_float_numerator',
     'void f(float *p,int n,float scale) { int i; if (n > 0) { for (i=0;i<n;i++) { p[i]=1.0f / scale; } } }',
     rows('blez $a1, .Lend|nop|lui $at, 0x3f80|mtc1 $at, $f0|lw $v1, 4($a0)|div.s $f2, $f0, $f12',
          'blez $a1, .Lend|nop|lw $v1, 4($a0)|lui $at, 0x3f80|mtc1 $at, $f0|div.s $f2, $f0, $f12'),
     'float repair_one = 1.0f;','n > 0','n != 0'),
    ('computed_float_word_store',
     'struct Out { float x; }; void f(struct Out *out,float a,float b) { out->x=a*b; }',
     rows('mul.s $f2, $f0, $f4|mfc1 $v0, $f2|sw $v0, 0($a0)',
          'mul.s $f2, $f0, $f4||swc1 $f2, 0($a0)'),
     'repair_float_word.bits','float x','double x'),
    ('volatile_record_word_address',
     'typedef struct { int field; } Item; extern Item *volatile table; '
     'Item *f(int index) { Item *base; base=table; return base + index; }',
     rows(VOLATILE,'sll $v1, $a0, 2|lw $v0, %lo(table)($at)|addu $v0, $v0, $v1'),
     '(unsigned int)sizeof(*base)', '*volatile table', '*table'),
]


def suggest(text,diff,compiler='gcc-2.7.2-kmc',**options):
    return proposals(text,'f','','mips',compiler,diff,'',**options)


@pytest.mark.parametrize('case',CASES,ids=[case[0] for case in CASES])
def test_requires_diff_and_eligible_source(case):
    family,source,diff,expected,from_,to = case
    results = [text for kind,text in suggest(source,diff) if kind==family]
    assert len(results)==1 and expected in results[0]
    for wrong_source,wrong_diff,compiler in (
        (source,[(' ',left,left) for _,left,_ in diff],'gcc-2.7.2-kmc'),
        (source.replace(from_,to),diff,'gcc-2.7.2-kmc'),
        (source,diff,'ido-5.3'),
    ):
        assert family not in {kind for kind,_ in suggest(wrong_source,wrong_diff,compiler)}


def test_float_word_store_respects_aliasing_flag():
    family,source,diff,*_ = CASES[-2]
    assert family not in {kind for kind,_ in suggest(source,diff,flags=('-fstrict-aliasing',))}
