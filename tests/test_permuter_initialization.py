import json
import sys

import pytest

from decompnet.app import permute
from decompnet.app.permuter_initialization import unsafe_temporary

PARENT = 'int f(int condition) { return condition; }'


@pytest.mark.parametrize('body,unsafe', [
    ('int temp; if (condition) temp=2; return temp;', True),
    ('int temp; if (1) temp=2; return temp;', False),
    ('int temp; if (1==1) temp=2; return temp;', False),
    ("int temp; if ('x') temp=2; return temp;", False),
    ('int temp; while(1) { temp=2; break; } return temp;', False),
    ('int temp; for(;;) { temp=2; break; } return temp;', False),
    ('int temp; if (condition) temp=2; else temp=3; return temp;', False),
    ('int temp; while(condition) { temp=2; } return temp;', True),
    ('int temp; do { temp=2; } while(condition); return temp;', False),
    ('int temp; for(;condition;) { temp=2; } return temp;', True),
    ('int temp; goto use; temp=2; use: return temp;', True),
    ('int temp; temp=2; goto use; use: return temp;', False),
    ('int temp; while(condition) { if(condition) break; temp=2; } return temp;', True),
    ('int temp; do { temp=2; break; } while(condition); return temp;', False),
    ('int temp; if(condition) { do { temp=2; } while(0); } return temp;', True),
    ('int temp; do { temp=2; } while(0); return temp;', False),
    ('int temp; do { consume(condition); } while(0); if(condition) temp=2; consume(temp); return 0;', True),
    ('int temp; switch(condition) { case 1: temp=2; break; default: temp=3; } return temp;', False),
    ('int temp; switch(condition) { case 1: temp=2; break; } return temp;', True),
    ('int temp; temp=temp+1; return temp;', True),
    ('int temp=temp+1; return temp;', True),
    ('int temp=2; return temp;', False),
    ('while(condition) { int temp; if(condition) { temp=2; continue; } return temp; } return 0;', True),
    ('int temp; temp = condition == 0; if(temp) return 1; return 0;', False),
    ('int temp; use(&temp); return temp;', False),
    ('int temp; UNKNOWN(temp); return temp;', False),
    ('int temp; if(condition && (temp=1)) return temp; return 0;', False),
    ('int temp; return condition ? temp : 0;', False),
    ('int temp; int other; temp=(other=2); return temp+other;', False),
    ('int other; int temp=(other=2); return temp+other;', False),
    ('int temp; return sizeof(temp);', False),
    ('int temp; temp=(1, temp=2); return temp;', False),
    ('int temp=record.temp; return temp;', False),
])
def test_introduced_temporaries(body, unsafe):
    assert unsafe_temporary(PARENT, 'int f(int condition) {'+body+'}', 'f') is unsafe


def test_existing_locals_and_unreachable_reads_do_not_block():
    parent = 'int f(int condition) { int temp; return temp; }'
    assert not unsafe_temporary(parent, parent, 'f')
    assert not unsafe_temporary(PARENT, 'int f(int condition) { int temp; return 0; return temp; }', 'f')


@pytest.mark.parametrize('declaration,read,initial', [
    ('unsigned short value', 'value', 'packet[0]'),
    ('int *value', '*value', 'packet'),
])
def test_existing_binding_cannot_lose_initialization_on_one_branch(declaration, read, initial):
    parent = ('int f(int condition, int *packet) {'+declaration+'; int count; int i; int total=0; '
              'if(condition) count=4; else count=8; value='+initial+'; '
              'for(i=0; i<count; i++) total+='+read+'; return total; }')
    bad = parent.replace('else count=8; value='+initial+';',
                         'else { count=8; value='+initial+'; }')
    good = parent.replace('if(condition) count=4; else count=8; value='+initial+';',
                          'if(condition) { count=4; value='+initial+'; } '
                          'else { count=8; value='+initial+'; }')
    assert not unsafe_temporary(parent, parent, 'f')
    assert unsafe_temporary(parent, bad, 'f')
    assert not unsafe_temporary(parent, good, 'f')


