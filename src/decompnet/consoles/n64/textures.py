"""Recognize indexed texture sets by their tables, dimensions and complete payloads.

The source-assisted layout is three indexed blocks: an eight-byte format record,
bitmap frames and palettes. No title, release address or source file is needed at runtime.
CI4/CI8, RGBA32 and the fixed six-level RGBA16 mipmap layout are checked separately.
"""
from __future__ import annotations

import re
import struct

from . import rnc

MAX_BANK = 4 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_BLOCKS = 16384
RAW_HEADER = re.compile(rb'\x00\x00\x00\x03\x00\x00\x00\x18')


def _blocks(data, start, end, *, empty=False):
    if start + 8 > end:return None
    count = int.from_bytes(data[start:start+4], 'big')
    if not (0 if empty else 1) <= count <= 256 or start + 4*(count+2) > end:return None
    offsets = struct.unpack_from('>'+str(count+1)+'I', data, start+4)
    if offsets[0] != (4*(count+2)+7)//8*8 or offsets[-1] != end-start:return None
    if any(a >= b or a % 8 for a,b in zip(offsets,offsets[1:])):return None
    return [(start+a,start+b) for a,b in zip(offsets,offsets[1:])]


# The display lists use these TMEM byte offsets and eight-byte padded rows.
# LoadBlock uses DXT=0: the exporter has already swapped four-byte words on odd rows.
MIP_LEVELS = tuple(dict(width=32>>level,height=32>>level,offset=offset,stride=max(8,(32>>level)*2))
                   for level,offset in enumerate((0,2048,2560,2688,2720,2736)))
MIP_BYTES = 2744


def bank_layout(data):
    """Require all independently indexed frames/palettes to fit the format record."""
    outer = _blocks(data, 0, len(data))
    if not outer or len(outer) != 3 or outer[0] != (24,32):return None
    fmt, speed, width_shift, height_shift, effect, mode, pad1, pad2 = data[24:32]
    if fmt not in (0,1,2,3,4) or not 0 <= width_shift <= 10 or not 0 <= height_shift <= 10:return None
    if pad1 or pad2 or mode > 3:return None
    frames = _blocks(data, *outer[1]); palettes = _blocks(data, *outer[2],empty=True)
    if not frames or palettes is None:return None
    width, height = 1 << width_shift, 1 << height_shift
    levels = [dict(width=width,height=height,offset=0)]
    if fmt in (0,1,3):
        size = width*height if fmt == 0 else ((width+1)//2)*height
        entries = 256 if fmt == 0 else 16
        if not palettes or any(end-start != entries*2 for start,end in palettes):return None
        pixel_format = 'ci8' if fmt == 0 else 'ci4'
    else:
        if palettes:return None
        pixel_format = 'rgba8888' if fmt == 2 else 'rgba5551-be'
        size = width*height*4 if fmt == 2 else MIP_BYTES
        if fmt == 4:
            if (width,height) != (32,32):return None
            levels = [dict(level) for level in MIP_LEVELS]
    # Indexed sets pad blocks to eight bytes. A one-pixel row is legal.
    padded_size = (size+7)//8*8
    if any(end-start != padded_size for start,end in frames):return None
    settings = dict(format=pixel_format,width=width,height=height,offset=frames[0][0])
    if palettes:settings.update(palette_offset=palettes[0][0],palette_format='ia16' if fmt == 3 else 'rgba5551-be')
    return dict(preview_settings=settings,frames=len(frames),palettes=len(palettes),
                frame_offsets=[start for start,end in frames],palette_offsets=[start for start,end in palettes],
                levels=levels,layout='mipmap-rgba16' if fmt == 4 else 'row-major',playback_speed=speed)


def layout(data):
    """Retain the small layout summary used by existing format checks."""
    found = bank_layout(data)
    if found:return found['preview_settings'],found['frames'],found['palettes']
    return None


def preview(data, asset, selection):
    """Resolve frame/palette/LOD indices from validated metadata, never arbitrary offsets."""
    from ...rom import asset_formats as formats
    frame = formats.integer(selection.get('frame',0),'Frame',0,asset['frames']-1)
    palette = formats.integer(selection.get('palette',0),'Palette',0,max(0,asset['palettes']-1))
    levels = asset.get('levels',[dict(width=asset['preview_settings']['width'],height=asset['preview_settings']['height'],offset=0)])
    mip = formats.integer(selection.get('mip',0),'Mip level',0,len(levels)-1)
    level = levels[mip]
    options = dict(asset['preview_settings'])
    options.update(offset=asset.get('frame_offsets',[options['offset']])[frame]+level['offset'],
                   width=level['width'],height=level['height'])
    if asset['palettes']:options['palette_offset']=asset.get('palette_offsets',[options['palette_offset']])[palette]
    if asset.get('layout')=='mipmap-rgba16':
        raw = bytearray()
        for y in range(level['height']):
            row = formats._take(data,options['offset']+y*level['stride'],level['stride'])
            raw.extend(row[x ^ (4 if y%2 else 0)] for x in range(level['width']*2))
        result = formats.preview(bytes(raw),{**options,'offset':0})
    else:result = formats.preview(data,options)
    result['details'].update(frame=frame,palette=palette if asset['palettes'] else 'none',mip=mip,
                             note=asset['reason']+' Frame, palette and mip selections are local previews.')
    result['settings']=dict(source='rom',format='auto',offset=asset['offset'],frame=frame,palette=palette,mip=mip)
    return result


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
        found = bank_layout(data)
        if not found:continue
        settings = found['preview_settings']
        if compressed:settings.update(source='decoded',block_offset=offset)
        else:
            settings.update(source='rom',offset=offset+settings['offset'])
            if 'palette_offset' in settings:settings['palette_offset']+=offset
            found['frame_offsets']=[offset+at for at in found['frame_offsets']]
            found['palette_offsets']=[offset+at for at in found['palette_offsets']]
        results.append(dict(offset=offset,size=extent,kind='image',label='Texture bank',confidence='validated',
                            reason='Source-assisted indexed-set structure: all frame extents match dimensions and all palette extents match the format.'+
                                   (' Both RNC checksums passed.' if compressed else '')+
                                   ' Runtime use is not established.',
                            **found))
    return results
