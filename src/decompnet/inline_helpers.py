"""Reject generated aggregate dispatch helpers unless the compiler fully removes them.

This is an additional candidate check, never a match verdict. Ordinary linking/data checks
still apply. Unknown object formats fail closed only for this opt-in generated helper family.
"""
import io

PREFIX = '__repair_aggregate_call_'


def check(source, obj):
    if PREFIX not in source:
        return ''
    import struct

    from elftools.common.exceptions import ELFError
    try:
        if obj.startswith(b'\x7fELF'):
            from elftools.elf.elffile import ELFFile
            from elftools.elf.sections import SymbolTableSection
            elf = ELFFile(io.BytesIO(obj))
            names = [s.name for section in elf.iter_sections() if isinstance(section, SymbolTableSection)
                     for s in section.iter_symbols()]
        else:
            from .coffobj import c_name, is_coff, sections
            if not is_coff(obj):
                return 'Generated aggregate helper needs ELF or supported COFF inline verification.'
            names = [c_name(name) for name, _, _ in sections(obj)]
            names += [c_name(r[2]) for _, _, relocs in sections(obj) for r in relocs]
        if any(PREFIX in name for name in names):
            return 'Generated aggregate helper did not fully inline; reject this source shape.'
    except (ValueError, TypeError, IndexError, KeyError, OSError, ELFError, struct.error):
        return 'Could not verify generated aggregate helper removal.'
    return ''
