"""MIPS assembly fixes needed to preserve the original bytes in a ROM export."""

import re

_INSTRUCTION = re.compile(
    r"^(?P<prefix>\s*/\*\s*[0-9a-f]+\s+(?P<address>[0-9a-f]{8})\s+"
    r"(?P<bytes>[0-9a-f]{8})\s*\*/\s*)(?P<instruction>[^\n]+)$", re.I | re.M
)
_HI = re.compile(r"^lui\s+\$\w+,\s*%hi\(\s*(D_[0-9a-f]{8})\s*\+\s*(0x[0-9a-f]+)\s*\)\s*$", re.I)


def full_low_addends(text: str) -> str:
    """Keep the full addend on LO16 relocations so GNU ld pairs their HI16 correctly.

    Masking an addend does not change its low half, but changes the addend GNU
    ld uses to apply the high-half carry. Splat emits this mask for large data
    offsets, causing an otherwise correct original function to rebuild wrongly.
    """
    return re.sub(r'%lo\(([^()\n]+)\+\s*\((0x[0-9a-f]+)\s*&\s*0xffff\)\)',
                  r'%lo(\1+ \2)', text, flags=re.I)


def backward_delay_highs(text: str, big_endian: bool) -> str:
    """Preserve a loop delay-slot high half whose matching low half precedes it.

    GNU MIPS ld pairs a HI16 relocation with a later LO16; a low half at the
    backwards branch target cannot supply its carry. The original word is
    retained only when the target, register and both address halves prove the
    pairing. Like other raw words in an export, it needs revisiting if that
    address is moved in a modified game.
    """
    lines = list(_INSTRUCTION.finditer(text))
    by_address = {int(line['address'], 16): line for line in lines}
    replacements = []
    order = 'big' if big_endian else 'little'
    for index, high in enumerate(lines):
        if not index or not (hi := _HI.fullmatch(high['instruction'].strip())):
            continue
        word = int.from_bytes(bytes.fromhex(high['bytes']), order)
        previous = lines[index - 1]
        branch = int.from_bytes(bytes.fromhex(previous['bytes']), order)
        address = int(high['address'], 16)
        if word >> 26 != 15 or int(previous['address'], 16) + 4 != address:
            continue
        # Conditional PC-relative branches, including the REGIMM variants.
        if branch >> 26 not in (1, 4, 5, 6, 7, 20, 21, 22, 23):
            continue
        displacement = branch & 0xffff
        displacement -= 0x10000 if displacement & 0x8000 else 0
        target = address + displacement * 4
        if target >= address or not (low := by_address.get(target)):
            continue
        low_word = int.from_bytes(bytes.fromhex(low['bytes']), order)
        symbol, addend = hi.groups()
        low_expression = re.compile(
            rf"%lo\(\s*{re.escape(symbol)}\s*\+\s*\(\s*{re.escape(addend)}\s*&\s*0x[fF]{{4}}\s*\)\s*\)", re.I
        )
        value = (int(symbol[2:], 16) + int(addend, 16)) & 0xffffffff
        if (not low_expression.search(low['instruction']) or (low_word >> 26 not in (9, 25)
                and low_word >> 26 < 32) or (low_word >> 21) & 31 != (word >> 16) & 31
                or low_word & 0xffff != value & 0xffff or word & 0xffff != ((value + 0x8000) >> 16) & 0xffff):
            continue
        replacements.append((high.start('instruction'), high.end('instruction'), f'.word 0x{word:08X}'))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text
