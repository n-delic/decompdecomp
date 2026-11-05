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
