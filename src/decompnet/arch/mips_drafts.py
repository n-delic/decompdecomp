"""Bounded register and stack evidence for initial C drafts, shared by MIPS consoles."""
import re

from .mips import abi_register_names

WIDTHS = {'lb': 's8', 'lbu': 'u8', 'lh': 's16', 'lhu': 'u16', 'lw': 's32',
          'lwc1': 'f32', 'ldc1': 'f64', 'ld': 's64'}


def read(asm):
    """Track named word-load call targets and fixed-frame loads; ambiguous frames get no stack verdict."""
    lines = [re.sub(r'/\*.*?\*/|#.*', '', s).strip() for s in abi_register_names(asm).splitlines()]
    lines = [s for s in lines if s and not s.startswith(('.', 'glabel', 'endlabel')) and not s.endswith(':')]
    registers = {}; tables = set(); slots = {}; adjustments = []; valid = True
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        op, operands = parts
        args = [s.strip() for s in operands.split(',')]
        if op == 'jalr':
            evidence = registers.get(args[-1], ())
            if evidence and evidence[0] == 'word':
                tables.update(evidence[1])
        dest = args[0]
        if dest == '$sp' and op not in ('sw', 'sd', 'sq'):
            if op == 'addiu' and len(args) == 3 and args[1] == '$sp':
                try: adjustments.append(int(args[2], 0))
                except ValueError: valid = False
            else:
                valid = False
        slot = re.fullmatch(r'(-?0x[\da-fA-F]+|-?\d+)\(\$sp\)', args[-1])
        if op in WIDTHS and slot:
            slots.setdefault(int(slot[1], 0), set()).add(WIDTHS[op])
        if op.startswith(('b', 'j')):
            if op in ('jal', 'jalr'):
                for reg in ['$v0', '$v1', *('$a'+str(n) for n in range(4)), *('$t'+str(n) for n in range(10))]:
                    registers.pop(reg, None)
            else:
                registers.clear()  # never infer a table across an unproved control-flow merge
            continue
        if op in ('sw', 'sb', 'sh', 'sd', 'sq', 'swc1', 'sdc1') or not dest.startswith('$'):
            continue
        symbols = set(re.findall(r'%(?:hi|lo)\(([A-Za-z_]\w*)\)', operands))
        inherited = set().union(*(set(registers.get(reg, ('address', ()))[1]) for reg in args[1:] if reg.startswith('$')))
        if op == 'lw':
            base = re.search(r'\((\$\w+)\)$', operands)
            inherited |= set(registers.get(base[1], ('address', ()))[1]) if base else set()
            registers[dest] = ('word', symbols | inherited)
        elif op in ('lui', 'addiu', 'addu', 'or', 'ori', 'sll'):
            registers[dest] = ('address', symbols | inherited)
        else:
            registers.pop(dest, None)
    negatives = [n for n in adjustments if n < 0]
    frame = -negatives[0] if valid and len(negatives) == 1 and all(n in (negatives[0], -negatives[0]) for n in adjustments) else 0
    return {'first_call': first_call(asm), 'callback_tables': sorted(tables), 'frame': frame if 0 < frame <= 4096 else 0,
            'stack_loads': {offset: next(iter(types)) for offset, types in slots.items() if len(types) == 1}}


def first_call(asm):
    """Contiguous GPR arguments set for the first unconditional direct call, delay slot included."""
    lines = [re.sub(r'/\*.*?\*/|#.*', '', s).strip() for s in abi_register_names(asm).splitlines()]
    lines = [s for s in lines if s and not s.startswith(('.', 'glabel', 'endlabel')) and not s.endswith(':')]
    written = set()
    stores = {'sw','sd','sq','sb','sh','swc1','sdc1'}
    for i,line in enumerate(lines):
        parts = line.split(None,1)
        if not parts:continue
        op = parts[0]
        if op == 'jal':
            if len(parts)!=2 or not re.fullmatch(r'[A-Za-z_]\w*',parts[1]):return None
            slot = lines[i+1] if i+1<len(lines) else ''
            match = re.match(r'(\w+)\s+\$a([0-3])\b',slot)
            if match and match[1] not in stores:written.add(int(match[2]))
            if not written or min(written)==0 or written != set(range(min(written),max(written)+1)):
                return None
            return {'callee':parts[1], 'first':min(written), 'count':max(written)+1}
        if op.startswith(('b','j')) or re.search(r'\$f\w*',line):return None
        match = re.match(r'(\w+)\s+\$a([0-3])\b',line)
        if match and match[1] not in stores:written.add(int(match[2]))
    return None
