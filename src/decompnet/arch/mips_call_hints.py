"""What a MIPS function takes and reads through its arguments, from its code and from recorded real calls.

The O32 ABI passes the first four integer arguments in $a0-$a3 and a leading float or double in $f12 ($fa0) and
$f14 ($fa1). A function's code says which of them it reads before writing (the inputs) and which loads and stores it
makes through them (offset, width, float or integer, signed or not). A recording of the function's real calls (the
console's registers and RAM at its entry: `consoles/n64/emulator.read_recording`) adds what the code cannot: the
values the arguments had and the values found at those offsets, so a hint can say "a1 was 0..40 in every call"
(a u8), "a0 pointed into RAM", "the word at +0x20 is a pointer", "no argument was negative".

`summarize(asm, recordings)` is the Arch.call_hints reader. A recording is anything with `.gpr` (32 values), `.fpr`
(32 words) and `.read(address, width)` (the RAM word at an address before the call, or None). Nothing here runs the
game; the app (`app/call_hints.py`) records, and a hint is a lead the judge confirms.
"""
from __future__ import annotations

import re
import struct

from .mips_callsites import effects, parse

INT_ARGS = ('a0', 'a1', 'a2', 'a3')
FLOAT_ARGS = ('fa0', 'fa1')
SAVED = frozenset({'s0', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 'fp', 'gp', 'sp', 'ra', 'zero'})
RAM = (0x80000000, 0x80800000)
MIN_CALLS = 2  # fewer recorded calls say nothing about a range
_MEMORY = re.compile(r'^(-?(?:0x[0-9A-Fa-f]+|\d+))\((\$\w+)\)$')
_WIDTH = {'lb': 1, 'lbu': 1, 'lh': 2, 'lhu': 2, 'lw': 4, 'lwu': 4, 'ld': 8, 'lwc1': 4, 'ldc1': 8,
          'sb': 1, 'sh': 2, 'sw': 4, 'sd': 8, 'swc1': 4, 'sdc1': 8}


def arg_use(asm):
    """{'int': [indexes], 'float': [indexes]}: the argument registers the code reads before it writes them, in the
    order of the listing (a stack store of an argument is a read)."""
    state = {}
    for _, op, rest, _, label in parse(asm):
        if label:
            continue
        read, written = effects(op, rest)
        for reg in read:
            if reg in INT_ARGS + FLOAT_ARGS:
                state.setdefault(reg, 'input')
        for reg in written:
            if reg in INT_ARGS + FLOAT_ARGS:
                state.setdefault(reg, 'local')
    return {'int': [i for i, r in enumerate(INT_ARGS) if state.get(r) == 'input'],
            'float': [i for i, r in enumerate(FLOAT_ARGS) if state.get(r) == 'input']}


def arg_count(use) -> int:
    """How many integer arguments the code takes: up to the highest one it reads (the ones before it are declared
    whether it reads them or not)."""
    return max(use['int'], default=-1) + 1


def narrowing(asm):
    """{argument index: 'u8' | 'u16' | 's8' | 's16'}: an argument the code masks or sign-extends where it first uses it
    (`andi $t, $a1, 0xFF`; `sll`/`sra` by 24 or 16), which is how a narrow parameter reads in code."""
    out, rows = {}, [r for r in parse(asm) if not r[4]]
    for at, (_, op, rest, _, _) in enumerate(rows):
        regs = re.findall(r'\$(\w+)', rest)
        if len(regs) < 2 or regs[1] not in INT_ARGS:
            continue
        index = INT_ARGS.index(regs[1])
        if index in out:
            continue
        constant = rest.split(',')[-1].strip()
        if op == 'andi' and constant in ('0xFF', '0xff', '255'):
            out[index] = 'u8'
        elif op == 'andi' and constant in ('0xFFFF', '0xffff', '65535'):
            out[index] = 'u16'
        elif op == 'sll' and constant in ('24', '16') and at + 1 < len(rows):
            follow = rows[at + 1]
            if follow[1] in ('sra', 'srl') and follow[2].split(',')[-1].strip() == constant:
                out[index] = ('s' if follow[1] == 'sra' else 'u') + ('8' if constant == '24' else '16')
    return out


