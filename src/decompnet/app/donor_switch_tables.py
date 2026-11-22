"""Try donor case-label changes only from verified overlay jump tables."""

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from ..arch.mips_indirect import jump_table, signed
from ..candidate_work import elf as candidate_elf
from ..elfobj import _function_symbols, mask_relocs, unmangle
from ..rom.match import raw_relocs


def variants(net, proj, fn, source, obj, candidate):
    """Propose case labels from corresponding bounded target and compiler tables."""
    if candidate is None:
        return []
    # Overlay metadata carries validated destinations; main-ROM and owned-build
    # table evidence need their own adapters (ROADMAP.md).
    placement = net.console(proj).export_overlay(net._rom_target(fn))
    if not placement or not placement.get('jump_tables'):
        return []
    target = net._target_obj(fn, proj)
    if target.arch != 'mips' or not target.big_endian or len(candidate.code) != len(target.code):
        return []
    actual = {(r.offset, r.type) for r in candidate.relocs}
    expected = {(r.offset, r.type) for r in target.relocs}
    if (len(actual) != len(candidate.relocs) or len(expected) != len(target.relocs) or actual != expected
            or mask_relocs(candidate.code, target.relocs, 'mips', True)
            != mask_relocs(target.code, target.relocs, 'mips', True)):
        return []
    try:
        elf = candidate_elf(unmangle(obj, lambda name: name == fn['name']), ELFFile)
        entry = _function_symbols(elf).get(fn['name'])
        if not entry:
            return []
        shndx, start, size = entry
        raw = raw_relocs(elf, shndx, start, size, candidate.code)
        sites = {item['off']: item for item in raw}
        if len(sites) != len(raw):
            return []
        from decomprepair.switch_tables import relabeled

        results = []
        for at, _, destinations in placement['jump_tables']:
            if len(destinations) < 2 or at < 8 or at + 8 > len(target.code):
                continue
            high, low = sites.get(at - 8), sites.get(at)
            if not high or not low or high['type'] != 5 or low['type'] != 6 or high['rela'] or low['rela']:
                continue
            first = high['symtab'].get_symbol(high['symidx'])
            symbol = low['symtab'].get_symbol(low['symidx'])
            section_index = symbol['st_shndx']
            if (first['st_shndx'] != section_index or first['st_info']['type'] != 'STT_SECTION'
                    or symbol['st_info']['type'] != 'STT_SECTION' or not isinstance(section_index, int)
                    or first['st_value'] or symbol['st_value'] or high['value'] or low['value'] >= 0x8000):
                continue
            data = elf.get_section(section_index)
            if data.name not in ('.rodata', '.rdata') or data['sh_type'] != 'SHT_PROGBITS' or data['sh_flags'] & 1:
                continue
            base, count = low['value'], len(destinations)
            if base % 4 or base + count * 4 > data['sh_size']:
                continue
            relocations = [(r, elf.get_section(table['sh_link'])) for table in elf.iter_sections()
                           if isinstance(table, RelocationSection) and table['sh_info'] == section_index
                           for r in table.iter_relocations() if base - 3 <= r['r_offset'] < base + count * 4]
            by_offset = {r['r_offset']: (r, symbols) for r, symbols in relocations}
            if len(by_offset) != len(relocations) or set(by_offset) != set(range(base, base + count * 4, 4)):
                continue
            blob = data.data()
            compiled = []
            for offset in range(base, base + count * 4, 4):
                relocation, symbols = by_offset[offset]
                reference = symbols.get_symbol(relocation['r_info_sym'])
                address = int.from_bytes(blob[offset:offset + 4], 'big') - start
                if (relocation.is_RELA() or relocation['r_info_type'] != 2 or reference['st_shndx'] != shndx
                        or reference['st_info']['type'] != 'STT_SECTION' or reference['st_value']
                        or not 0 <= address < size or address % 4):
                    break
                compiled.append(address)
            else:
                if compiled == destinations:
                    continue
                address = int.from_bytes(target.code[at + 2:at + 4], 'big')
                def reader(wanted, length, address=address, count=count, destinations=destinations):
                    if wanted != address or length != count * 4:
                        return None
                    return b''.join(value.to_bytes(4, 'big') for value in destinations)

                table = jump_table(target.code, 0, len(target.code), 0, at + 4, 'big', reader=reader)
                if not table or table['state'] != 'validated' or table['entry_count'] != count:
                    continue
                guard = table['guard_address']
                if guard + 8 > len(target.code):
                    continue
                bound = int.from_bytes(target.code[guard:guard + 4], 'big')
                branch = int.from_bytes(target.code[guard + 4:guard + 8], 'big')
                if bound >> 26 != 11 or branch >> 26 not in (4, 20):
                    continue
                index = (bound >> 21) & 31
                low_value = 0
                if guard >= 4:
                    prior = int.from_bytes(target.code[guard - 4:guard], 'big')
                    if prior >> 26 == 9 and (prior >> 16) & 31 == index and (prior >> 21) & 31:
                        low_value = -signed(prior)
                default = guard + 8 + signed(branch) * 4
                results.extend(relabeled(source, fn['name'], low_value, compiled, destinations, default))
        return results
    except (ELFError, ValueError, TypeError, IndexError, KeyError):
        return []
