"""Judge cartridge overlay text before runtime relocation, checking bytes and symbol bindings."""
import re
from collections import defaultdict
from dataclasses import dataclass

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from ...candidate_work import elf as candidate_elf
from ...elfobj import FunctionObj, Reloc, _function_symbols, unmangle
from ...rom.link import LinkResult
from ...rom.match import RomTarget, raw_relocs
from .uso import POOL_LOAD_WIDTHS


def alias(section, index):
    return f'uso_{section:X}_sym_{index}'


_CALL_MASK = 0x03ffffff


def local_alias(section, index, offset):
    return f'{alias(section, index)}_text_{offset:X}'


_REFERENCE = re.compile(r'(uso_[0-9A-F]+_sym_[0-9]+(?:_text_[0-9A-F]+)?)__ref[1-9][0-9]*')
_BINDING = re.compile(r'uso_[0-9A-F]+_sym_[0-9]+(?:_text_[0-9A-F]+)?')
_DATA_BINDING = re.compile(r'(uso_[0-9A-F]+_sym_[0-9]+)_data_([0-9A-F]+)__ref([1-9][0-9]*)')
_EXPLICIT_BINDING = re.compile(r'decomp_overlay_bind_([A-Za-z_]\w*)')


def separate_reference(binding, number):
    """Give the compiler a distinct name that resolves to the same loader record."""
    return f'{binding}__ref{number}' if _BINDING.fullmatch(binding) else binding


def data_reference(binding, offset, number):
    """Name an explicit zero-initialized object within a loader symbol."""
    if re.fullmatch(r'uso_[0-9A-F]+_sym_[0-9]+', binding) and 0 <= offset < 0x8000 and number > 0:
        return f'{binding}_data_{offset:X}__ref{number}'
    return None


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
    pool_sites = {at for at, _, _ in target.uso.get('pool_loads', ())}
    pool_sites.update(at for at, _, _ in target.uso.get('jump_tables', ()))
    for at, kind, index in target.uso['relocs'] or ():
        delta, length, mask = MASKS_BY_ARCH.get(('mips', kind), (0, 4, 0xFFFFFFFF))
        field = int.from_bytes(target.code[at + delta:at + delta + length], 'big') & mask
        section = target.uso['section']
        offset = local_calls.get((at, index)) if kind == 4 else None
        binding = local_alias(section, index, offset) if offset is not None else alias(section, index)
        relocation = Reloc(at, kind, binding, field)
        relocations.append(relocation)
        if offset is not None or at in pool_sites:
            linker_relocs.append(relocation)
    return FunctionObj(target.name, 'mips', True, target.code, relocations, linker_relocs=linker_relocs)


def _matches_table(blob, relocations, offset, targets, entry):
    section, start, size = entry
    end = offset + len(targets) * 4
    if end > len(blob) or any(at % 4 for at in relocations if offset - 3 <= at < end):
        return False
    for at, destination in zip(range(offset, end, 4), targets, strict=True):
        if at not in relocations or not 0 <= destination < size or destination % 4:
            return False
        relocation, symbols = relocations[at]
        symbol = symbols.get_symbol(relocation['r_info_sym'])
        if (relocation['r_info_type'] != 2 or relocation.is_RELA() or symbol['st_shndx'] != section
                or symbol['st_info']['type'] != 'STT_SECTION' or symbol['st_value']
                or int.from_bytes(blob[at:at + 4], 'big') != start + destination):
            return False
    return True


