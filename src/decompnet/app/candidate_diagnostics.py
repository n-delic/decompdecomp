"""Explain linking blockers and standalone layout separately from the exact function verdict."""
import re

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from ..candidate_work import elf as candidate_elf
from ..elfobj import _function_symbols
from ..rom.export import _section_bases, _single_definition


def unresolved(problems):
    """A missing address is not a reason to permute otherwise identical instructions."""
    names = sorted({m[1] for p in problems for m in re.finditer(r'`([^`]+)` is not a known symbol', p)})
    if not names:
        return None
    return {'kind': 'unresolved_symbols', 'symbols': names,
            'message': 'Resolve the addresses of '+', '.join(names)+
                       ' before random permutations. Use target relocation evidence; do not guess addresses.'}


def export_layout(obj, name, exact, placements):
    """An advisory check, never a substitute for rebuilding and comparing the complete image."""
    if not exact:
        return {'state': 'unmatched', 'ready': False, 'message': 'Function is not yet byte-identical.'}
    try:
        elf = candidate_elf(obj, ELFFile)
        if elf['e_machine'] != 'EM_MIPS' or elf['e_type'] != 'ET_REL':
            return {'state': 'unsupported', 'ready': None,
                    'message': 'Standalone layout checks are not available for this object format yet.'}
        # Old assemblers can emit a tentative object with raw SHN_XINDEX but no index
        # table. The function judge need not read that unused symbol; GNU ld rejects it.
        for index,section in enumerate(elf.iter_sections()):
            if section['sh_type'] not in ('SHT_SYMTAB','SHT_DYNSYM'):continue
            extended=any(s['sh_type']=='SHT_SYMTAB_SHNDX' and s['sh_link']==index for s in elf.iter_sections())
            if not extended and any(s['st_shndx'] in (0xffff,'SHN_XINDEX') for s in section.iter_symbols()):
                return {'state':'blocked','ready':False,
                        'message':'The object has an extended symbol index without its required index table. '
                                  'Check unintended tentative definitions in the private header against actual extern declarations; '
                                  'do not change the target or patch symbol indices to force linking.'}
        if not _single_definition(obj, name):
            problem = 'The candidate emits extra function bodies; export needs one function per object.'
        else:
            _, problem = _section_bases(placements)
            if problem:
                problem = ('The candidate has non-contiguous constant pools. Reference existing ROM data '
                           'separately or split the pools before standalone export.')
            if not problem and next(iter(_function_symbols(elf).values()))[1]:
                return {'state': 'requires_normalization', 'ready': None,
                        'message': 'The compiler emitted code before the function entry; verify prefix normalization with the standalone builder.'}
        return {'state': 'blocked' if problem else 'layout_checked', 'ready': not bool(problem),
                'message': problem or 'Standalone layout checked; a complete byte-identical rebuild is still required.'}
    except (ELFError, ValueError, KeyError, StopIteration):
        return {'state': 'unsupported', 'ready': None,
                'message': 'Standalone layout could not be checked for this object format.'}


def text(checks):
    parts = [checks['link']['message']] if checks.get('link') else []
    for row in checks.get('address_mismatches', [])[:4]:
        parts.append(f"Raw MIPS address at diff row {row['row']+1}: target 0x{row['target']:08X}, yours 0x{row['candidate']:08X}. Verify this expression before scheduling permutations; symbolic labels may obscure the address.")
    if checks.get('instructions', {}).get('exact') and checks.get('data'):
        parts.append('Instructions match; constant data differs. Check initializer values, object sizes and anonymous-array padding before changing control flow.\n'+'\n'.join(checks['data']))
    if checks.get('linked_bytes', {}).get('exact'):
        parts.append('Linked bytes verified identical. Symbolic relocation labels or displayed diff annotations do not override this verdict; do not repair those annotations.')
    if checks.get('export', {}).get('state') not in (None, 'unmatched'):
        parts.append(checks['export']['message'])
    return '\n'.join(parts)
