"""Create objdiff references from a verified splat image or a byte-exact, fully linked rebuild.

Only explicit code/data mappings are supported. A private all-assembly split supplies the reference bytes and
relocations; linking those references must reproduce every initialized section's original image range before
any objdiff configuration is published. Existing configurations and expected objects are never replaced.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .. import limits, toolchains
from ..hostenv import NO_WINDOW, console_python
from .project import STATE, ForkError

MAX_UNITS = 2000
TIMEOUT = 180
SYMBOL = re.compile(r"[A-Za-z_.$][\w.$]*")
ADDRESS_COMMENT = re.compile(r"/\* [0-9A-Fa-f]+ ([0-9A-Fa-f]{8})")
ADDRESS_NAME = re.compile(r"(?:\.L|D_|func_|jtbl_)([0-9A-Fa-f]{8})(?:_[A-Za-z0-9_.$]+)?")


def inside(root: Path, value: str) -> Path:
    """Generated references may not write through external or symlinked project paths."""
    if not isinstance(value, (str, Path)):
        raise ForkError("automatic objdiff setup needs file paths written as strings in the splat config")
    path = (root / value).resolve()
    if path != root and root not in path.parents:
        raise ForkError("automatic objdiff setup needs paths inside this checkout; adjust its splat config first")
    return path


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha1').hexdigest()


def run(command: list[str], root: Path, log) -> None:
    result = subprocess.run(limits.capped(command), cwd=root, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=TIMEOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    if result.returncode:
        raise ForkError("automatic objdiff reference step failed:\n" + (result.stdout + result.stderr)[-3000:])
    if result.stdout:
        log(result.stdout[-1000:])


def plan(root: Path, config: dict) -> list[dict]:
    """Accept linear code mappings; C units have candidates, assembly units remain source-less targets."""
    options = config.get('options', {})
    source_root = options.get('src_path', 'src')
    build_root = options.get('build_path', 'build')
    units = []
    segments = config.get('segments', [])
    if not isinstance(segments, list):
        raise ForkError("automatic objdiff setup needs a list of explicit splat segments")
    for segment in segments:
        if not isinstance(segment, dict):
            if isinstance(segment, list) and segment and isinstance(segment[0], int) and (
                    len(segment) == 1 or segment[1] in ('header', 'bin', 'pad')):
                continue
            raise ForkError("automatic objdiff setup needs explicit splat segment mappings; set this project up manually")
        if segment.get('type') != 'code':
            continue
        if not isinstance(segment.get('start'), int) or not isinstance(segment.get('vram'), int):
            raise ForkError("automatic objdiff setup needs a numeric start and vram for each code segment")
        if any(key in segment for key in ('compressed', 'decompressor', 'exclusive_ram_id')):
            raise ForkError("automatic objdiff setup cannot yet verify compressed or overlay code segments")
        entries = segment.get('subsegments', [])
        if not isinstance(entries, list):
            raise ForkError("automatic objdiff setup needs a list of explicit splat subsegments")
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get('type') in ('bss', '.bss'):
                continue
            if not isinstance(entry, list) or len(entry) < 2:
                raise ForkError("automatic objdiff setup does not support this splat subsegment layout yet")
            offset, kind = entry[:2]
            if not isinstance(offset, int) or not isinstance(kind, str):
                raise ForkError("automatic objdiff setup needs explicit numeric subsegment offsets")
            if kind not in ('c', 'asm', 'hasm', 'data', 'rodata', 'bss', '.data', '.rodata', '.bss', 'bin'):
                raise ForkError(f"automatic objdiff setup does not support splat subsegment {kind!r} yet")
            if kind not in ('c', 'asm', 'hasm'):
                continue
            following = entries[index + 1] if index + 1 < len(entries) else None
            if isinstance(following, list) and following and following[0] == offset:
                continue  # An explicitly empty code subsegment has no function denominator.
            name = str(entry[2]) if len(entry) > 2 else f'{offset:X}'
            name = (Path(str(segment.get('dir', ''))) / name).as_posix()
            if Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name or ':' in name:
                raise ForkError("automatic objdiff setup needs relative unit names without parent paths")
            source = base = None
            if kind == 'c':
                source = inside(root, str(Path(source_root) / (name + '.c')))
                relative = source.relative_to(root)
                candidates = [inside(root, str(Path(build_root) / relative.with_suffix('.o'))),
                              inside(root, str(Path(build_root) / (str(relative) + '.o')))]
                built = [path for path in candidates if path.is_file()]
                if not source.is_file() or not built:
                    raise ForkError("automatic objdiff setup needs the project's C objects built first; follow its README")
                if len(built) != 1:
                    raise ForkError(f"ambiguous built objects for {relative}; remove stale build outputs or configure objdiff manually")
                base = built[0]
            unit = {'name': name, 'source': source, 'base': base, 'offset': offset,
                    'vram': segment['vram'] + offset - segment['start'],
                    'delta': segment['start'] - segment['vram']}
            mapped_sections = {}
            for sibling in entries:
                if isinstance(sibling, dict) and sibling.get('type') in ('bss', '.bss'):
                    sibling_name = (Path(str(segment.get('dir', ''))) / str(sibling.get('name', ''))).as_posix()
                    if sibling_name == name and isinstance(sibling.get('vram'), int):
                        mapped_sections['.bss'] = sibling['vram']
                    continue
                if not isinstance(sibling, list) or len(sibling) < 3 or not isinstance(sibling[0], int):
                    continue
                section = str(sibling[1]).lstrip('.')
                sibling_name = (Path(str(segment.get('dir', ''))) / str(sibling[2])).as_posix()
                if section in ('data', 'rodata') and sibling_name == name:
                    mapped_sections['.' + section] = segment['vram'] + sibling[0] - segment['start']
            if mapped_sections:
                unit['sections'] = mapped_sections
            units.append(unit)
    if not units or len(units) > MAX_UNITS or len({u['name'] for u in units}) != len(units):
        raise ForkError("automatic objdiff setup needs distinct code units in a supported splat layout")
    return units


def symbols(elf_path: Path) -> dict[str, int]:
    with elf_path.open('rb') as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name('.symtab')
        if table is None:
            raise ForkError("automatic objdiff setup needs the project's unstripped linked ELF")
        return {symbol.name: symbol['st_value'] for symbol in table.iter_symbols()
                if SYMBOL.fullmatch(symbol.name) and symbol['st_shndx'] != 'SHN_UNDEF'}


def verify(elf_path: Path, image: bytes, unit: dict) -> None:
    """Check resolved executable bytes, not only disassembly similarity or compiler output."""
    with elf_path.open('rb') as stream:
        elf = ELFFile(stream)
        text = elf.get_section_by_name('.text')
        if text is None or not text['sh_size']:
            raise ForkError("generated objdiff reference has no code")
        for section in elf.iter_sections():
            if not section['sh_flags'] & 2 or section['sh_type'] == 'SHT_NOBITS' or not section['sh_size']:
                continue
            # Only explicitly mapped initialized sections may contribute bytes to a reference.
            if section.name not in ('.text', '.data', '.rodata'):
                raise ForkError(f"generated reference has an unsupported allocated section: {section.name}")
            start = section['sh_addr'] + unit['delta']
            end = start + section['sh_size']
            if start < 0 or end > len(image) or section.data() != image[start:end]:
                raise ForkError("ROM-derived objdiff reference bytes did not verify; configure this project manually")


def assemble(root: Path, work: Path, unit: dict, definitions: dict, image: bytes, assembler: str,
             linker: str, flags: tuple, log) -> Path:
    name = unit['name']
    parts = [work / 'asm' / (name + '.s')]
    sections = {'.text': unit['vram']}
    for section in ('data', 'rodata', 'bss'):
        path = work / 'asm/data' / (name + '.' + section + '.s')
        if path.exists():
            match = ADDRESS_COMMENT.search(path.read_text(encoding='utf-8'))
            configured = unit.get('sections', {}).get('.' + section)
            if configured is None and not match:
                raise ForkError("cannot establish a reference data section's original address")
            sections['.' + section] = configured if configured is not None else int(match[1], 16)
            parts.append(path)
    source = work / 'combined' / (name + '.s')
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('\n'.join(path.read_text(encoding='utf-8') for path in parts), encoding='utf-8')
    target = work / 'objects' / (name + '.o')
    target.parent.mkdir(parents=True, exist_ok=True)
    run([assembler, *flags, '-I', str(work / 'include'), '-I', str(root / 'include'),
         '-o', str(target), str(source)], root, log)
    definitions = dict(definitions)
    with target.open('rb') as stream:
        elf = ELFFile(stream)
        for symbol in elf.get_section_by_name('.symtab').iter_symbols():
            match = ADDRESS_NAME.fullmatch(symbol.name)
            if symbol['st_shndx'] == 'SHN_UNDEF' and match:
                definitions[symbol.name] = int(match[1], 16)
    script = target.with_suffix('.ld')
    script.write_text('\n'.join(f'PROVIDE({name} = {hex(value)});' for name, value in definitions.items())
                      + '\nSECTIONS {\n' + '\n'.join(f'{name} {hex(value)} : {{ *({name}) }}'
                                                       for name, value in sections.items())
                      + '\n/DISCARD/ : { *(.reginfo) *(.MIPS.abiflags) }\n}\n', encoding='utf-8')
    linked = target.with_suffix('.elf')
    run([linker, *(flag for flag in flags if flag in ('-EB', '-EL')), '-T', str(script), '-e', hex(unit['vram']), '--no-check-sections', '-o', str(linked), str(target)],
        root, log)
    verify(linked, image, unit)
    return target



def source_symbol_mappings(target: Path, base: Path, definitions: dict[str, int], vram: int) -> dict[str, str]:
    """Pair a generated name with its unique linked C function, without changing the byte comparison."""
    try:
        with target.open('rb') as stream:
            elf = ELFFile(stream)
            targets = [(s.name, s['st_value']) for s in elf.get_section_by_name('.symtab').iter_symbols()
                       if s['st_info']['type'] == 'STT_FUNC' and isinstance(s['st_shndx'], int)]
        with base.open('rb') as stream:
            elf = ELFFile(stream)
            candidates = {s.name for s in elf.get_section_by_name('.symtab').iter_symbols()
                          if s['st_info']['type'] == 'STT_FUNC' and isinstance(s['st_shndx'], int)}
    except (OSError, ELFError, AttributeError):
        return {}  # A missing or unfinished candidate still gets its original reference.
    mappings = {}
    for name, offset in targets:
        if name in candidates:
            continue
        aliases = [symbol for symbol in candidates if definitions.get(symbol) == vram + offset]
        if len(aliases) == 1:
            mappings[name] = aliases[0]
    return mappings


def reference_symbols(work: Path) -> dict[str, int]:
    """Original addresses printed by the disassembler, including labels shared across code units.

    A candidate ELF can have shifted addresses while its decompilation is incomplete. Reference labels must
    come from the pinned input instead. Every resolved reference section is still compared byte for byte.
    """
    definitions = {}
    for path in sorted((work / 'asm').rglob('*.s')):
        pending = []
        for line in path.read_text(encoding='utf-8').splitlines():
            label = re.match(r'\s*(?:glabel|dlabel|jlabel|alabel)\s+([A-Za-z_.$][\w.$]*)\s*$', line)
            local = re.match(r'\s*([A-Za-z_.$][\w.$]*):\s*$', line)
            if label or local:
                pending.append((label or local)[1])
            address = ADDRESS_COMMENT.search(line)
            if address:
                value = int(address[1], 16)
                for name in pending:
                    if name in definitions and definitions[name] != value:
                        raise ForkError('the original reference assembly has ambiguous symbol addresses')
                    definitions[name] = value
                pending.clear()
    for name in ('undefined_funcs.txt', 'undefined_syms.txt'):
        path = work / name
        if path.is_file():
            for symbol, value in re.findall(r'^\s*([A-Za-z_.$][\w.$]*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;',
                                             path.read_text(encoding='utf-8'), re.M):
                definitions.setdefault(symbol, int(value, 16))
    return definitions


def generate(project, config_path: Path, config: dict, console, version: str | None = None, log=print) -> bool:
    """Publish references and objdiff.json only after the complete set passes verification."""
    root = project.root.resolve()
    if (root / 'objdiff.json').exists():
        return False
    options = config.get('options', {})
    sha1 = config.get('sha1') or options.get('sha1')
    if not isinstance(sha1, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', sha1):
        raise ForkError("automatic objdiff setup needs a SHA-1 in the splat config; check the project's ROM version")
    image_path = inside(root, options.get('target_path', ''))
    if not image_path.is_file() or digest(image_path) != sha1.lower():
        raise ForkError("automatic objdiff setup needs the exact ROM named by the splat config's SHA-1")
    original_base = inside(root, str(config_path.parent / options.get('base_path', '.')))
    if original_base != root:
        raise ForkError("automatic objdiff setup needs the splat base_path to identify this checkout's root")
    units = plan(root, config)
    configured_elf = inside(root, options.get('elf_path', 'build/project.elf'))
    candidates = [configured_elf] if configured_elf.is_file() else list(inside(root, 'build').rglob('*.elf'))
    # Prefer a complete matching image; original disassembly addresses override an incomplete build below.
    matching = [elf for elf in candidates if any(p.is_file() and digest(p) == sha1.lower()
                                                  for p in (elf.with_suffix('.z64'), elf.with_suffix('.bin'),
                                                            elf.with_suffix('.exe')))]
    if len(matching) == 1:
        candidates = matching
    elif len(candidates) == 1:
        log('The source rebuild is incomplete; deriving and verifying references independently from the original image.')
    elif all(unit['source'] is None for unit in units) and not candidates:
        log('Deriving assembly-only references directly from the original image; no C candidates are marked matched.')
    else:
        raise ForkError("automatic objdiff setup needs one linked ELF beside a rebuilt image with the original SHA-1")
    try:
        definitions = symbols(candidates[0]) if candidates else {}
    except ELFError:
        raise ForkError('automatic objdiff setup needs a readable linked ELF for unresolved symbols') from None
    tid, assembler_name, linker_name = console.reference_tools
    directory = toolchains.require(tid)
    def executable(name):
        for path in (directory / name, directory / (name + '.exe'), directory / 'bin' / name,
                     directory / 'bin' / (name + '.exe')):
            if path.is_file():
                return str(path)
        raise ForkError(f"the installed reference tool is missing {name}; reinstall {tid} in Compilers & tools")
    assembler, linker = executable(assembler_name), executable(linker_name)
    parent = inside(root, STATE)
    parent.mkdir(exist_ok=True)
    destination = inside(root, STATE + '/objdiff-references')
    if destination.exists():
        raise ForkError("existing automatic reference files were retained; restore their objdiff.json or set up manually")
    with tempfile.TemporaryDirectory(prefix='reference-stage-', dir=parent) as temporary:
        work = Path(temporary)
        split = copy.deepcopy(config)
        opts = split['options']
        for key in ('symbol_addrs_path', 'reloc_addrs_path'):
            if key in opts:
                values = opts[key]
                opts[key] = [str(inside(original_base, value)) for value in values] if isinstance(values, list) else str(
                    inside(original_base, values))
        opts.update(base_path=str(root), target_path=str(image_path), asm_path=str(work / 'asm'),
                    generated_asm_macros_directory=str(work / 'include'),
                    data_path=str(work / 'asm/data'), nonmatchings_path=str(work / 'asm/nonmatchings'),
                    matchings_path=str(work / 'asm/matchings'), cache_path=str(work / '.splache'),
                    src_path=str(work / 'src'), asset_path=str(work / 'assets'), build_path=str(work / 'objects'),
                    ld_script_path=str(work / 'reference.ld'), elf_path=str(work / 'reference.elf'),
                    undefined_funcs_auto_path=str(work / 'undefined_funcs.txt'),
                    undefined_syms_auto_path=str(work / 'undefined_syms.txt'))
        # Splat also emits headers and optional partial-link files. A reference split must not rewrite
        # the project's headers, cache, or linker metadata while another build uses them.
        for key, value in {'ld_symbol_header_path': 'include/ld_symbols.h',
                           'elf_section_list_path': 'elf_sections.txt',
                           'ld_partial_scripts_path': 'partial-scripts',
                           'ld_partial_build_segments_path': 'partial-objects'}.items():
            if opts.get(key) is not None:
                opts[key] = str(work / value)
        for segment in split['segments']:
            if not isinstance(segment, dict):
                continue
            for entry in segment.get('subsegments', []):
                if not isinstance(entry, list):
                    continue
                if entry[1] == 'c':
                    entry[1] = 'asm'
                elif entry[1] in ('.data', '.rodata', '.bss'):
                    entry[1] = entry[1][1:]
        private_config = work / 'config.yaml'
        private_config.write_text(yaml.safe_dump(split, sort_keys=False), encoding='utf-8')
        log('Generating ROM-derived objdiff reference assembly…')
        run([console_python(sys.executable), '-m', 'splat', 'split', str(private_config)], root, log)
        linked_definitions = dict(definitions)
        definitions.update(reference_symbols(work))
        entries = []
        image = image_path.read_bytes()
        for index, unit in enumerate(units, 1):
            log(f'Verifying objdiff reference {index}/{len(units)}: {unit["name"]}')
            target = assemble(root, work, unit, definitions, image, assembler, linker, console.as_flags, log)
            entry = {'name': unit['source'].relative_to(root).with_suffix('').as_posix() if unit['source'] else 'asm/' + unit['name'],
                     'target_path': (destination / target.relative_to(work)).relative_to(root).as_posix()}
            if unit['source']:
                entry.update(base_path=unit['base'].relative_to(root).as_posix(),
                             metadata={'source_path': unit['source'].relative_to(root).as_posix()})
                mappings = source_symbol_mappings(target, unit['base'], linked_definitions, unit['vram'])
                if mappings:
                    entry['symbol_mappings'] = mappings
            entries.append(entry)
        build = project.setup_commands(version)[-1]
        pending = work / 'objdiff.json'
        pending.write_text(json.dumps({'custom_make': build[0], 'custom_args': build[1:],
                                       'build_target': False, 'build_base': True,
                                       'units': entries,
                                       'coverage_notes': ([] if matching else [
                                           'The source rebuild has not passed the whole-image checksum. Function '
                                           'progress uses independently byte-verified original sections.'])},
                                      indent=2) + '\n', encoding='utf-8')
        # All outputs stay private to the project. Never overwrite a hand-written configuration or expected/.
        if (root / 'objdiff.json').exists():
            raise ForkError("objdiff.json appeared during setup; the existing configuration was retained")
        try:
            shutil.move(str(work), destination)
            # A same-filesystem link publishes the fully written file atomically, without overwriting
            # anything another setup or the user created meanwhile. Unsupported filesystems fail closed.
            os.link(destination / 'objdiff.json', root / 'objdiff.json')
        except OSError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    log(f'Generated objdiff.json with {len(entries)} verified ROM-derived reference units.')
    return True