def _pool_fields(elf, raw, code, target, expected, info, entry):
    """Bind read-only data only when every constant or jump table load is proven."""
    pool = {(at, index): bytes.fromhex(value) for at, index, value in info.get('pool_loads', ())}
    tables = {(at, index): targets for at, index, targets in info.get('jump_tables', ())}
    if not (pool or tables) or len(code) != len(target):
        return {}
    groups = defaultdict(list)
    for item in raw:
        symbol = item['symtab'].get_symbol(item['symidx'])
        section_index = symbol['st_shndx']
        if symbol['st_info']['type'] == 'STT_SECTION' and isinstance(section_index, int):
            groups[section_index].append(item)
    verified = {}
    for section_index, uses in groups.items():
        data = elf.get_section(section_index)
        if data.name not in ('.rodata', '.rdata') or data['sh_type'] != 'SHT_PROGBITS' or data['sh_flags'] & 1:
            continue
        if any(item['rela'] or item['type'] not in (5, 6)
               or item['off'] not in expected or expected[item['off']][0] != item['type'] for item in uses):
            continue
        indices = {expected[item['off']][1] for item in uses}
        if len(indices) != 1:
            continue
        index, = indices
        highs = [item for item in uses if item['type'] == 5]
        lows = [item for item in uses if item['type'] == 6]
        if not highs or not lows or any(item['value'] or target[item['off']+2:item['off']+4] != b'\0\0'
                                        for item in highs):
            continue
        data_relocs = [(r, elf.get_section(section['sh_link'])) for section in elf.iter_sections()
                       if isinstance(section, RelocationSection) and section['sh_info'] == section_index
                       for r in section.iter_relocations()]
        if any(r['r_info_type'] not in (2, 5, 6) for r, _ in data_relocs):
            continue
        by_offset = {r['r_offset']: (r, symbols) for r, symbols in data_relocs}
        if len(by_offset) != len(data_relocs):
            continue
        blob = data.data()
        for item in lows:
            opcode = code[item['off']] >> 2
            offset = item['value']
            width = POOL_LOAD_WIDTHS.get(opcode)
            if not width or offset % width or offset >= 0x8000:
                break
            if targets := tables.get((item['off'], index)):
                if opcode != 0x23 or not _matches_table(blob, by_offset, offset, targets, entry):
                    break
                continue
            original = pool.get((item['off'], index), b'')
            if (any(offset - 3 <= off < offset + width for off in by_offset)
                    or len(original) != width or original != blob[offset:offset + width]):
                break
        else:
            verified.update((item['off'], int.from_bytes(target[item['off']+2:item['off']+4], 'big')) for item in uses)
    return verified


def _defined_data_fields(elf, raw, target, expected, section):
    """Link explicitly named zero-initialized definitions to their loader binding.

    A compiler may generate different instructions for a definition and an
    extern declaration. The source must name both the binding and its offset;
    anonymous section symbols never authorize this link.
    """
    groups = defaultdict(list)
    for item in raw:
        symbol = item['symtab'].get_symbol(item['symidx'])
        if _DATA_BINDING.fullmatch(symbol.name):
            groups[symbol.name].append((item, symbol))
    verified = {}
    for name, uses in groups.items():
        match = _DATA_BINDING.fullmatch(name)
        binding, offset = match[1], int(match[2], 16)
        if offset >= 0x8000:
            continue
        item, symbol = uses[0]
        shndx = symbol['st_shndx']
        if (not isinstance(shndx, int) or symbol['st_info']['bind'] != 'STB_GLOBAL'
                or symbol['st_info']['type'] != 'STT_OBJECT' or not symbol['st_size']):
            continue
        data = elf.get_section(shndx)
        if (data['sh_type'] != 'SHT_NOBITS' or data['sh_flags'] & 3 != 3
                or symbol['st_value'] + symbol['st_size'] > data['sh_size']):
            continue
        kinds = {r['type'] for r, _ in uses}
        if kinds != {5, 6}:
            continue
        fields = {}
        for reloc, _ in uses:
            at, kind = reloc['off'], reloc['type']
            wanted = expected.get(at)
            if (reloc['rela'] or not wanted or wanted[0] != kind
                    or binding != alias(section, wanted[1])):
                break
            value = reloc['value']
            actual = int.from_bytes(target[at + 2:at + 4], 'big')
            if kind == 5:
                if value or actual:
                    break
            elif value >= 0x8000 or value + offset >= 0x8000 or actual != value + offset:
                break
            fields[at] = actual
        else:
            verified.update(fields)
    return verified


