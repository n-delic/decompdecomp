"""Objdiff references for a project whose own build is a byte-exact MIPS rebuild but that has no splat config.

Community N64 decomps (a Makefile and a linker script, no splat yaml) leave `ForkProject.generate_objdiff` with
nothing to plan from. Their linker map and linked ELF say where each object's text/data/rodata landed, so the
units come from the build itself: every object whose `.text` the map places, found back to its C or assembly
source.

A unit's reference is the project's own built object (its relocations are the build's), published only after it is
proved against the ORIGINAL image: each initialized section of the object is relocated with the addresses the
linker gave it (the map for sections and the object's own symbols, the ELF's symbol table for the rest) and must
equal the original ROM's bytes at that section's load address. A unit with any section that does not reproduce
(a NON_MATCHING build, a section stored compressed, data the ROM does not hold) is left out and counted, never
published. Existing configurations and expected objects are never replaced; the files are written the same way as
splatrefs.py's (a staged folder under the project's state folder, then an atomic link of objdiff.json).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import tempfile
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .project import STATE, ForkError
from .splatrefs import inside

MAX_UNITS = 4000
SOURCE_SUFFIXES = ('.c', '.s', '.S')

R_MIPS_NONE, R_MIPS_16, R_MIPS_32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16 = 0, 1, 2, 4, 5, 6
R_MIPS_PC16 = 10

_INPUT = re.compile(r'^ (\S+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+(\S.*?)\s*$')
_INPUT_SPLIT_NAME = re.compile(r'^ (\S+)\s*$')
_INPUT_SPLIT_BODY = re.compile(r'^\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+(\S.*?)\s*$')


class Unverified(Exception):
    """One object's bytes do not reproduce the original image: it is left out, with this as the reason."""


def parse_map(text: str) -> dict[tuple[str, str], tuple[int, int] | None]:
    """{(object path, section name): (address, size)} for every input section the linker kept (a nonzero address
    and size). A section that appears twice for one path is None: its address is ambiguous."""
    sections: dict[tuple[str, str], tuple[int, int] | None] = {}
    started = False
    pending = None
    for line in text.splitlines():
        if not started:
            started = line.startswith('Linker script and memory map')
            continue
        entry = None
        if pending:
            body = _INPUT_SPLIT_BODY.match(line)
            if body:
                entry = (pending, int(body[1], 16), int(body[2], 16), body[3])
            pending = None
        if entry is None:
            full = _INPUT.match(line)
            if full:
                entry = (full[1], int(full[2], 16), int(full[3], 16), full[4])
            else:
                name = _INPUT_SPLIT_NAME.match(line)
                pending = name[1] if name and name[1].startswith('.') else None
                continue
        section, address, size, owner = entry
        if not address or not size or not owner.endswith(('.o', ')')):
            continue
        key = (owner, section)
        sections[key] = None if key in sections else (address, size)
    return sections


def object_sections(elf: ELFFile) -> dict[str, tuple[int, str, int, bytes | None]]:
    """{section name: (index, type, size, contents or None for NOBITS)} for the allocated sections of an object."""
    found = {}
    for index, section in enumerate(elf.iter_sections()):
        if not section['sh_flags'] & 2 or not section['sh_size']:
            continue
        nobits = section['sh_type'] == 'SHT_NOBITS'
        found[section.name] = (index, section['sh_type'], section['sh_size'], None if nobits else section.data())
    return found


def load_ranges(elf: ELFFile) -> list[tuple[int, int, int]]:
    """(address, size, load address) of each initialized allocated section of the linked ELF, the load address being
    where the flat cartridge image holds it (the ROM offset); same rule as splatrefs.binary_span."""
    loads = [segment for segment in elf.iter_segments() if segment['p_type'] == 'PT_LOAD']
    ranges = []
    for section in elf.iter_sections():
        if not section['sh_flags'] & 2 or section['sh_type'] == 'SHT_NOBITS' or not section['sh_size']:
            continue
        load = section['sh_addr']
        for segment in loads:
            if segment.section_in_segment(section):
                load += segment['p_paddr'] - segment['p_vaddr']
                break
        ranges.append((section['sh_addr'], section['sh_size'], load))
    return ranges


def global_symbols(elf: ELFFile) -> dict[str, int | None]:
    """name -> address of the linked ELF's defined global symbols; None when two definitions disagree."""
    table = elf.get_section_by_name('.symtab')
    if table is None:
        raise ForkError("references from the build need the project's unstripped linked ELF")
    found: dict[str, int | None] = {}
    for symbol in table.iter_symbols():
        if not symbol.name or symbol['st_shndx'] == 'SHN_UNDEF' or symbol['st_info']['bind'] not in (
                'STB_GLOBAL', 'STB_WEAK') or symbol['st_info']['type'] == 'STT_SECTION':
            continue
        value = symbol['st_value']
        found[symbol.name] = value if found.get(symbol.name, value) == value else None
    return found


