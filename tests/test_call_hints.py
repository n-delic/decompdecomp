"""Hints from a function's recorded real calls (arch/mips_call_hints.py, app/call_hints.py, the emulator's recording
reader). The recordings are synthetic: made up registers and RAM, no game data. The live recorder needs the user's
ROM and the installed emulator (CI has neither); its parts are stood in for here."""
import json
import struct

from decompnet import arch
from decompnet.app import call_hints
from decompnet.arch import mips_call_hints as hints
from decompnet.consoles.n64 import emulator

# a function that takes (record *p, int n, float x): it masks n to a byte, loads a halfword and a pointer from p, stores
# a word through p and, through a copy of p, a signed byte; it never reads $a2 or $a3
ASM = """glabel f
/* 0 80001000 00000000 */  addiu      $sp, $sp, -0x18
/* 4 80001004 00000000 */  andi       $t0, $a1, 0xFF
/* 8 80001008 00000000 */  lhu        $t1, 0x1C($a0)
/* C 8000100C 00000000 */  lw         $t2, 0x20($a0)
/* 10 80001010 00000000 */  sw         $t0, 0x24($a0)
/* 14 80001014 00000000 */  move       $s0, $a0
/* 18 80001018 00000000 */  lb         $t3, 0x30($s0)
/* 1C 8000101C 00000000 */  swc1       $fa0, 0x8($sp)
/* 20 80001020 00000000 */  jal        other
/* 24 80001024 00000000 */  addu       $a2, $t0, $zero
/* 28 80001028 00000000 */  lw         $t4, 0x34($s0)
/* 2C 8000102C 00000000 */  jr         $ra
/* 30 80001030 00000000 */  addiu      $sp, $sp, 0x18
"""


class Call:
    def __init__(self, a0, a1, f12, memory):
        self.gpr = [0] * 32
        self.gpr[4], self.gpr[5] = a0, a1
        self.fpr = [0] * 32
        self.fpr[12] = struct.unpack('>I', struct.pack('>f', f12))[0]
        self.memory = memory

    def read(self, address, width=4):
        return self.memory.get(address)


def calls(n=4):
    out = []
    for i in range(n):
        p = 0x80100000 + 0x100 * i
        out.append(Call(p, 10 * i + 5, 0.5 * i, {p + 0x1C: 3 * i, p + 0x20: 0x80200000 + 8 * i, p + 0x30: 0xFE if i == 1 else i,
                                                 p + 0x34: 1000 + i}))
    return out


def test_the_code_says_which_arguments_it_reads_and_what_it_does_through_them():
    use = hints.arg_use(ASM)
    assert use == {'int': [0, 1], 'float': [0]}    # $a2 is written (for the call) before any read
    assert hints.arg_count(use) == 2
    assert hints.narrowing(ASM) == {1: 'u8'}
    found = {(f['arg'], f['offset'], f['width']): f for f in hints.field_accesses(ASM)}
    assert set(found) == {(0, 0x1C, 2), (0, 0x20, 4), (0, 0x24, 4), (0, 0x30, 1), (0, 0x34, 4)}   # the copy is followed
    assert found[(0, 0x1C, 2)]['kinds'] == {'load'} and found[(0, 0x24, 4)]['kinds'] == {'store'}
    assert found[(0, 0x30, 1)]['signed'] is True and found[(0, 0x34, 4)]['kinds'] == {'load'}


def test_a_call_ends_the_copies_that_do_not_survive_it():
    asm = ASM.replace('move       $s0, $a0', 'move       $t5, $a0').replace('$s0', '$t5')
    assert {(f['offset']) for f in hints.field_accesses(asm)} == {0x1C, 0x20, 0x24, 0x30}   # $t5 is gone after the jal


def test_values_from_the_recordings_become_ranges_kinds_and_widths():
    found = hints.summarize(ASM, calls())
    assert found['calls'] == 4 and found['count'] == 2
    a0, a1 = found['ints']
    assert a0['kind'] == 'pointer' and a0['distinct'] == 4
    assert (a1['kind'], a1['min'], a1['max'], a1['fits'], a1['negative']) == ('integer', 5, 35, 'u8', False)
    (f12,) = found['floats']
    assert (f12['min'], f12['max'], f12['plausible']) == (0.0, 1.5, True)
    by = {(f['offset']): f for f in found['fields']}
    assert (by[0x1C]['min'], by[0x1C]['max'], by[0x1C]['fits']) == (0, 9, 'u8')
    assert by[0x20]['kind'] == 'pointer' and 'min' not in by[0x24]            # a store only: nothing to compare
    assert (by[0x30]['min'], by[0x30]['max']) == (-2, 3) or by[0x30]['kind'] == 'integer'   # lb: 0xFE is -2
    assert hints.consistent_narrowing(found) == {1: 'u8'}
    assert hints.consistent_narrowing(json.loads(json.dumps(found, default=list))) == {1: 'u8'}   # as saved and read back


def test_a_constant_or_a_single_call_proves_no_narrowing():
    assert hints.consistent_narrowing(hints.summarize(ASM, calls(1))) == {}
    same = [Call(0x80100000, 7, 0.0, {}) for _ in range(3)]
    assert hints.consistent_narrowing(hints.summarize(ASM, same)) == {}
    wide = calls()
    wide[2].gpr[5] = 300                                                      # a value that does not fit the mask
    assert hints.consistent_narrowing(hints.summarize(ASM, wide)) == {}


