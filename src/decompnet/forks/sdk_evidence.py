"""Verified SDK identities in imported ELF reference objects."""

from __future__ import annotations

import copy
import hashlib
import json
from io import BytesIO
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from .reference_cache import Cache, ChangedDuringRead, read


def identities(console, root: Path, target: Path | None) -> dict[str, dict]:
    """Identify complete SDK bodies; console-specific relocated shapes require their own proof."""
    if target is None or not console.sdk_assembly_sizes:
        return {}
    root = root.resolve()
    target = target.resolve()
    if root not in target.parents:
        return {}
    try:
        raw = read(target)
        digest = hashlib.sha256(raw).hexdigest()
        result = _CACHE.get((console, root, target, digest), lambda: _identities(console, root, target, raw, digest))
        return copy.deepcopy(result)
    except (OSError, ChangedDuringRead):
        return {}


_CACHE = Cache()


def _identities(console, root: Path, target: Path, raw: bytes, digest: str) -> dict[str, dict]:
    try:
        with BytesIO(raw) as stream:
            elf = ELFFile(stream)
            if elf.little_endian == console.big_endian:
                return {}
            table = elf.get_section_by_name('.symtab')
            if table is None:
                return {}
            relocs: dict[int, list[tuple[int, int, int, bool]]] = {}
            for section in elf.iter_sections():
                if isinstance(section, RelocationSection):
                    relocs.setdefault(section['sh_info'], []).extend(
                        (int(r['r_offset']), int(r['r_info_type']), int(r['r_info_sym']),
                         section.is_RELA() or section['sh_link'] != elf.get_section_index('.symtab')
                         or int(r['r_info_sym']) >= table.num_symbols())
                        for r in section.iter_relocations())
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
                code = section.data()[offset:offset + size]
                inside = [(at - section_base - offset, kind, symbol, invalid)
                          for at, kind, symbol, invalid in relocs.get(index, [])
                          if offset <= at - section_base < offset + size]
                if inside:
                    if elf['e_type'] != 'ET_REL' or any(invalid for _, _, _, invalid in inside):
                        continue
                    identity = console.sdk_relocated_assembly_identity(
                        code, [(at, kind, symbol) for at, kind, symbol, _ in inside], elf['e_machine'])
                else:
                    identity = console.sdk_assembly_identity(code)
                if not identity or identity.get('from_c'):
                    continue
                if name in found:
                    duplicates.add(name)
                found[name] = {**identity, 'verified': True, 'kind': 'sdk',
                               'verification_method': 'fork-sdk-digest', 'symbol': name,
                               'object_path': target.relative_to(root).as_posix(),
                               'object_sha256': digest,
                               'evidence': (identity.get('evidence') if inside else
                                            'Complete reference-object bytes match a self-contained SDK routine; no relocations.')}
            return {name: value for name, value in found.items() if name not in duplicates}
    except (OSError, ValueError, TypeError, KeyError, IndexError, ELFError):
        return {}


def reverify(console, root: Path, unit_name: str, symbol: str, size: int, proof: dict) -> bool:
    """Re-read the current unit's reference and verify it is the object the proof identified."""
    rel = proof.get('object_path')
    if (not isinstance(rel, str) or Path(rel).is_absolute() or '..' in Path(rel).parts
            or proof.get('symbol') != symbol):
        return False
    try:
        config = json.loads((root / 'objdiff.json').read_text(encoding='utf-8'))
        units = config.get('units') or config.get('objects') or []
        matches = [u for u in units if (u.get('name') or u.get('path') or '?') == unit_name]
        if len(matches) != 1:
            return False
        entry = matches[0]
        target = entry.get('target_path')
        if not target and config.get('target_dir') and entry.get('path'):
            target = str(Path(config['target_dir']) / entry['path'])
        if not isinstance(target, str) or (root / target).resolve() != (root / rel).resolve():
            return False
        current = identities(console, root, root / target).get(symbol)
        return bool(current and current.get('bytes') == size and current.get('object_sha256') == proof.get('object_sha256')
                    and current.get('target_sha256') == proof.get('target_sha256') and current.get('names') == proof.get('names'))
    except (OSError, ValueError, TypeError, AttributeError):
        return False