def relocate(obj: ELFFile, key: str, addresses: dict[tuple[str, str], tuple[int, int] | None],
             globals_: dict[str, int | None]) -> dict[str, bytes]:
    """Each initialized allocated section of one built object with its relocations applied at the addresses the
    link gave it. Raises Unverified for what it cannot place or resolve."""
    sections = object_sections(obj)
    names = {index: name for name, (index, *_rest) in sections.items()}
    placed = {}
    for name, (_index, kind, size, _data) in sections.items():
        address = addresses.get((key, name), 0)
        if address is None:
            raise Unverified(f'{name} has two placements in the linker map')
        if address and address[1] != size:
            raise Unverified(f'the map gives {name} a different size than the object')
        placed[name] = address[0] if address else None
    table = obj.get_section_by_name('.symtab')
    symbols = list(table.iter_symbols()) if table is not None else []

    def value(symbol_index: int) -> int:
        symbol = symbols[symbol_index]
        shndx = symbol['st_shndx']
        if isinstance(shndx, int) and shndx in names:
            base = placed.get(names[shndx])
            if base is None:
                raise Unverified(f'{names[shndx]} is not in the linked image')
            return base + (0 if symbol['st_info']['type'] == 'STT_SECTION' else symbol['st_value'])
        if shndx == 'SHN_ABS':
            return symbol['st_value']
        if isinstance(shndx, int) and shndx:
            raise Unverified(f'symbol {symbol.name} is in a section the link dropped')
        found = globals_.get(symbol.name)
        if found is None:
            raise Unverified(f'symbol {symbol.name or symbol_index} has no single address in the linked ELF')
        return found

    out = {name: bytearray(data) for name, (_i, _k, _s, data) in sections.items() if data is not None}
    for relocs in obj.iter_sections():
        if relocs['sh_type'] not in ('SHT_REL', 'SHT_RELA'):
            continue
        target = names.get(relocs['sh_info'])
        if target is None or target not in out:
            continue  # relocations for sections the image doesn't hold (debug info) change nothing
        if placed[target] is None:
            raise Unverified(f'{target} is not in the linked image')
        data, origin = out[target], placed[target]
        entries = list(relocs.iter_relocations())
        rela = relocs['sh_type'] == 'SHT_RELA'
        for position, entry in enumerate(entries):
            kind, offset = entry['r_info_type'], entry['r_offset']
            if kind == R_MIPS_NONE:
                continue
            if offset + 4 > len(data) and kind != R_MIPS_16:
                raise Unverified('a relocation lies outside its section')
            word = struct.unpack_from('>I', data, offset)[0]
            addend = entry['r_addend'] if rela else None
            symbol = value(entry['r_info_sym'])
            place = origin + offset
            if kind == R_MIPS_32:
                result = (symbol + (addend if rela else word)) & 0xFFFFFFFF
                struct.pack_into('>I', data, offset, result)
            elif kind == R_MIPS_26:
                field = ((word & 0x3FFFFFF) << 2) if not rela else addend
                result = (((field | ((place + 4) & 0xF0000000)) + symbol) >> 2) & 0x3FFFFFF
                struct.pack_into('>I', data, offset, (word & 0xFC000000) | result)
            elif kind == R_MIPS_16:
                half = struct.unpack_from('>H', data, offset + 2)[0]
                field = addend if rela else (half - 0x10000 if half & 0x8000 else half)
                struct.pack_into('>H', data, offset + 2, (symbol + field) & 0xFFFF)
            elif kind == R_MIPS_LO16:
                half = word & 0xFFFF
                field = addend if rela else (half - 0x10000 if half & 0x8000 else half)
                struct.pack_into('>I', data, offset, (word & 0xFFFF0000) | ((symbol + field) & 0xFFFF))
            elif kind == R_MIPS_HI16:
                if rela:
                    combined = addend
                else:  # the addend is this half and the low half of the LO16 that pairs with it (same symbol, next)
                    pair = next((later for later in entries[position + 1:] if later['r_info_type'] == R_MIPS_LO16
                                 and later['r_info_sym'] == entry['r_info_sym']), None)
                    if pair is None:
                        raise Unverified('a HI16 relocation has no matching LO16')
                    low = struct.unpack_from('>H', data, pair['r_offset'] + 2)[0]
                    combined = ((word & 0xFFFF) << 16) + (low - 0x10000 if low & 0x8000 else low)
                total = symbol + combined
                struct.pack_into('>I', data, offset, (word & 0xFFFF0000) | (((total + 0x8000) >> 16) & 0xFFFF))
            elif kind == R_MIPS_PC16:
                field = addend if rela else (((word & 0xFFFF) ^ 0x8000) - 0x8000) << 2
                struct.pack_into('>I', data, offset, (word & 0xFFFF0000) | (((symbol + field - place) >> 2) & 0xFFFF))
            else:
                raise Unverified(f'relocation type {kind} is not supported')
    return {name: bytes(data) for name, data in out.items()}


