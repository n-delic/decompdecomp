"""Give generated ROM-derived, source-less reference units C candidate files using the project's own build."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from .. import seeds
from .project import FORBIDDEN_PARTS, STATE, ForkError

HEADER = '''/* C candidates for an undecompiled unit. The original ROM/assembly remains the linked baseline. */
typedef signed char s8;
typedef unsigned char u8;
typedef signed short s16;
typedef unsigned short u16;
typedef signed int s32;
typedef unsigned int u32;
typedef signed long long s64;
typedef unsigned long long u64;
typedef float f32;
typedef double f64;
''' + seeds.M2C_MACROS + '\n'


def _has_handwritten_annotations(assembly: Path) -> bool:
    """Defer mixed units that the upstream disassembler flags as handwritten.

    The annotation is not proof of SDK identity. It is a reason to leave the
    whole source-less unit for review until its functions can be classified;
    assigning a C file to the unit would otherwise open every function in it.
    """
    text = assembly.read_text(encoding='utf-8', errors='replace')
    return bool(re.search(r'(?m)^\s*/\*\s*Handwritten function\s*\*/\s*\n\s*glabel\s+\S+', text))


def _object_layout(config: dict) -> tuple[Path, bool]:
    """Infer the C object directory and whether the build retains the source suffix."""
    layouts = set()
    for entry in config.get('units', []):
        source = (entry.get('metadata') or {}).get('source_path')
        base = entry.get('base_path')
        if not isinstance(source, str) or not isinstance(base, str):
            continue
        source_path, object_path = Path(source), Path(base)
        if (source_path.is_absolute() or object_path.is_absolute() or '..' in source_path.parts
                or '..' in object_path.parts):
            continue
        for keep_suffix in (True, False):
            expected = Path(source + '.o') if keep_suffix else source_path.with_suffix('.o')
            if (len(object_path.parts) > len(expected.parts)
                    and object_path.parts[-len(expected.parts):] == expected.parts):
                layouts.add((object_path.parts[:-len(expected.parts)], keep_suffix))
    if len(layouts) == 1:
        prefix, keep_suffix = next(iter(layouts))
        return Path(*prefix), keep_suffix
    return Path('build'), True


def _object_prefix(config: dict) -> Path:
    return _object_layout(config)[0]


def _child_layout(config: dict, child: Path) -> tuple[Path, bool]:
    """Infer a child build's layout from C units belonging to that child."""
    units = []
    for entry in config.get('units', []):
        source = (entry.get('metadata') or {}).get('source_path')
        base = entry.get('base_path')
        if not isinstance(source, str) or not isinstance(base, str):
            continue
        try:
            relative_source = Path(source).relative_to(child)
            relative_base = Path(base).relative_to(child)
        except ValueError:
            continue
        units.append({'base_path': relative_base.as_posix(), 'metadata': {'source_path': relative_source.as_posix()}})
    return _object_layout({'units': units})


