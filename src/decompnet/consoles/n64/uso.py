"""Read executable text from cartridge USO section directories."""
import re
import struct
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ...rom.split import SplitResult

_MAGIC = b'\x12\x34\x56\x78'
_FILE = re.compile(rb'\x12\x34\x56\x78.{8}([a-zA-Z0-9_.-]{1,32}\.uso)\0', re.S)
_KINDS = frozenset(range(1, 10)) | {11}
_FLAGS = {0x1000, 0x1001, 0x0800, 0x2000, 0x2001}
_MAX_SECTION = 8 * 1024 * 1024
_MAX_MODULES = 1024
_MAX_TOTAL_TEXT = 64 * 1024 * 1024


@dataclass(frozen=True)
class Text:
    """A module-relative, pre-relocation text image."""
    name: str
    offset: int
    data: bytes
    compressed: bool | str
    entries: tuple[int, ...] = ()
    stored_size: int = 0
    relocations: tuple[tuple[int, int, int], ...] | None = None  # (text offset, ELF kind, symbol index)
    text_bases: tuple[int, ...] = ()  # zero-valued text-section symbols in the loader table
    rodata: bytes = b''
    rodata_bases: tuple[int, ...] = ()
    rodata_relocations: tuple[tuple[int, int, int], ...] | None = None



def _yay0(source, replacement=None):
    """Decode within the section's declared stored length, including up to three padding bytes."""
    if len(source) < 16 or source[:4] != b'Yay0':
        return None
    size, links, chunks = struct.unpack_from('>III', source, 4)
    if not (0 < size <= _MAX_SECTION and 16 <= links <= chunks <= len(source)):
        return None
    if replacement is not None and len(replacement) != size:
        return None
    encoded = bytearray(source) if replacement is not None else None
    mask_end, link_end = links, chunks
    mask_at, mask, remaining = 16, 0, 0
    out = bytearray()
    while len(out) < size:
        if not remaining:
            if mask_at + 4 > mask_end:
                return None
            mask, = struct.unpack_from('>I', source, mask_at)
            mask_at += 4
            remaining = 32
        literal = mask & 0x80000000
        mask = (mask << 1) & 0xFFFFFFFF
        remaining -= 1
        if literal:
            if chunks >= len(source):
                return None
            if encoded is not None:
                encoded[chunks] = replacement[len(out)]
            out.append(source[chunks] if encoded is None else encoded[chunks])
            chunks += 1
            continue
        if links + 2 > link_end:
            return None
        word, = struct.unpack_from('>H', source, links)
        links += 2
        distance, length = (word & 0xFFF) + 1, word >> 12
        if length:
            length += 2
        else:
            if chunks >= len(source):
                return None
            length = source[chunks] + 18
            chunks += 1
        if distance > len(out) or len(out) + length > size:
            return None
        for _ in range(length):
            out.append(out[-distance])
    if len(source) - chunks > 3:
        return None
    if encoded is not None:
        return bytes(encoded) if out == replacement else None
    return bytes(out)


