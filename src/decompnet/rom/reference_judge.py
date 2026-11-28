"""Judge a project object against a verified ROM reference without publishing a result.

A caller must bind the object to the source and compiler run that made it. This module
compares only the object, image and manifest it receives; it is not a source proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import export_entry, match, split
from .symbols import Symbol, SymbolTable


@dataclass(frozen=True)
class Comparison:
    """Relocated objects for instruction comparison, plus the ROM judge's data verdict."""
    target_object: bytes
    candidate_object: bytes | None
    exact: bool
    problems: tuple[str, ...]


def _inside(root: Path, path: str) -> Path:
    path = Path(path)
    resolved = (root / path).resolve()
    if path.is_absolute() or '..' in path.parts or root not in resolved.parents:
        raise ValueError('ROM reference paths must stay inside the project.')
    return resolved


class ReferenceImage:
    """One verified image and symbol table shared by several reference functions."""

    def __init__(self, root: Path, manifest: dict):
        self.root = root.resolve()
        self.manifest = manifest
        _inside(self.root, manifest['image'])
        self.image, self.read = export_entry.verified_image_reader(self.root, manifest)
        symbols = [Symbol(name, address, 0, 'data') for name, address in manifest['symbols'].items()]
        symbols += [Symbol(f['name'], f['vram'], 0, 'func') for f in manifest['functions']]
        symbols += [Symbol(f['symbol'], f['vram'], 0, 'func') for f in manifest['functions'] if f.get('symbol')]
        symbols += [Symbol(d['name'], d['vram'], d.get('size', 0), 'data') for d in manifest.get('data', [])]
        symbols += [Symbol(n, a, 0, 'data') for n, a in manifest.get('labels', {}).items()
                    if n not in manifest['symbols']]
        self.table = SymbolTable(symbols)

    def target(self, fn: dict):
        """Resolve one original body and check its image bytes and address."""
        parsed = split.parse_function(_inside(self.root, fn['asm']), fn['seg'], self.manifest['big_endian'])
        if parsed is None:
            raise ValueError('The reference assembly does not contain the recorded function.')
        target = match.target_from_split(parsed, self.table.resolve, self.manifest['big_endian'],
                                         self.manifest.get('gp') or 0)
        export_entry.verify_target(self.image, self.manifest, fn, target)
        return target


def reference(root: Path, manifest: dict, fn: dict):
    """Read an original function and its mapped image, checking every reference path."""
    image = ReferenceImage(root, manifest)
    return image.target(fn), image.table, image.read


def compare(root: Path, manifest: dict, fn: dict, compiled_object: bytes) -> Comparison:
    """Link one compiled object at its original address and verify its referenced data.

    Both objects contain only linked instructions; the separate verdict must accompany
    any objdiff result, since equal instructions alone do not prove equal constants.
    Missing image mappings and unresolved relocations can never produce an exact result.
    """
    target, table, read = reference(root, manifest, fn)
    name = fn.get('symbol') or fn['name']
    from decompnet.consoles import get

    console = get(manifest.get('platform'))
    linker = console.link_function if console.rust_desktop_judge else match.link
    linked = linker(compiled_object, name, target, table, read)
    problems = list(linked.problems)
    problems.extend(f'unverified constant at 0x{address:08X} ({size} bytes)'
                    for address, size in linked.missing_data_reads)
    if not linked.exact and not problems:
        problems.append('Linked code or data did not verify against the image.')
    target_object = export_entry.object_for(compiled_object, name, target.code)
    candidate_object = (export_entry.object_for(compiled_object, name, linked.linked_code)
                        if linked.linked_code else None)
    return Comparison(target_object, candidate_object,
                      bool(linked.exact and not problems and candidate_object), tuple(problems))
