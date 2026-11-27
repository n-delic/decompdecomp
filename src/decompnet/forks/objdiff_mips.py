"""Temporary objdiff views of MIPS relocatable objects with section addresses."""

from __future__ import annotations

from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile


def view(path: Path, directory: Path, cache: dict[Path, Path]) -> Path:
    """Clear nonzero section addresses in a temporary ELF32 MIPS ET_REL view.

    ET_REL symbols and relocations are section-relative. Some assemblers nevertheless assign addresses to the
    sections; objdiff can reject those objects as having symbols outside their sections. Preserve the original
    object, all contents, symbols and relocations. Do not touch executables, other architectures, or malformed ELF.
    """
    path = path.resolve()
    if path in cache:
        return cache[path]
    result = path
    try:
        with path.open('rb') as f:
            elf = ELFFile(f)
            if elf.elfclass != 32 or elf.header['e_type'] != 'ET_REL' or elf.header['e_machine'] != 'EM_MIPS':
                return path
            offsets = [int(elf.header['e_shoff']) + i * int(elf.header['e_shentsize']) + 12
                       for i, section in enumerate(elf.iter_sections()) if i and section['sh_addr']]
            if not offsets:
                return path
            f.seek(0)
            data = bytearray(f.read())
        if any(offset + 4 > len(data) for offset in offsets):
            return path
        for offset in offsets:
            data[offset:offset + 4] = b'\0' * 4
        result = directory / f'{len(cache)}.o'
        result.write_bytes(data)
    except (OSError, ELFError, ValueError, IndexError):
        pass
    cache[path] = result
    return result
