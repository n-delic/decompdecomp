"""Recover split MIPS global addresses within straight-line aligned feedback.

This only produces repair evidence. Unknown instructions and control flow discard register state;
all proposed C still has to pass the ordinary compiler/link judge.
"""

import re

from ..rom.symbols import AUTO_NAME

EXPR = re.compile(r'%(hi|lo)\(([A-Za-z_]\w*)(?:([+-])(0x[0-9a-fA-F]+|[0-9]+))?\)')
MEMORY = {'lb', 'lbu', 'lh', 'lhu', 'lw', 'sb', 'sh', 'sw'}
WRITES = {'addu', 'add', 'subu', 'sub', 'and', 'or', 'xor', 'nor', 'slt', 'sltu',
          'sll', 'srl', 'sra', 'sllv', 'srlv', 'srav', 'andi', 'ori', 'xori', 'slti', 'sltiu',
          'addiu', 'addi', 'move', 'mfhi', 'mflo', 'lui'}


def expression(text):
    m = EXPR.search(text)
    if not m:
        return None
    base = AUTO_NAME.fullmatch(m[2])
    if not base:
        return None
    offset = int(m[4], 16 if m[4].lower().startswith('0x') else 10) * (-1 if m[3] == '-' else 1) if m[4] else 0
    return m[2], int(base[1], 16), offset


def recover(rows, side):
    registers, found = {}, {}
    for i, row in enumerate(rows):
        if row[0] in {'<', '>'}:
            registers.clear()
            continue
        text = row[side]
        parts = text.split(' ', 1)
        op = parts[0]
        operands = parts[1].split(', ') if len(parts) > 1 else []
        if op == 'lui' and len(operands) == 2:
            registers.pop(operands[0], None)
            value = expression(operands[1])
            if value and operands[1].startswith('%hi('):
                name, base, offset = value
                registers[operands[0]] = (i, name, base, ((base + offset + 0x8000) >> 16) << 16)
            continue
        low, reg = None, None
        if op in MEMORY and len(operands) == 2:
            m = re.fullmatch(r'(.+)\((\$\w+)\)', operands[1])
            if m:
                low, reg = m.groups()
        elif op == 'addiu' and len(operands) == 3:
            reg, low = operands[1:]
        if reg in registers and low is not None:
            high_index, name, base, high = registers[reg]
            value = expression(low)
            immediate = None
            if value and low.startswith('%lo('):
                immediate = (value[1] + value[2]) & 0xffff
            elif re.fullmatch(r'-?(?:0x[0-9a-fA-F]+|[0-9]+)', low):
                immediate = int(low, 16 if '0x' in low else 10)
            if immediate is not None and -32768 <= immediate <= 65535:
                if immediate >= 32768:
                    immediate -= 65536
                address = (high + immediate) & 0xffffffff
                offset = address - base
                expr = name + (f'+{offset}' if offset > 0 else str(offset) if offset < 0 else '')
                suffix = '%lo(' + expr + ')' + ('(' + reg + ')' if op in MEMORY else '')
                found[i] = (high_index, expr, text.rsplit(', ', 1)[0] + ', ' + suffix)
        # Stores read their first operand; loads and arithmetic overwrite it. Clear on anything
        # unmodelled (including branches/calls and their delay slots), rather than guessing liveness.
        if op in MEMORY:
            if op.startswith('l') and operands:
                registers.pop(operands[0], None)
        elif op in WRITES and operands:
            registers.pop(operands[0], None)
        elif op != 'nop':
            registers.clear()
    return found


def normalize(report):
    rows = report.get('rows', [])
    wanted, compiled = recover(rows, 1), recover(rows, 2)
    remove, extra = set(), []
    for i in sorted(wanted.keys() & compiled.keys()):
        a, b = wanted[i], compiled[i]
        # Existing fully-symbolic pairs already carry the source expression. Re-basing those
        # onto the LUI label can hide a useful C symbol, so preserve their original evidence.
        if EXPR.search(rows[i][1]) and EXPR.search(rows[i][2]):
            continue
        if EXPR.sub('%address', a[2]) != EXPR.sub('%address', b[2]):
            continue
        # Synthetic paired evidence allows the existing conservative addend proposer to handle
        # non-adjacent memory low halves without assuming that one LUI has only one consumer.
        remove.update((a[0], b[0], i))
        extra.extend([('i', 'lui $at, %hi(' + a[1] + ')', 'lui $at, %hi(' + b[1] + ')'),
                      ('i', a[2], b[2])])
    return {**report, 'rows': [r for i, r in enumerate(rows) if i not in remove] + extra}


def alias_variant(source, report):
    """Match address-valued C identifiers even when disassembly labels another symbol plus an offset."""
    from collections import defaultdict

    from .similar_matches import TOKENS

    votes, halves = defaultdict(set), defaultdict(set)
    for _, wanted, compiled in report.get('rows', []):
        a, b = EXPR.search(wanted), EXPR.search(compiled)
        av, bv = expression(wanted), expression(compiled)
        if not a or not b or not av or not bv or a[1] != b[1]:
            continue
        if EXPR.sub('%address', wanted) != EXPR.sub('%address', compiled):
            continue
        old, new = (bv[1] + bv[2]) & 0xffffffff, (av[1] + av[2]) & 0xffffffff
        votes[old].add(new)
        halves[old, new].add(a[1])
    addresses = {old: next(iter(values)) for old, values in votes.items()
                 if len(values) == 1 and old not in values and old >= 0x10000000
                 and next(iter(values)) >= 0x10000000
                 and halves[old, next(iter(values))] == {'hi', 'lo'}}
    if len(addresses) > 2:
        return None
    def replace(token):
        name = token.group()
        m = AUTO_NAME.fullmatch(name)
        if m and int(m[1], 16) in addresses:
            return name.rsplit('_', 1)[0] + '_' + format(addresses[int(m[1], 16)], '08X')
        return name
    result = TOKENS.sub(replace, source)
    return result if result != source else None
