"""Private address hypotheses only when every non-relocation instruction bit agrees."""
from io import BytesIO

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

MAX_BYTES = 64 * 1024
MAX_SYMBOLS = 64
JUMP, HIGH, LOW = 4, 5, 6
WORD_MASK = 0xffffffff
SIGNED_LOW_OPS = {0x08, 0x09, 0x18, 0x19} | set(range(0x20,0x40))
MASKS = {JUMP:0xfc000000, HIGH:0xffff0000, LOW:0xffff0000}


def signed(value):
    return value - 0x10000 if value & 0x8000 else value


def bindings(obj, name, target, vram, resolve):
    """ELF relocation order pairs pending HI16s with their LO16, including shared lows.

    A missing opcode, register change, unpaired relocation, conflicting occurrence,
    defined section/object, or unsupported relocation supplies no address guess.
    Nothing is installed in the symbol table; the normal linker/data judge must
    still verify a recompiled source/header alternative.
    """
    from ..elfobj import _function_symbols
    if not target or len(target) > MAX_BYTES or len(target) % 4 or vram is None:return []
    try:
        elf = ELFFile(BytesIO(obj))
        if elf['e_machine'] != 'EM_MIPS' or elf.elfclass != 32:return []
        span = _function_symbols(elf).get(name)
        if not span:return []
        section, start, size = span
        if size != len(target):return []
        order = 'little' if elf.little_endian else 'big'
        code = elf.get_section(section).data()[start:start+size]
        relocations = []
        for table in elf.iter_sections():
            if not isinstance(table, RelocationSection) or table['sh_info'] != section:continue
            symbols = elf.get_section(table['sh_link'])
            for relocation in table.iter_relocations():
                at = relocation['r_offset'] - start
                if not 0 <= at < size:continue
                kind = relocation['r_info_type']
                if at % 4 or kind not in MASKS or table.is_RELA():return []
                symbol = symbols.get_symbol(relocation['r_info_sym'])
                relocations.append((at, kind, symbol.name, symbol['st_shndx'] == 'SHN_UNDEF'))
        masks = {at:MASKS[kind] for at,kind,_,_ in relocations}
        if len(masks) != len(relocations):return []
        word = lambda data,at:int.from_bytes(data[at:at+4],order)
        if any((word(code,at)^word(target,at)) & masks.get(at,WORD_MASK) for at in range(0,size,4)):return []
        pending = {}; values = {}; bad = set(); seen = set()
        for at,kind,symbol,undefined in relocations:
            if not undefined or not symbol or resolve(symbol) is not None:continue
            seen.add(symbol)
            if len(seen) > MAX_SYMBOLS:return []
            addresses = []
            if kind == JUMP:
                if word(code,at)>>26 not in (2,3):bad.add(symbol);continue
                actual = ((vram+at+4)&0xf0000000)|((word(target,at)&0x3ffffff)<<2)
                addresses = [(actual-((word(code,at)&0x3ffffff)<<2))&WORD_MASK]
            elif kind == HIGH:
                if word(code,at)>>26 != 0x0f:bad.add(symbol);continue
                pending.setdefault(symbol,[]).append(at)
                continue
            elif kind == LOW:
                if word(code,at)>>26 not in SIGNED_LOW_OPS:bad.add(symbol);continue
                highs = pending.pop(symbol,[])
                if not highs:bad.add(symbol);continue
                for high in highs:
                    # A shared low must really use the high's destination register.
                    if (word(code,high)>>16)&31 != (word(code,at)>>21)&31:
                        bad.add(symbol);continue
                    addend = ((word(code,high)&0xffff)<<16)+signed(word(code,at)&0xffff)
                    actual = ((word(target,high)&0xffff)<<16)+signed(word(target,at)&0xffff)
                    addresses.append((actual-addend)&WORD_MASK)
            values.setdefault(symbol,set()).update(addresses)
        bad.update(pending)
        return [{'name':symbol,'address':next(iter(addresses)),'evidence':'all non-relocation instruction bits agree'}
                for symbol,addresses in values.items() if symbol not in bad and len(addresses)==1]
    except (ELFError, ValueError, KeyError, IndexError, TypeError):
        return []