def test_existing_unsafe_or_unknown_binding_is_not_a_new_regression():
    bad = 'int f(int condition) { int value; if(condition) value=2; return value; }'
    assert not unsafe_temporary(bad, bad, 'f')
    opaque = 'int f(int condition) { int value; UNKNOWN(); value=2; return value; }'
    assert not unsafe_temporary(opaque, bad, 'f', '#define UNKNOWN() hidden_effect()')
    qualified = bad.replace('int value;', 'volatile int value;')
    assert not unsafe_temporary(qualified, bad, 'f')


def test_one_unknown_existing_binding_does_not_hide_another_regression():
    parent = ('int f(int condition) { int opaque; int value; '
              'use((opaque=condition)); value=2; return value; }')
    assert unsafe_temporary(parent, parent.replace('value=2;', 'if(condition) value=2;'), 'f')


def test_generator_filters_lost_existing_initialization_and_preserves_valid_lineage(tmp_path):
    parent = 'int f(int condition) { int value; value=2; return value; }'
    bad = parent.replace('value=2;', 'if(condition) value=2;')
    good = parent.replace('value=2;', 'if(condition) value=2; else value=3;')
    script = tmp_path/'generator.py'
    script.write_text('import json,sys\nfor line in sys.stdin:\n'
                      ' for source in '+repr([bad, good])+':\n'
                      '  print(json.dumps({"variant":source,"lineage":["perm_reorder_stmts"]}))\n'
                      ' print(json.dumps({"done":True}),flush=True)\n')
    gen = permute.Generator([sys.executable, str(script)])
    try:
        result = gen.batch(dict(source=parent, function='f'))
    finally:
        gen.close()
    assert result == [good]
    assert result[0].lineage == ['perm_reorder_stmts']


@pytest.mark.parametrize('declaration,parameter', [
    ('u8 *temp', 'u8 *bytes'), ('u8*temp', 'u8*bytes'),
    ('void **temp', 'void **bytes'), ('void * *temp', 'void * *bytes'),
])
def test_introduced_pointer_captures_need_assignment_on_every_path(declaration, parameter):
    parent = 'int f(int condition, '+parameter+') { consume(bytes); return 0; }'
    bad = 'int f(int condition, '+parameter+') {'+declaration+'; if(condition) temp=bytes; consume(temp); return 0; }'
    assert unsafe_temporary(parent, bad, 'f')
    assert not unsafe_temporary(parent, bad.replace('if(condition) temp=bytes;', 'temp=bytes;'), 'f')
    assert not unsafe_temporary(parent, bad.replace('if(condition) temp=bytes;',
                                                    'if(condition) temp=bytes; else temp=bytes+1;'), 'f')


@pytest.mark.parametrize('body', [
    'u8 *temp; while(condition) { temp=bytes; } return temp[0];',
    'u8 *temp; goto use; temp=bytes; use: return *temp;',
    'u8 *temp; *temp=1; return 0;',
    'u8 *temp; temp[0]=1; return 0;',
])
def test_pointer_use_or_pointee_write_does_not_initialize_pointer(body):
    assert unsafe_temporary('int f(int condition, u8 *bytes) { return 0; }',
                            'int f(int condition, u8 *bytes) {'+body+'}', 'f')


def test_initialized_pointee_write_and_existing_pointer_remain_allowed():
    parent = 'int f(u8 *bytes) { return bytes[0]; }'
    good = 'int f(u8 *bytes) { u8 *temp=bytes; *temp=1; return temp[0]; }'
    assert not unsafe_temporary(parent, good, 'f')
    existing = 'int f(u8 *bytes) { u8 *temp; return *temp; }'
    assert not unsafe_temporary(existing, existing, 'f')


@pytest.mark.parametrize('header', ['typedef volatile int s32;', 'typedef const int s32;'])
def test_qualified_pointee_alias_has_no_verdict(header):
    assert not unsafe_temporary(PARENT, 'int f(int condition) { s32 *temp; return *temp; }', 'f', header)


@pytest.mark.parametrize('body', [
    'u8 *temp; initialize(&temp); return *temp;',
    'const u8 *temp; if(condition) temp=bytes; return *temp;',
    'u8 *volatile temp; if(condition) temp=bytes; return *temp;',
    'Unknown *temp; if(condition) temp=bytes; consume(temp); return 0;',
    'int (*temp)(void); if(condition) temp=callback; return temp();',
    'u8 *temp; return sizeof(temp);',
    'struct Record { u8 *temp; } record; return 0;',
    'u8 *temp; if(condition) { u8 *temp=bytes; consume(temp); } return *temp;',
])
def test_ambiguous_pointer_bindings_keep_no_verdict(body):
    assert not unsafe_temporary('int f(int condition, u8 *bytes) { return 0; }',
                                'int f(int condition, u8 *bytes) {'+body+'}', 'f')


