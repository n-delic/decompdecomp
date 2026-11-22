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


_CALL_MASK = 0x03ffffff


def local_alias(section, index, offset):
    return f'{alias(section, index)}_text_{offset:X}'


def _local_calls(info):
    return {(at, index): offset for at, index, offset in info.get('local_calls', ())}


@dataclass
class Target(RomTarget):
    uso: dict | None = None


def symbolic_target(target):
    from ...elfobj import MASKS_BY_ARCH

    relocations = []
    linker_relocs = []
    local_calls = _local_calls(target.uso)
    for at, kind, index in target.uso['relocs'] or ():
        delta, length, mask = MASKS_BY_ARCH.get(('mips', kind), (0, 4, 0xFFFFFFFF))
        field = int.from_bytes(target.code[at + delta:at + delta + length], 'big') & mask
        section = target.uso['section']
        offset = local_calls.get((at, index)) if kind == 4 else None
        binding = local_alias(section, index, offset) if offset is not None else alias(section, index)
        relocation = Reloc(at, kind, binding, field)
        relocations.append(relocation)
        if offset is not None:
            linker_relocs.append(relocation)
    return FunctionObj(target.name, 'mips', True, target.code, relocations, linker_relocs=linker_relocs)


def link(obj, name, target, info):
    section = info['section']
    expected = {at: (kind, index) for at, kind, index in info['relocs'] or ()}
    local_calls = _local_calls(info)
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
    linked = bytearray(code)
    problems = (['This overlay function needs validated relocation metadata. Reimport the ROM and try again.']
                if info['relocs'] is None else [])
    seen = set()
    for item in raw:
        at, kind = item['off'], item['type']
        symbol = item['symtab'].get_symbol(item['symidx'])
        binding = symbol.name
        candidate.append(Reloc(at, kind, binding, item['value']))
        original = expected.get(at)
        valid = False
        if original and kind == original[0] and not item['rela'] and symbol['st_shndx'] == 'SHN_UNDEF' and at not in seen:
            index = original[1]
            valid = binding == alias(section, index)
            offset = local_calls.get((at, index)) if kind == 4 else None
            # A local alias names a checked offset under the original loader symbol.
            # The object must still make an undefined direct call with no offset of its own.
            if not valid and offset is not None and binding == local_alias(section, index, offset):
                word = int.from_bytes(code[at:at + 4], 'big')
                if word >> 26 == 3 and not item['value'] and 0 < offset <= _CALL_MASK * 4 and offset % 4 == 0:
                    linked[at:at + 4] = (word | (offset >> 2)).to_bytes(4, 'big')
                    valid = True
        if not valid:
            wanted = alias(section, original[1]) if original else 'no relocation'
            problems.append(f'Overlay relocation at +0x{at:X} requires {wanted} with the original relocation kind.')
        seen.add(at)
    for at in expected.keys() - seen:
        problems.append(f'Overlay relocation at +0x{at:X} requires {alias(section, expected[at][1])}.')
    linked_code = bytes(linked)
    exact_bytes = linked_code == target
    return LinkResult(FunctionObj(name, 'mips', True, code, candidate), exact_bytes and not problems,
                      problems, instructions_exact=exact_bytes, linked_code=linked_code)
