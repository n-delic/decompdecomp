"""Same-type compound staging preserves conversions and rejects observable reads."""
import pytest

from decompnet.app import draft_repairs
from decompnet.app import loaded_byte_repairs as repairs

HEADER = 'typedef unsigned char u8; typedef unsigned int u32;'
SOURCE = '''extern u8 values[5];
void test(void) {
 u32 j;
 for(j=0;j<4;j++) {
  values[j] += values[j] >> 1;
 }
 for(j=0;j<4;j++) {
  values[j] += (values[j] * values[4]) / 100;
 }
}'''


def generated(source=SOURCE, header=HEADER, arch='mips'):
    return repairs.variants(source, 'test', header, arch)


def test_pair_before_singles_keeps_byte_conversion_and_memory_order():
    candidates = generated()
    assert len(candidates) == 3
    family, text = candidates[0]
    assert family == repairs.FAMILY
    assert text.count('u8 __repair_loaded_byte;') == 1
    assert '__repair_loaded_byte = values[j]; __repair_loaded_byte += __repair_loaded_byte >> 1; values[j] = __repair_loaded_byte;' in text
    assert '__repair_loaded_byte += (__repair_loaded_byte * values[4]) / 100;' in text
    assert [text.count('__repair_loaded_byte = values[j]') for _,text in candidates] == [2,1,1]
    labels = {}
    shortlisted = draft_repairs.variants(SOURCE, 'test', 'mips', header=HEADER, labels=labels)
    assert candidates[0][1] in shortlisted
    assert labels[candidates[0][1]] == repairs.FAMILY
    assert repairs.FAMILY in draft_repairs.AGENT_FAMILIES


@pytest.mark.parametrize('before,after', [
    ('extern u8 values[5];','extern volatile u8 values[5];'),
    ('extern u8 values[5];','extern _Atomic u8 values[5];'),
    ('extern u8 values[5];','extern u8 *values;'),
    ('extern u8 values[5];','extern signed char values[5];'),
    (' u32 j;',' u32 j; volatile u8 *values;'),
    (' u32 j;',' u32 j; u8 values[5];'),
    (' u32 j;',' u32 j; { u8 *values; }'),
    ('values[j] >> 1','values[j] >> 8'),
    ('values[j] >> 1','values[j] >> 0'),
    ('values[j] >> 1','values[j] >> shift()'),
    ('values[j] >> 1','values[j] >> other'),
    ('values[j]','values[j++]'),
    ('values[j]','values[next()]'),
])
def test_rejected_first_site_is_not_staged(before,after):
    source = SOURCE.replace(before,after)
    candidates = generated(source)
    assert all('+= __repair_loaded_byte >>' not in text for _,text in candidates)


@pytest.mark.parametrize('factor', ['other[4]','values[next()]','values[unknown]','values[4] + call()'])
def test_unknown_or_effectful_factor_is_not_staged(factor):
    source = SOURCE.replace('values[j] * values[4]', 'values[j] * '+factor)
    assert all('+= (__repair_loaded_byte *' not in text for _,text in generated(source))


@pytest.mark.parametrize('divisor', ['0','2147483648','divisor','divide()'])
def test_division_requires_positive_bounded_literal(divisor):
    source = SOURCE.replace('/ 100','/ '+divisor)
    assert all('+= (__repair_loaded_byte *' not in text for _,text in generated(source))


@pytest.mark.parametrize('header', [HEADER+'\n#define values other', HEADER+'\n#define j next()',
                                   'typedef volatile unsigned char u8; typedef unsigned int u32;',
                                   '#define u8 unsigned char\ntypedef unsigned int u32;'])
def test_macros_and_qualified_aliases(header):
    assert not generated(header=header)


def test_unknown_architecture_and_conflicting_declarations():
    assert not generated(arch='unknown')
    assert not generated(SOURCE.replace('extern u8 values[5];','extern u8 values[5]; extern volatile u8 values[5];'))


def test_whitespace_does_not_leave_repeated_rhs_load():
    source = SOURCE.replace('values[j] += values[j] >> 1;', 'values [ j ] += values[j] >> 1;')
    assert '__repair_loaded_byte += __repair_loaded_byte >> 1;' in generated(source)[0][1]


def test_collision_and_bounded_sampling():
    source = SOURCE.replace(' u32 j;', ' u32 j; u8 __repair_loaded_byte;')
    assert 'u8 __repair_loaded_byte_;' in generated(source)[0][1]
    many = SOURCE.replace('  values[j] += values[j] >> 1;', '  values[j] += values[j] >> 1;\n'*20)
    assert len(generated(many)) <= 10


def test_compiled_original_and_generated_byte_conversions_exhaustively(tmp_path):
    import shutil
    import subprocess

    compiler = shutil.which('cc')
    if not compiler:
        pytest.skip('a host C compiler is unavailable')
    original = SOURCE.replace('values','left').replace('test(', 'original(')
    changed = generated()[0][1].replace('values','right').replace('test(', 'changed(')
    main = '''
u8 left[5], right[5];
int main(void) {
 unsigned value, factor, i;
 for(value=0;value<256;value++) for(factor=0;factor<256;factor++) {
  for(i=0;i<4;i++) left[i]=right[i]=(u8)(value+i);
  left[4]=right[4]=(u8)factor;
  original(); changed();
  for(i=0;i<5;i++) if(left[i]!=right[i]) return 1;
 }
 return 0;
}'''
    source = tmp_path/'comparison.c'
    source.write_text(HEADER+'\n'+original+'\n'+changed+'\n'+main)
    executable = tmp_path/'comparison'
    subprocess.run([compiler,'-std=c89','-O2',str(source),'-o',str(executable)],check=True,capture_output=True,timeout=30)
    subprocess.run([str(executable)],check=True,capture_output=True,timeout=30)


@pytest.mark.parametrize('arch', ['mips','ppc','x86','arm','thumb'])
def test_known_32_bit_target_type_adapters(arch):
    assert generated(arch=arch)


def test_comments_within_a_load_keep_one_evaluation():
    source = SOURCE.replace('values[j] >> 1','values /* ordinary */ [j] >> 1')
    text = generated(source)[0][1]
    assert '__repair_loaded_byte += __repair_loaded_byte >> 1;' in text
