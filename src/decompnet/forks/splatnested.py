"""References for a cartridge build whose executable images have their own nested Splat projects.

The outer split can contain only binary assets, including compressed programs. Use the project's declared
extraction rules and each inner image's original checksum; never turn an opaque binary into one fake function.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

from .project import STATE, ForkError, ForkProject
from .splatrefs import config_sha1, digest, inside


def built_child_sha1(root: Path, child: Path, image: Path, outer_sha1: str) -> str | None:
    """The SHA-1 of a child's original image when it names none: an image the project extracts from the game
    (decompressed) is the original when the child's own build, linked from the sources, reproduces it byte for
    byte and the rebuilt cartridge carries the original cartridge's checksum."""
    if not image.is_file():
        return None
    built = sorted((child / 'build').glob('*.bin'))
    if not any(b != image and b.is_file() and digest(b) == digest(image) for b in built):
        return None
    rebuilt = [p for p in sorted((root / 'build').glob('*')) if p.suffix in ('.z64', '.bin', '.n64')
               and p.is_file() and digest(p) == outer_sha1.lower()]
    return digest(image) if rebuilt else None


def child_rooted(root: Path, path: Path, cfg: dict, image: Path, checksum: str, destination: Path, console,
                 log) -> list[dict]:
    """Units of a child that has C: its sources and built objects are the child's own, so the references are made
    with the child as the project (its own paths), then named under the outer project. The child's checksum is
    pinned in the config, so the usual verification of every reference against the image applies unchanged."""
    child = path.parent
    staged = copy.deepcopy(cfg)
    staged.pop('sha1', None)
    staged['options'].pop('sha1', None)
    staged['sha1'] = checksum
    staged['options']['target_path'] = image.relative_to(child).as_posix()
    log(f'Preparing nested program {path.relative_to(root)} from its own sources')
    sub = ForkProject(child)
    console.generate_objdiff(sub, path, staged, log=log)
    made = child / 'objdiff.json'
    units = json.loads(made.read_text(encoding='utf-8'))['units']
    prefix = child.relative_to(root).as_posix()
    for unit in units:
        unit['name'] = f'{prefix}/{unit["name"]}'
        for key in ('target_path', 'base_path'):
            if unit.get(key):
                unit[key] = f'{prefix}/{unit[key]}'
        meta = unit.get('metadata') or {}
        for key in ('source_path', 'draft_assembly', 'assembly_source'):
            if meta.get(key):
                meta[key] = f'{prefix}/{meta[key]}'
    made.unlink()  # (the outer project's objdiff.json is the only one; the references stay under the child)
    return units


