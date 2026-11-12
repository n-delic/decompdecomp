"""Dead pointer capture reuse retains evaluations, scopes and all live values."""
import shutil
import subprocess

import pytest

from decompnet.app import draft_repairs
from decomprepair import pointer_capture_repairs as repairs
from decomprepair import pointer_pair_repairs

HEADER = 'typedef unsigned char u8; typedef int s32;'
PREFIX = '''typedef struct Record { s32 words[8]; } Record;
extern Record *table[4];
extern void consume(void *);
#define GROUP(p, index) ((u8 *)(p) + (index) * 4)
'''
BODY = '''void test(void) {
 Record *r;
 u8 *p;
 unsigned int k;
 r = table[0];
 consume(r);
 p = (u8 *)table[1];
 k = 0;
 p = (u8 *)GROUP(p, k);
 consume(p);
}'''
SOURCE = PREFIX+BODY


def generated(source=SOURCE):
    return repairs.variants(source, 'test', HEADER)


def test_existing_dead_pointer_retains_capture_boundaries():
    variants = generated()
    assert len(variants) == 1
    family, text = variants[0]
    assert family == repairs.FAMILY
    assert 'r = table[1];\n k = 0;\n p = (u8 *)GROUP(((u8 *)r), k);' in text
    assert text.count('table[1]') == 1
    assert text.count('consume(p)') == 1
    assert 'Record *r;' in text and 'u8 *p;' in text


def two_sites():
    return SOURCE.replace(' consume(p);', ''' consume(p);
 p = (u8 *)table[2];
 k = 0;
 p = (u8 *)GROUP(p, k);
 consume(p);''')


def test_pair_is_offered_before_worse_single_lifetime_changes():
    variants = generated(two_sites())
    assert len(variants) == 3
    assert 'r = table[1];' in variants[0][1]
    assert 'r = table[2];' in variants[0][1]
    assert [text.count('r = table[') for _, text in variants] == [3, 2, 2]
    labels = {}
    shortlisted = draft_repairs.variants(two_sites(), 'test', 'mips', flags=['-g3'], labels=labels, header=HEADER)
    assert any(labels.get(text) == repairs.FAMILY for text in shortlisted)
    assert repairs.FAMILY in draft_repairs.AGENT_FAMILIES


def test_macro_formal_does_not_shadow_function_local():
    part = pointer_pair_repairs.context(SOURCE, 'test', HEADER)[0]
    assert 'p' in {d['name'] for d in pointer_pair_repairs.declarations(part)}
    assert not generated(SOURCE.replace('void test(void)', 'void test(Record **table)'))


@pytest.mark.parametrize(('before', 'after'), [
    (' consume(p);', ' consume(p); consume(r);'),
    (' k = 0;', ' k = 0; consume(r);'),
    (' k = 0;', ' k = 0; consume(p);'),
    (' k = 0;', ' k++;'),
    (' k = 0;', ' *table[3] = *table[0];'),
    (' k = 0;', ' k = read_value();'),
    (' k = 0;', ' if (k) goto end; k = 0; end:;'),
    (' consume(r);', ' consume(r); escape(&r);'),
    (' consume(r);', ' consume(r); escape(&(p));'),
    (' consume(r);', ' consume(r); { Record *r; r = table[0]; consume(r); }'),
    ('table[1]', 'table[4]'),
    ('table[1]', 'table[08]'),
    ('extern Record *table[4];', 'extern Record *table[08];'),
    ('table[1]', 'table[-1]'),
    ('table[1]', 'table[k]'),
    ('table[1]', 'get_record()'),
    ('extern Record *table[4];', 'extern Record **table;'),
    ('extern Record *table[4];', 'extern void *table[4];'),
    ('extern Record *table[4];', 'extern volatile Record *table[4];'),
    ('extern Record *table[4];', 'extern Record *const table[4];'),
    ('s32 words[8]', 'volatile s32 words[8]'),
    ('s32 words[8]', 'const s32 words[8]'),
    ('s32 words[8]', '_Atomic s32 words[8]'),
    ('#define GROUP(p, index) ((u8 *)(p) + (index) * 4)', '#define GROUP(p, index) ((u8 *)(p) + (index) * 4 + observe(r))'),
    ('#define GROUP(p, index) ((u8 *)(p) + (index) * 4)', '#define GROUP(p, index) hidden((p), (index))'),
    ('#define GROUP(p, index) ((u8 *)(p) + (index) * 4)', '#define GROUP(p, index) ((u8 *)(p) + ((u8 *)(p) - (u8 *)table[0]) + (index))'),
    ('#define GROUP(p, index) ((u8 *)(p) + (index) * 4)', '#define GROUP(p, index) GROUP(p, index)'),
    ('#define GROUP(p, index) ((u8 *)(p) + (index) * 4)', '#define GROUP(p, index) ((u8 *)(p) + sizeof(index))'),
    ('p = (u8 *)GROUP(p, k);', 'p = (u8 *)GROUP(p, k); if (k) consume(r);'),
    ('p = (u8 *)GROUP(p, k);', 'p = (u8 *)GROUP(p, k); goto prior;'),
    ('void test(void)', 'void test(Record *r)'),
    ('void test(void)', 'void test(void)\n#if FLAG\n#endif'),
])
def test_unknown_effects_aliases_types_and_live_values_are_refused(before, after):
    assert before in SOURCE
    assert not generated(SOURCE.replace(before, after))