def test_negative_integers_and_integer_bits_in_a_float_register_are_said():
    neg = calls()
    neg[1].gpr[5] = 0xFFFFFFFE
    a1 = hints.summarize(ASM, neg)['ints'][1]
    assert a1['negative'] and a1['min'] == -2 and a1['fits'] == 's8'
    bits = calls()
    bits[2].fpr[12] = 0x00000007                                              # an integer in a float register
    assert hints.summarize(ASM, bits)['floats'][0]['plausible'] is False


def test_the_hints_read_as_sentences():
    text = '\n'.join(hints.lines(hints.summarize(ASM, calls())))
    assert 'a0 a pointer into RAM in all 4 calls' in text and 'a1 5..35 (fits u8, never negative)' in text
    assert 'f12 0..1.5' in text and 'a0+0x1C 16-bit 0..9' in text and 'a0+0x20 32-bit a pointer' in text
    assert 'Written through the arguments: a0+0x24 32-bit.' in text
    assert hints.lines({'calls': 0}) == [] and hints.lines(None) == []


DRAFT = '''void f(void *arg0, s32 arg1, s32 arg2, s32 arg3) {
    s32 temp;

    temp = arg1 & 0xFF;
    g(arg0, temp);
}
'''


def test_the_draft_gets_a_narrow_parameter_and_loses_the_parameters_the_code_never_reads():
    found = dict(call_hints.draft_candidates(DRAFT, 'f', hints.summarize(ASM, calls())))
    assert 'u8 arg1' in found['call_hints_narrow'] and 'temp = arg1;' in found['call_hints_narrow']
    assert found['call_hints_narrow'].startswith('void f(void *arg0, u8 arg1, s32 arg2, s32 arg3)')
    # f takes two integer arguments and a float in the code: the float means trailing ones are not dropped
    assert 'call_hints_args' not in found
    no_float = hints.summarize(ASM.replace('swc1       $fa0, 0x8($sp)', 'sw         $t0, 0x8($sp)'), calls())
    dropped = dict(call_hints.draft_candidates(DRAFT, 'f', no_float))['call_hints_args']
    assert dropped.startswith('void f(void *arg0, s32 arg1) {')
    used = DRAFT.replace('g(arg0, temp)', 'g(arg0, arg3)')
    assert 'call_hints_args' not in dict(call_hints.draft_candidates(used, 'f', no_float))   # arg3 is read in the body
    assert call_hints.draft_candidates(DRAFT, 'f', {}) == [] and call_hints.draft_candidates('', 'f', no_float) == []


def test_the_emulators_recording_is_read_from_its_files(tmp_path):
    state = '80001000 ' + ' '.join(f'{i:X}' for i in range(32)) + ' 0 0 ' + ' '.join(f'{0x100 + i:X}' for i in range(32))
    (tmp_path / '80001000_0.state').write_text(state)
    ram = bytearray(0x1000)
    ram[0x40:0x44] = (0xDEADBEEF).to_bytes(4, 'big')
    (tmp_path / '80001000_0.rdram').write_bytes(bytes(ram))
    got = emulator.read_recording(tmp_path / '80001000_0')
    assert got.pc == 0x80001000 and got.gpr[4] == 4 and got.fpr[12] == 0x10C
    assert got.read(0x80000040) == 0xDEADBEEF and got.read(0x80000041, 1) == 0xAD and got.read(0x80100000) is None


class _Console:
    name = 'N64'
    emulator = emulator


class _Net:
    def __init__(self, root):
        self.root = root

    def console(self, proj):
        return _Console()

    def _asm_text(self, fn, proj):
        return ASM


def test_hints_are_saved_beside_the_recordings_and_stale_ones_are_not_used(tmp_path):
    proj, fn = {'arch': 'mips', 'root': str(tmp_path)}, {'name': 'f'}
    bases = []
    for i in range(3):
        base = tmp_path / 'rec' / f'80001000_{i}'
        base.parent.mkdir(exist_ok=True)
        gpr = [0] * 32
        gpr[4], gpr[5] = 0x80000100, 10 + i
        (base.parent / f'80001000_{i}.state').write_text('80001000 ' + ' '.join(f'{g:X}' for g in gpr) + ' 0 0 ' + '0 ' * 32)
        ram = bytearray(0x1000)
        ram[0x11C:0x11E] = (i + 1).to_bytes(2, 'big')
        (base.parent / f'80001000_{i}.rdram').write_bytes(bytes(ram))
        bases.append(base)
    net = _Net(tmp_path)
    made = call_hints.derive(net, proj, fn, bases)
    assert made['calls'] == 3 and (tmp_path / 'build' / 'hints' / 'f' / 'hints.json').is_file()
    assert call_hints.saved(net, proj, fn)['fields'][0]['min'] == 1
    lines = call_hints.brief_lines(net, proj, fn)
    assert lines[1] == '### What the console passed (recorded calls)' and any('a0+0x1C' in line for line in lines)
    net._asm_text = lambda fn, proj: ASM + '/* 34 80001034 00000000 */  nop\n'     # the code changed since
    assert call_hints.saved(net, proj, fn) is None and call_hints.brief_lines(net, proj, fn) == []
    assert call_hints.derive(net, proj, fn, []) is None
    stored = json.loads((tmp_path / 'build' / 'hints' / 'f' / 'hints.json').read_text())
    assert 'key' in stored


def test_the_reader_is_the_mips_one_and_n64_projects_get_the_tool():
    assert arch.get('mips').call_hints is hints.summarize
    from decompnet.app.tools import tool_names
    assert 'get_call_hints' in tool_names(False) and 'get_call_hints' not in tool_names(True)
    assert not call_hints.enabled({}) and call_hints.enabled({'draft_call_hints': True})   # measured no gain: off