def generate(project, config_path: Path, config: dict, console, *, version=None, log=print) -> bool:
    """Publish one reference set for declared assembly images after all original checksums and code verify."""
    root = project.root.resolve()
    if (root / 'objdiff.json').exists():
        return False
    options = config['options']
    if inside(root, str(config_path.parent / options.get('base_path', '.'))) != root:
        raise ForkError('The cartridge Splat base_path must identify this checkout before nested import')
    original = inside(root, options.get('target_path', ''))
    sha1 = config_sha1(config_path, config)
    if not isinstance(sha1, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', sha1):
        raise ForkError('Nested reference generation needs the original cartridge SHA-1 in its Splat config')
    if not original.is_file() or digest(original) != sha1.lower():
        raise ForkError('Nested reference generation needs the matching original cartridge image')
    version = version or project.splat_default_version()
    if not version or not re.fullmatch(r'[A-Za-z0-9._-]+', version):
        raise ForkError('Select the project version before generating nested program references')
    children = []
    # A nested build must declare the exact versioned split, not merely contain an unrelated YAML file.
    for makefile in sorted(root.glob('*/Makefile')):
        child = inside(root, str(makefile.parent))
        if child.name.startswith('.') or child.name in ('tools', 'build', 'assets', 'config'):
            continue
        declared = makefile.read_text(encoding='utf-8').replace('$(VERSION)', version)
        for path in sorted(child.glob(f'*.{version}.yaml')):
            inside(root, str(path))
            if not re.fullmatch(r'[A-Za-z0-9._-]+', path.stem):
                raise ForkError('Nested Splat configs need portable versioned filenames')
            if path.name not in declared:
                continue
            cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
            if not isinstance(cfg, dict) or not isinstance(cfg.get('options'), dict):
                continue
            segments = cfg.get('segments')
            if not isinstance(segments, list):
                raise ForkError(f'{path.relative_to(root)} needs explicit Splat segments before import')
            code = [s for s in segments if isinstance(s, dict) and s.get('type') == 'code']
            if not code:
                continue
            if any(not isinstance(s.get('subsegments'), list) for s in code):
                raise ForkError(f'{path.relative_to(root)} needs explicit Splat subsegments before import')
            opts = cfg['options']
            if inside(child, str(path.parent / opts.get('base_path', '.'))) != child:
                raise ForkError('Nested Splat base_path must identify its own build directory')
            image = inside(child, opts.get('target_path', ''))
            checksum = cfg.get('sha1') or opts.get('sha1')
            if checksum is None and path.with_suffix('.sha1').is_file():
                checksum = inside(child, str(path.with_suffix('.sha1'))).read_text(encoding='utf-8').strip()
            if checksum is None:
                checksum = built_child_sha1(root, child, image, sha1)
            if not isinstance(checksum, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', checksum):
                raise ForkError(f'{path.relative_to(root)} needs an original-image SHA-1 before automatic import')
            children.append((path, cfg, image, checksum.lower()))
    if not children:
        raise ForkError('The outer Splat config contains no code. No version-matched nested program splits were '
                        'declared by its child Makefiles; configure the executable images before import')
    missing = [image.relative_to(root).as_posix() for _, _, image, _ in children if not image.is_file()]
    if missing:
        log('Extracting nested original program images with the project\'s build rules')
        ok, tail = project.run(['make', f'VERSION={version}', '--', *missing], log=log)
        if not ok:
            raise ForkError(f'The project could not extract its nested program images:\n{tail}')
    for path, _, image, checksum in children:
        if not image.is_file() or digest(image) != checksum:
            raise ForkError(f'{path.relative_to(root)}: extracted program SHA-1 does not match its original; '
                            'no objdiff configuration was published')
    parent = inside(root, STATE)
    parent.mkdir(exist_ok=True)
    destination = inside(root, STATE + '/objdiff-references')
    if destination.exists():
        raise ForkError('Existing automatic references were retained; restore their objdiff.json before retrying')
    with tempfile.TemporaryDirectory(prefix='nested-reference-stage-', dir=parent) as temporary:
        work = Path(temporary)
        publish = work / 'publish'
        publish.mkdir()
        entries = []
        for index, (path, cfg, image, checksum) in enumerate(children):
            if any(isinstance(e, list) and len(e) > 1 and e[1] in ('c', 'cpp')
                   for s in cfg['segments'] if isinstance(s, dict) and s.get('type') == 'code'
                   for e in s.get('subsegments', [])):
                entries += child_rooted(root, path, cfg, image, checksum, destination, console, log)
                continue
            private = work / f'image-{index}'
            private.mkdir()
            staged = copy.deepcopy(cfg)
            opts = staged['options']
            # Legacy extractors used these options; neither affects original code bytes or addresses.
            opts.pop('create_detected_syms', None)
            opts.pop('find_file_boundaries', None)
            for segment in staged.get('segments', []):
                if not isinstance(segment, dict):
                    continue
                parts = segment.get('subsegments', [])
                # Old Splat used a zero-byte linker marker before the following data segment.
                # Remove only markers with an explicit coincident boundary; payload stays mapped.
                segment['subsegments'] = [entry for i, entry in enumerate(parts) if not (
                    isinstance(entry, list) and len(entry) > 1 and entry[1] == 'linker'
                    and i + 1 < len(parts) and isinstance(parts[i + 1], list)
                    and parts[i + 1][0] == entry[0])]
            opts.pop('sha1', None)
            extensions = opts.get('extensions_path')
            if extensions:  # (the project's own splat segment types, found from the child's folder)
                opts['extensions_path'] = str(inside(root, str(path.parent / extensions)))
            staged['sha1'] = checksum
            opts.update(base_path='.', target_path='original.bin',
                        platform=opts.get('platform') or options.get('platform') or console.id)
            for key in ('symbol_addrs_path', 'reloc_addrs_path'):
                values = opts.get(key)
                if values is None:
                    continue
                values = values if isinstance(values, list) else [values]
                copied = []
                for value in values:
                    source = inside(path.parent, value)
                    if source.is_file():
                        target = private / f'symbols-{len(copied)}-{key}.txt'
                        shutil.copy2(source, target)
                        copied.append(target.name)
                if copied:
                    opts[key] = copied
                else:
                    opts.pop(key)
            shutil.copy2(image, private / 'original.bin')
            staged_path = private / 'splat.yaml'
            staged_path.write_text(yaml.safe_dump(staged), encoding='utf-8')
            log(f'Preparing nested program {path.relative_to(root)}')
            console.generate_objdiff(ForkProject(private), staged_path, staged, log=log)
            generated = json.loads((private / 'objdiff.json').read_text(encoding='utf-8'))
            references = private / STATE / 'objdiff-references'
            identity = f'{index}-{path.stem}'
            for folder in ('objects', 'asm'):
                if (references / folder).is_dir():
                    shutil.copytree(references / folder, publish / folder / identity)
            for unit in generated['units']:
                relative = inside(private, unit['target_path']).relative_to(references / 'objects')
                unit['name'] = identity + '/' + unit['name']
                unit['target_path'] = (destination / 'objects' / identity / relative).relative_to(root).as_posix()
                if (references / 'asm' / relative.with_suffix('.s')).is_file():
                    unit['metadata'] = {'draft_assembly': (
                        destination / 'asm' / identity / relative.with_suffix('.s')).relative_to(root).as_posix()}
                entries.append(unit)
        pending = publish / 'objdiff.json'
        notes = ['Counts original program images declared by the selected nested Splat configs: '
                 + ', '.join(path.stem for path, _, _, _ in children)
                 + '. Other opaque binary cartridge sections are not included in function progress.']
        pending.write_text(json.dumps({'custom_make': 'make', 'custom_args': [f'VERSION={version}'],
                                     'build_target': False, 'build_base': True, 'units': entries,
                                     'coverage_notes': notes}, indent=2) + '\n', encoding='utf-8')
        if (root / 'objdiff.json').exists():
            raise ForkError('objdiff.json appeared during setup; the existing configuration was retained')
        try:
            shutil.move(str(publish), destination)
            os.link(destination / 'objdiff.json', root / 'objdiff.json')
        except OSError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    log(f'Generated objdiff.json with {len(entries)} verified objects from {len(children)} original program images')
    return True
