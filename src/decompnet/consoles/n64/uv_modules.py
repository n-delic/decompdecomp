"""Recognise executable UV modules and their section-relative relocation records."""

import re
import struct
from dataclasses import dataclass

_FORM = re.compile(rb'FORM.{4}MODU', re.S)
_CHUNK = struct.Struct('>4sI')
_COMMON = struct.Struct('>7I4s2I')
_WORD = struct.Struct('>I')
_CHUNKS = (b'PAD ', b'COMM', b'CODE', b'MDBG', b'RELA')
_MAX_FORM = 8 << 20


@dataclass(frozen=True)
class Relocation:
    offset: int
    section: int
    target: int
    kind: int


@dataclass(frozen=True)
class Module:
    offset: int
    name: str
    code_offset: int
    entry: int
    text_size: int
    data_size: int
    rodata_size: int
    bss_size: int
    relocations: tuple[Relocation, ...]
    code: bytes
    stored_size: int
    compressed: bool


def _module(image, start):
    length = _WORD.unpack_from(image, start+4)[0]
    end = start + 8 + length
    if length > _MAX_FORM or end > len(image):
        return None
    cursor = start+12
    chunks = {}
    for _ in range(16):
        if cursor == end:
            break
        if cursor+_CHUNK.size > end:
            return None
        tag, size = _CHUNK.unpack_from(image, cursor)
        payload = cursor+_CHUNK.size
        cursor = payload+size
        if cursor > end:
            return None
        if tag == b'PAD ':
            continue
        compressed = tag == b'GZIP'
        if compressed:
            if size < 24:
                return None
            tag, expected = struct.unpack_from('>4sI', image, payload)
            payload += 8
            size -= 8
            if image[payload:payload+4] != b'MIO0' or expected > _MAX_FORM:
                return None
            from ...rom import asset_formats
            try:
                data, codec = asset_formats.unpack(image[payload:payload+size])
            except asset_formats.AssetError:
                return None
            if codec != 'MIO0' or len(data) != expected:
                return None
        else:
            data = image[payload:payload+size]
        if tag not in (b'COMM', b'CODE', b'MDBG', b'RELA') or tag in chunks:
            return None
        chunks[tag] = payload, size, data, compressed
    if cursor != end or set(chunks) != {b'COMM', b'CODE', b'MDBG', b'RELA'} or len(chunks[b'COMM'][2]) != _COMMON.size:
        return None
    alignment, entry, text, data, rodata, bss, records, name, _, reserved = _COMMON.unpack(chunks[b'COMM'][2])
    if (reserved or not re.fullmatch(rb'[A-Za-z0-9_]{4}', name)
            or not text or entry >= text or (entry | text | data | rodata | bss) % 4
            or alignment % 16 or bss > _MAX_FORM or text+data+rodata != len(chunks[b'CODE'][2])):
        return None
    relocations_data = chunks[b'RELA'][2]
    reloc_end = records*_WORD.size
    if not 0 <= len(relocations_data)-reloc_end < 8 or any(relocations_data[reloc_end:]):
        return None
    # The loader numbers source sections as text, read-only data, data.
    # Target section numbering follows the in-memory CODE and BSS order.
    sizes = (text, rodata, data)
    relocations = []
    for word, in _WORD.iter_unpack(relocations_data[:reloc_end]):
        target, section, kind = word >> 28, (word >> 26) & 3, (word >> 22) & 15
        offset = (word & 0x3fffff)*2
        if (target > 3 or section >= len(sizes) or kind not in (0, 1, 2, 5)
                or offset%4 or offset+4 > sizes[section]):
            return None
        relocations.append(Relocation(offset, section, target, kind))
    return Module(start, name.decode('ascii'), chunks[b'CODE'][0], entry, text, data, rodata, bss,
                  tuple(relocations), chunks[b'CODE'][2], chunks[b'CODE'][1], chunks[b'CODE'][3])


def read(image, *, check=lambda: None):
    """Return complete, bounded modules with validated native relocation sites."""
    modules = []
    end = 0
    for candidate in _FORM.finditer(image):
        check()
        start = candidate.start()
        if start < end or start%4:
            continue
        if module := _module(image, start):
            modules.append(module)
            end = start+8+_WORD.unpack_from(image, start+4)[0]
    return modules


def text_regions(image, *, check=lambda: None):
    """Expose pre-relocation text and the loader's text relocation sites."""
    from .uso import Text

    elf_kinds = {0: 5, 1: 6, 2: 4, 5: 2}
    for module in read(image, check=check):
        start = module.code_offset
        code = module.code[:module.text_size]
        entries = {module.entry}
        relocs = {}
        ambiguous = False
        for relocation in module.relocations:
            at = relocation.offset
            if relocation.section == 0:
                if at in relocs:
                    ambiguous = True
                relocs[at] = elf_kinds[relocation.kind], relocation.target+1
                if relocation.kind == 2 and relocation.target == 0:
                    word, = _WORD.unpack_from(code, at)
                    if word >> 26 == 3:
                        entries.add((word & 0x3ffffff)*4)
            elif relocation.kind == 5 and relocation.target == 0:
                section = module.text_size + (module.data_size if relocation.section == 1 else 0)
                entries.add(_WORD.unpack_from(module.code, section+at)[0])
        valid_entries = tuple(sorted(at for at in entries if at%4 == 0 and at < len(code)))
        relocation_rows = None if ambiguous else tuple((at, *fields) for at, fields in sorted(relocs.items()))
        yield Text(module.name, start, code, 'mio0' if module.compressed else False, valid_entries,
                   module.stored_size, relocation_rows)


def bodies(region, log):
    """Keep resident jump targets out of the module-relative code analysis."""
    from .loader_code import complete

    if region.relocations is None:
        return []
    local_jumps = {at for at, kind, index in region.relocations if kind == 4 and index == 1}
    analysis = bytearray(region.data)
    for at, (word,) in enumerate(_WORD.iter_unpack(region.data)):
        offset = at*4
        if word >> 26 in (2, 3) and offset not in local_jumps:
            # A pseudo-direct resident address encodes its low bits in the instruction.
            # Move it beyond the module only in the analysis copy.
            _WORD.pack_into(analysis, offset, word | 0x00400000)
    return complete(analysis, [], [(0, len(analysis), 0)], entries=region.entries, log=log)
