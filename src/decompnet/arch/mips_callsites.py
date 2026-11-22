"""Where a MIPS function is called, and whether each caller reads the result (the `Arch.call_sites` reader).

The O32 ABI returns an integer in $v0 ($v1 too for 64 bits) and a float or double in $f0 ($f0/$f1). A caller that
wants the value reads that register after the `jal` returns, before anything else writes it; a caller that ignores
it writes the register itself, or calls something else (which clobbers it), or leaves. This reads each `jal NAME`
(and a `j NAME` tail call) in a caller's assembly and follows straight-line code after the call: the delay slot
runs before the callee, so the scan starts one instruction later, and it stops at the first branch (the other path
is not read), the next call or the return.

A site's `use`:
- 'value'       $v0 is read first ('kind' says int, or 'long long' when $v1 is read too)
- 'float'/'double' $f0 is read first (a `.s` or `.d` operation, a store, a move to an integer register)
- 'discarded'   $v0 is written, or a later call clobbers it, with no read of $v0, $v1 or $f0 before
- 'passthrough' the caller returns with the registers untouched: its own result is the callee's, which says
                nothing about the callee unless the caller's type is known
- 'unknown'     a branch or the end of the scan came first (also a tail call: `j NAME`)

A caller that reads $v0 after a call to a void function does not exist in compiled code, so 'value' is strong
evidence. 'discarded' is weak: plenty of non-void functions have their result ignored by some callers.
"""
import re

_LINE = re.compile(r'^\s*(?:/\*(?P<comment>.*?)\*/\s*)?(?P<op>[a-z][\w.]*)\s*(?P<rest>[^#]*?)\s*$')
_LABEL = re.compile(r'^\s*(?:\.L\w+|\w+):\s*$')
_REG = re.compile(r'\$(\w+)')
# the float result register, as the listing names it ($fv0 in the N64's and PlayStation's, $f0 elsewhere)
_ALIAS = {'f0': 'fv0', 'fv0f': 'fv0', 'f1': 'fv0'}

_STORES = frozenset({'sb', 'sh', 'sw', 'swl', 'swr', 'sd', 'sdl', 'sdr', 'swc1', 'sdc1', 'swc2', 'sc', 'scd', 'sq'})
_PARTIAL_LOADS = frozenset({'lwl', 'lwr', 'ldl', 'ldr'})  # they merge into their destination: it is read too
_NO_DEST = frozenset({
    'beq', 'bne', 'beqz', 'bnez', 'blez', 'bgtz', 'bltz', 'bgez', 'bltzal', 'bgezal', 'beql', 'bnel', 'beqzl',
    'bnezl', 'blezl', 'bgtzl', 'bltzl', 'bgezl', 'bc1t', 'bc1f', 'bc1tl', 'bc1fl', 'b', 'j', 'jr',
    'mult', 'multu', 'div', 'divu', 'dmult', 'dmultu', 'ddiv', 'ddivu', 'mthi', 'mtlo', 'ctc1', 'mtc0', 'ctc0',
    'teq', 'tne', 'tge', 'tlt', 'tgeu', 'tltu', 'teqi', 'tnei', 'sync', 'cache', 'nop', 'break', 'syscall'})
_BRANCHES = frozenset({
    'beq', 'bne', 'beqz', 'bnez', 'blez', 'bgtz', 'bltz', 'bgez', 'bltzal', 'bgezal', 'beql', 'bnel', 'beqzl',
    'bnezl', 'blezl', 'bgtzl', 'bltzl', 'bgezl', 'bc1t', 'bc1f', 'bc1tl', 'bc1fl', 'b', 'j'})
_CALLS = frozenset({'jal', 'jalr', 'bal'})
_TRACKED = ('v0', 'v1', 'fv0')
WINDOW = 40


def parse(asm):
    """[(address text or '', op, operands, raw line, is_label)] of an assembly listing, directives left out."""
    rows = []
    for raw in (asm or '').splitlines():
        if _LABEL.match(raw) and not raw.lstrip().startswith(('glabel', 'dlabel')):
            rows.append(('', '', '', raw.strip(), True))
            continue
        found = _LINE.match(raw)
        if not found or found['op'].startswith(('glabel', 'dlabel', 'endlabel', 'nonmatching', 'jlabel')):
            continue
        comment = (found['comment'] or '').split()
        rows.append((comment[1] if len(comment) > 1 else '', found['op'], found['rest'], raw.strip(), False))
    return rows


