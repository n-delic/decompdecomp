"""Exact self-contained SDK bodies in imported ELF reference objects."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection


def identities(console, root: Path, target: Path | None) -> dict[str, dict]:
    """Identify complete symbol bodies with no relocations; names alone never prove an SDK identity."""
    if target is None or not console.sdk_assembly_sizes:
        return {}
    root = root.resolve()
    target = target.resolve()
    if root not in target.parents:
        return {}
    try:
        raw = target.read_bytes()
        with BytesIO(raw) as stream:
            elf = ELFFile(stream)
            if elf.little_endian == console.big_endian:
                return {}
            table = elf.get_section_by_name('.symtab')
            if table is None:
                return {}
            relocs: dict[int, list[int]] = {}
            for section in elf.iter_sections():
                if isinstance(section, RelocationSection):
                    relocs.setdefault(section['sh_info'], []).extend(r['r_offset'] for r in section.iter_relocations())
            found = {}
            duplicates = set()
            for sym in table.iter_symbols():
                name, size, index = sym.name, int(sym['st_size']), sym['st_shndx']
                if not name or sym['st_info']['type'] != 'STT_FUNC' or size not in console.sdk_assembly_sizes or not isinstance(index, int):
                    continue
                section = elf.get_section(index)
                if not section or not section['sh_flags'] & 4:
                    continue
                section_base = 0 if elf['e_type'] == 'ET_REL' else int(section['sh_addr'])
                offset = int(sym['st_value']) - section_base
                if offset < 0 or offset + size > section['sh_size']:
                    continue
                if any(offset <= int(r) - section_base < offset + size for r in relocs.get(index, [])):
                    continue
                code = section.data()[offset:offset + size]
                identity = console.sdk_assembly_identity(code)
                if not identity or identity.get('from_c'):
                    continue
                if name in found:
                    duplicates.add(name)
                found[name] = {**identity, 'verified': True, 'kind': 'sdk',
                               'verification_method': 'fork-sdk-digest', 'symbol': name,
                               'object_path': target.relative_to(root).as_posix(),
                               'object_sha256': hashlib.sha256(raw).hexdigest(),
                               'evidence': 'Complete reference-object bytes match a self-contained SDK routine; no relocations.'}
            return {name: value for name, value in found.items() if name not in duplicates}
    except (OSError, ValueError, TypeError, KeyError, IndexError, ELFError):
        return {}


def reverify(console, root: Path, symbol: str, size: int, proof: dict) -> bool:
    """Re-read the reference object when the UI or an agent asks whether the proof still holds."""
    rel = proof.get('object_path')
    if not isinstance(rel, str) or Path(rel).is_absolute() or '..' in Path(rel).parts:
        return False
    current = identities(console, root, root / rel).get(symbol)
    return bool(current and current.get('bytes') == size and current.get('object_sha256') == proof.get('object_sha256')
                and current.get('target_sha256') == proof.get('target_sha256') and current.get('names') == proof.get('names'))