def field_accesses(asm):
    """[{'arg', 'offset', 'width', 'kinds' ({'load', 'store'}), 'float', 'signed' (True, False or None)}]: what the
    code loads and stores through each argument register, a copy of it followed (`move $s0, $a0`) until the register
    is written otherwise, offsets as written (a `%lo(symbol)` is a global, not a field)."""
    alias = {r: i for i, r in enumerate(INT_ARGS)}
    found = {}
    for _, op, rest, _, label in parse(asm):
        if label:
            continue
        if op in _WIDTH:
            parts = [p.strip() for p in rest.split(',', 1)]
            memory = _MEMORY.match(parts[1]) if len(parts) == 2 else None
            if memory and memory[2][1:] in alias:
                offset = int(memory[1], 0)
                width = _WIDTH[op]
                key = (alias[memory[2][1:]], offset, width, op.endswith(('c1',)))
                entry = found.setdefault(key, {'kinds': set(), 'signed': None})
                entry['kinds'].add('load' if op.startswith('l') else 'store')
                if op in ('lb', 'lh'):
                    entry['signed'] = True
                elif op in ('lbu', 'lhu'):
                    entry['signed'] = False if entry['signed'] is None else entry['signed']
        if op in ('jal', 'jalr'):
            alias = {r: i for r, i in alias.items() if r in SAVED}
            continue
        read, written = effects(op, rest)
        regs = re.findall(r'\$(\w+)', rest)
        copy = None
        if op == 'move' and len(regs) == 2 or op in ('addu', 'or', 'daddu') and len(regs) == 3 and regs[2] == 'zero' \
                or op == 'addiu' and len(regs) == 2 and rest.split(',')[-1].strip() in ('0', '0x0'):
            copy = regs[:2]
        for reg in written:
            alias.pop(reg, None)
        if copy and copy[1] in alias and copy[0] != 'zero':
            alias[copy[0]] = alias[copy[1]]
    return [{'arg': k[0], 'offset': k[1], 'width': k[2], 'float': k[3], 'kinds': v['kinds'], 'signed': v['signed']}
            for k, v in sorted(found.items())]


def _signed(value, width):
    bits = width * 8
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def _fits(low, high):
    """The narrowest of u8, s8, u16, s16 (in that order of preference) that holds low..high, or None."""
    for name, lo, hi in (('u8', 0, 255), ('s8', -128, 127), ('u16', 0, 65535), ('s16', -32768, 32767)):
        if lo <= low and high <= hi:
            return name
    return None


def describe_ints(values, width=4, signed=None):
    """{'kind': 'pointer' | 'integer' | 'zero', 'min', 'max', 'distinct', 'negative', 'fits', 'samples'} of the values an
    integer argument or field had. A pointer: every value is zero or inside RAM, 4-aligned, and some is not zero."""
    values = [v & ((1 << (width * 8)) - 1) for v in values]
    if not values:
        return None
    pointerish = width == 4 and all(v == 0 or (RAM[0] <= v < RAM[1] and not v & 3) for v in values) and any(values)
    if pointerish:
        return {'kind': 'pointer', 'samples': len(values), 'distinct': len(set(values)), 'null': values.count(0)}
    # an argument or a word is an s32; a narrow field is signed or not as its load says (lb/lh: signed)
    shown = [_signed(v, width) if width == 4 or signed else v for v in values]
    low, high = min(shown), max(shown)
    return {'kind': 'zero' if not any(shown) else 'integer', 'min': low, 'max': high, 'samples': len(shown),
            'distinct': len(set(shown)), 'negative': low < 0, 'fits': _fits(low, high)}


def describe_floats(words):
    """{'min', 'max', 'samples', 'distinct', 'plausible'} of single-precision values (given as 32-bit words);
    plausible: every one is finite and, if not zero, between 1e-9 and 1e12 (not an integer's bits)."""
    values = [struct.unpack('>f', struct.pack('>I', w & 0xFFFFFFFF))[0] for w in words]
    finite = [v for v in values if v == v and abs(v) != float('inf')]
    return {'min': min(finite) if finite else None, 'max': max(finite) if finite else None, 'samples': len(values),
            'distinct': len(set(words)),
            'plausible': len(finite) == len(values) and all(v == 0 or 1e-9 < abs(v) < 1e12 for v in values)}