def effects(op, rest):
    """(registers read, registers written) among $v0, $v1 and $f0 ($fv0), by the operation's usual operand order."""
    regs = [_ALIAS.get(r, r) for r in _REG.findall(rest)]
    if op in _CALLS:
        return ({regs[-1]} if op == 'jalr' and regs else set()), set()
    if op in _STORES or op in _NO_DEST or op.startswith('c.'):
        return set(regs), set()
    if op in ('mtc1', 'dmtc1'):
        return set(regs[:1]), set(regs[1:])
    if not regs:
        return set(), set()
    read = set(regs[1:])
    if op in _PARTIAL_LOADS:
        read |= {regs[0]}
    if op.endswith('.d') and regs[0].startswith('f'):
        read |= {r for r in regs[1:]}
    return read, {regs[0]}


def _kind(op):
    """What reading $f0 says about the result: the operation's source format (add.d, cvt.s.d and sdc1 read a
    double; add.s, cvt.d.s, swc1 and mfc1 a float)."""
    return 'double' if op == 'sdc1' or op.endswith('.d') else 'float'



def _scan(rows, start):
    """The use of the call at rows[start]: {'use', 'kind', 'at'} (see the module docstring)."""
    state = {r: None for r in _TRACKED}  # 'read' | 'written'
    reads_v1 = False
    float_kind = None
    first_read = None
    end = None
    pending_stop = None  # the instruction after a branch or return, its delay slot, is still executed
    for at in range(start + 2, min(len(rows), start + 2 + WINDOW)):
        _, op, rest, _, label = rows[at]
        if label:
            continue
        read, written = effects(op, rest)
        # a call's delay slot is the instruction after it; handled by pending_stop below
        for r in _TRACKED:
            if r in read and state[r] is None:
                state[r] = 'read'
                first_read = first_read or (r, at)
                if r == 'fv0':
                    float_kind = _kind(op)
        if 'v1' in read and state['v1'] == 'read':
            reads_v1 = True
        for r in _TRACKED:
            if r in written and state[r] is None and r not in read:
                state[r] = 'written'
        if pending_stop is not None:
            end = pending_stop
            if end == 'call':
                for r in _TRACKED:
                    if state[r] is None:
                        state[r] = 'written'  # the next call clobbers every result register
            break
        if op in _CALLS:
            pending_stop = 'call'
            continue
        if op in _BRANCHES or op == 'jr':
            pending_stop = 'return' if (op == 'jr' and '$ra' in rest) else 'branch'
            continue
    if state['v0'] == 'read':
        kind = 'long long' if state['v1'] == 'read' or reads_v1 else 'int'
        return {'use': 'value', 'kind': kind, 'at': first_read[1]}
    if state['fv0'] == 'read':
        return {'use': float_kind or 'float', 'kind': float_kind or 'float', 'at': first_read[1]}
    if state['v1'] == 'read':
        return {'use': 'value', 'kind': 'long long', 'at': first_read[1]}
    if end == 'return' and state['v0'] is None:
        return {'use': 'passthrough', 'kind': None, 'at': None}
    if state['v0'] == 'written':
        return {'use': 'discarded', 'kind': None, 'at': None}
    return {'use': 'unknown', 'kind': None, 'at': None}


def sites(asm, name, before=3, after=7):
    """Every call of `name` in a function's assembly: [{kind ('call' or 'tail'), index, use, result kind, excerpt}].

    `excerpt` is the instructions around the call (`before` setting up arguments, the call, its delay slot and
    `after` more), each as the listing wrote it."""
    rows = parse(asm)
    out = []
    for i, (address, op, rest, raw, label) in enumerate(rows):
        if label or op not in ('jal', 'j') or rest.split()[:1] != [name]:
            continue
        verdict = {'use': 'unknown', 'kind': None, 'at': None} if op == 'j' else _scan(rows, i)
        lo, hi = max(0, i - before), min(len(rows), i + 2 + after)
        out.append({'kind': 'tail' if op == 'j' else 'call', 'index': i, 'address': address, 'use': verdict['use'],
                    'result': verdict['kind'], 'excerpt': [r[3] for r in rows[lo:hi]], 'mark': i - lo})
    return out
