from types import SimpleNamespace

from decompnet.app import residual_trials


def assembly(*lines):
    return '\n'.join(f'/* {i*4:X} {0x80000000+i*4:08X} 00000000 */ {line}'
                     for i,line in enumerate(lines))


def test_target_branch_must_share_operands_and_delay_slot():
    source = 'int f(int i) { int v; if (i != 2) { return 0; } v = i < 4; tail: return v ? 1 : 0; }'
    branch = ('|', 'bne $a0, $v0, 0x20', 'bnel $a0, $v0, 0x20')
    slot = (' ', 'move $v0, $zero', 'move $v0, $zero')
    assert len(residual_trials.proposals(source,'f','',[branch,slot])) == 2
    assert not residual_trials.proposals(source,'f','',[branch,('|',slot[1],'nop')])


def test_saved_register_rotation_rejects_different_prologue_slots():
    stores = [f'sw $s{i}, {i*4}($sp)' for i in (1,2,3)]
    rows = [('i',a,b) for a,b in zip(stores,stores[1:]+stores[:1])]
    rows += [('r',f'move $a0, $s{a}',f'move $a0, $s{b}') for a,b in ((1,2),(2,3),(3,1))]
    assert residual_trials.prologue_rotation(rows)
    rows[0] = ('i',stores[0],'sw $s2, 0($sp)')
    assert not residual_trials.prologue_rotation(rows)


def test_forwarding_needs_an_untouched_caller_register_and_a_callee_read():
    code = ['sw $ra, 16($sp)', 'jal helper', 'addiu $a0, $a0, 4', 'c.lt.s $f0, $f2']
    callee = assembly('lwc1 $f0, 0($a0)','lwc1 $f2, 0($a1)','jr $ra','nop')
    helper = {'asm':callee}
    store = SimpleNamespace(all=lambda *_: [helper])
    net = SimpleNamespace(_asm_text=lambda fn,proj:fn['asm'], project_store=lambda _:store)
    source = 'float helper(void *); float f(char *s, float *p) { return helper(s + 4); }'
    fn = {'name':'f','asm':assembly(*code)}
    assert residual_trials.forwarded_pointer(net,{'id':1},fn,source,'')
    fn['asm'] = assembly(*code[:2],'move $a1, $zero',code[-1])
    assert not residual_trials.forwarded_pointer(net,{'id':1},fn,source,'')
    fn['asm'] = assembly(*code)
    helper['asm'] = assembly('lwc1 $f0, 0($a0)','jr $ra','nop')
    assert not residual_trials.forwarded_pointer(net,{'id':1},fn,source,'')


def test_frame_followups_keep_a_measured_regression_with_smaller_frame():
    def rows(frame):
        return [('s','addiu $sp, $sp, -0x68',f'addiu $sp, $sp, -{frame:#x}')]
    source = 'extern int buf; void f(int x) { format((char *)&buf, x); notify(x, x, &buf, 0); }'
    assert residual_trials.followups(source,'f','',rows(0x78),rows(0x70))
    assert not residual_trials.followups(source,'f','',rows(0x78),rows(0x78))


def test_shared_float_return_prioritizes_the_rewrite_and_explains_the_hint():
    source = ('int f(int mode, float a, float b) {\n int flag;\n'
              ' if (mode) { flag = a < b; goto compare; }\n'
              ' flag = b < a;\n compare: return flag ? 1 : 0;\n }')
    target = ['bne $a1, $v0, 0x30', 'move $v0, $zero', 'c.lt.s $f0, $f2',
              'bc1t 0x30', 'addiu $v0, $zero, 1']
    rows = [('|',target[0],target[0].replace('bne','bnel'))] + [(' ',t,t) for t in target[1:]]
    [(family, candidate)] = residual_trials.proposals(source,'f','',rows)
    assert family == 'shared_fp_return' and candidate.count('goto __repair_fp_yes') == 2
    assert 'later shared floating-point return' in residual_trials.shared_fp_return_hint(source,'f','',rows,'mips')
    assert not residual_trials.shared_fp_return_hint(source,'f','',rows,'ppc')
    rows[1] = ('|',target[1],'nop')
    assert not residual_trials.shared_fp_return_hint(source,'f','',rows,'mips')


BYTE_MASK_ROWS = [
    (' ', 'lbu $v0, 0x40($a0)', 'lbu $v0, 0x40($a0)'),
    (' ', 'addiu $v1, $zero, 2', 'addiu $v1, $zero, 2'),
    ('i', 'andi $v0, $v0, 0xff', 'andi $v0, $v0, 0x7f'),
    (' ', 'bne $v0, $v1, 0x24', 'bne $v0, $v1, 0x24'),
]
BYTE_GUARD = """int f0(int i) {
    if ((*(u8 *)((u8 *)&flags + (i * 16)) & 0x7f) == 2) {
        return 1;
    }
    return 0;
}
"""


def test_single_mask_followup_is_routed_from_linked_rows():
    proposals = residual_trials.proposals(BYTE_GUARD, 'f0', '', BYTE_MASK_ROWS)
    assert proposals and all(family in residual_trials.FAMILIES for family, _ in proposals)
    assert all(family == 'last_byte_mask' for family, _ in proposals)
    second = [*BYTE_MASK_ROWS, ('i', 'nop', 'move $a0, $a1')]
    assert not residual_trials.proposals(BYTE_GUARD, 'f0', '', second)
