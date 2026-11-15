import pytest

from decompnet.app.address_repairs import variants


def report():
    return {'rows': [('i', 'lui $v0, %hi(destination)', 'lui $v0, %hi(original)'),
                     ('i', 'lw $v0, %lo(destination)($v0)', 'lw $v0, %lo(original)($v0)')]}


def test_address_repair_preserves_comments_strings_and_substrings():
    source = 'return original + original_extra; /* original */ "original";'
    assert variants(source, report(), {}, True, {'destination'}) == [source.replace('return original ', 'return destination ')]


@pytest.mark.parametrize('problem', ['unknown', 'half', 'conflict', 'register', 'addend'])
def test_address_repairs_reject_ambiguous_or_unaligned_evidence(problem):
    r = report(); known = {'destination'}
    if problem == 'unknown': known.clear()
    if problem == 'half': r['rows'].pop()
    if problem == 'conflict': r['rows'].append(('i', 'lui $v0, %hi(other)', 'lui $v0, %hi(original)'))
    if problem == 'register': r['rows'][0] = ('r', 'lui $a0, %hi(destination)', 'lui $v0, %hi(original)')
    if problem == 'addend': r['rows'][0] = ('i', 'lui $v0, %hi(destination+4)', 'lui $v0, %hi(original)')
    assert variants('return original;', r, {}, True, known) == []


@pytest.mark.parametrize('big', [True, False])
@pytest.mark.parametrize('opcode', [0x24820000, 0x8c820000])
def test_combines_address_with_constant_or_field_offset(big, opcode):
    order = 'big' if big else 'little'
    r = report()
    r['candidate_target'] = (opcode | 16).to_bytes(4, order).hex()
    r['candidate_relocs'] = '[]'
    target = {'target': (opcode | 20).to_bytes(4, order), 'relocs': '[]'}
    assert variants('return original + 16;', r, target, big, {'destination'}) == [
        'return destination + 16;', 'return destination + 20;']


def test_relocated_literals_are_not_changed():
    r = report(); r.update(candidate_target='24820010', candidate_relocs='[[0,5,0]]')
    target = {'target': bytes.fromhex('24820014'), 'relocs': '[]'}
    assert variants('return original + 16;', r, target, True, {'destination'}) == ['return destination + 16;']


def test_automatic_address_names_also_rewrite_local_declarations():
    r = {'rows': [('i','lui $v0, %hi(D_80002000)','lui $v0, %hi(D_80001000)'),
                  ('i','lw $v0, %lo(D_80002000)($v0)','lw $v0, %lo(D_80001000)($v0)')]}
    source = 'extern int D_80001000; int f(void) { return D_80001000; }'
    assert variants(source,r,{},True,set()) == [source.replace('D_80001000','D_80002000')]


def test_numeric_address_casts_keep_type_suffix_and_comments():
    r = {'rows': [('i','lui $v0, %hi(D_80002000)','lui $v0, %hi(D_80001000)'),
                  ('i','lw $v0, %lo(D_80002000)($v0)','lw $v0, %lo(D_80001000)($v0)')]}
    source = 'return *(int *)0x80001000U; /* 0x80001000U */'
    assert variants(source,r,{},True,set()) == ['return *(int *)0x80002000U; /* 0x80001000U */']
