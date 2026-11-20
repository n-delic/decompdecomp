"""Report stack-backed call arguments and direct stores observed in their callees."""
import re

from ..arch.mips_returns import instructions
from .allocation_evidence import diagnose

NUMBER = r'(?:0x[0-9a-fA-F]+|[0-9]+)'
ARGUMENT = re.compile(rf'addiu\s+\$(a[0-3]),\s*\$sp,\s*({NUMBER})')
WIDTH = {'sb':1, 'sh':2, 'sw':4, 'swc1':4, 'sd':8, 'sdc1':8}
STORE = re.compile(rf'({"|".join(WIDTH)})\s+\$\w+,\s*({NUMBER})\(\$(a[0-3])\)')
FLOW = re.compile(r'(?:b\w*|j\w*|syscall|break)\b')


def writes(asm, register):
    """Observe an unmodified incoming register in a bounded straight-line callee."""
    rows = instructions(asm)
    if not 2 <= len(rows) <= 128 or not re.fullmatch(r'jr\s+\$ra', rows[-2]):
        return []
    result = set()
    for line in [*rows[:-2], rows[-1]]:
        if FLOW.match(line):
            return []
        if not re.search(r'\$'+register+r'\b', line):
            continue
        match = STORE.fullmatch(line)
        if not match or match[3] != register:
            return []  # Alias, overwrite or unsupported use of the incoming address.
        offset, width = int(match[2],0), WIDTH[match[1]]
        if offset + width > 128:
            return []
        result.add((offset, width))
    return sorted(result)


def observe(net, proj, fn, rows):
    if proj['arch'] != 'mips' or not getattr(net, 'is_rom', lambda _:False)(proj):
        return []
    diagnosis = diagnose(rows, 'mips')
    target, candidate = diagnosis['target_frame'], diagnosis['candidate_frame']
    if target is None or candidate is None or target <= candidate:
        return []
    asm = instructions(net._asm_text(fn, proj))
    results = []
    footprints = {}
    for i, op in enumerate(asm[:-1]):
        call = re.fullmatch(r'jal\s+([A-Za-z_]\w*)', op)
        if not call:
            continue
        regs = set()
        for line in [asm[i+1], *reversed(asm[max(0,i-8):i])]:
            if FLOW.match(line):
                break
            arg = ARGUMENT.fullmatch(line)
            if arg and arg[1] not in regs:
                register, offset = arg[1], int(arg[2],0)
                regs.add(register)
                if offset >= target:
                    continue
                key = (call[1], register)
                if key not in footprints:
                    if len(footprints) >= 12:
                        return results
                    found = net.project_store(proj['id']).all(
                        'SELECT * FROM functions WHERE project_id=? AND name=? LIMIT 2', proj['id'], call[1])
                    footprints[key] = writes(net._asm_text(found[0], proj), register) if len(found)==1 else []
                footprint = footprints[key]
                if len(footprint) > 1 and offset + max(off+width for off,width in footprint) <= target:
                    results.append((call[1], register, offset, footprint))
                    if len(results) == 3:
                        return results
            regs.update(re.findall(r'\$(a[0-3])\b',line))
    return results


def messages(net, proj, fn, rows):
    return [f'Callee stack writes: {name} receives sp+0x{slot:X} in ${register} and writes '+
            ', '.join(f'+0x{offset:X} ({width} bytes)' for offset,width in stores)+
            '. Compare the local buffer extent before testing unused stack locals.'
            for name,register,slot,stores in observe(net, proj, fn, rows)]
