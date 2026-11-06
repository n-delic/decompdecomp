"""Conservative absolute addresses from raw MIPS words, never pretty relocation labels."""

import re

MEMORY = {32, 33, 35, 36, 37, 39, 40, 41, 43, 49, 53, 55, 57, 61, 63}


def resolved(code, big=True):
    """Offset -> absolute effective address in straight-line LUI sequences.

    Unknown instructions, calls, branches and register clobbers discard state. No
    addresses are inferred across control flow or from disassembler symbol names.
    """
    if len(code) % 4:
        return {}
    registers, result = {}, {}
    for offset in range(0, len(code), 4):
        word = int.from_bytes(code[offset:offset+4], 'big' if big else 'little')
        op, rs, rt, imm = word >> 26, (word >> 21) & 31, (word >> 16) & 31, word & 65535
        signed = imm if imm < 32768 else imm - 65536
        if op == 15:
            if rt:
                registers[rt] = imm << 16
            continue
        if op in MEMORY or op in {9, 13}:
            value = None
            if rs in registers:
                value = ((registers[rs] | imm) if op == 13 else registers[rs] + signed) & 0xffffffff
                result[offset] = value
            if op in {9, 13}:
                registers.pop(rt, None)
                if rt and value is not None:
                    registers[rt] = value
            elif op in {32, 33, 35, 36, 37, 39, 55}:
                registers.pop(rt, None)
            continue
        if op == 0:
            if word & 63 in {8, 9, 12, 13}:  # jumps/syscall/break (including their delay slot)
                registers.clear()
            elif word:
                registers.pop((word >> 11) & 31, None)
        else:
            registers.clear()
    return result


def addresses(asm):
    """Read annotated target words; a gap or non-instruction line is never fabricated."""
    words = re.findall(r'/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})\s*\*/', asm or '')
    result, chunk, previous = set(), bytearray(), None
    for address, word in words:
        pc = int(address, 16)
        if previous is not None and pc != previous+4:
            result.update(resolved(chunk).values())
            chunk.clear()
        chunk.extend(bytes.fromhex(word))
        previous = pc
    result.update(resolved(chunk).values())
    return result


def aligned(rows, target, candidate, big=True):
    """Attach raw effective addresses to displayed rows without changing rows or scores."""
    if len(target) != 4*sum(bool(r[1]) for r in rows) or len(candidate) != 4*sum(bool(r[2]) for r in rows):
        return []
    wanted, actual = resolved(target, big), resolved(candidate, big)
    ti = ci = 0
    result = []
    for index, (_, left, right) in enumerate(rows):
        a, b = wanted.get(ti) if left else None, actual.get(ci) if right else None
        if a is not None or b is not None:
            result.append({'row': index, 'target': a, 'candidate': b})
        ti += 4*bool(left)
        ci += 4*bool(right)
    return result