def _text(rom, name, at, end):
    sections = set()
    result = None
    symbols = relocations = rodata = rodata_relocs = b''
    for _ in range(len(_KINDS)):
        for pad in range(4):
            if at + pad + 12 > end:
                return None
            kind, size, flags = struct.unpack_from('>III', rom, at + pad)
            if kind in _KINDS and flags >> 16 in _FLAGS:
                offset = at + pad + 12
                break
        else:
            return None
        if kind in sections or size > _MAX_SECTION:
            return None
        if kind == 11:
            if result is None or not {1, 2, 5} <= sections:
                return None
            if len(symbols) % 12:
                return result
            # A zero-valued text symbol is an import, not entry-point evidence.
            entries = set()
            text_bases, rodata_bases = [], []
            for index, (tag, value, _) in enumerate(struct.iter_unpack('>III', symbols)):
                symbol_type = (tag >> 16) & 0xFF
                if symbol_type == 0x52 and 0 < value < len(result.data) and value % 4 == 0:
                    entries.add(value)
                if symbol_type == 0x54 and value == 0:
                    text_bases.append(index)
                if symbol_type == 0x74 and value == 0:
                    rodata_bases.append(index)
            return replace(result, entries=tuple(sorted(entries)), text_bases=tuple(text_bases),
                           relocations=_relocations(relocations, len(symbols) // 12, len(result.data)),
                           rodata=rodata, rodata_bases=tuple(rodata_bases),
                           rodata_relocations=_relocations(rodata_relocs, len(symbols) // 12, len(rodata)))
        sections.add(kind)
        compressed = bool(flags & 0x10000)
        at = offset + (size if kind != 8 else 0)
        if at > end:
            return None
        payload = b''
        if compressed:
            if kind == 8:
                return None
            payload = _yay0(rom[offset:at])
            if payload is None:
                return None
        elif kind in (1, 2, 4, 5, 7):
            payload = rom[offset:at]
        if kind == 1:
            symbols = payload
        if kind == 2:
            relocations = payload
        if kind == 4:
            rodata_relocs = payload
        if kind == 7:
            rodata = payload
        if kind == 5 and len(payload) >= 8 and len(payload) % 4 == 0:
            result = Text(name, offset, payload, compressed, stored_size=size)
    return None


def _relocations(payload, symbols, size):
    if len(payload) % 12:
        return None
    # The USO loader uses a different kind numbering from ELF.
    kinds = {1: 4, 2: 6, 3: 5, 4: 2}
    by_offset = {}
    for info, at, _ in struct.iter_unpack('>III', payload):
        kind, index = info & 15, info >> 4
        if kind not in kinds or not 0 < index < symbols or at % 4 or at >= size or at in by_offset:
            return None
        by_offset[at] = (kinds[kind], index)
    return tuple((at, *fields) for at, fields in sorted(by_offset.items()))


def function_relocations(region, start, size):
    if region.relocations is None:
        return None
    from bisect import bisect_left

    relocs = region.relocations
    begin = bisect_left(relocs, (start,))
    end = bisect_left(relocs, (start + size,))
    return [[at - start, kind, index] for at, kind, index in relocs[begin:end]]


def function_local_calls(region, start, size, relocs):
    """Return validated module-relative direct calls, as site, base symbol and byte offset."""
    targets = []
    bases = set(getattr(region, 'text_bases', ()))
    for at, kind, index in relocs or ():
        if kind != 4 or index not in bases or not 0 <= at <= size - 4:
            continue
        word = int.from_bytes(region.data[start + at:start + at + 4], 'big')
        offset = (word & 0x03ffffff) << 2
        if word >> 26 == 3 and 0 < offset <= len(region.data) - 4:
            targets.append([at, index, offset])
    return targets


# Direct reads from the loader's read-only section. Address formation and partial loads
# need separate evidence because the instruction alone does not describe the bytes used.
POOL_LOAD_WIDTHS = {0x23: 4, 0x31: 4, 0x35: 8}  # LW, LWC1, LDC1


def function_pool_loads(region, start, size, relocs):
    """Attach direct reads of proven loader-owned constant bytes."""
    rodata = getattr(region, 'rodata', b'')
    data_relocs = getattr(region, 'rodata_relocations', None)
    if not rodata or data_relocs is None:
        return []
    bases = set(region.rodata_bases)
    pointers = {at for at, _, _ in data_relocs}
    highs, otherwise = {}, set()
    for at, kind, index in relocs or ():
        if index not in bases or not 0 <= at <= size - 4:
            continue
        word = int.from_bytes(region.data[start + at:start + at + 4], 'big')
        if kind == 5:
            highs.setdefault(index, []).append(word)
        elif kind != 6:
            otherwise.add(index)
    supported = {index for index, group in highs.items()
                 if index not in otherwise and all(word >> 26 == 15 and not word & 0xffff for word in group)}
    lows = []
    for at, kind, index in relocs or ():
        if kind != 6 or index not in supported or not 0 <= at <= size - 4:
            continue
        word = int.from_bytes(region.data[start + at:start + at + 4], 'big')
        offset = word & 0xffff
        width = POOL_LOAD_WIDTHS.get(word >> 26)
        if (not width or offset % width or offset >= 0x8000 or offset + width > len(rodata)
                or any(at in pointers for at in range(offset, offset + width, 4))):
            continue
        lows.append([at, index, rodata[offset:offset + width].hex()])
    return lows


def function_jump_tables(region, start, size, relocs):
    """Prove the extent and destinations of indexed reads from the loader's text pointers."""
    from ...arch.mips_indirect import jump_table

    if not (region.rodata and region.rodata_relocations and region.text_bases):
        return []
    data_relocs = {at: (kind, index) for at, kind, index in region.rodata_relocations}
    sites = {at: (kind, index) for at, kind, index in relocs or ()}
    tables = []
    for at, (kind, index) in sites.items():
        if (kind != 6 or index not in region.rodata_bases or at + 12 > size
                or sites.get(at - 8) != (5, index)):
            continue
        high, _, word = struct.unpack_from('>III', region.data, start + at - 8)
        if high >> 26 != 15 or high & 0xffff or word >> 26 != 0x23 or word & 3 or word & 0x8000:
            continue
        base = word & 0xffff

        def read(address, length, base=base):
            if address != base or address + length > len(region.rodata):
                return None
            records = [data_relocs.get(p) for p in range(address, address + length, 4)]
            first = records[0]
            if not first or first[0] != 2 or first[1] not in region.text_bases or any(r != first for r in records):
                return None
            return region.rodata[address:address + length]

        table = jump_table(region.data, start, start + size, start, start + at + 4, 'big', reader=read)
        if table and table['state'] == 'validated':
            tables.append((at, index, table))
    destinations = {target for _, _, table in tables for target in table['targets']}
    return [[at, index, [address - start for address in table['targets']]]
            for at, index, table in tables
            if not any(table['guard_address'] < target <= table['address'] + 4 for target in destinations)]


def _binding_metadata(region, start, size, relocs):
    metadata = {}
    if calls := function_local_calls(region, start, size, relocs):
        metadata['local_calls'] = calls
    if loads := function_pool_loads(region, start, size, relocs):
        metadata['pool_loads'] = loads
    if tables := function_jump_tables(region, start, size, relocs):
        metadata['jump_tables'] = tables
    return metadata


def _native_metadata(regions, offset, size, matches):
    """Existing disassembly can cover uncompressed module text under a runtime address."""
    if offset is None or size <= 0 or size % 4:
        return None
    owners = [region for region in regions if not region.compressed and region.relocations
              and 0 <= offset - region.offset <= len(region.data) - size]
    if len(owners) != 1:
        return None
    region, = owners
    start = offset - region.offset
    if start % 4 or not matches(region.data[start:start + size]):
        return None
    relocs = function_relocations(region, start, size)
    if not relocs:
        return None  # the ordinary byte judge already handles this body
    info = {'section': region.offset, 'start': start, 'size': size, 'relocs': relocs, 'native': True}
    return info | _binding_metadata(region, start, size, relocs)


def metadata_updates(image, banks, rows):
    """Upgrade saved overlay metadata only where the loader records and whole body still agree."""
    import json

    regions = {region.offset: region for region in read(image)}
    if not regions:
        return
    for row in rows:
        meta = json.loads(row['meta'] or '{}')
        info = meta.get('uso')
        if not info:
            code = row['target'] or b''
            native = _native_metadata(regions.values(), row['rom_offset'], len(code), code.__eq__)
            if native:
                yield row['id'], json.dumps(meta | {'uso': native})
            continue
        if info.get('relocs') is None:
            continue
        region = regions.get(info['section'])
        start, size = info['start'], info['size']
        bank = region.offset if region and info.get('native') and not region.compressed else banks.get(str(info['section']))
        if (region is None or row['rom_offset'] is None or bank != row['rom_offset'] - start
                or start < 0 or size <= 0 or start + size > len(region.data)
                or region.data[start:start + size] != row['target']
                or function_relocations(region, start, size) != info['relocs']):
            continue
        new = _binding_metadata(region, start, size, info['relocs'])
        updates = {key: value for key, value in new.items() if key not in info}
        for key in ('pool_loads', 'jump_tables'):
            saved, refreshed = info.get(key, []), new.get(key, [])
            if saved != refreshed and all(item in refreshed for item in saved):
                updates[key] = refreshed
        if updates:
            yield row['id'], json.dumps(meta | {'uso': info | updates})


def replace_text(cartridge, region, text):
    if len(text) != len(region.data):
        raise ValueError('USO text length changed')
    if region.compressed == 'mio0':
        # The judge only accepts identical linked bytes; keep the original MIO0 stream.
        if text != region.data:
            raise ValueError('Compressed module text differs from the original')
        return
    start, end = region.offset, region.offset + region.stored_size
    body = _yay0(cartridge[start:end], text) if region.compressed else text
    if body is None or len(body) != region.stored_size:
        raise ValueError('USO text no longer fits the original Yay0 encoding')
    cartridge[start:end] = body


def rebuild(image, entries):
    from collections import defaultdict

    groups = defaultdict(list)
    for name, info, obj in entries:
        groups[info['section']].append((name, info, obj))
    from .uso_match import link
    from .uv_modules import text_regions

    regions = {region.offset: region for region in [*read(image), *text_regions(image)]}
    cartridge = bytearray(image)
    for offset, patches in groups.items():
        region = regions.get(offset)
        if region is None:
            raise ValueError(f'Overlay text section at 0x{offset:X} is missing')
        text = bytearray(region.data)
        end = 0
        for name, info, obj in sorted(patches, key=lambda patch: patch[1]['start']):
            start, size = info['start'], info['size']
            if (start < end or start + size > len(text) or size <= 0
                    or function_relocations(region, start, size) != info['relocs']):
                raise ValueError(f'Overlay function placement at 0x{offset:X}+0x{start:X} has changed')
            end = start + size
            local_calls = function_local_calls(region, start, size, info['relocs'])
            if info.get('local_calls') and info['local_calls'] != local_calls:
                raise ValueError(f'Overlay local call metadata at 0x{offset:X}+0x{start:X} has changed')
            saved_pool = info.get('pool_loads')
            if saved_pool:
                original_pool = function_pool_loads(region, start, size, info['relocs'])
                if any(load not in original_pool for load in saved_pool):
                    raise ValueError(f'Overlay constant pool metadata at 0x{offset:X}+0x{start:X} has changed')
            if saved_tables := info.get('jump_tables'):
                original_tables = function_jump_tables(region, start, size, info['relocs'])
                if any(table not in original_tables for table in saved_tables):
                    raise ValueError(f'Overlay jump table metadata at 0x{offset:X}+0x{start:X} has changed')
            verified = link(obj, name, region.data[start:end], info)
            if not verified.exact:
                details = ', '.join(verified.problems) or 'text bytes differ'
                raise ValueError(f'Overlay {name} no longer matches: {details}')
            text[start:end] = verified.linked_code
        replace_text(cartridge, region, text)
    return bytes(cartridge)


def read(rom: bytes) -> list[Text]:
    """Recognize named filesystem records and complete, bounded text section directories."""
    from itertools import islice

    entries = list(islice(_FILE.finditer(rom), _MAX_MODULES + 1))
    if len(entries) > _MAX_MODULES:
        return []
    result = []
    stored = 0
    for index, entry in enumerate(entries):
        end = entries[index + 1].start() if index + 1 < len(entries) else len(rom)
        header = rom.find(_MAGIC, entry.end(), min(end, entry.start() + 256))
        if header == -1 or header + 16 > end:
            continue
        name = entry[1].decode('ascii')
        if text := _text(rom, name, header + 16, end):
            stored += len(text.data) + len(text.rodata)
            if stored > _MAX_TOTAL_TEXT:
                return result
            result.append(text)
    return result



def add_to_split(result: "SplitResult", workdir: "Path", original: bytes, log) -> None:
    """Recover closed bodies anchored by native exports and verified code references."""
    from ...rom.split import Segment, function_from_bytes
    from .loader_code import complete
    from .uv_modules import bodies as uv_bodies
    from .uv_modules import text_regions

    check = getattr(log, 'check_cancel', lambda: None)
    regions = [('uso', region) for region in read(original)]
    regions.extend(('uv', region) for region in text_regions(original, check=check))
    if not regions:
        return
    path = workdir / result.image
    image = bytearray(path.read_bytes())
    if not image.startswith(original):
        return
    covered = [(fn.rom, fn.rom + fn.size) for fn in result.functions]
    native = [region for kind, region in regions if kind == 'uso' and not region.compressed]
    for fn in result.functions:
        check()
        def matches(code, fn=fn):
            return all(word == line.word for (word,), line in zip(struct.iter_unpack('>I', code), fn.lines, strict=True))
        if info := _native_metadata(native, fn.rom, fn.size, matches):
            fn.uso = info

    def quiet(*_):
        pass

    quiet.check_cancel = check
    counts = {}
    for kind, region in regions:
        check()
        if not region.entries:
            continue
        # A raw module may have some entries recovered already. Keep their original
        # identities and report only new non-overlapping spans in this bank.
        bodies = (uv_bodies(region, quiet) if kind == 'uv' else
                  complete(region.data, [], [(0, len(region.data), 0)], entries=region.entries, log=quiet))
        if not region.compressed:
            bodies = [(a, b, v) for a, b, v in bodies
                      if not any(lo < region.offset + b and region.offset + a < hi for lo, hi in covered)]
        if not bodies:
            continue
        image.extend(bytes(-len(image) % 16))
        start = len(image)
        image.extend(region.data)
        result.analysis_banks[str(region.offset)] = start
        # Use the donor review's bank identity so reimport can pair previously recovered rows.
        name = f'donor_{kind}_{region.offset:X}'
        result.segments.append(Segment(name, start, len(image), 0))
        for a, b, v in bodies:
            entry = f'func_{v:08X}_{kind}_{region.offset:X}'
            fn = function_from_bytes(entry, region.data[a:b], start + a, v, name)
            relocs = function_relocations(region, a, b - a)
            fn.uso = {'section': region.offset, 'start': a, 'size': b - a, 'relocs': relocs}
            fn.uso.update(_binding_metadata(region, a, b - a, relocs))
            result.functions.append(fn)
        functions, banks = counts.get(kind, (0, 0))
        counts[kind] = functions+len(bodies), banks+1
    if counts:
        result.analysis_base = result.image
        result.image = 'overlay-analysis.z64' if 'uv' in counts else 'uso-analysis.z64'
        (workdir / result.image).write_bytes(image)
        for kind, (count, banks) in counts.items():
            format_name = kind.upper()
            result.coverage_notes.append(
                f'Recovered {count} closed functions in {banks} {format_name} text banks using the ROM’s '
                'own entry points and code references. Bank addresses are relative. Export preserves their cartridge bytes.')
            log(f'Recovered {count} native {format_name} functions in {banks} banks.')
