"""Hand-written dispatch entries in imported reference objects."""
from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection


def padding_only(target: Path) -> bool:
    """A lone MIPS nop has no return and may be padding misreported as a function.

    This only defers candidate preparation; it does not assign an SDK or
    assembly identity. Relocations, multiple functions and other code stay open
    to ordinary preparation.
    """
    try:
        # This check is only for tiny padding objects. Avoid reading an entire
        # large reference just to decide whether to defer it.
        with target.open('rb') as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return False
        elf = ELFFile(BytesIO(raw))
        if elf['e_machine'] != 'EM_MIPS':
            return False
        table = elf.get_section_by_name('.symtab')
        if table is None:
            return False
        funcs = [s for s in table.iter_symbols() if s['st_info']['type'] == 'STT_FUNC']
        if len(funcs) != 1:
            return False
        sym = funcs[0]
        index = sym['st_shndx']
        if not isinstance(index, int) or int(sym['st_size']) != 4:
            return False
        section = elf.get_section(index)
        if not section or not section['sh_flags'] & 4:
            return False
        base = 0 if elf['e_type'] == 'ET_REL' else int(section['sh_addr'])
        offset = int(sym['st_value']) - base
        if offset < 0 or offset + 4 > section['sh_size']:
            return False
        for rel in elf.iter_sections():
            if isinstance(rel, RelocationSection) and rel['sh_info'] == index:
                if any(offset <= int(r['r_offset']) - base < offset + 4 for r in rel.iter_relocations()):
                    return False
        return section.data()[offset:offset + 4] == b'\0' * 4
    except (OSError, ValueError, TypeError, KeyError, IndexError, ELFError):
        return False


def dispatches(console, root: Path, target: Path | None) -> dict[str, dict]:
    """Classify complete, relocation-free dispatch entries using the console's ABI rules.

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
            if not name or sym['st_info']['type'] != 'STT_FUNC' or size not in (8, 12) or not isinstance(index, int):
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
            words = [int.from_bytes(code[i:i + 4], order) for i in range(0, size, 4)]
            # The longer table jumps to a fixed dispatcher and passes its service
            # number in $t0, outside the C calling convention. Restrict the delay
            # slot to the two observed ways of loading that register.
            long_dispatch = (size == 12 and words[:2] == [0x3c091000, 0x01200008]
                             and (words[2] & 0xffff0000 == 0x24080000 or words[2] == 0x00004025))
            if not (size == 8 and words[0] == 8) and not long_dispatch:
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