def test_pointer_guard_filters_protocol_outputs(tmp_path):
    parent = 'int f(int condition, u8 *bytes) { return bytes[0]; }'
    bad = 'int f(int condition, u8 *bytes) { u8 *temp; if(condition) temp=bytes; return temp[0]; }'
    good = bad.replace('if(condition) temp=bytes;', 'temp=bytes;')
    script = tmp_path/'pointer_generator.py'
    script.write_text('import json,sys\nfor line in sys.stdin:\n'
                      ' for source in '+repr([bad, good])+':\n'
                      '  print(json.dumps({"variant":source,"lineage":["perm_temp_for_expr"]}))\n'
                      ' print(json.dumps({"done":True}),flush=True)\n')
    gen = permute.Generator([sys.executable, str(script)])
    try:
        result = gen.batch(dict(source=parent, function='f'))
    finally:
        gen.close()
    assert result == [good]
    assert result[0].lineage == ['perm_temp_for_expr']


def test_guard_filters_each_protocol_output_and_preserves_lineage(tmp_path):
    # The generator owns its internal random walk; the wrapper independently checks each output.
    bad = 'int f(int condition) { int temp; if(condition) temp=2; return temp; }'
    good = 'int f(int condition) { int temp; temp=condition == 0; return temp; }'
    script = tmp_path/'generator.py'
    script.write_text('import json,sys\nfor line in sys.stdin:\n'
                      ' for source in '+repr([bad, good])+':\n'
                      '  print(json.dumps({"variant":source,"lineage":["perm_temp_for_expr"]}))\n'
                      ' print(json.dumps({"done":True}),flush=True)\n')
    gen = permute.Generator([sys.executable, str(script)])
    try:
        result = gen.batch(dict(source=PARENT, function='f'))
    finally:
        gen.close()
    assert result == [good]
    assert result[0].lineage == ['perm_temp_for_expr']
    assert json.dumps(result) == json.dumps([good])


def test_header_macro_effects_are_not_inferred():
    candidate = 'int f(int condition) { int temp; initialize(temp); return temp; }'
    assert not unsafe_temporary(PARENT, candidate, 'f', '#define initialize(x) x=1')


@pytest.mark.parametrize('header', [
    'typedef volatile int inner; typedef inner s32;',
    'typedef const int inner; typedef inner s32;',
    '#define s32 int',
    '#define temp another_name',
])
def test_qualified_aliases_and_macro_declarations_receive_no_verdict(header):
    assert not unsafe_temporary(PARENT, 'int f(int condition) { s32 temp; return temp; }', 'f', header)


def test_record_fields_are_not_scalar_locals():
    candidate = 'int f(int condition) { struct Record { int temp; } record; return temp; }'
    assert not unsafe_temporary(PARENT, candidate, 'f')


def test_hex_letter_parameter_is_not_a_literal_condition():
    parent = 'int f(int a1) { return a1; }'
    assert unsafe_temporary(parent, 'int f(int a1) { int temp; if(a1) temp=2; return temp; }', 'f')


@pytest.mark.parametrize('header,expression', [
    ('#define ROOT (*(Record **)(buffer+4))', '(void)ROOT;'),
    ('#define ROOT (*pointer)', '(void)ROOT;'),
    ('#define use(a,b) ((Item*)use(a,b))', 'use(condition, condition);'),
    ('#define ROOT \\\n (*(Record **)(buffer+4))', '(void)ROOT;'),
])
def test_unrelated_transparent_macros_do_not_disable_initialization_check(header, expression):
    candidate = 'int f(int condition) { int temp; '+expression+' if(condition) temp=2; return temp; }'
    assert unsafe_temporary(PARENT, candidate, 'f', header)
    initialized = candidate.replace('if(condition) temp=2;', 'if(condition) temp=2; else temp=3;')
    assert not unsafe_temporary(PARENT, initialized, 'f', header)