def test_direct_cast_arithmetic_and_zero_intervening_statements():
    source = SOURCE.replace(' k = 0;\n', '').replace('GROUP(p, k)', '((u8 *)p + 4)')
    assert generated(source)


def test_three_intervening_assignments_are_outside_bound():
    assert not generated(SOURCE.replace(' k = 0;', ' k = 0; k = 1; k = 2;'))


@pytest.mark.parametrize('byte_step', [False, True])
def test_host_semantics_keep_pointer_reads_and_call_order(tmp_path, byte_step):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('host C compiler unavailable')
    source = two_sites().replace('extern Record *table[4];', 'Record records[4]; Record *table[4];').replace(
        'extern void consume(void *);', '''unsigned long trace[8]; unsigned count;
void consume(void *p) { trace[count++] = (u8 *)p - (u8 *)records; if(count==2) table[2]=&records[3]; }''')
    source += '''
int main(void) {
 unsigned i;
 for (i=0; i<4; ++i) table[i]=&records[i];
 test();
 return !(count==3 && trace[0]==0 && trace[1]==sizeof(Record) && trace[2]==3*sizeof(Record));
}
'''
    if byte_step:
        source = source.replace('GROUP(p, k)', '(p+1)').replace('trace[1]==sizeof(Record)', 'trace[1]==sizeof(Record)+1').replace('trace[2]==3*sizeof(Record)', 'trace[2]==3*sizeof(Record)+1')
    variants = generated(source)
    assert variants
    for i, text in enumerate([source]+[text for _, text in variants]):
        path = tmp_path/f'capture{i}.c'
        path.write_text(HEADER+'\n'+text)
        output = tmp_path/f'capture{i}'
        subprocess.run([cc, '-std=c99', '-O2', '-fsanitize=undefined', str(path), '-o', str(output)], check=True, capture_output=True, timeout=30)
        subprocess.run([str(output)], check=True, capture_output=True, timeout=10)


def test_live_pointer_on_real_backedge_is_not_reused():
    source = SOURCE.replace(' consume(r);', ' prior: consume(r);').replace(' consume(p);', ' consume(p); if (k) goto prior;')
    assert not generated(source)


def test_type_and_root_macros_are_not_a_type_proof():
    for directive in ('#define table other', '#define Record Unknown', '#define u8 unsigned short', '#define r hidden', '#define p hidden', '#define k (*hidden)'):
        assert not generated(SOURCE.replace('void test', directive+'\nvoid test'))


def test_incoming_jump_to_sink_does_not_share_a_capture_proof():
    source = SOURCE.replace(' consume(r);', ' consume(r); if (k) goto sink;').replace(' p = (u8 *)GROUP(p, k);', ' sink: p = (u8 *)GROUP(p, k);')
    assert not generated(source)


def test_old_pointer_read_in_successor_branch_is_live():
    source = SOURCE.replace(' consume(p);', ' if (k) consume(r); else consume(p);')
    assert not generated(source)


def test_sink_arithmetic_keeps_byte_stride_not_record_stride():
    variants = generated(SOURCE.replace('GROUP(p, k)', '(p + 1)'))
    assert len(variants) == 1
    assert '(((u8 *)r) + 1)' in variants[0][1]


@pytest.mark.parametrize('expression', ['(*fp)(p)', '(fp)(p)', 'callbacks[0](p)'])
def test_indirect_sink_calls_have_no_simple_expression_proof(expression):
    assert not generated(SOURCE.replace('GROUP(p, k)', expression))


def test_qualified_nested_table_shadow_is_not_the_declared_global_array():
    source=SOURCE.replace(' p = (u8 *)table[1];',' { const Record **table=hidden; p = (u8 *)table[1];').replace(' consume(p);',' consume(p); }')
    assert generated(source) == []
    source=SOURCE.replace(' p = (u8 *)table[1];',' { __typeof__(hidden) table=hidden; p = (u8 *)table[1];').replace(' consume(p);',' consume(p); }')
    assert generated(source) == []
