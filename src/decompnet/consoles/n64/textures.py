"""Recognize indexed texture sets by their tables, dimensions and complete payloads.

The source-assisted layout is three indexed blocks: an eight-byte format record,
bitmap frames and palettes. No title, release address or source file is needed at runtime.
Only CI4/CI8 variants are supported; mipmapped and direct-color layouts need separate evidence.
"""
from __future__ import annotations

import re
import struct

from . import rnc

MAX_BANK = 4 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_BLOCKS = 16384
RAW_HEADER = re.compile(rb'\x00\x00\x00\x03\x00\x00\x00\x18')


def _blocks(data, start, end):
    if start + 8 > end:return None
    count = int.from_bytes(data[start:start+4], 'big')
    if not 1 <= count <= 256 or start + 4*(count+2) > end:return None
    offsets = struct.unpack_from('>'+str(count+1)+'I', data, start+4)
    if offsets[0] != (4*(count+2)+7)//8*8 or offsets[-1] != end-start:return None
    if any(a >= b or a % 8 for a,b in zip(offsets,offsets[1:])):return None
    return [(start+a,start+b) for a,b in zip(offsets,offsets[1:])]


def layout(data):
    """Require every frame and palette to fit the independently recorded dimensions."""
    outer = _blocks(data, 0, len(data))
    if not outer or len(outer) != 3 or outer[0] != (24,32):return None
    fmt, speed, width_shift, height_shift, effect, mode, pad1, pad2 = data[24:32]
    if fmt not in (0,1,3) or not 1 <= width_shift <= 10 or not 1 <= height_shift <= 10:return None
    if pad1 or pad2 or mode > 3:return None
    frames = _blocks(data, *outer[1]); palettes = _blocks(data, *outer[2])
    if not frames or not palettes:return None
    width, height = 1 << width_shift, 1 << height_shift
    size = width*height if fmt == 0 else width*height//2
    entries = 256 if fmt == 0 else 16
    if any(end-start != size for start,end in frames):return None
    if any(end-start != entries*2 for start,end in palettes):return None
    return dict(format='ci8' if fmt == 0 else 'ci4',width=width,height=height,
                offset=frames[0][0],palette_offset=palettes[0][0],
                palette_format='ia16' if fmt == 3 else 'rgba5551-be'), len(frames), len(palettes)


def decode(data):
    """Return None for other codecs so the common preview reader can handle them."""
    if data[:4] not in (b'RNC\x01',b'RNC\x02'):return None
    try:return (rnc.unpack if data[3]==1 else rnc.unpack_m2)(data[:MAX_BANK], max_output=MAX_BANK), 'RNC method '+str(data[3])
    except rnc.RncError as exc:
        from ...rom.asset_formats import AssetError
        raise AssetError('The RNC block is incomplete or its checksum failed. Check its offset.') from exc


def regions(image, *, check=lambda: None, progress=lambda _: None):
    """Bound decoding across the entire scan, checking cancellation between blocks."""
    expanded = 0
    contained_end = 0
    results = []
    # The strong raw header also finds sets not compressed on the cartridge.
    candidates = sorted({m.start() for m in RAW_HEADER.finditer(image)} |
                        {m.start() for m in re.finditer(b'RNC[\x01\x02]',image)})
    for index, offset in enumerate(candidates[:MAX_BLOCKS]):
        check()
        if offset < contained_end:continue
        if index % 64 == 0:progress(dict(phase='Recognizing texture banks',done=index,total=min(len(candidates),MAX_BLOCKS)))
        compressed = image[offset:offset+4] in (b'RNC\x01',b'RNC\x02')
        if compressed:
            if offset+18 > len(image):continue
            size, packed = struct.unpack_from('>II',image,offset+4)
            if not 0 < size <= MAX_BANK or not 2 <= packed <= MAX_BANK-18 or offset+18+packed > len(image):continue
            if expanded+size > MAX_EXPANDED:break
            expanded += size
            try:data = (rnc.unpack if image[offset+3]==1 else rnc.unpack_m2)(image[offset:offset+18+packed],max_output=MAX_BANK)
            except rnc.RncError:continue
            extent = 18+packed
            contained_end = offset+extent
        else:
            if offset+20 > len(image):continue
            extent = int.from_bytes(image[offset+16:offset+20],'big')
            if not 32 <= extent <= MAX_BANK or offset+extent > len(image):continue
            data = image[offset:offset+extent]
        found = layout(data)
        if not found:continue
        settings, frames, palettes = found
        if compressed:settings.update(source='decoded',block_offset=offset)
        else:
            settings.update(source='rom',offset=offset+settings['offset'],palette_offset=offset+settings['palette_offset'])
        results.append(dict(offset=offset,size=extent,kind='image',label='Indexed texture bank',confidence='validated',
                            reason='Source-assisted indexed-set structure: all frame extents match dimensions and all palette extents match the format.'+
                                   (' Both RNC checksums passed.' if compressed else '')+
                                   ' Runtime use is not established; preview shows the first frame and palette.',
                            preview_settings=settings,frames=frames,palettes=palettes))
    return results