def verify_object(path: Path, key: str, addresses, globals_, ranges, image: bytes) -> tuple[list[str], list[str]]:
    """Prove one built object against the original image (raises Unverified). Returns (the sections that reproduce
    the image, the sections whose load address lies beyond the end of the cartridge image).

    The second kind is data a project's link places after the image because its build stores it compressed or
    patches it in later (a decompressed code-data segment); the original cartridge holds no bytes there to compare.
    Only data may be like that: an object's code must reproduce the image, and a section that does lie inside the
    image must match it, or the object is left out."""
    covered, external = [], []
    try:
        with path.open('rb') as stream:
            obj = ELFFile(stream)
            if obj.header['e_machine'] != 'EM_MIPS' or obj.little_endian:
                raise Unverified('not a big-endian MIPS object')
            relocated = relocate(obj, key, addresses, globals_)
    except (OSError, ELFError, struct.error, IndexError, KeyError) as error:
        raise Unverified(f'unreadable object ({error})') from None
    for name, content in relocated.items():
        address = addresses.get((key, name))
        if not address:
            continue  # a section the linker dropped: nothing of it is in the image
        start, size = address
        home = next((load + start - base for base, length, load in ranges
                     if base <= start and start + size <= base + length), None)
        if home is not None and home >= len(image) and name != '.text':
            external.append(name)
            continue
        if home is None or home < 0 or home + size > len(image):
            raise Unverified(f'{name} is not stored in the original image')
        if image[home:home + size] != content:
            raise Unverified(f'{name} does not reproduce the original image at {home:#x}')
        covered.append(name)
    if '.text' not in covered:
        raise Unverified('no code in the original image')
    return covered, external


def source_for(root: Path, build: Path, obj: Path) -> Path | None:
    """The C or assembly file an object in the build folder came from (build/<version>/src/a.o -> src/a.c)."""
    try:
        relative = obj.relative_to(build)
    except ValueError:
        return None
    parts = relative.parts
    for skip in range(len(parts)):  # (the first folders may name the version: build/us/src/a.o)
        rest = Path(*parts[skip:])
        names = [rest.with_suffix(suffix) for suffix in SOURCE_SUFFIXES] + [rest.with_suffix('')]
        for candidate in names:
            found = root / candidate
            if found.suffix in SOURCE_SUFFIXES and found.is_file() and 'build' not in candidate.parts[:1]:
                return found
    return None


def find_link(root: Path) -> tuple[Path, Path]:
    """The linked ELF and its map: the build's biggest ELF that has a map of its own beside it."""
    build = root / 'build'
    pairs = []
    for elf in build.rglob('*.elf') if build.is_dir() else []:
        for linker_map in (elf.with_suffix('.map'), elf.with_name(elf.name + '.map')):
            if linker_map.is_file() and elf.stat().st_size:
                pairs.append((linker_map.stat().st_size, elf, linker_map))
                break
    if not pairs:
        raise ForkError("references from the build need its linked ELF and linker map; build the project first")
    pairs.sort(key=lambda item: (-item[0], item[1].as_posix()))
    if len(pairs) > 1 and pairs[0][0] == pairs[1][0]:
        raise ForkError("the build has several linked ELF files of the same size; configure objdiff manually")
    return pairs[0][1], pairs[0][2]


def original_rom(project, version: str | None) -> Path:
    """The user's original game file inside the project (where place_game put it)."""
    candidates = []
    placed = getattr(project, '_rom_placed', None)
    if placed:
        candidates.append(Path(placed))
    try:
        need = project.needs(version).get('path')
    except ForkError:
        need = None
    if need and not need.endswith('/'):
        candidates.append(project.root / need)
    candidates += sorted(project.root.glob('baserom*.z64'))
    for path in candidates:
        if path.is_file():
            return path
    raise ForkError("references from the build need the original game file in the project")


