"""Processor observations describe evidence without changing source, scores or match decisions."""
import copy
import json

import pytest

from decompnet import scoring
from decompnet.arch import (
    advice,
    get,
    mips_arguments,
    mips_regions,
    mips_return_paths,
    mips_returns,
    mips_search,
    mips_stack,
)
from decompnet.compiler import CompileResult
from decompnet.elfobj import FunctionObj
from decompnet.network import Network

STACK = [('s', 'addiu $sp, $sp, -0x28', 'addiu $sp, $sp, -0x20'),
         ('s', 'sw $ra, 0x20($sp)', 'sw $ra, 0x18($sp)'),
         ('s', 'lw $ra, 0x20($sp)', 'lw $ra, 0x18($sp)'),
         ('s', 'addiu $sp, $sp, 0x28', 'addiu $sp, $sp, 0x20')]


def asm(*lines):
    return '\n'.join(f'/* {i*4:06X} {0x80001000+i*4:08X} 00000000 */ {line}' for i, line in enumerate(lines))


def words(values, big=True):
    return b''.join(word.to_bytes(4, 'big' if big else 'little') for word in values)


def test_stack_frame_requires_agreeing_prologue_epilogue_and_saved_registers():
    assert mips_stack.frame_growth(STACK) == 8
    report = mips_stack.layout(STACK)
    assert report['frame_delta'] == 8 and report['saved_deltas'] == [8]
    assert report['target_frame'] == 40 and report['candidate_frame'] == 32
    assert mips_stack.frame_growth(STACK[:-1]) is None
    assert mips_stack.frame_growth([STACK[0], ('s', 'sw $s0, 0x20($sp)', 'sw $ra, 0x18($sp)'), *STACK[2:]]) is None
    assert mips_stack.frame_growth([('r', *STACK[0][1:]), *STACK[1:]]) is None


def test_decimal_stack_operands_with_leading_zeroes_do_not_break_feedback():
    rows = [('s', 'addiu $sp, $sp, -040', 'addiu $sp, $sp, -032'),
            ('s', 'sw $ra, 032($sp)', 'sw $ra, 024($sp)'),
            ('s', 'lw $ra, 032($sp)', 'lw $ra, 024($sp)'),
            ('s', 'addiu $sp, $sp, 040', 'addiu $sp, $sp, 032')]
    assert mips_stack.frame_growth(rows) == 8
    assert mips_stack.layout(rows)['frame_delta'] == 8


def test_flow_blocks_keep_delay_slots_with_transfers_and_decline_unknown_edges():
    target = ['lw $t0, 0($a0)', 'addu $v0, $t0, $a1', 'beqz $v0, 0x18', 'nop',
              'jr $ra', 'nop', 'jr $ra', 'nop']
    graph = mips_regions.read(target)
    assert graph['block_for'] == [0, 0, 0, 0, 4, 4, 6, 6]
    assert graph['successors']['0'] == [4, 6]
    assert (0, 1, 't0') in graph['dependencies']
    assert mips_regions.read(['j 0x4', 'nop', 'nop']) is None  # edge into delay slot
    assert mips_regions.read(['jalx 0x100', 'nop']) is None
    assert mips_regions.read(['jr $ra']) is None


def test_register_chains_and_delay_slot_mismatches_are_advisory():
    rows = [('r', 'addu $t0, $a0, $a1', 'addu $t1, $a0, $a1'),
            ('r', 'addu $t1, $t0, $a2', 'addu $t2, $t1, $a2')]
    assert mips_search.read(rows)['register_chains'] == [['$t0', '$t1', '$t2']]
    report = mips_search.read([(' ', 'jr $ra', 'jr $ra'), ('|', 'nop', 'move $v0, $a0')])
    assert report['delay_slot']
    assert not mips_search.read([('|', 'beq label', 'beq other')])['delay_slot']