def _marker_value(elf, symbol):
    """Read one global pointer marker and its single symbol relocation."""
    shndx, start = symbol['st_shndx'], symbol['st_value']
    if (not isinstance(shndx, int) or start % 4 or symbol['st_size'] != 4
            or symbol['st_info']['bind'] != 'STB_GLOBAL' or symbol['st_info']['type'] != 'STT_OBJECT'):
        return None
    data = elf.get_section(shndx)
    if data['sh_type'] != 'SHT_PROGBITS' or data['sh_flags'] & 3 != 3 or start + 4 > data['sh_size']:
        return None
    offset = int.from_bytes(data.data()[start:start + 4], 'big', signed=True)
    refs = [(r, elf.get_section(table['sh_link'])) for table in elf.iter_sections()
            if isinstance(table, RelocationSection) and table['sh_info'] == shndx
            for r in table.iter_relocations() if start <= r['r_offset'] < start + 4]
    if len(refs) != 1:
        return None
    reloc, symbols = refs[0]
    if reloc['r_offset'] != start or reloc['r_info_type'] != 2 or reloc.is_RELA():
        return None
    return symbols.get_symbol(reloc['r_info_sym']), offset


def _explicit_bindings(elf):
    """Read source-declared aliases for unresolved donor symbols.

    Each binding is a global pointer initialized by one relocation to an
    undefined loader symbol. No pointer bytes or relocations from the marker
    are copied into the cartridge; they specify the real text relocation.
    """
    symtab = elf.get_section_by_name('.symtab')
    if symtab is None:
        return {}
    markers = defaultdict(list)
    for symbol in symtab.iter_symbols():
        match = _EXPLICIT_BINDING.fullmatch(symbol.name)
        if not match:
            continue
        value = _marker_value(elf, symbol)
        if value is None:
            markers[match[1]].append(None)
            continue
        binding, offset = value
        distinct = _REFERENCE.fullmatch(binding.name)
        binding_name = distinct[1] if distinct else binding.name
        if binding['st_shndx'] != 'SHN_UNDEF' or not _BINDING.fullmatch(binding_name):
            markers[match[1]].append(None)
            continue
        markers[match[1]].append((binding_name, offset))
    return {name: values[0] for name, values in markers.items() if len(values) == 1 and values[0] is not None}


def _constructor_entry(elf):
    """Resolve an explicit entry marker to exactly one defined constructor."""
    symtab = elf.get_section_by_name('.symtab')
    if symtab is None:
        return None
    symbols = list(symtab.iter_symbols())
    markers = [symbol for symbol in symbols if re.fullmatch(r'decomp_overlay_entry(?:_[A-Za-z_]\w*)?', symbol.name)]
    if len(markers) != 1 or not (value := _marker_value(elf, markers[0])):
        return None
    reference, offset = value
    if (offset or reference['st_info']['bind'] != 'STB_GLOBAL'
            or not re.fullmatch(r'__ct__[A-Za-z0-9_]+', reference.name)):
        return None
    definitions = [symbol for symbol in symbols if symbol.name == reference.name
                   and symbol['st_info']['type'] == 'STT_FUNC' and symbol['st_info']['bind'] == 'STB_GLOBAL'
                   and isinstance(symbol['st_shndx'], int)
                   and symbol['st_size'] > 0]
    if len(definitions) != 1:
        return None
    symbol, = definitions
    if (reference['st_shndx'] != 'SHN_UNDEF'
            and (reference['st_shndx'], reference['st_value']) != (symbol['st_shndx'], symbol['st_value'])):
        return None
    section = elf.get_section(symbol['st_shndx'])
    if not section['sh_flags'] & 4 or symbol['st_value'] + symbol['st_size'] > section['sh_size']:
        return None
    return symbol['st_shndx'], symbol['st_value'], symbol['st_size']


