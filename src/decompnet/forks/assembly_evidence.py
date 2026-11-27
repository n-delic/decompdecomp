"""Hand-written dispatch entries in imported reference objects."""
from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection


def dispatches(console, root: Path, target: Path | None) -> dict[str, dict]:
    """Classify complete, relocation-free null-jump entries using the console's ABI rules.

    A project's assembly can contain whole tables of dispatch entries, none of which
    should get a C scaffold. SDK identities take precedence over the generic rule.
    """
    if target is None or console.arch != 'mips':
        return {}
    root, target = root.resolve(), target.resolve()
    if root not in target.parents:
        return {}
    try:
        raw = target.read_bytes()
        elf = ELFFile(BytesIO(raw))
        if elf['e_machine'] != 'EM_MIPS' or elf.little_endian == console.big_endian:
            return {}
        table = elf.get_section_by_name('.symtab')
        if table is None:
            return {}
        relocs = {}
        for section in elf.iter_sections():
            if isinstance(section, RelocationSection):
                relocs.setdefault(section['sh_info'], []).extend(int(r['r_offset']) for r in section.iter_relocations())
        found, duplicates, classified = {}, set(), {}
        digest = hashlib.sha256(raw).hexdigest()
        order = 'big' if console.big_endian else 'little'
        for sym in table.iter_symbols():
            name, size, index = sym.name, int(sym['st_size']), sym['st_shndx']
            if not name or sym['st_info']['type'] != 'STT_FUNC' or size != 8 or not isinstance(index, int):
                continue
            section = elf.get_section(index)
            if not section or not section['sh_flags'] & 4:
                continue
            base = 0 if elf['e_type'] == 'ET_REL' else int(section['sh_addr'])
            offset = int(sym['st_value']) - base
            if offset < 0 or offset + size > section['sh_size']:
                continue
            if any(offset <= r - base < offset + size for r in relocs.get(index, [])):
                continue
            code = section.data()[offset:offset + size]
            if int.from_bytes(code[:4], order) != 8:
                continue
            if code not in classified:
                classified[code] = None if console.sdk_assembly_identity(code) else console.asm_reason(code)
            reason = classified[code]
            if not reason:
                continue
            if name in found:
                duplicates.add(name)
            found[name] = {'object_sha256': digest, 'object_path': target.relative_to(root).as_posix(),
                           'symbol': name, 'bytes': size, 'reason': reason}
        return {name: proof for name, proof in found.items() if name not in duplicates}
    except (OSError, ValueError, TypeError, KeyError, IndexError, ELFError):
        return {}
