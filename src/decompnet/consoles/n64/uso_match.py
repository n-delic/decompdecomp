"""Judge cartridge overlay text before runtime relocation, checking bytes and symbol bindings."""
from dataclasses import dataclass
from io import BytesIO

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from ...elfobj import FunctionObj, Reloc, _function_symbols, unmangle
from ...rom.link import LinkResult
from ...rom.match import RomTarget, raw_relocs


def alias(section, index):
    return f'uso_{section:X}_sym_{index}'


@dataclass
class Target(RomTarget):
    uso: dict | None = None


def symbolic_target(target):
    from ...elfobj import MASKS_BY_ARCH

    relocations = []
    for at, kind, index in target.uso['relocs'] or ():
        delta, length, mask = MASKS_BY_ARCH.get(('mips', kind), (0, 4, 0xFFFFFFFF))
        field = int.from_bytes(target.code[at + delta:at + delta + length], 'big') & mask
        relocations.append(Reloc(at, kind, alias(target.uso['section'], index), field))
    return FunctionObj(target.name, 'mips', True, target.code, relocations)


def link(obj, name, target, info):
    section = info['section']
    expected = {at: (kind, alias(section, index)) for at, kind, index in info['relocs'] or ()}
    try:
        obj = unmangle(obj, lambda symbol: symbol == name)
        elf = ELFFile(BytesIO(obj))
        if elf.little_endian or elf['e_machine'] != 'EM_MIPS':
            return LinkResult(None, False, ['Overlay text requires a big-endian MIPS object.'])
        entry = _function_symbols(elf).get(name)
        if not entry:
            return LinkResult(None, False, [f'The object has no function named {name}.'])
        shndx, start, size = entry
        code = elf.get_section(shndx).data()[start:start + size]
        raw = raw_relocs(elf, shndx, start, size, code)
    except (ELFError, ValueError, TypeError, IndexError):
        return LinkResult(None, False, ['The compiler object could not be read as overlay text.'])

    candidate = []
    problems = (['This overlay function needs validated relocation metadata. Reimport the ROM and try again.']
                if info['relocs'] is None else [])
    seen = set()
    for item in raw:
        at, kind = item['off'], item['type']
        symbol = item['symtab'].get_symbol(item['symidx'])
        binding = symbol.name
        candidate.append(Reloc(at, kind, binding, item['value']))
        # The cartridge stores relocation addends in its text. Retain them and require an
        # undefined ELF reference to the specific loader symbol, with no RELA override.
        if item['rela'] or symbol['st_shndx'] != 'SHN_UNDEF' or at in seen or expected.get(at) != (kind, binding):
            wanted = expected[at][1] if at in expected else 'no relocation'
            problems.append(f'Overlay relocation at +0x{at:X} requires {wanted} with the original relocation kind.')
        seen.add(at)
    for at in expected.keys() - seen:
        problems.append(f'Overlay relocation at +0x{at:X} requires {expected[at][1]}.')
    exact_bytes = code == target
    return LinkResult(FunctionObj(name, 'mips', True, code, candidate), exact_bytes and not problems,
                      problems, instructions_exact=exact_bytes, linked_code=code)
