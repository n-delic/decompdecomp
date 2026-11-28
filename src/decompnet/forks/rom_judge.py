"""Measure a ROM-linked candidate with the same objdiff result as a project unit.

Candidate scores are bound to a fresh source and compiler snapshot, and original
instructions and data are checked against the owned image before publication.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..rom import reference_judge
from . import objdiff
from .project import ForkError


class CandidateNotBuilt(ForkError):
    """A source is absent, a placeholder, or does not compile yet."""


def _compiler_profile(manifest: dict, fn: dict) -> dict:
    """Accept pinned compiler profiles, never project-provided executable commands."""
    from ..project import compiler_for

    profile = compiler_for(manifest, fn['vram'], fn)
    if not isinstance(profile, dict) or set(profile) - {'toolchain', 'flags', 'timeout'}:
        raise ForkError('ROM references require a pinned compiler without a custom command.')
    flags = profile.get('flags', [])
    blocked = ('-B', '-I', '-o', '-include', '-imacros', '-isystem', '-specs', '-wrapper',
               '-fplugin', '--sysroot', '-Xlinker', '-Xassembler', '-Xpreprocessor', '-Wa,', '-Wp,')
    if (not isinstance(profile.get('toolchain'), str) or not isinstance(flags, list)
            or any(not isinstance(flag, str) or not re.fullmatch(r'[A-Za-z0-9_.,=+:-]+', flag)
                   or flag.startswith(blocked) for flag in flags)):
        raise ForkError('The ROM reference compiler flags contain an unsupported path or command option.')
    timeout = profile.get('timeout', 20)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 120:
        raise ForkError('The ROM reference compiler timeout must be between 0 and 120 seconds.')
    return profile


@lru_cache(maxsize=8)
def _draft_profiles(path: Path, mtime: int, size: int):
    """Cache draft hints by manifest stamp; the judge independently verifies every score."""
    import json

    manifest = json.loads(path.read_text(encoding='utf-8'))
    functions = {}
    for fn in manifest['functions']:
        if fn.get('c'):
            functions.setdefault(fn['c'], []).append(fn)
    return manifest, functions


def compiler_for_unit(project, unit) -> str:
    """Read the pinned profile for this reference, including per-function overlays."""
    root = project.root.resolve()
    cfg = project.objdiff_config()
    path = Path(cfg.get('rom_reference_manifest') or '')
    resolved = (root/path).resolve()
    if path.is_absolute() or '..' in path.parts or root not in resolved.parents:
        raise ForkError('The ROM reference manifest must stay inside the project.')
    try:
        stamp = resolved.stat()
        manifest, by_source = _draft_profiles(resolved, stamp.st_mtime_ns, stamp.st_size)
        functions = by_source.get(unit.source, [])
        if len(functions) > 1:
            entries = [entry for entry in cfg.get('units', []) if entry.get('name') == unit.name]
            if len(entries) != 1:
                raise ForkError('The ROM reference unit is missing or ambiguous.')
            name = (entries[0].get('metadata') or {}).get('rom_reference_function') or unit.name.rsplit('/', 1)[-1]
            functions = [fn for fn in functions if fn.get('name') == name]
        if len(functions) != 1:
            raise ForkError('The ROM reference compiler has no unique source function.')
        return _compiler_profile(manifest, functions[0])['toolchain']
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ForkError(f'Could not read the ROM reference compiler: {error}') from error


def diff(root: Path, manifest: dict, fn: dict, compiled_object: bytes) -> objdiff.FunctionDiff:
    """Compare linked instructions and keep ROM data failures out of match scores."""
    judged = reference_judge.compare(root, manifest, fn, compiled_object)
    name = fn.get('symbol') or fn['name']
    with tempfile.TemporaryDirectory(prefix='decomp-rom-judge-') as scratch:
        folder = Path(scratch)
        target = folder / 'target.o'
        target.write_bytes(judged.target_object)
        base = None
        if judged.candidate_object is not None:
            base = folder / 'candidate.o'
            base.write_bytes(judged.candidate_object)
        result = objdiff.diff(target, base, name, cwd=folder)
    if not judged.exact:
        # objdiff sees instructions only. A 100% unit score must not escape to
        # forkengine._apply_scores when data or relocation verification failed.
        for symbol, score in result.unit_scores.items():
            if score is not None and score >= 100:
                result.unit_scores[symbol] = 99.99
        if result.match_percent is not None and result.match_percent >= 100:
            result.literals.extend(objdiff.DataConflict(p) for p in judged.problems)
            if not judged.problems:
                result.literals.append(objdiff.DataConflict('The ROM judge could not verify this candidate.'))
    if name != fn['name']:
        if name in result.unit_scores:
            result.unit_scores[fn['name']] = result.unit_scores.pop(name)
        if name in result.ops:
            result.ops[fn['name']] = result.ops.pop(name)
        result.order = [fn['name'] if item == name else item for item in result.order]
        result.symbol = fn['name']
    return result


def diff_source(root: Path, manifest_path: Path, function: str) -> objdiff.FunctionDiff:
    """Compile a private snapshot and immediately judge it; never cache its score.

    The caller must hold the project tree lock while applying a result. Changes to
    any source, header, target, image, manifest or installed toolchain during the
    check invalidate the result. This is deliberately not a persisted build proof.
    """
    import hashlib
    import json
    import re

    from ..compiler import compile_source
    from ..project import compiler_config
    from .project import ForkError
    from .toolchain_evidence import snapshot as toolchain_snapshot

    root = root.resolve()

    def inside(path: str | Path) -> Path:
        path = Path(path)
        if path.is_absolute() or '..' in path.parts:
            raise ForkError('ROM reference paths must stay inside the project.')
        resolved = (root / path).resolve()
        if root not in resolved.parents:
            raise ForkError('ROM reference paths must stay inside the project.')
        return resolved

    def headers() -> list[Path]:
        directory = inside('include')
        if not directory.is_dir():
            raise ForkError('The ROM reference project has no include directory.')
        paths = sorted(directory.rglob('*'))
        if any(path.is_symlink() for path in paths):
            raise ForkError('The ROM reference include directory contains a symbolic link.')
        return [path.relative_to(root) for path in paths if path.is_file()]

    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    manifest_file = inside(manifest_path)
    try:
        manifest_data = manifest_file.read_bytes()
        manifest = json.loads(manifest_data)
        entries = [entry for entry in manifest['functions'] if entry['name'] == function]
        if len(entries) != 1 or not entries[0].get('c'):
            raise ForkError('The ROM reference needs one source-backed function with that name.')
        fn = entries[0]
        from . import csource
        source_data = inside(fn['c']).read_bytes()
        source_text = source_data.decode('utf-8')
        symbol = fn.get('symbol') or fn['name']
        span = csource.find(source_text, symbol)
        if not span or csource.is_stub(source_text[span[0]:span[1]]):
            raise CandidateNotBuilt('The ROM reference has no C definition for this function.')
        header_paths = headers()
        inputs = {manifest_file.relative_to(root), Path(fn['c']), Path(fn['asm']), Path(manifest['image']), *header_paths}
        captured = {path: inside(path).read_bytes() for path in inputs}
        if captured[manifest_file.relative_to(root)] != manifest_data or captured[Path(fn['c'])] != source_data:
            raise ForkError('The ROM reference source or manifest changed during preparation; try again.')
        if sum(len(captured[path]) for path in header_paths) > 16 * 1024 * 1024:
            raise ForkError('The ROM reference headers exceed 16 MiB.')
        profile = _compiler_profile(manifest, fn)
        toolchain = profile['toolchain']
        before = toolchain_snapshot(toolchain)
        if not before:
            raise ForkError('The installed compiler cannot be verified for this ROM reference.')
        with tempfile.TemporaryDirectory(prefix='decomp-rom-source-') as scratch:
            staged = Path(scratch)
            (staged / 'include').mkdir()
            for path, data in captured.items():
                dest = staged / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            text = source_text
            include = re.match(r'\s*#include\s+"([^"\n]+)"\s*\n', text)
            header_path = Path('include') / include.group(1) if include else None
            if header_path is not None and header_path not in captured:
                raise ForkError('The source names a header outside the captured include directory.')
            header = captured[header_path].decode('utf-8') if header_path is not None else ''
            config = compiler_config(staged, {'compiler': profile})
            config.include_dirs = (str(staged / 'include'),)
            compiled = compile_source(config, header, text[include.end():] if include else text, trusted=True)
            if not compiled.ok:
                raise CandidateNotBuilt('The ROM reference candidate did not compile: ' + compiled.log[-1500:])
            result = diff(staged, manifest, fn, compiled.obj)
        if (header_paths != headers() or any(digest(inside(path).read_bytes()) != digest(data)
                                             for path, data in captured.items())
                or toolchain_snapshot(toolchain) != before):
            raise ForkError('The ROM reference inputs changed during the check; try again.')
        return result
    except (OSError, ValueError, KeyError, UnicodeError) as error:
        raise ForkError(f'Could not check the ROM reference: {error}') from error


def unit_diff(project, unit, symbol: str | None, manifest_path: str) -> objdiff.FunctionDiff:
    """Measure a configured ROM-reference unit from its source under the ordinary project API."""
    import json

    from .project import ForkError

    path = Path(manifest_path)
    root = project.root.resolve()
    if path.is_absolute() or '..' in path.parts or root not in (root/path).resolve().parents:
        raise ForkError('The ROM reference manifest must stay inside the project.')
    if not unit.source:
        return objdiff.diff(unit.target, None, symbol, cwd=root, options=project.diff_options(unit))
    try:
        manifest = json.loads((root/path).read_bytes())
        sources = [f for f in manifest['functions'] if f.get('c') == unit.source]
        entries = [entry for entry in project.objdiff_config().get('units', []) if entry.get('name') == unit.name]
        if len(entries) != 1:
            raise ForkError('The ROM reference unit is missing or ambiguous.')
        name = (entries[0].get('metadata') or {}).get('rom_reference_function')
        if name:
            sources = [fn for fn in sources if fn.get('name') == name]
        if symbol is not None:
            sources = [f for f in sources if symbol in (f['name'], f.get('symbol'))]
        if len(sources) != 1:
            raise ForkError('The ROM reference source does not identify one function for this unit.')
        return diff_source(root, path, sources[0]['name'])
    except (OSError, ValueError, KeyError) as error:
        raise ForkError(f'Could not read the ROM reference manifest: {error}') from error


def report(project, manifest_path: str, log=print) -> dict:
    """Measure every unit from a private source snapshot; publish no partial report.

    Recheck the report's complete input set after the last compile so that an edit
    to an earlier unit cannot become a stale project-wide match.
    """
    import hashlib
    import json
    import math

    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile

    from ..elfobj import _function_symbols
    from ..rom import export_entry, match, split
    from ..rom.symbols import Symbol, SymbolTable
    from .project import ForkError
    from .toolchain_evidence import snapshot as toolchain_snapshot

    root = project.root.resolve()

    def path_of(path):
        path = Path(path)
        resolved = (root / path).resolve()
        if path.is_absolute() or '..' in path.parts or root not in resolved.parents:
            raise ForkError('ROM reference paths must stay inside the project.')
        return resolved

    def checksum(path):
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None

    def header_paths():
        folder = path_of('include')
        if not folder.is_dir():
            raise ForkError('The ROM reference project has no include directory.')
        paths = sorted(folder.rglob('*'))
        if any(p.is_symlink() for p in paths):
            raise ForkError('The ROM reference include directory contains a symbolic link.')
        return [p for p in paths if p.is_file()]

    before = {}
    def watch(path):
        if path not in before:
            before[path] = checksum(path)
        return path

    manifest_file = watch(path_of(manifest_path))
    try:
        manifest = json.loads(manifest_file.read_bytes())
        watch(path_of(manifest['image']))
        watch(path_of('objdiff.json'))
        functions = {f['name']: f for f in manifest['functions']}
        if len(functions) != len(manifest['functions']):
            raise ForkError('The ROM reference has duplicate function names.')
        image, _ = export_entry.verified_image_reader(root, manifest)
        table = SymbolTable([Symbol(n, a, 0, 'data') for n, a in manifest['symbols'].items()]
                            + [Symbol(f['name'], f['vram'], 0, 'func') for f in manifest['functions']]
                            + [Symbol(f['symbol'], f['vram'], 0, 'func') for f in manifest['functions'] if f.get('symbol')]
                            + [Symbol(n, a, 0, 'data') for n, a in manifest.get('labels', {}).items()
                               if n not in manifest['symbols']])
        cfg = project.objdiff_config()
        units = project.units()
        raw_units = cfg.get('units', [])
        if len(units) != len(raw_units):
            raise ForkError('The ROM reference unit list changed; try again.')
        initial_headers = header_paths()
        for path in initial_headers:
            watch(path)
        planned = {}
        compilers = {}
        check_cancel = getattr(log, 'check_cancel', lambda: None)
        for unit, raw in zip(units, raw_units):
            check_cancel()
            name = (raw.get('metadata') or {}).get('rom_reference_function') or unit.name.rsplit('/', 1)[-1]
            fn = functions.get(name)
            if fn is None or unit.name in planned or not unit.target:
                raise ForkError('Each ROM reference unit must identify one original function and target object.')
            if unit.complete:
                raise ForkError('A ROM reference unit cannot claim completion without a verified match.')
            target_file = watch(path_of(unit.target.resolve().relative_to(root)))
            asm_file = watch(path_of(fn['asm']))
            parsed = split.parse_function(asm_file, fn['seg'], manifest['big_endian'])
            if parsed is None:
                raise ForkError(f'{name}: the original assembly is missing.')
            target = match.target_from_split(parsed, table.resolve, manifest['big_endian'], manifest.get('gp') or 0)
            export_entry.verify_target(image, manifest, fn, target)
            with target_file.open('rb') as stream:
                elf = ELFFile(stream)
                symbols = _function_symbols(elf)
                if set(symbols) != {name}:
                    raise ForkError(f'{name}: the target object does not contain exactly that function.')
                section, offset, size = symbols[name]
                if elf.get_section(section).data()[offset:offset+size] != target.code:
                    raise ForkError(f'{name}: the target object differs from the verified image.')
            if unit.source:
                if unit.source != fn.get('c'):
                    raise ForkError(f'{name}: the unit and ROM reference disagree on its source.')
                source = watch(path_of(unit.source))
                if source.is_file():
                    profile = _compiler_profile(manifest, fn)
                    compilers[profile['toolchain']] = None
            planned[unit.name] = (unit, fn)
        for tc in compilers:
            compilers[tc] = toolchain_snapshot(tc)
            if not compilers[tc]:
                raise ForkError('The installed compiler cannot be verified for this ROM reference.')
        # Run objdiff only on target objects. No existing candidate object is trusted.
        with tempfile.TemporaryDirectory(prefix='decomp-rom-report-') as scratch:
            folder = Path(scratch)
            clean = dict(cfg)
            clean.pop('rom_reference_manifest', None)
            clean['build_base'] = False
            clean['build_target'] = False
            clean['units'] = []
            for unit, raw in zip(units, raw_units):
                item = dict(raw)
                item.pop('base_path', None)
                item['metadata'] = dict(item.get('metadata') or {}, complete=False)
                item['target_path'] = str(unit.target.resolve())
                clean['units'].append(item)
            (folder/'objdiff.json').write_text(json.dumps(clean), encoding='utf-8')
            result = objdiff.report(folder, folder/'report.json')
        total_matched = total_bytes = reported_bytes = 0
        fuzzy_bytes = 0.0
        measured = unbuilt = 0
        source_total = sum(bool(unit.source) for unit, _ in planned.values())
        progress = getattr(log, 'progress', lambda _: None)
        reported = set()
        for row in result.get('units', []):
            check_cancel()
            if row['name'] in reported:
                raise ForkError('Objdiff repeated a ROM reference unit.')
            reported.add(row['name'])
            unit, fn = planned[row['name']]
            entries = row.get('functions', [])
            if len(entries) != 1 or entries[0]['name'] != fn['name']:
                raise ForkError(f"{fn['name']}: objdiff did not report exactly the original function.")
            entry = entries[0]
            entry.setdefault('metadata', {})['virtual_address'] = fn['vram']
            size = int(entry.get('size') or 0)
            reported_bytes += size
            value = None
            if unit.source and path_of(unit.source).is_file():
                # Most newly generated files are still placeholders. Avoid loading
                # the image and manifest for them; the final input check catches edits.
                from . import csource
                source_text = path_of(unit.source).read_text(encoding='utf-8')
                span = csource.find(source_text, fn.get('symbol') or fn['name'])
                if span and not csource.is_stub(source_text[span[0]:span[1]]):
                    try:
                        judged = diff_source(root, Path(manifest_path), fn['name'])
                        value = judged.match_percent
                        if value is not None and value >= 100 and not judged.matched:
                            value = 99.99
                    except CandidateNotBuilt as error:
                        unbuilt += 1
                        if unbuilt <= 10:
                            log(f"{fn['name']}: {error}")
                        value = None
            if unit.source:
                measured += 1
                if measured % 50 == 0 or measured == source_total:
                    progress({'phase':'Checking ROM-backed source', 'done':measured, 'total':source_total})
                    log(f'Checked {measured} source-backed functions against the original image')
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)
                                      or not 0 <= value <= 100):
                raise ForkError(f"{fn['name']}: objdiff returned an invalid score.")
            entry['fuzzy_match_percent'] = value
            matched = value is not None and value >= 100
            if matched:
                total_matched += 1
                total_bytes += size
            fuzzy_bytes += size * (value or 0) / 100
            measures = row.setdefault('measures', {})
            measures.update(matched_functions=int(matched), matched_code=str(size if matched else 0),
                            matched_functions_percent=100.0 if matched else 0.0,
                            matched_code_percent=100.0 if matched else 0.0,
                            complete_code=str(size if matched else 0),
                            complete_code_percent=100.0 if matched else 0.0,
                            complete_units=int(matched), fuzzy_match_percent=value or 0.0)
        if reported != set(planned):
            raise ForkError('Objdiff omitted a ROM reference unit; the previous progress was kept.')
        if unbuilt > 10:
            log(f'{unbuilt} source files did not compile; see the first errors above.')
        measures = result.setdefault('measures', {})
        count, size = int(measures.get('total_functions') or 0), int(measures.get('total_code') or 0)
        if count != len(planned) or size != reported_bytes:
            raise ForkError('Objdiff totals disagree with the verified ROM reference units.')
        measures.update(matched_functions=total_matched, matched_code=str(total_bytes),
                        matched_functions_percent=100 * total_matched / count if count else 0,
                        matched_code_percent=100 * total_bytes / size if size else 0,
                        complete_code=str(total_bytes), complete_code_percent=100 * total_bytes / size if size else 0,
                        complete_units=total_matched, fuzzy_match_percent=100 * fuzzy_bytes / size if size else 0)
        if (initial_headers != header_paths() or any(checksum(path) != old for path, old in before.items())
                or any(toolchain_snapshot(tc) != old for tc, old in compilers.items())):
            raise ForkError('The ROM reference inputs changed during the report; try again.')
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ELFError) as error:
        raise ForkError(f'Could not measure the ROM reference project: {error}') from error


@dataclass(frozen=True)
class ExportPlan:
    config: dict
    targets: dict[str, bytes]
    inputs: dict[Path, str | None]
    manifest: dict
    scaffolds: dict[str, str]
    toolchains: dict[str, str]


def plan_export(project, manifest_path: str, log=print):
    """Plan ROM-backed units from an exported tree without changing the project.

    Only a target's ELF ABI is taken from its existing object. Its instructions
    come from the SHA-checked image after verifying the split and address mapping.
    The caller must publish the plan atomically and recheck its inputs first.
    """
    import hashlib
    import json
    import re

    from .. import consoles
    from ..compiler import compile_source
    from ..project import compiler_config
    from ..rom import export_entry, reference_judge
    from .toolchain_evidence import snapshot as toolchain_snapshot

    root = project.root.resolve()

    def path_of(path):
        path = Path(path)
        resolved = (root / path).resolve()
        if path.is_absolute() or '..' in path.parts or root not in resolved.parents:
            raise ForkError('ROM reference paths must stay inside the project.')
        return resolved

    def checksum(path):
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None

    check_cancel = getattr(log, 'check_cancel', lambda:None)
    progress = getattr(log, 'progress', lambda _:None)
    inputs = {}
    def watch(path):
        if path not in inputs:
            inputs[path] = checksum(path)
        return path

    try:
        manifest_file = watch(path_of(manifest_path))
        cfg_file = watch(path_of('objdiff.json'))
        manifest = json.loads(manifest_file.read_bytes())
        watch(path_of(manifest['image']))
        image = reference_judge.ReferenceImage(root, manifest)
        functions = {fn['name']:fn for fn in manifest['functions']}
        if len(functions) != len(manifest['functions']):
            raise ForkError('The ROM reference has duplicate function names.')
        raw = json.loads(cfg_file.read_bytes())
        units = project.units()
        if len(units) != len(raw.get('units', [])):
            raise ForkError('The exported unit list changed; try again.')
        result, targets, scaffolds, seen = [], {}, {}, set()
        templates, compilers = {}, {}
        console = consoles.get(manifest.get('platform'))
        common = '#include \"common.h\"\n#include \"rom-reference/@generation.h\"\n'
        for index, (unit, entry) in enumerate(zip(units, raw['units'])):
            check_cancel()
            if index % 50 == 0:
                progress({'phase':'Preparing original-code references', 'done':index, 'total':len(units)})
            name = (entry.get('metadata') or {}).get('rom_reference_function') or unit.name.rsplit('/', 1)[-1]
            fn = functions.get(name)
            if fn is None or name in seen or not unit.target:
                raise ForkError('Each exported unit must identify one unique original function and target object.')
            seen.add(name)
            if unit.source != fn.get('c'):
                raise ForkError(f'{name}: the exported unit and manifest disagree on the source.')
            if unit.source:
                path_of(unit.source)
            asm = watch(path_of(fn['asm']))
            target_file = watch(path_of(unit.target.resolve().relative_to(root)))
            target = image.target(fn)
            if target_file.is_file():
                template = target_file.read_bytes()
            else:
                profile = _compiler_profile(manifest, fn)
                key = json.dumps(profile, sort_keys=True)
                if key not in templates:
                    tc = profile['toolchain']
                    stamp = toolchain_snapshot(tc)
                    if not stamp:
                        raise ForkError('The installed compiler cannot be verified for this ROM reference.')
                    compiler = compiler_config(root, {'compiler':profile})
                    compiler.include_dirs = (str(root/'include'),)
                    built = compile_source(compiler, '', 'int __rom_reference_abi(void) { return 0; }', trusted=True)
                    if not built.ok:
                        raise ForkError('Could not make a ROM reference object: ' + built.log[-1500:])
                    if toolchain_snapshot(tc) != stamp:
                        raise ForkError('The installed compiler changed while preparing ROM references; try again.')
                    compilers[tc] = stamp
                    templates[key] = built.obj
                template = templates[key]
            source = unit.source
            if not source and re.fullmatch(r'[A-Za-z_]\w*', fn.get('symbol') or name, re.ASCII):
                sdk = None if target.relocs else console.sdk_assembly_identity(target.code)
                # SDK identity always comes first. Unclassified assembly stays
                # source-less until its identity can be reviewed.
                if not sdk and not console.asm_reason(target.code):
                    source = f'src/rom-reference/@generation/{index:08x}.c'
                    scaffolds[source] = common
                    fn['c'] = source
            # Rechecking this file at the end binds the parser to its captured input.
            if not asm.is_file():
                raise ForkError(f'{name}: the original assembly is missing.')
            output = f'.decomp-wtf/rom-reference/targets/{index:08x}.o'
            targets[output] = export_entry.object_for(template, name, target.code)
            metadata = {'complete':False, 'rom_reference_function':name, 'source_path':source,
                        'draft_assembly':fn['asm']}
            item = {'name':unit.name, 'target_path':output, 'metadata':metadata}
            if source:
                item['base_path'] = f'.decomp-wtf/rom-reference/candidates/{index:08x}.o'
            result.append(item)
        check_cancel()
        progress({'phase':'Preparing original-code references', 'done':len(units), 'total':len(units)})
        if seen != set(functions):
            raise ForkError('The export has functions without unique reference objects; finish its targets first.')
        if scaffolds and not path_of('include/common.h').is_file():
            raise ForkError('The exported analysis needs include/common.h before adding candidate sources.')
        if (any(checksum(path) != old for path, old in inputs.items())
                or any(toolchain_snapshot(tc) != old for tc, old in compilers.items())):
            raise ForkError('The exported ROM reference changed while preparing it; try again.')
        config = {'rom_reference_manifest':str(manifest_path), 'build_base':False,
                  'build_target':False, 'units':result}
        return ExportPlan(config, targets, inputs, manifest, scaffolds, compilers)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ForkError(f'Could not prepare the exported ROM references: {error}') from error


def publish_export(project, plan: ExportPlan, log=print) -> dict:
    """Publish a prepared export atomically while retaining its original objdiff file.

    The caller holds the project tree exclusively. Each generation gets a new
    private folder, so a failed or interrupted publication cannot replace a
    reference another reader is using. No source or original target is changed.
    """
    import hashlib
    import json
    import os
    import secrets
    import shutil

    root = project.root.resolve()
    config, targets, inputs = plan.config, plan.targets, plan.inputs
    check_cancel = getattr(log, 'check_cancel', lambda:None)
    progress = getattr(log, 'progress', lambda _:None)
    old_config = root/'objdiff.json'
    if old_config not in inputs:
        raise ForkError('The original project configuration is not part of the verified plan.')

    def check_inputs():
        from .toolchain_evidence import snapshot as toolchain_snapshot

        for path, expected in inputs.items():
            check_cancel()
            resolved = path.resolve()
            try:
                actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
            except FileNotFoundError:
                actual = None
            if root not in resolved.parents or actual != expected:
                raise ForkError('The exported ROM reference changed while preparing it; try again.')
        if any(toolchain_snapshot(tc) != expected for tc, expected in plan.toolchains.items()):
            raise ForkError('The installed compiler changed while preparing ROM references; try again.')

    expected_targets = [u.get('target_path') for u in config.get('units', [])]
    if (len(expected_targets) != len(set(expected_targets)) or set(expected_targets) != set(targets)
            or any(not isinstance(p, str) or Path(p).parent != Path('.decomp-wtf/rom-reference/targets')
                   or '..' in Path(p).parts for p in expected_targets)):
        raise ForkError('The ROM reference plan has invalid or duplicate targets.')
    stage = generation = source_stage = source_generation = macro_stage = macro_header = temp_config = None
    published = False
    try:
        check_inputs()
        original = old_config.read_bytes()
        private = root/'.decomp-wtf'
        for directory in (private, private/'rom-reference', private/'rom-reference'/'generations'):
            if directory.is_symlink():
                raise ForkError('The ROM reference state folder cannot be a symbolic link.')
            directory.mkdir(exist_ok=True)
        parent = private/'rom-reference'/'generations'
        token = secrets.token_hex(16)
        generation = parent/token
        stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=parent))
        (stage/'targets').mkdir()
        for index, (path, data) in enumerate(targets.items()):
            check_cancel()
            if index % 50 == 0:
                progress({'phase':'Writing original-code references', 'done':index, 'total':len(targets)})
            (stage/'targets'/Path(path).name).write_bytes(data)
        (stage/'original.objdiff.json').write_bytes(original)
        if plan.scaffolds:
            source_parent = root/'src'
            for directory in (source_parent, source_parent/'rom-reference'):
                if directory.is_symlink():
                    raise ForkError('The ROM reference source folder cannot be a symbolic link.')
                directory.mkdir(exist_ok=True)
            source_parent = source_parent/'rom-reference'
            source_generation = source_parent/token
            source_stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=source_parent))
            for path, content in plan.scaffolds.items():
                check_cancel()
                if (Path(path).parent != Path('src/rom-reference/@generation')
                        or '..' in Path(path).parts):
                    raise ForkError('The ROM reference plan has an invalid source path.')
                (source_stage/Path(path).name).write_text(content.replace('@generation', token), encoding='utf-8')
            include = root/'include'
            macro_dir = include/'rom-reference'
            if include.is_symlink() or macro_dir.is_symlink() or not include.is_dir():
                raise ForkError('The ROM reference include folder must be inside the project.')
            macro_dir.mkdir(exist_ok=True)
            macro_header = macro_dir/f'{token}.h'
            fd, temp = tempfile.mkstemp(prefix='.staging-', dir=macro_dir)
            macro_stage = Path(temp)
            from .. import seeds
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(seeds.M2C_MACROS)
        manifest = json.loads(json.dumps(plan.manifest))
        for fn in manifest['functions']:
            if fn.get('c') in plan.scaffolds:
                fn['c'] = f"src/rom-reference/{token}/{Path(fn['c']).name}"
        (stage/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        check_inputs()
        if generation.exists():
            raise ForkError('The ROM reference generation already exists; try again.')
        os.rename(stage, generation)
        stage = None
        if source_stage is not None:
            if source_generation.exists():
                raise ForkError('The ROM reference source generation already exists; try again.')
            os.rename(source_stage, source_generation)
            source_stage = None
            if macro_header.exists() or macro_header.is_symlink():
                raise ForkError('The ROM reference draft header already exists; try again.')
            os.rename(macro_stage, macro_header)
            macro_stage = None
        updated = dict(config)
        updated['units'] = []
        prefix = f'.decomp-wtf/rom-reference/generations/{token}'
        updated['rom_reference_manifest'] = f'{prefix}/manifest.json'
        for unit in config['units']:
            item = dict(unit)
            item['metadata'] = dict(item.get('metadata') or {})
            source = item['metadata'].get('source_path')
            if source in plan.scaffolds:
                item['metadata']['source_path'] = f'src/rom-reference/{token}/{Path(source).name}'
            item['target_path'] = f"{prefix}/targets/{Path(unit['target_path']).name}"
            if unit.get('base_path'):
                item['base_path'] = f"{prefix}/candidates/{Path(unit['base_path']).name}"
            updated['units'].append(item)
        fd, name = tempfile.mkstemp(prefix='.objdiff-', dir=root)
        temp_config = Path(name)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(updated, stream)
            stream.flush()
            os.fsync(stream.fileno())
        check_inputs()
        check_cancel()
        os.replace(temp_config, old_config)
        temp_config = None
        published = True
        return updated
    except OSError as error:
        raise ForkError(f'Could not publish the ROM references: {error}') from error
    finally:
        if temp_config is not None:
            temp_config.unlink(missing_ok=True)
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        if source_stage is not None:
            shutil.rmtree(source_stage, ignore_errors=True)
        if macro_stage is not None:
            macro_stage.unlink(missing_ok=True)
        if macro_header is not None and not published:
            macro_header.unlink(missing_ok=True)
        if source_generation is not None and not published:
            shutil.rmtree(source_generation, ignore_errors=True)
        if generation is not None and not published:
            shutil.rmtree(generation, ignore_errors=True)
