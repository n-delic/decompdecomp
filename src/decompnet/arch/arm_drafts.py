"""Small straight-line ARM/Thumb instruction shapes as C, always compiled and judged (#133).

Only caller-saved integer registers, fixed-offset loads/stores and a return are lifted. Branches, floating point,
stack frames, conditional execution and unknown instructions go to the machine decompiler. A shape is a candidate,
not evidence of a match; no instruction bytes are injected into C.
"""

from __future__ import annotations

import re

import capstone

MAX_BYTES = 64


class NoShape(ValueError):
    pass


def exact(code: bytes, address: int, thumb: bool, name: str, header: str = '') -> list[str]:
    if len(code) > MAX_BYTES or re.search(rf'\b{re.escape(name)}\s*\(', header):
        return []
    reader = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB if thumb else capstone.CS_MODE_ARM)
    reader.detail = True
    regs, used, statements = {}, set(), []
    wrote_return = False

    def register(ins, op):
        name = ins.reg_name(op.reg)
        if name not in ('r0', 'r1', 'r2', 'r3'):
            raise NoShape('not a caller-saved argument register')
        if name not in regs:
            index = int(name[1:])
            used.add(index)
            return f'arg{index}'
        return regs[name]

    def value(ins, op):
        if op.type == capstone.arm.ARM_OP_IMM:
            result = f'{op.imm & 0xFFFFFFFF}U'
        elif op.type == capstone.arm.ARM_OP_REG:
            result = register(ins, op)
        else:
            raise NoShape('not an integer operand')
        if op.shift.type:
            shifts = {capstone.arm.ARM_SFT_LSL: '<<', capstone.arm.ARM_SFT_LSR: '>>', capstone.arm.ARM_SFT_ASR: '>>'}
            if op.shift.type not in shifts:
                raise NoShape('a register shift or rotate')
            if op.shift.type == capstone.arm.ARM_SFT_ASR:
                result = f'((int)({result}))'
            result = f'(({result}) {shifts[op.shift.type]} {op.shift.value})'
        return result

    def memory(ins, op, ctype):
        if op.type != capstone.arm.ARM_OP_MEM or op.mem.index or ins.writeback:
            raise NoShape('an indexed or writeback memory operand')
        base = ins.reg_name(op.mem.base)
        if base not in ('r0', 'r1', 'r2', 'r3'):
            raise NoShape('a stack or literal-pool memory operand')
        fake = type('Register', (), {'reg': op.mem.base})()
        return f'(*({ctype} *)((unsigned char *)({register(ins, fake)}) + {op.mem.disp}))'

    try:
        for ins in reader.disasm(code, address):
            mnemonic, ops = ins.mnemonic, ins.operands
            if ins.cc not in (capstone.arm.ARM_CC_AL, capstone.arm.ARM_CC_INVALID):
                raise NoShape('a conditional instruction')
            if mnemonic == 'bx' and ins.op_str == 'lr':
                ret = f'return {regs["r0"]};' if wrote_return else 'return;'
                result_type = 'unsigned int' if wrote_return else 'void'
                params = ', '.join(f'unsigned int arg{k}' for k in range(max(used) + 1)) if used else 'void'
                return [f'{result_type} {name}({params}) {{\n' + '\n'.join(statements + [ret]) + '\n}']
            if mnemonic == 'nop':
                continue
            if not ops or ops[0].type != capstone.arm.ARM_OP_REG:
                raise NoShape('an unsupported destination')
            dst = ins.reg_name(ops[0].reg)
            if mnemonic.startswith('str'):
                ctype = {'str': 'unsigned int', 'strb': 'unsigned char', 'strh': 'unsigned short'}.get(mnemonic)
                if ctype is None or len(ops) != 2:
                    raise NoShape('an unsupported store')
                statements.append(f'{memory(ins, ops[1], ctype)} = {value(ins, ops[0])};')
                continue
            if dst not in ('r0', 'r1', 'r2', 'r3'):
                raise NoShape('a callee-saved destination')
            if mnemonic in ('mov', 'movs', 'movw') and len(ops) == 2:
                expression = value(ins, ops[1])
            elif mnemonic == 'movt' and len(ops) == 2 and dst in regs:
                expression = f'(({regs[dst]}) & 65535U) | ({value(ins, ops[1])} << 16)'
            elif mnemonic in ('mvn', 'mvns') and len(ops) == 2:
                expression = f'~({value(ins, ops[1])})'
            elif mnemonic.startswith('ldr') and len(ops) == 2:
                ctype = {'ldr': 'unsigned int', 'ldrb': 'unsigned char', 'ldrh': 'unsigned short',
                         'ldrsb': 'signed char', 'ldrsh': 'short'}.get(mnemonic)
                if ctype is None:
                    raise NoShape('an unsupported load')
                expression = memory(ins, ops[1], ctype)
            else:
                operation = mnemonic.removesuffix('s')
                operators = {'add': '+', 'sub': '-', 'mul': '*', 'and': '&', 'orr': '|', 'eor': '^',
                             'lsl': '<<', 'lsr': '>>', 'asr': '>>', 'rsb': '-', 'bic': '&'}
                if operation not in operators or len(ops) not in (2, 3):
                    raise NoShape('an unsupported arithmetic instruction')
                left = value(ins, ops[1] if len(ops) == 3 else ops[0])
                right = value(ins, ops[2] if len(ops) == 3 else ops[1])
                if operation == 'rsb':
                    left, right = right, left
                if operation == 'bic':
                    right = f'~({right})'
                if operation == 'asr':
                    left = f'((int)({left}))'
                expression = f'(({left}) {operators[operation]} ({right}))'
            regs[dst] = expression
            wrote_return |= dst == 'r0'
    except NoShape:
        return []
    return []
