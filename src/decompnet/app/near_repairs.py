"""Bounded local hypotheses for nearly matched code; only the normal byte judge accepts them."""

import re

from ..arch.mips_addresses import addresses
from ..forks.csource import _blank, find


def variants(source, name, arch, asm=''):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    body = clean[opening + 1:span[1] - 1]
    if opening < 0 or '#' in body:
        return []
    offset, proposals = opening + 1, []

    def apply(edits):
        ordered = sorted(edits)
        if any(a[1] > b[0] for a,b in zip(ordered, ordered[1:])):
            return
        text = source
        for a, b, value in reversed(ordered):
            text = text[:a] + value + text[b:]
        if text != source and text not in proposals:
            proposals.append(text)

    # Keep aliases as expressions at their use sites. Renaming one to an existing label can make GCC
    # cache a shared address in a saved register and lose an otherwise matching instruction layout.
    if arch == 'mips':
        targets = addresses(asm)
        edits = []
        loads = {m[2]: m[1] for m in re.finditer(r'\bextern\s+((?:s32|u32|char|u8|s8|void)(?:\s*\*)?)\s*(D_[0-9A-Fa-f]{8})\s*;', clean[:opening])}
        arrays = set(re.findall(r'\bextern\s+(?:char|u8|s8)\s+(D_[0-9A-Fa-f]{8})\[\s*\]\s*;', clean[:opening]))
        for m in re.finditer(r'\b(?:0x[0-9A-Fa-f]{8}|D_[0-9A-Fa-f]{8})\b', body):
            old = int(m[0][2:], 16)
            if old in targets:
                continue
            nearby = [v for v in targets if abs(v-old) == 65536 or 0 < abs(v-old) <= 16]
            if len(nearby) != 1:
                continue
            new = nearby[0]
            if m[0].startswith('D_'):
                if m[0] in arrays:
                    value = '(' + m[0] + f'{new-old:+d})'
                elif m[0] in loads:
                    value = '(*(' + loads[m[0]] + ' *)' + hex(new) + ')'
                else:
                    continue
            else:
                value = hex(new)
            edit = (offset + m.start(), offset + m.end(), value)
            apply([edit]); edits.append(edit)
        address_edits = list(edits)
        if len(edits) > 1:
            apply(edits)

    # Unreferenced ordinary local byte padding affects old compiler stack frames. Do not remove
    # volatile storage, initialized objects or anything whose address/size is used.
    for m in re.finditer(r'(?m)^\s*(?:char|u8|s8)\s+((?:pad|padding)\w*)\s*\[\s*(?:0x[\da-fA-F]+|\d+)\s*\]\s*;', body):
        if len(re.findall(r'\b' + re.escape(m[1]) + r'\b', body)) == 1:
            apply([(offset + m.start(), offset + m.end(), '')])

    # Independent stores into a local aggregate, with constant indices and no calls/volatile accesses.
    # Sorting their destinations can reproduce a compiler's original load/store order. Keep scalar
    # assignments between them in place. Never reorder through control flow or an unknown statement.
    local = set(re.findall(r'(?m)^\s*(?!(?:volatile|const|return)\b)(?:struct\s+)?[A-Za-z_]\w*\s+([A-Za-z_]\w*)\s*;', body))
    store = re.compile(r'(?P<lhs>(?P<root>\w+)(?:\.\w+|\[\d+\])+)' r'\s*=\s*(?P<rhs>[^;{}]+);')
    runs, current, last = [], [], 0
    for m in store.finditer(body):
        between = body[last:m.start()]
        pure = not re.search(r'\b\w+\s*\(|\*|->|\bvolatile\b|\+\+|--|=', m['rhs'])
        gap = not re.sub(r'\b\w+\s*=\s*\w+\s*;', '', between).strip()
        if not gap:
            if len(current) > 1:
                runs.append(current)
            current = []
        if m['root'] in local and pure:
            current.append(m)
        else:
            current = []
        last = m.end()
    if len(current) > 1:
        runs.append(current)
    for run in runs[:2]:
        if len(run) > 8 or len({m['lhs'] for m in run}) != len(run):
            continue
        # A read of any destination makes the order observable even within one local object.
        if any(a['lhs'] in b['rhs'] for a in run for b in run):
            continue
        for reverse in (False, True):
            groups = sorted({m['lhs'].split('[', 1)[0] for m in run}, reverse=reverse)
            ordered = sorted(run, key=lambda m: (groups.index(m['lhs'].split('[', 1)[0]), m['lhs']))
            apply([(offset+a.start(), offset+a.end(), source[offset+b.start():offset+b.end()]) for a,b in zip(run, ordered)])
    # Candidate-local byte padding pairs preserve the offsets of fields after the second pad.
    # Restrict to small, ordinary declarations; unknown packing and shared header types are left alone.
    if arch in {'mips', 'ppc', 'arm', 'thumb', 'x86'} and not re.search(r'\b(?:packed|union|pragma)\b', clean):
        pair = re.compile(r'\b(?:u8|char|s8)\s+pad\w*\[(0x[0-9A-Fa-f]+|\d+)\]\s*;'
                          r'\s*(?:s16|u16|short)\s+\w+\s*;\s*(?:u8|char|s8)\s+pad\w*\[(0x[0-9A-Fa-f]+|\d+)\]')
        bias = re.search(r'\(\s*(?:u8|char)\s*\*\s*\)\s*(\w+)\s*\+\s*(0x[0-9A-Fa-f]+|\d+)', body)
        for m in list(pair.finditer(clean[:opening]))[:1]:
            a, b = (int(v, 16 if v.lower().startswith('0x') else 10) for v in m.groups())
            for delta in (-8, 8):
                if min(a+delta, b-delta) <= 0 or max(a+delta, b-delta) > 4096:
                    continue
                edits = [(m.start(1), m.end(1), str(a+delta)), (m.start(2), m.end(2), str(b-delta))]
                apply(edits)
                if bias:
                    apply(edits + [(offset+bias.start(), offset+bias.end(), '(u8 *)'+bias[1])] + (address_edits if arch == 'mips' else []))
    return proposals[:16]
