"""Cartridge-resident overlay tables with zlib program images and paired byte-patch banks.

The marker and records describe load addresses explicitly, including mutually exclusive RAM slots. Unknown
storage formats remain opaque and are reported, rather than guessed to contain executable instructions.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from ...rom import RomError

MAGIC = b'OVERLAYTABLENUMC'
MAX_IMAGES = 128
MAX_OUTPUT = 8 << 20


@dataclass
class Overlay:
    index: int
    offset: int
    packed: int
    address: int
    size: int
    data: bytes | None
    storage: str = "zlib"


def read(rom: bytes) -> list[Overlay]:
    """Read bounded table entries and verify complete zlib streams, including their Adler-32 checksums."""
    table = rom.find(MAGIC, 0x1000, min(len(rom), 0x20000))
    if table < 0:
        return []
    entries = []
    for index in range(1, MAX_IMAGES + 1):
        at = table + index * 16
        if at + 16 > len(rom):
            raise RomError('The overlay table is truncated; the current project was retained')
        cart, packed, address, end = struct.unpack_from('>4I', rom, at)
        if not (0xB0000000 <= cart < 0xC0000000 and 0x80000000 <= address < 0x80800000):
            return _patches(entries, rom)
        size, offset = end - address, cart & 0x0FFFFFFF
        if packed == 0 and size == 0:
            continue
        if not (0 < size <= MAX_OUTPUT and end <= 0x80800000 and packed > 0
                and offset >= 0x1000 and offset + packed <= len(rom)):
            raise RomError(f'Overlay {index} has an invalid size or cartridge range')
        payload = rom[offset:offset + packed]
        data = None
        if (len(payload) >= 2 and payload[0] & 15 == 8 and payload[0] >> 4 <= 7
                and int.from_bytes(payload[:2], 'big') % 31 == 0):
            try:
                decoder = zlib.decompressobj()
                data = decoder.decompress(payload, size + 1)
                if len(data) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                    raise ValueError('stream length differs from table')
            except (zlib.error, ValueError) as error:
                raise RomError(f'Overlay {index} failed its original zlib size/checksum verification: {error}') from None
        entries.append(Overlay(index, offset, packed, address, size, data))
    raise RomError('The overlay table has no bounded terminator')


def _patches(entries: list[Overlay], rom: bytes) -> list[Overlay]:
    """A second bank can reuse the preceding bank through cumulative-offset byte replacements.

    The loader reads big-endian u16 deltas and u8 values at four-byte strides. Identify the paired
    bank by its complete size sequence and distinct uniform load slot, not by fixed cartridge offsets.
    """
    first = next((i for i, entry in enumerate(entries) if entry.data is None), len(entries))
    count = len(entries) - first
    if not count or first < count:
        return entries
    bases, patches = entries[first - count:first], entries[first:]
    if (any(e.data is None for e in bases) or any(e.data is not None for e in patches)
            or len({e.address for e in bases}) != 1 or len({e.address for e in patches}) != 1
            or bases[0].address == patches[0].address
            or [e.size for e in bases] != [e.size for e in patches]):
        return entries
    for base, entry in zip(bases, patches):
        payload = rom[entry.offset:entry.offset + entry.packed]
        if len(payload) % 4 or any(payload[3::4]):
            return entries  # not the recognized padded patch-record format
    decoded = []
    for base, entry in zip(bases, patches):
        data, position = bytearray(base.data), 0
        for delta, value, _ in struct.iter_unpack('>HBB', rom[entry.offset:entry.offset + entry.packed]):
            position += delta
            if position >= len(data):
                raise RomError(f'Overlay {entry.index} has a byte patch outside its base image')
            data[position] = value
        decoded.append(bytes(data))
    for entry, data in zip(patches, decoded):
        entry.data, entry.storage = data, 'byte-patches'
    return entries


def add(config: Path, workdir: Path, image: str, log) -> str | None:
    """Append verified program images and give overlapping banks distinct symbols and Splat contexts."""
    import yaml

    rom = (workdir / image).read_bytes()
    entries = read(rom)
    if not entries:
        return None
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    expanded = bytearray(rom)
    segments = [s for s in cfg['segments'] if not (isinstance(s, list) and len(s) == 1)]
    decoded, patched, skipped = 0, 0, []
    for entry in entries:
        if entry.data is None:
            skipped.append(entry.index)
            continue
        # Program images include initialized data after text. The last return's delay slot remains code;
        # the trailing bytes stay data, so jump tables and strings are not counted as functions.
        returns = [i for i in range(0, len(entry.data) - 3, 4)
                   if entry.data[i:i + 4] == b'\x03\xe0\x00\x08']
        if not returns:
            skipped.append(entry.index)
            continue
        text_size = min(returns[-1] + 8, len(entry.data))
        start = len(expanded) + (-len(expanded) % 16) + 0x2000
        expanded += bytes(start - len(expanded)) + entry.data
        name = f'overlay_{entry.index:03d}'
        parts = [[start, 'c', name]]
        if text_size < len(entry.data):
            parts.append([start + text_size, 'data', name])
        segments.append({'name': name, 'type': 'code', 'start': start, 'vram': entry.address,
                         'exclusive_ram_id': 'table_overlays', 'symbol_name_format': f'$VRAM_{name}',
                         'symbol_name_format_no_rom': f'$VRAM_{name}', 'subsegments': parts})
        segments.append({'type': 'bin', 'start': len(expanded)})
        decoded += 1
        patched += entry.storage == 'byte-patches'
    if not decoded:
        raise RomError('No supported executable overlay images were decoded from the table')
    # The last bin begins exactly at EOF; keep only the single end marker there.
    segments = [s for s in segments if not (isinstance(s, dict) and s.get('start') == len(expanded))]
    segments.append([len(expanded)])
    result = f'{Path(image).stem}.overlays{Path(image).suffix}'
    (workdir / result).write_bytes(expanded)
    cfg['options']['target_path'] = result
    cfg['sha1'] = hashlib.sha1(expanded).hexdigest()
    cfg['segments'] = segments
    config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
    notes = [f'Includes {decoded} overlay program images: {decoded - patched} checksummed zlib images and '
             f'{patched} images reconstructed from bounded byte patches against those bases.',
             'Text boundaries are inferred; decoded table coverage does not establish complete game-function coverage.']
    if skipped:
        notes.append(f'{len(skipped)} overlay-table entries use unsupported storage or have no identified '
                     'executable text and are excluded: ' + ', '.join(map(str, skipped)) + '.')
    (workdir / 'analysis-coverage.json').write_text(json.dumps(notes), encoding='utf-8')
    log(f'Unpacked {decoded} table-defined overlays ({sum(len(e.data) for e in entries if e.data) // 1024} KiB); '
        f'{len(skipped)} entries remain outside the measured population')
    return result
