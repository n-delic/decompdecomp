"""Assembly for machine drafts from a MIPS reference object's actual instructions."""
from __future__ import annotations

import re


def assembly(function) -> str | None:
    """Keep assembled delay slots and relocation symbols; decline unresolved control flow."""
    from ..scoring import disassemble

    code = function.code
    identifier = r'[A-Za-z_.$][A-Za-z0-9_.$]*'
    if (not code or len(code) > 256 * 1024 or len(code) % 4
            or not re.fullmatch(identifier, function.name)
            or any(not re.fullmatch(identifier, rel.symbol) for rel in function.relocs)):
        return None
    instructions = disassemble(function)
    if (len(instructions) * 4 != len(code)
            or any(ins.offset != at * 4 or ins.mnemonic.startswith('.') for at, ins in enumerate(instructions))):
        return None
    if instructions[-1].mnemonic.startswith(('b', 'j')) and instructions[-1].mnemonic != 'break':
        return None  # The delay slot would lie outside this reference function.
    labels = {}
    branches = {}
    for ins in instructions:
        if ins.mnemonic in ('j', 'jal'):
            # Without a relocation the object does not establish the final absolute target.
            targets = [rel for rel in function.relocs if rel.offset == ins.offset]
            if (len(targets) != 1 or targets[0].type != 4 or not targets[0].symbol
                    or targets[0].symbol.startswith('.')):
                return None
        if not ins.mnemonic.startswith('b') or ins.mnemonic == 'break':
            continue
        match = re.search(r'(?<![\w.$-])(\.L[0-9a-fA-F]+|0x[0-9a-fA-F]+|\d+)\s*$', ins.text)
        if not match:
            return None
        raw = match.group(1)
        target = int(raw[2:], 16) if raw.startswith('.L') else int(raw, 0)
        if target < 0 or target >= len(code) or target % 4:
            return None
        labels[target] = f'.L{target:08X}'
        branches[ins.offset] = (match.start(), target)
    lines = [f'glabel {function.name}']
    for ins in instructions:
        if ins.offset in labels:
            lines.append(labels[ins.offset] + ':')
        text = ins.text
        if ins.offset in branches:
            start, target = branches[ins.offset]
            text = text[:start] + labels[target]
        lines.append('    ' + text)
    return '\n'.join(lines) + '\n'