@pytest.mark.parametrize('big', [True, False])
def test_argument_reader_requires_a_consistent_ordinary_register_shift(big):
    before, after = words([0x00808021, 0x03e00008, 0], big), words([0x00a08021, 0x03e00008, 0], big)
    assert mips_arguments.evidence(before, after, set(), big) == ('incoming', 1, {0})
    assert mips_arguments.evidence(before, after, {0}, big) is None
    assert mips_arguments.evidence(words([0x03e00008, 0x24820001], big),
                                   words([0x03e00008, 0x24a20001], big), set(), big) == ('incoming', 1, {0})
    assert mips_arguments.evidence(b'x', b'y', set(), big) is None
    for prefix in [0x24040001, 0x24050001, 0x0c000800, 0x10000001]:
        assert mips_arguments.evidence(words([prefix, 0x00808021], big),
                                       words([prefix, 0x00a08021], big), set(), big) is None


def test_return_paths_distinguish_ambiguous_register_values_and_likely_delay_slots():
    target = asm('addiu $v0, $a0, 1', 'bgtz $v0, 0x80001014', 'move $a1, $v0',
                 'li $v0, 3', 'sw $v0, 0($a0)', 'jr $ra', 'nop')
    paths = mips_return_paths.read(target)
    assert paths['exits'][0]['definitions'] == [0, 3]
    assert paths['exits'][0]['ambiguous']
    assert paths['guarded_exits'][0]['condition'] == '> 0'
    likely = asm('li $v0, 1', 'beql $a0, $zero, 0x80001014', 'li $v0, 2',
                 'b 0x80001014', 'nop', 'jr $ra', 'nop')
    assert mips_return_paths.read(likely)['exits'][0]['definitions'] == [0, 2]
    assert mips_return_paths.read(target.replace('jr $ra', 'jr $t9')) is None
    assert mips_return_paths.read(target.replace('0x80001014', 'missing_label')) is None


def test_calls_clobber_return_evidence_and_caller_consumption_stays_distinct():
    assert mips_return_paths.read(asm('jal g', 'li $v0, 3', 'jr $ra', 'nop'))['exits'][0]['definitions'] == [-1]
    for tail, expected in [('sw $v0, 0($s0)', 'consumed'), ('li $v0, 1', 'discarded'), ('nop', 'unknown')]:
        assert mips_return_paths.caller_use(asm('jal f', 'nop', tail, 'jr $ra', 'nop'), 'f')[0]['use'] == expected
    assert mips_returns.result(asm('move $v0, $s0', 'jr $ra', 'nop'))['caller_consumes'] is False
    assert mips_returns.result(asm('lw $v0, 0($a0)', 'jr $ra', 'nop'))['requires_caller']
    assert mips_returns.result(asm('jr $ra', 'addiu $v0, $a0, 1'))
    assert mips_returns.result(asm('jr $ra', 'addu $v0, $a0, $zero'))


def test_objdiff_mips_text_gets_register_normalisation_for_advice():
    rows = [('i', 'addiu sp, sp, -16', 'addiu sp, sp, -24'),
            (' ', 'sw ra, 12(sp)', 'sw ra, 12(sp)'),
            (' ', 'b 0x10', 'b 0x10'), (' ', 'nop', 'nop'),
            (' ', 'lw ra, 12(sp)', 'lw ra, 12(sp)'),
            (' ', 'jr ra', 'jr ra'), (' ', 'addiu sp, sp, 16', 'addiu sp, sp, 16')]
    text = '\n'.join(advice.diff_lines('mips', rows, objdiff=True))
    assert 'within-block register dependencies' in text
    assert '0 explicit within-block' not in text
    assert advice.assembly_lines('mips', 'jr ra\naddiu v0, a0, 1', objdiff=True)
    assert mips_return_paths.read(asm('jr $ra', '')) is None


def test_objdiff_operand_rows_are_sorted_into_register_and_stack_differences():
    """objdiff marks every operand mismatch 'i'; the chain and frame readers need scoring's 'r' and 's'."""
    stack = [('i', 'addiu sp, sp, -0x28', 'addiu sp, sp, -0x20'),
             ('i', 'sw ra, 0x20(sp)', 'sw ra, 0x18(sp)'),
             ('i', 'lw ra, 0x20(sp)', 'lw ra, 0x18(sp)'),
             ('i', 'addiu sp, sp, 0x28', 'addiu sp, sp, 0x20')]
    assert 'consistently differ by 8 bytes' in '\n'.join(advice.diff_lines('mips', stack, objdiff=True))
    chain = [('i', 'addu t0, a0, a1', 'addu t1, a0, a1'), ('i', 'addu t1, t0, a2', 'addu t2, t1, a2')]
    assert 'Register mismatch chain: $t0 → $t1 → $t2.' in '\n'.join(advice.diff_lines('mips', chain, objdiff=True))
    other = [('i', 'lw a0, 0xc(a2)', 'lw a0, 0x10(a2)')]  # an operand that isn't a register or a stack slot stays 'i'
    assert advice._objdiff_row(*other[0])[0] == 'i'