def prepare(project, log=print, *, limit: int = 32, units: set[str] | None = None, stop=None) -> list[str]:
    """Prepare a bounded batch of source-less units with app-verified reference objects.

    Probe the actual project's object rule before publishing each candidate. Never invent a compiler or alter
    Makefiles/linker scripts. Rebuilt references may use the project's original assembly paths.
    Unsupported builds retain their source-less units and original config; later calls continue the batch.
    """
    cfg = copy.deepcopy(project.objdiff_config())
    original = (project.root / 'objdiff.json').read_bytes()
    object_prefix, keep_suffix = _object_layout(cfg)
    refs = (project.root / STATE / 'objdiff-references').resolve()
    if project.root.resolve() not in refs.parents:
        return []
    made = []
    sources = []
    try:
        for entry in cfg.get('units', []):
            if stop is not None and stop.is_set():
                break
            if units is not None and entry.get('name') not in units:
                continue
            source_path = (entry.get('metadata') or {}).get('source_path')
            if source_path and Path(source_path).suffix.lower() not in ('.s', '.asm'):
                continue
            target = (project.root / entry.get('target_path', '')).resolve()
            layout = next((refs / name for name in ('objects', 'targets')
                           if refs / name in target.parents), None)
            if not target.is_file():
                continue
            child = None
            relative_target = target.relative_to(project.root.resolve()) if project.root.resolve() in target.parents else None
            if relative_target and STATE in relative_target.parts:
                index = relative_target.parts.index(STATE)
                if index and relative_target.parts[index + 1:index + 3] == ('objdiff-references', 'targets'):
                    candidate_child = Path(*relative_target.parts[:index])
                    if (project.root / candidate_child / 'Makefile').is_file() and (cfg.get('custom_make') or 'make') == 'make':
                        child = candidate_child
            state_root = (project.root / STATE).resolve()
            target_parts = target.relative_to(state_root).parts if state_root in target.parents else ()
            nested = (len(target_parts) >= 4 and target_parts[0].startswith('verified-program-references-')
                      and target_parts[2] == 'objects')
            if child is not None:
                relative = Path(*relative_target.parts[index + 3:]).with_suffix('')
                metadata = entry.get('metadata') or {}
                value = metadata.get('draft_assembly') or metadata.get('assembly_source')
                if not isinstance(value, str) or Path(value).is_absolute():
                    continue
                assembly = (project.root / value).resolve()
                if (project.root / child).resolve() not in assembly.parents:
                    continue
            elif nested:
                # Verified program references use program/objects while their split is asm/program.
                program = target_parts[1]
                relative = Path(program, *target_parts[3:]).with_suffix('')
                assembly = (project.root / 'asm' / program / Path(*target_parts[3:]).with_suffix('.s')).resolve()
                if project.root.resolve() not in assembly.parents:
                    continue
            elif layout is None:
                # Native assembly-only objdiff units can use an existing generic C object rule too.
                # Probe it; never add a Makefile rule or replace the assembly's linked baseline.
                name = str(entry.get('name') or '')
                relative = Path(name).with_suffix('')
                if not name or relative.is_absolute() or '..' in relative.parts:
                    continue
                value = (entry.get('metadata') or {}).get('assembly_source') or source_path
                assembly = project.root / value if isinstance(value, str) else project.root / relative.with_suffix('.s')
                if project.root.resolve() not in assembly.resolve().parents:
                    continue
            elif layout.name == 'targets':
                relative = target.relative_to(layout).with_suffix('')
                metadata = entry.get('metadata') or {}
                # A verified rebuild keeps its original split assembly even if `make clean` removed it.
                value = metadata.get('draft_assembly') or metadata.get('assembly_source')
                if not isinstance(value, str) or Path(value).is_absolute():
                    continue
                assembly = (project.root / value).resolve()
                if project.root.resolve() not in assembly.parents:
                    continue
            else:
                relative = target.relative_to(layout).with_suffix('')
                assembly = refs / 'asm' / relative.with_suffix('.s')
            if not assembly.is_file():
                continue
            if _has_handwritten_annotations(assembly):
                log(f'Deferred {entry.get("name")}: its assembly marks functions as handwritten; review them first.')
                continue
            from .assembly_evidence import padding_only
            if padding_only(target):
                log(f'Deferred {entry.get("name")}: its only function is a nop; review its boundary first.')
                continue
            candidate = relative.with_suffix('.c')
            if set(candidate.parts) & FORBIDDEN_PARTS:
                # References mirror build/asm paths; C candidates must pass the same write gate as agents.
                identity = hashlib.sha256(relative.as_posix().encode()).hexdigest()[:12]
                candidate = Path('candidates') / f'{relative.stem}_{identity}.c'
            child_root = project.root / child if child is not None else project.root
            source = child_root / 'src' / 'decompdecomp' / candidate
            if project.root.resolve() not in source.resolve().parents:
                raise ForkError('The candidate source path leaves this checkout; no files were changed')
            project.safe_path(source.relative_to(project.root).as_posix(), write=True)
            if source.exists():
                raise ForkError(f'{source.relative_to(project.root)} already exists; it was retained')
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(HEADER, encoding='utf-8')
            entry['metadata'] = {**entry.get('metadata', {}), 'source_path': source.relative_to(project.root).as_posix(),
                                 'autopilot_scaffold': True,
                                 'draft_assembly': assembly.resolve().relative_to(project.root.resolve()).as_posix()}
            if child is not None:
                entry['metadata']['autopilot_build_dir'] = child.as_posix()
            prefix, keep = _child_layout(cfg, child) if child is not None else (object_prefix, keep_suffix)
            candidate_path = source.relative_to(child_root)
            object_name = Path(str(candidate_path) + '.o') if keep else candidate_path.with_suffix('.o')
            entry['base_path'] = ((child or Path()) / prefix / object_name).as_posix()
            obj = project.root / entry['base_path']
            made.append((source, obj))
            unit = project._units_of({'units': [entry]})[0]
            log(f'Preparing C candidate: {source.relative_to(project.root)}')
            # Some generic object rules do not make parent directories themselves.
            if project.root.resolve() not in unit.base.resolve().parents:
                raise ForkError('The candidate object path leaves this checkout; no files were changed')
            unit.base.parent.mkdir(parents=True, exist_ok=True)
            ok, output = project.build_unit(unit)
            if not ok or not unit.base.is_file():
                raise ForkError(f"This project's build cannot compile the generated C candidate {unit.source}:\n{output[-2000:]}")
            sources.append(unit.source)
            if len(sources) >= limit:
                break  # Probe bounded batches; subsequent runs prepare the next source-less units.
        if sources:
            (project.root / 'objdiff.json').write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
    except BaseException:
        (project.root / 'objdiff.json').write_bytes(original)
        for source, obj in made:
            source.unlink(missing_ok=True)
            if project.root.resolve() in obj.resolve().parents:
                obj.unlink(missing_ok=True)
        raise
    return sources