def summarize(asm, recordings):
    """The hints for one function: {'calls', 'count' (integer arguments), 'ints': [{'arg', ...describe_ints}],
    'floats': [{'arg', ...describe_floats}], 'narrow' (what the code's own masks say), 'fields': [{'arg', 'offset',
    'width', 'float', 'kinds', ...describe_ints | describe_floats}]}. Ranges come from the recordings only."""
    use = arg_use(asm)
    out = {'calls': len(recordings), 'count': arg_count(use), 'narrow': narrowing(asm), 'ints': [], 'floats': [],
           'fields': [], 'float_args': use['float']}
    for index in use['int']:
        found = describe_ints([r.gpr[4 + index] for r in recordings])
        if found:
            out['ints'].append({'arg': index, **found})
    for index in use['float']:
        words = [r.fpr[12 + 2 * index] for r in recordings]
        if words:
            out['floats'].append({'arg': index, **describe_floats(words)})
    for access in field_accesses(asm):
        entry = {k: access[k] for k in ('arg', 'offset', 'width', 'float', 'kinds')}
        entry['kinds'] = sorted(entry['kinds'])
        if 'load' in access['kinds']:
            values = []
            for r in recordings:
                base = r.gpr[4 + access['arg']] & 0xFFFFFFFF
                if RAM[0] <= base < RAM[1]:
                    got = r.read(base + access['offset'], access['width'])
                    if got is not None:
                        values.append(got)
            if values:
                found = (describe_floats(values) if access['float'] and access['width'] == 4
                         else describe_ints(values, access['width'], access['signed']))
                entry.update(found or {})
        out['fields'].append(entry)
    return out


def consistent_narrowing(hints):
    """{argument index: type}: where the code masks an argument to a narrow type AND the recorded values fit it
    (at least MIN_CALLS calls, more than one distinct value: a constant says nothing)."""
    out = {}
    narrow = {int(k): v for k, v in (hints.get('narrow') or {}).items()}  # (saved as JSON: its keys are text)
    for item in hints.get('ints', []):
        kind = narrow.get(item['arg'])
        if (kind and item['kind'] in ('integer', 'zero') and hints['calls'] >= MIN_CALLS and item['distinct'] >= 2
                and item.get('fits') and _holds(kind, item['min'], item['max'])):
            out[item['arg']] = kind
    return out


def _holds(kind, low, high):
    lo, hi = {'u8': (0, 255), 's8': (-128, 127), 'u16': (0, 65535), 's16': (-32768, 32767)}[kind]
    return lo <= low and high <= hi


def lines(hints):
    """The hints as sentences for the brief (empty when nothing was recorded)."""
    if not hints or not hints.get('calls'):
        return []
    n = hints['calls']
    parts = []
    for item in hints['ints']:
        name = f"a{item['arg']}"
        if item['kind'] == 'pointer':
            parts.append(f"{name} a pointer into RAM in all {n} calls" + (f" ({item['null']} null)" if item['null'] else ''))
        elif item['kind'] == 'zero':
            parts.append(f'{name} was 0 in all {n} calls')
        else:
            note = f"fits {item['fits']}" if item.get('fits') else 'does not fit 16 bits'
            parts.append(f"{name} {item['min']}..{item['max']} ({note}{', negative' if item['negative'] else ', never negative'})")
    for item in hints['floats']:
        what = f"{item['min']:g}..{item['max']:g}" if item['min'] is not None else 'not a float'
        parts.append(f"f{12 + 2 * item['arg']} {what}" + ('' if item['plausible'] else ' (not float-like: check it is a float)'))
    text = [f"Recorded calls ({n} real calls of this function on the console): " + '; '.join(parts) + '.'] if parts else []
    reads, writes = [], []
    for f in hints['fields']:
        label = f"a{f['arg']}+0x{f['offset']:X} {f['width'] * 8}-bit" + (' float' if f['float'] else '')
        if 'load' in f['kinds'] and f.get('kind') == 'pointer':
            reads.append(label + ' a pointer')
        elif 'load' in f['kinds'] and f.get('fits'):
            reads.append(f"{label} {f['min']}..{f['max']}")
        elif 'load' in f['kinds'] and f.get('min') is not None:
            reads.append(f"{label} {f['min']:g}..{f['max']:g}")
        elif 'load' in f['kinds']:
            reads.append(label)
        if 'store' in f['kinds']:
            writes.append(label)
    if reads:
        text.append('Read through the arguments: ' + ', '.join(reads[:12]) + '.')
    if writes:
        text.append('Written through the arguments: ' + ', '.join(writes[:12]) + '.')
    return text
