"""Read executable text from cartridge USO section directories."""
import re
import struct
from dataclasses import dataclass

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
    compressed: bool


def _yay0(source):
    """Decode within the section's declared stored length, including up to three padding bytes."""
    if len(source) < 16 or source[:4] != b'Yay0':
        return None
    size, links, chunks = struct.unpack_from('>III', source, 4)
    if not (0 < size <= _MAX_SECTION and 16 <= links <= chunks <= len(source)):
        return None
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
            out.append(source[chunks])
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
    return bytes(out)


def _text(rom, name, at, end):
    sections = set()
    result = None
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
            return result if {1, 2, 5} <= sections else None
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
        elif kind == 5:
            payload = rom[offset:at]
        if kind == 5 and len(payload) >= 8 and len(payload) % 4 == 0:
            result = Text(name, offset, payload, compressed)
    return None


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
            stored += len(text.data)
            if stored > _MAX_TOTAL_TEXT:
                return result
            result.append(text)
    return result
