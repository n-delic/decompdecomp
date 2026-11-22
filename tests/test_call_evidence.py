"""Callers and the result type they show (arch/mips_callsites.py, app/call_evidence.py, the draft stage's use of them)."""
from decompnet.app import call_evidence, draft_return_type
from decompnet.arch import get, mips_callsites


def listing(*lines):
    return 'glabel caller\n' + '\n'.join(f'/* {i * 4:X} {0x80000000 + i * 4:08X} 00000000 */  {line}' if not line.endswith(':')
                                         else line for i, line in enumerate(lines))


def use(*lines, name='g'):
    return [s['use'] for s in mips_callsites.sites(listing(*lines), name)]


def test_a_caller_that_reads_v0_after_the_call_proves_a_value():
    assert use('jal g', 'nop', 'beqz $v0, .L1', 'nop') == ['value']
    assert use('jal g', 'nop', 'move $s0, $v0') == ['value']
    assert use('jal g', 'nop', 'addiu $v0, $v0, 0x1') == ['value']          # read before it is written
    assert use('jal g', 'nop', 'sw $v0, 0x0($s0)') == ['value']
    assert use('jal g', 'nop', 'lw $a0, 0x0($a1)', 'jal h', 'move $a1, $v0') == ['value']  # a call's delay slot reads it


def test_a_caller_that_writes_v0_or_calls_something_else_drops_the_result():
    assert use('jal g', 'nop', 'addiu $v0, $zero, 0x1', 'sw $v0, 0x0($s0)') == ['discarded']
    assert use('jal g', 'nop', 'lw $a0, 0x0($s0)', 'jal h', 'nop') == ['discarded']
    assert use('jal g', 'move $v0, $a0', 'sw $a1, 0x0($s0)', 'jal h', 'nop') == ['discarded']  # the delay slot runs first


def test_floats_doubles_and_64_bit_results_are_told_apart():
    assert use('jal g', 'nop', 'swc1 $fv0, 0x0($s0)') == ['float']
    assert use('jal g', 'nop', 'add.s $fv1, $fv0, $fv1') == ['float']
    assert use('jal g', 'nop', 'add.d $f2, $f0, $f2') == ['double']
    site = mips_callsites.sites(listing('jal g', 'nop', 'sw $v1, 0x4($s0)', 'sw $v0, 0x0($s0)'), 'g')[0]
    assert (site['use'], site['result']) == ('value', 'long long')


def test_a_tail_return_or_a_branch_first_says_nothing():
    assert use('jal g', 'nop', 'jr $ra', 'nop') == ['passthrough']
    assert use('jal g', 'nop', 'bnez $a0, .L1', 'nop', 'move $s0, $v0') == ['unknown']
    assert [s['kind'] for s in mips_callsites.sites(listing('j g', 'nop'), 'g')] == ['tail']
    assert use('jal other', 'nop') == []


def test_the_excerpt_marks_the_call_with_its_neighbours():
    site = mips_callsites.sites(listing('addiu $a0, $s0, 0x4', 'jal g', 'nop', 'move $s1, $v0', 'jr $ra', 'nop'), 'g')[0]
    assert site['excerpt'][site['mark']].endswith('jal g') and len(site['excerpt']) == 6 and 'move $s1, $v0' in site['excerpt'][3]


def test_c_uses_tells_a_dropped_result_from_a_used_one():
    source = ('extern s32 g(s32);\nvoid f(void) {\n    s32 x;\n    g(1);\n    x = g(2);\n    if (g(3)) { g(4); }\n'
              '    (void)g(5);\n    switch (x) { case 1: g(6); break; }\n    return;\n}\n')
    assert call_evidence.c_uses(source, 'g') == ['statement', 'value', 'value', 'statement', 'statement', 'statement']
    assert call_evidence.c_uses('Node *g(void);\n#define X 1\nvoid f(void) { return g(); }\n', 'g') == ['value']
    assert call_evidence.prototype('extern void g(s32);\ns32 h(void);\n', 'g') == 'void'
    assert call_evidence.prototype('s32 h(void);\n', 'g') is None
    assert call_evidence.source_type('static f32 g(f32 a) { return a; }', 'g') == 'f32'


def _evidence(*callers, proto=None, twins=()):
    return {'callers': [{'name': n, 'status': 'open', 'sites': [{'use': u, 'result': r} for u, r in sites], 'c_uses': list(cu)}
                        for n, sites, cu in callers], 'total': len(callers), 'prototype': proto, 'twins': list(twins)}


def test_the_verdict_is_a_value_when_any_caller_reads_it_and_void_only_on_weaker_agreement():
    read = call_evidence.verdict(_evidence(('a', [('value', 'int')], ()), ('b', [('discarded', None)], ())))
    assert (read['verdict'], read['type'], read['strength']) == ('value', 's32', 'strong')
    assert call_evidence.verdict(_evidence(('a', [('float', 'float')], ())))['type'] == 'f32'
    assert call_evidence.verdict(_evidence(('a', [], ('value',))))['verdict'] == 'value'
    one = call_evidence.verdict(_evidence(('a', [('discarded', None)], ())))
    two = call_evidence.verdict(_evidence(('a', [('discarded', None)], ()), ('b', [], ('statement',))))
    assert one['verdict'] == 'unknown' and (two['verdict'], two['strength']) == ('void', 'weak')
    assert call_evidence.verdict(_evidence(proto='void'))['verdict'] == 'void'
    assert call_evidence.verdict(_evidence(twins=[('t', 'u8')]))['type'] == 'u8'
    assert call_evidence.verdict(_evidence())['verdict'] == 'unknown'


def test_render_lists_each_site_with_its_code():
    evidence = _evidence(('a', [('value', 'int')], ()))
    evidence['callers'][0]['sites'][0].update(kind='call', excerpt=['jal g', 'nop'], mark=0)
    text = call_evidence.render({'name': 'g'}, evidence)
    assert text.startswith('g is called from 1 place.') and 'Result: value (s32, strong)' in text
    assert '## a' in text and '  > jal g' in text and 'reads the result' in text


DRAFT = 'void f(s32 arg0) {\n    g(arg0);\n}\n'


def test_the_callers_decide_the_draft_s_result_type_where_its_exits_are_ambiguous():
    value = {'verdict': 'value', 'type': 's32', 'strength': 'strong'}
    float_value = dict(value, type='f32')
    void = {'verdict': 'void', 'type': 'void', 'strength': 'weak'}
    assert draft_return_type.retype(DRAFT, 'f', 'tested', value) == 's32'
    assert draft_return_type.retype(DRAFT, 'f', None, float_value) == 'f32'
    assert draft_return_type.retype(DRAFT, 'f', 'tested', void) is None          # already void
    assert draft_return_type.retype(DRAFT, 'f', 'tested', {'verdict': 'unknown', 'type': None, 'strength': None}) is None
    assert draft_return_type.retype(DRAFT.replace('void f', 's32 f'), 'f', None, void) == 'void'
    assert draft_return_type.retype(DRAFT.replace('void f', 's32 f'), 'f', None, float_value) == 'f32'
    assert draft_return_type.retype(DRAFT, 'f', 'void', value) is None           # unambiguous exits are not overruled
    assert draft_return_type.retype(DRAFT, 'f', 'tested') == 's32'               # no callers known: the old guess


def test_mips_has_the_reader_and_the_others_say_so():
    assert get('mips').call_sites is mips_callsites.sites