def _explicit_offset_fields(raw, target, expected, explicit, section):
    """Apply an explicit marker addend only when every HI16/LO16 pair agrees."""
    groups = defaultdict(list)
    for item in raw:
        symbol = item['symtab'].get_symbol(item['symidx'])
        if symbol.name in explicit and explicit[symbol.name][1]:
            groups[symbol.name].append((item, symbol))
    fields = {}
    for name, uses in groups.items():
        binding, offset = explicit[name]
        if {item['type'] for item, _ in uses} != {5, 6}:
            continue
        if any(item['rela'] or symbol['st_shndx'] != 'SHN_UNDEF'
               or item['off'] not in expected or expected[item['off']][0] != item['type']
               or binding != alias(section, expected[item['off']][1]) for item, symbol in uses):
            continue
        highs = [(item['value'], int.from_bytes(target[item['off'] + 2:item['off'] + 4], 'big'))
                 for item, _ in uses if item['type'] == 5]
        lows = [(item['value'], int.from_bytes(target[item['off'] + 2:item['off'] + 4], 'big'))
                for item, _ in uses if item['type'] == 6]
        if len(set(highs)) != 1:
            continue
        old_high, new_high = highs[0]
        for old_low, new_low in lows:
            signed_low = old_low - 0x10000 if old_low & 0x8000 else old_low
            value = (old_high << 16) + signed_low + offset
            if new_low != value & 0xFFFF or new_high != ((value + 0x8000) >> 16) & 0xFFFF:
                break
        else:
            fields.update((item['off'], int.from_bytes(target[item['off'] + 2:item['off'] + 4], 'big'))
                          for item, _ in uses)
    return fields


def link(obj, name, target, info):
    section = info['section']
    expected = {at: (kind, index) for at, kind, index in info['relocs'] or ()}
    local_calls = _local_calls(info)
    try:
        obj = unmangle(obj, lambda symbol: symbol == name)
        elf = candidate_elf(obj, ELFFile)
        if elf.little_endian or elf['e_machine'] != 'EM_MIPS':
            return LinkResult(None, False, ['Overlay text requires a big-endian MIPS object.'])
        entry = _function_symbols(elf).get(name) or _constructor_entry(elf)
        if not entry:
            return LinkResult(None, False, [f'The object has no function named {name}.'])
        shndx, start, size = entry
        code = elf.get_section(shndx).data()[start:start + size]
        raw = raw_relocs(elf, shndx, start, size, code)
        pool = _pool_fields(elf, raw, code, target, expected, info, entry)
        defined_data = _defined_data_fields(elf, raw, target, expected, section)
        explicit = _explicit_bindings(elf)
        explicit_offsets = _explicit_offset_fields(raw, target, expected, explicit, section)
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
        binding, offset = (explicit.get(symbol.name, (symbol.name, 0)) if symbol['st_shndx'] == 'SHN_UNDEF'
                           else (symbol.name, 0))
        if at in pool or at in defined_data or at in explicit_offsets:
            value = (pool[at] if at in pool else defined_data[at] if at in defined_data else explicit_offsets[at])
            candidate.append(Reloc(at, kind, alias(section, expected[at][1]), value))
        else:
            candidate.append(Reloc(at, kind, binding, item['value']))
        distinct = _REFERENCE.fullmatch(binding)
        if distinct:
            binding = distinct[1]
        original = expected.get(at)
        valid = False
        if (original and kind == original[0] and not item['rela'] and not offset
                and symbol['st_shndx'] == 'SHN_UNDEF' and at not in seen):
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
        if at in pool and at not in seen:
            linked[at+2:at+4] = pool[at].to_bytes(2, 'big')
            valid = True
        if at in defined_data and at not in seen:
            linked[at+2:at+4] = defined_data[at].to_bytes(2, 'big')
            valid = True
        if at in explicit_offsets and at not in seen:
            linked[at+2:at+4] = explicit_offsets[at].to_bytes(2, 'big')
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