@pytest.mark.parametrize('header,expression', [
    ('#define ROOT (*(int *)&temp)', '(void)ROOT;'),
    ('#define ROOT (*get())', '(void)ROOT;'),
    ('#define ROOT (*(pointer++))', '(void)ROOT;'),
    ('#define ROOT (*(pointer ? pointer : other))', '(void)ROOT;'),
    ('#define STOP return 0', 'STOP;'),
    ('#define use(a,b) ((Item*)use(b,a))', 'use(condition, condition);'),
    ('#define use(a,b) ((Item*)use(a,a))', 'use(condition, condition);'),
    ('#define Item temp\n#define use(a,b) ((Item*)use(a,b))', 'use(condition, condition);'),
    ('#define ROOT (*pointer)\n#define ROOT initialize(temp)', '(void)ROOT;'),
])
def test_opaque_macros_still_receive_no_verdict(header, expression):
    candidate = 'int f(int condition) { int temp; '+expression+' if(condition) temp=2; return temp; }'
    assert not unsafe_temporary(PARENT, candidate, 'f', header)


def test_generator_checks_bad_temporary_despite_unrelated_header_alias(tmp_path):
    bad = 'int f(int condition) { int temp; (void)ROOT; if(condition) temp=2; return temp; }'
    good = bad.replace('if(condition) temp=2;', 'temp=condition;')
    script = tmp_path/'generator.py'
    script.write_text('import json,sys\nfor line in sys.stdin:\n'
                      ' for source in '+repr([bad, good])+':\n'
                      '  print(json.dumps({"variant":source,"lineage":["perm_temp_for_expr"]}))\n'
                      ' print(json.dumps({"done":True}),flush=True)\n')
    gen = permute.Generator([sys.executable, str(script)])
    try:
        result = gen.batch(dict(source=PARENT, function='f', header='#define ROOT (*pointer)'))
    finally:
        gen.close()
    assert result == [good]
    assert result[0].lineage == ['perm_temp_for_expr']


def test_each_temporary_has_an_independent_initialization_verdict():
    candidate = ('int f(int condition) { int opaque; int *pointer; '
                 'use((opaque=condition)); if(condition) pointer=base; return *pointer; }')
    assert unsafe_temporary(PARENT, candidate, 'f')
    assert not unsafe_temporary(PARENT, candidate.replace('if(condition) pointer=base;',
                                                        'pointer=base;'), 'f')


def test_type_only_local_record_does_not_hide_a_conditional_pointer_capture():
    candidate = ('int f(int condition) { int opaque; int *pointer; '
                 'typedef struct { short rotation[9]; short alignment; int translation[3]; } Matrix; '
                 'Matrix matrix; use(&matrix); use((opaque=condition)); '
                 'while(condition) { pointer=base; condition--; } return *pointer; }')
    assert unsafe_temporary(PARENT, candidate, 'f')
    assert not unsafe_temporary(PARENT, candidate.replace('int *pointer;', 'int *pointer=base;'), 'f')


@pytest.mark.parametrize('record', [
    'typedef struct { int temp; int values[3]; } Record;',
    'typedef struct Named { int temp; } Record;',
])
def test_record_field_spelling_is_not_a_read_of_the_temporary(record):
    candidate = 'int f(int condition) { int temp; '+record+' temp=condition; return temp; }'
    assert not unsafe_temporary(PARENT, candidate, 'f')
    # A same-spelled field retains the existing conservative shadowing exclusion.
    assert not unsafe_temporary(PARENT, candidate.replace('temp=condition;', ''), 'f')


@pytest.mark.parametrize('record,header', [
    ('typedef struct { int values[bound()]; } Record;', ''),
    ('typedef struct { int values[condition]; } Record;', ''),
    ('typedef struct { int (*callback)(void); } Record;', ''),
    ('typedef struct { struct Nested { int value; } item; } Record;', ''),
    ('typedef struct { int value : 3; } Record;', ''),
    ('typedef struct { int value; } Record;', '#define value hidden_effect()'),
    ('typedef struct { int value; } Record;', '#define Record hidden_effect()'),
    ('struct Record { int value; } record = {side_effect()};', ''),
])
def test_complex_or_macro_record_declarations_keep_no_verdict(record, header):
    candidate = 'int f(int condition) { int temp; '+record+' return temp; }'
    assert not unsafe_temporary(PARENT, candidate, 'f', header)
