"""ARMv7 data and Thumb-2 relocations for linked ELF code, following AAELF32 (#133).

Compute the relocated bits from the symbol, addend and place. GOT references must identify a real slot whose
dynamic relocation names the same symbol; local data is placed and compared by the shared ARM judge.
"""

from __future__ import annotations

WORD = 0xFFFFFFFF
DATA_RELOCS = frozenset((3, 24, 25, 26, 96))  # REL32, GOTOFF32, BASE_PREL, GOT_BREL, GOT_PREL
MOV_RELOCS = frozenset(range(43, 51))


def immediate(word, thumb):
    if thumb:
        first, second = word & 0xFFFF, word >> 16
        return (first & 0xF) << 12 | ((first >> 10) & 1) << 11 | ((second >> 12) & 7) << 8 | (second & 0xFF)
    return (word & 0xF0000) >> 4 | word & 0xFFF


def put_immediate(word, value, thumb):
    value &= 0xFFFF
    if thumb:
        mask = 0x70FF040F
        bits = (value >> 12) | (((value >> 11) & 1) << 10) | (((value >> 8) & 7) << 28) | ((value & 0xFF) << 16)
    else:
        mask, bits = 0x000F0FFF, ((value & 0xF000) << 4) | (value & 0xFFF)
    return (word & ~mask) | bits


def data_relocation(kind, *, code, at, pc, target, sym, name, is_section, symbols, place, problem):
    addend = int.from_bytes(code[at:at + 4], 'little', signed=True)
    original = int.from_bytes(target.code[at:at + 4], 'little')
    context = target.image
    base = context.value(name, symbols)
    defined = isinstance(sym['st_shndx'], int) and sym['st_shndx'] != 0
    if kind in (26, 96):
        if kind == 26 and context.got_base is None:
            problem('The original ELF has no GOT base for R_ARM_GOT_BREL.')
            return
        slot = (original - addend + (pc if kind == 96 else context.got_base)) & WORD
        item = context.slots.get(slot)
        if defined:
            if item is None or item[1] is None or base is not None and not context.slot_matches(slot, name, symbols):
                problem(f'GOT reference to local {name} has no verified original data slot.')
                return
            place((sym['st_shndx'], 0 if is_section else sym['st_value']), item[1])
        elif not context.slot_matches(slot, name, symbols):
            problem(f'GOT reference to {name} does not name that symbol in the original ELF (slot 0x{slot:08X}).')
            return
        value = slot + addend - (pc if kind == 96 else context.got_base)
    elif kind == 25:
        if context.got_base is None:
            problem('The original ELF has no GOT base for R_ARM_BASE_PREL.')
            return
        if name not in ('', '_GLOBAL_OFFSET_TABLE_'):
            problem(f'R_ARM_BASE_PREL refers to {name}, not the global offset table.')
            return
        value = context.got_base + addend - pc
    else:
        relative = pc if kind == 3 else context.got_base
        if relative is None:
            problem('The original ELF has no GOT base for R_ARM_GOTOFF32.')
            return
        if defined:
            if is_section or symbols.resolve(name) is None:
                base = (original + relative - addend) & WORD
            place((sym['st_shndx'], 0 if is_section else sym['st_value']), base)
        elif base is None:
            problem(f'Unknown symbol {name} in an ARM position-independent reference.')
            return
        pointer = ((base + addend) & WORD) if defined and (is_section or symbols.resolve(name) is None) else context.pointer(name, symbols, addend)
        value = pointer - relative
    code[at:at + 4] = (value & WORD).to_bytes(4, 'little')


def mov_relocation(kind, *, code, at, pc, target, sym, name, is_section, symbols, place, problem):
    thumb = kind >= 47
    high = kind in (44, 46, 48, 50)
    relative = kind in (45, 46, 49, 50)
    word = int.from_bytes(code[at:at + 4], 'little')
    addend = immediate(word, thumb)
    if addend & 0x8000:
        addend -= 1 << 16
    base = target.image.value(name, symbols)
    if base is None:
        problem(f'Unknown symbol {name} in a MOVW/MOVT relocation; name its data before matching.')
        return
    function = target.image.types.get(name) == 'STT_FUNC' or (base & ~1) in target.image.functions
    pointer = target.image.pointer(name, symbols, addend)
    value = (((base & ~1) + addend) if high and function else pointer) - (pc if relative else 0)
    value &= WORD
    if isinstance(sym['st_shndx'], int) and sym['st_shndx'] != 0 and not function:
        place((sym['st_shndx'], 0 if is_section else sym['st_value']), base)
    encoded = put_immediate(word, value >> 16 if high else value, thumb)
    code[at:at + 4] = encoded.to_bytes(4, 'little')


def thumb_jump(code, at, pc, target, name, symbols, problem):
    def decode(raw, address):
        first = int.from_bytes(raw[:2], 'little')
        second = int.from_bytes(raw[2:4], 'little')
        if first & 0xF800 != 0xF000 or second & 0xD000 != 0x9000:
            return None
        sign = first >> 10 & 1
        value = ((sign << 24) | ((~((second >> 13) ^ sign) & 1) << 23)
                 | ((~((second >> 11) ^ sign) & 1) << 22) | ((first & 0x3FF) << 12) | ((second & 0x7FF) << 1))
        return (address + 4 + value - ((1 << 25) if sign else 0)) & WORD
    addend = decode(code[at:at + 4], 0)
    destination = decode(target.code[at:at + 4], pc)
    if addend is None or destination is None:
        problem('R_ARM_THM_JUMP24 is not on a Thumb-2 B.W instruction.')
        return
    choices = target.image.destinations(name, symbols) if target.image else [symbols.resolve(name)]
    actual = target.image.through_veneer(destination) if target.image else destination
    if not any(base is not None and ((base + addend) & 0xFFFFFFFE) == actual for base in choices):
        problem(f'Thumb tail call to {name} differs from the original destination.')
        return
    mine = int.from_bytes(code[at:at + 4], 'little')
    original = int.from_bytes(target.code[at:at + 4], 'little')
    mask = 0x2FFF07FF
    code[at:at + 4] = ((mine & ~mask) | (original & mask)).to_bytes(4, 'little')
