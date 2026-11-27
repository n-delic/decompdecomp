"""A delay slot that changes what a branch's condition was computed from, written above the branch before m2c reads it.

    lbu   $v0, 0($s0)
    beq   $v0, $v1, .Ldone
    addiu $s0, $s0, 1          # runs after $v0 was loaded

m2c records the branch's condition as the expression (`*var_s0 != '.'`), then writes the delay slot's `var_s0 += 1;`
ahead of the `if`: in a loop, where the register is one of m2c's own variables, the draft compares the byte after the
one the target compares (a machine draft of an asset loader's extension scan did; an `slti` before the counter's
increment drafted `while (i < 10)` on the new `i` the same way). With the same instructions in program order, the
increment before the branch, m2c keeps the old value in a temporary and the draft is right. For a plain (not
"likely") branch the two orders do the same thing whenever the slot writes a register the branch doesn't read, so
that is what m2c is given. Only the draft's input changes; the target is untouched.
"""
import re

_LINE = re.compile(r'^[ \t]*(?:/\*.*?\*/[ \t]*)?(?P<op>[a-z][a-z0-9.]*)(?:[ \t]+(?P<operands>[^#]*?))?[ \t]*(?:#.*)?$')
_REGISTER = re.compile(r'\$\w+')
_BRANCHES = {'beq', 'bne', 'beqz', 'bnez', 'blez', 'bgtz', 'bltz', 'bgez'}
# instructions whose first register is the one they write and the others the ones they read
_WRITERS = {'add', 'addu', 'addi', 'addiu', 'sub', 'subu', 'daddu', 'daddiu', 'dsubu', 'and', 'andi', 'or', 'ori',
            'xor', 'xori', 'nor', 'sll', 'srl', 'sra', 'sllv', 'srlv', 'srav', 'slt', 'slti', 'sltu', 'sltiu', 'move',
            'li', 'lui', 'negu', 'not', 'lb', 'lbu', 'lh', 'lhu', 'lw', 'lwu', 'ld'}
_NOT_MOVED = {'$zero', '$0', '$sp', '$29'}


def _instruction(line):
    """(op, operands' registers) of an instruction line, or None for a label, a directive or a blank line."""
    hit = _LINE.match(line)
    if not hit or hit['op'] in ('glabel', 'dlabel', 'jlabel', 'alabel', 'endlabel', 'nonmatching', 'enddlabel'):
        return None
    return hit['op'], _REGISTER.findall(hit['operands'] or '')


def _depends(lines, branch, register, written):
    """Whether the instruction that set `register`, earlier in the branch's block, read `written`."""
    for line in reversed(lines[:branch]):
        found = _instruction(line)
        if not found:
            if line.strip().endswith(':') or 'label' in line:
                return False
            continue
        op, registers = found
        if op.startswith(('b', 'j')):
            return False
        if op in _WRITERS and registers:
            if registers[0] == register:
                return written in registers[1:]
            if registers[0] == written:
                return False  # `written` already changed after anything earlier read it
    return False


def hoist(asm_text: str) -> str:
    """The assembly with each such delay-slot instruction moved above its branch and a `nop` left in the slot."""
    lines = asm_text.split('\n')
    at = 0
    while at + 1 < len(lines):
        branch, slot = _instruction(lines[at]), _instruction(lines[at + 1])
        if branch and slot and branch[0] in _BRANCHES and slot[0] in _WRITERS and slot[1]:
            written = slot[1][0]
            if (written not in _NOT_MOVED and written not in branch[1]
                    and any(_depends(lines, at, register, written) for register in branch[1])):
                lines[at:at + 2] = [lines[at + 1], lines[at], 'nop']
                at += 2
        at += 1
    return '\n'.join(lines)