def test_objdiff_branch_destinations_count_from_the_functions_start():
    """objdiff prints a branch's destination as a section offset: a function past the section's first had no blocks
    or return paths until they were counted from its own start."""
    code = 'lw v0, 0x0(a0)\nbnez v0, 0x110\nnop\naddiu v0, zero, 0x1\njr ra\nnop'
    assert advice.assembly_lines('mips', code, objdiff=True) == []  # 0x110 isn't in a function read from 0
    assert 'Observed 1 return site(s)' in '\n'.join(advice.assembly_lines('mips', code, objdiff=True, address=0x100))
    rows = [(' ' if i else 'i', line, line.replace('0x0(', '0x4(')) for i, line in enumerate(code.splitlines())]
    assert not any('control-flow blocks' in line for line in advice.diff_lines('mips', rows, objdiff=True))
    assert any('control-flow blocks' in line
               for line in advice.diff_lines('mips', rows, objdiff=True, addresses=(0x100, 0x100)))


@pytest.mark.parametrize('processor', ['x86', 'ppc', 'arm', 'unknown'])
def test_other_processors_do_not_get_mips_observations(processor):
    assert get(processor).instruction_regions is None
    assert advice.diff_lines(processor, STACK) == []
    assert advice.assembly_lines(processor, asm('move $v0, $s0', 'jr $ra', 'nop')) == []


@pytest.mark.parametrize('rows', [[], [()], [('x', 'only two')], [('r', 1, 2)], STACK * 3000])
def test_unknown_or_oversized_reports_decline_analysis(rows):
    assert advice.diff_lines('mips', rows) == []


def test_readers_do_not_mutate_the_scored_diff_or_claim_return_types():
    before = copy.deepcopy(STACK)
    result = scoring.DiffResult(40, 400, STACK, False)
    lines = advice.diff_lines('mips', result.rows)
    assert any('consistently differ by 8 bytes' in line for line in lines)
    assert STACK == before and result.score == 40 and not result.bytes_match
    assert 'does not establish a C return contract' in '\n'.join(advice.assembly_lines(
        'mips', asm('move $v0, $s0', 'jr $ra', 'nop')))


def test_compile_feedback_keeps_original_score_and_verdict(tmp_path, monkeypatch):
    net = Network(tmp_path / 'test.db')
    target = words([0x27bdffd8, 0xafbf0020, 0x8fbf0020, 0x03e00008, 0x27bd0028])
    candidate = FunctionObj('f', 'mips', True, words([0x27bdffe0, 0xafbf0018, 0x8fbf0018, 0x03e00008, 0x27bd0020]))
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',
                         ('game', 'Game', 'mips', 1, str(tmp_path), json.dumps({}))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at) VALUES(?,1,'',0)", (pid,))
        db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm) VALUES(?,?,?,?,?,?,?)',
                   (pid, 'f', len(target), 5, target, '[]', asm('move $v0, $s0', 'jr $ra', 'nop')))
    agent = net.authenticate(net.register_agent('helper'))
    monkeypatch.setattr(net, '_compile', lambda *a: CompileResult(True, b'object', ''))
    monkeypatch.setattr(net, '_evaluate', lambda *a: (candidate, False, []))
    expected = scoring.diff(net._target_obj(net._function('f', 'game'), net._project('game')), candidate)
    output = net.attempt(agent, 'f', 'void f(void) {}', 'game')
    assert output['score'] == expected.score and output['matched'] is False
    assert 'Processor observations' in output['text'] and 'target 40 bytes, candidate 32 bytes' in output['text']
