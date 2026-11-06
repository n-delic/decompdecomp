"""Expose independently linked images as ordinary, byte-checked objdiff units.

The project owns decompression, compilation and relocation. It passes only source-backed
bodies after its image gates; passthrough assembly has a target and never a candidate.
These units are read-only: grouped/whole-program compilers need their own editing adapter.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .project import ForkError


def _inside(root: Path, path: str) -> Path:
    result = (root / path).resolve()
    if root.resolve() not in result.parents:
        raise ForkError('linked-image paths must stay inside the project')
    return result


def export(root: Path, populations: list[dict], *, object_writer) -> list[dict]:
    """Validate all populations before publishing generated targets/candidates.

    Each population supplies an original image + SHA-256, base address, non-overlapping
    function extents, and source-owned linked bodies. A body requires source checksums;
    references always come from the original image, never from compiled candidates.
    Existing objdiff units are merged by the caller, never replaced by this helper.
    """
    root = root.resolve()
    work = _inside(root, '.decomp-wtf/linked-images')
    planned, ids = [], set()
    for pop in populations:
        identity = pop['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', identity) or identity in ids:
            raise ForkError('linked images need unique, simple population identifiers')
        ids.add(identity)
        image = _inside(root, pop['image']).read_bytes()
        if hashlib.sha256(image).hexdigest() != pop['sha256']:
            raise ForkError(f'{identity}: original image SHA-256 does not match')
        end, symbols = 0, set()
        for fn in sorted(pop['functions'], key=lambda f: f['address']):
            name, size = fn['name'], fn['size']
            start = fn['address'] - pop['base']
            if (not re.fullmatch(r'[A-Za-z_.$][\w.$]*', name) or name in symbols or
                    size <= 0 or size % 4 or start < end or start < 0 or start + size > len(image)):
                raise ForkError(f'{identity}: overlapping, duplicate or invalid function extent')
            symbols.add(name)
            end = start + size
            target = object_writer(name, fn['address'], image[start:end])
            body = fn.get('body')
            guards = fn.get('source_checksums') or {}
            if body is not None:
                if not guards or body != image[start:end]:
                    raise ForkError(f'{identity}/{name}: source-backed linked body does not match the image')
                for path, digest in guards.items():
                    if hashlib.sha256(_inside(root, path).read_bytes()).hexdigest() != digest:
                        raise ForkError(f'{identity}/{name}: source changed after compilation')
            entry = {'name': f'images/{identity}/{name}',
                     'target_path': str((work / identity / f'{name}.target.o').relative_to(root)),
                     'metadata': {'progress_population': pop['label'], 'linked_image': True,
                                  'source_checksums': guards,
                                  'original_source': fn.get('source')}}
            if body is not None:
                entry['base_path'] = str((work / identity / f'{name}.base.o').relative_to(root))
            planned.append((entry, target, object_writer(name, fn['address'], body) if body is not None else None))
    for entry, target, base in planned:
        dest = _inside(root, entry['target_path'])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(target)
        elif dest.read_bytes() != target:
            raise ForkError('a linked-image reference changed; retain the existing references and investigate')
        if base is not None:
            _inside(root, entry['base_path']).write_bytes(base)
    return [entry for entry, _, _ in planned]


def stale(root: Path, unit: dict, cache: dict) -> bool:
    """Do not award a match to a stale source-backed image snapshot."""
    guards = (unit.get('metadata') or {}).get('source_checksums')
    if not guards:
        return False
    for path, expected in guards.items():
        if path not in cache:
            try:
                cache[path] = hashlib.sha256(_inside(root, path).read_bytes()).hexdigest()
            except OSError:
                cache[path] = None
        if cache[path] != expected:
            return True
    return False


def populations(root: Path, rep: dict, aside: set[str]) -> list[dict]:
    """Disjoint code-byte totals, keeping images that share addresses separate."""
    cfg = json.loads((root / 'objdiff.json').read_text(encoding='utf-8'))
    labels = {u.get('name') or u.get('path'): (u.get('metadata') or {}).get('progress_population', 'Static code')
              for u in cfg.get('units', [])}
    grouped = {}
    for unit in rep.get('units', []):
        grouped.setdefault(labels.get(unit['name'], 'Static code'), []).append(unit)
    if len(grouped) < 2:
        return []
    out = []
    for label, units in grouped.items():
        functions = [f for u in units for f in u.get('functions', []) if f['name'] not in aside]
        matched = [f for f in functions if float(f.get('fuzzy_match_percent') or 0) >= 100]
        size = sum(int(f.get('size') or 0) for f in functions)
        done = sum(int(f.get('size') or 0) for f in matched)
        out.append(dict(name=label, total_functions=len(functions), matched_functions=len(matched),
                        total_code=size, matched_code=done, matched_code_percent=100 * done / size if size else 0))
    return out
