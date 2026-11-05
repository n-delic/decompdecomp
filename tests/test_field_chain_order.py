"""Equal field conversions permit one bounded chain alternative, without moving its RHS."""
import pytest

from decompnet.app import draft_repairs, field_order_repairs

SOURCE = '''typedef struct { short first; short second; float unused; } Record;
void work(Record *out, int value) {
 out->first =
 out->second = value / 3;
}
'''


def chains(source=SOURCE, header=''):
    return [text for family,text in field_order_repairs.variants(source,'work',header)
            if family=='equal_type_field_chain_order']


def test_equal_field_chain_preserves_rhs_and_lines():
    candidates=chains()
    assert len(candidates)==1
    assert candidates[0]==SOURCE.replace('out->first =\n out->second', 'out->second =\n out->first')
    assert candidates[0].count('value / 3')==1
    assert candidates[0].count('\n')==SOURCE.count('\n')
    labels={}
    first=draft_repairs.variants(SOURCE,'work','mips',labels=labels)
    assert candidates[0] in first
    assert labels[candidates[0]]=='equal_type_field_chain_order'


@pytest.mark.parametrize('before,after',[
    ('short second;', 'unsigned short second;'),
    ('short second;', 'short second[2];'),
    ('short first;', 'volatile short first;'),
    ('typedef struct', 'typedef union'),
    ('out->second = value / 3', 'out->first = value / 3'),
    ('value / 3', 'next(value)'),
    ('value / 3', '(next)(value)'),
    ('value / 3', '(*next)(value)'),
    ('value / 3', '(value <<= 1)'),
    ('value / 3', '(value >>= 1)'),
    ('value / 3', '(out = other)->first'),
    ('value / 3', 'value++'),
    ('value / 3', '(value, 3)'),
    (' out->first =', ' escape(&out); out->first ='),
])
def test_unsafe_chain_is_rejected(before,after):
    assert not chains(SOURCE.replace(before,after))


def test_macros_and_qualified_alias_are_rejected():
    assert not chains(SOURCE, '#define value opaque_value\n')
    assert not chains(SOURCE.replace('short first; short second;', 'Word first; Word second;'),
                      'typedef volatile short Word;')


def test_float_alias_in_other_field_does_not_hide_plain_record():
    assert chains(SOURCE.replace('float unused;', 'f32 unused;'), 'typedef float f32;')


def test_generated_chain_preserves_narrowing_in_c(tmp_path):
    import shutil
    import subprocess

    cc=shutil.which('cc')
    if not cc:
        pytest.skip('host C compiler unavailable')
    alternate=chains()[0].split('void work',1)[1]
    program=SOURCE+'\nvoid alternate'+alternate+'''
int main(void) {
 int values[] = {-2147483647, -100001, -32769, -1, 0, 1, 32768, 100001, 2147483647};
 unsigned int i;
 for (i=0; i<sizeof(values)/sizeof(values[0]); ++i) {
  Record a = {7, 11, 2.0f}, b = {7, 11, 2.0f};
  work(&a, values[i]); alternate(&b, values[i]);
  if (a.first!=b.first || a.second!=b.second || a.unused!=b.unused) return 1;
 }
 return 0;
}
'''
    source=tmp_path/'chain.c';source.write_text(program)
    executable=tmp_path/'chain'
    subprocess.run([cc,'-std=c90','-O2',str(source),'-o',str(executable)],check=True,
                   capture_output=True,timeout=20)
    subprocess.run([str(executable)],check=True,capture_output=True,timeout=10)