def make_variables(project, version: str | None) -> list[str]:
    """The VAR=value words the project's own setup passes to make (VERSION=us): objdiff rebuilds a unit's object
    with the same ones, so it lands where the unit's base_path says."""
    try:
        commands = project.setup_commands(version)
    except ForkError:
        return []
    for command in reversed(commands):
        if command and Path(command[0]).name.removesuffix('.exe') in ('make', 'gmake'):
            return [word for word in command[1:] if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=\S*', word)]
    return []


def generate(project, version: str | None = None, log=print) -> bool:
    """Publish references and objdiff.json for a matching build with no splat config; False when it doesn't apply."""
    root = project.root.resolve()
    if (root / 'objdiff.json').exists() or getattr(project, 'platform', None) not in (None, 'n64'):
        return False
    if not (root / 'build').is_dir() or not (root / 'Makefile').is_file():
        return False
    try:
        elf_path, map_path = find_link(root)
        rom = original_rom(project, version)
    except ForkError:
        return False
    build_dir = inside(root, 'build')
    image = rom.read_bytes()
    with elf_path.open('rb') as stream:
        linked = ELFFile(stream)
        if linked.header['e_machine'] != 'EM_MIPS':
            return False
        ranges, globals_ = load_ranges(linked), global_symbols(linked)
    addresses = parse_map(map_path.read_text(errors='replace', encoding='utf-8'))
    objects = sorted({owner for (owner, name) in addresses if name == '.text' and addresses[(owner, name)]})
    units, left_out, skipped, outside = [], {}, 0, []
    for owner in objects:
        if owner.endswith(')'):
            skipped += 1  # library members have no source in the project
            continue
        obj = inside(root, owner)
        source = source_for(root, build_dir, obj)
        if source is None or not obj.is_file():
            skipped += 1
            continue
        try:
            _covered, external = verify_object(obj, owner, addresses, globals_, ranges, image)
        except Unverified as reason:
            left_out[source.relative_to(root).as_posix()] = str(reason)
            continue
        units.append((source, obj))
        if external:
            outside.append(source.relative_to(root).as_posix())
    if not units or len(units) > MAX_UNITS:
        raise ForkError(f"the build's objects can't be verified against the original game ({len(units)} of "
                        f"{len(objects)}); configure objdiff manually")
    by_name = {}
    for source, obj in sorted(units, key=lambda item: (item[0].suffix != '.c', item[0].as_posix())):
        by_name.setdefault(source.relative_to(root).with_suffix('').as_posix(), (source, obj))
    destination = inside(root, STATE + '/objdiff-references')
    parent = inside(root, STATE)
    parent.mkdir(exist_ok=True)
    if destination.exists():
        raise ForkError("existing automatic reference files were retained; restore their objdiff.json or set up manually")
    make_args = make_variables(project, version)
    with tempfile.TemporaryDirectory(prefix='reference-stage-', dir=parent) as temporary:
        work = Path(temporary)
        entries = []
        for name, (source, obj) in by_name.items():
            target = work / 'targets' / obj.relative_to(build_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(obj, target)
            entry = {'name': name, 'target_path': (destination / target.relative_to(work)).relative_to(root).as_posix()}
            if source.suffix == '.c':
                entry.update(base_path=obj.relative_to(root).as_posix(),
                             metadata={'source_path': source.relative_to(root).as_posix()})
            else:
                entry['metadata'] = {'assembly_source': source.relative_to(root).as_posix()}
            entries.append(entry)
        notes = []
        if left_out:
            notes.append(f'{len(left_out)} linked objects did not reproduce the original game and have no reference: '
                         + ', '.join(sorted(left_out)[:20]) + (' ...' if len(left_out) > 20 else ''))
        if outside:
            notes.append(f'{len(outside)} objects keep data the original cartridge does not hold (their link places it '
                         'after the image); their code is verified, that data is the build\'s own.')
        (work / 'objdiff.json').write_text(json.dumps({
            'custom_make': 'make', 'custom_args': make_args, 'build_target': False, 'build_base': True,
            'units': entries, 'coverage_notes': notes}, indent=2) + '\n', encoding='utf-8')
        if (root / 'objdiff.json').exists():
            raise ForkError("objdiff.json appeared during setup; the existing configuration was retained")
        try:
            shutil.move(str(work), destination)
            os.link(destination / 'objdiff.json', root / 'objdiff.json')
        except OSError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    log(f'Generated objdiff.json with {len(entries)} objects, each proved against the original game '
        f'({len(left_out)} left out, {skipped} without a source file).')
    return True
