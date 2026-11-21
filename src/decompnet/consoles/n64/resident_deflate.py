"""Resident text/data pairs identified by the boot DMA and resident loader."""

import struct
import zlib
from itertools import pairwise

from ...arch.mips_assets import read

MAX_STREAM = 1024*1024


def _stream(rom, at):
    if not 0 <= at < len(rom)-2:
        return None
    size = 16*int.from_bytes(rom[at:at+2], 'big')
    if size < 16:
        return None
    decoder = zlib.decompressobj(-15)
    source = rom[at+2:at+2+MAX_STREAM]
    try:
        data = decoder.decompress(source, size+1)
    except zlib.error:
        return None
    if decoder.eof and len(data) == size:
        return at+2+len(source)-len(decoder.unused_data), data
    return None


def _pair(rom, at):
    from .code import _looks_like_code

    if not (text := _stream(rom, at)) or not _looks_like_code(text[1]):
        return None
    if not (data := _stream(rom, text[0])):
        return None
    end = (data[0]+15)&~15
    if any(rom[data[0]:end]):
        return None
    return end, text[1], data[1]


def _calls(vram, data):
    for event in read(vram, data)['events']:
        if event['kind'] == 'call':
            args = [arg['value'] if arg and arg['kind'] == 'constant' else None for arg in event['args']]
            yield event['instruction'], event['target'], args


def _directory(rom, at):
    size = int.from_bytes(rom[at:at+4], 'big')
    if not 128 <= size <= 65536 or size%4 or at+size > len(rom):
        return None
    offsets = [value for value, in struct.iter_unpack('>I', rom[at:at+size])]
    if at+offsets[-1] > len(rom):
        return None
    pairs = []
    for start, end in pairwise(offsets):
        if end == start:
            pairs.append(None)
            continue
        if start < size or end-start < 18:
            return None
        header = struct.unpack_from('>6HIH', rom, at+start)
        if not header[0] or not header[4] or header[6]&0xff != 0x82:
            return None
        pairs.append((at+start, at+end))
    return pairs


def find(rom, boot_ranges):
    """Recover the decompressed program bytes and the corroborated link addresses."""
    matches = {}
    for start, end, vram in boot_ranges:
        if start >= 0x10000:
            continue
        boot_end = vram+min(end-start, 0x10000-start)
        calls = list(_calls(vram, rom[start:start+boot_end-vram]))
        for instruction, _, args in calls:
            at = args[1]
            if args[0] != 0 or at is None or not 0x10000 <= at < len(rom) or at%16:
                continue
            if not (first := _pair(rom, at)) or not (second := _pair(rom, first[0])):
                continue
            if not (directory := _directory(rom, second[0])):
                continue
            linked = at+len(first[1])+len(first[2])
            for caller, base, _ in calls:
                if not instruction < caller < instruction+0x200 or not boot_end <= base < 0x80800000:
                    continue
                for _, target, setup in _calls(base, first[1][:0x4000]):
                    core2 = setup[1]
                    if core2 is None or not base+len(first[1])+len(first[2]) <= core2 < 0x80800000:
                        continue
                    text_end = core2+len(second[1])
                    data_end = text_end+len(second[2])
                    if (base <= target < base+len(first[1]) and setup[:2] == [0, core2]
                            and setup[2] is not None and data_end <= setup[2] < 0x80800000
                            and setup[3:8] == [linked, linked+len(second[1])+len(second[2]), core2, text_end, text_end]):
                        matches[at, base, core2] = ((base, *first[1:]), (core2, *second[1:]), directory)
    return next(iter(matches.values())) if len(matches) == 1 else None


def _inflate_overlay(rom, index, start, end):
    """Decode one overlay and verify that its checksums, exports and relocations agree."""
    a, sizes, b, flags, units = struct.unpack_from('>4IH', rom, start)
    size = 16*units
    stream = zlib.decompressobj(-15)
    try:
        payload = stream.decompress(rom[start+18:end], size+1)
    except zlib.error:
        return None
    if not stream.eof or len(payload) != size or len(stream.unused_data) > 15:
        return None

    sum_bytes = checksum = 0
    for byte in payload:
        sum_bytes += byte
        checksum ^= (byte << (sum_bytes & 0x17)) & 0xffffffff
    text_units, rodata_units = divmod(a ^ sum_bytes, 65536)
    data_units, bss_units = divmod(sizes, 65536)
    exports, relocations = divmod(b ^ checksum, 65536)
    name_size = (flags >> 8)&255
    if not text_units or not exports or not name_size:
        return None
    text, rodata, initialized, bss = (16*units for units in (text_units, rodata_units, data_units, bss_units))
    names = 40+4*exports
    reloc_at = names+((name_size+3)&~3)
    code_at = (reloc_at+2*relocations+15)&~15
    if code_at+text+rodata+initialized != size:
        return None
    entries = tuple(entry for entry, in struct.iter_unpack('>I', payload[40:names]))
    if any(entry%4 or entry >= text for entry in entries):
        return None
    key = int.from_bytes(rom[0x42+4*index:0x44+4*index], 'big')
    relocs = [divmod(item ^ key, 4) for item, in struct.iter_unpack('>H', payload[reloc_at:reloc_at+2*relocations])]
    if any(4*word >= text+rodata+initialized for word, _ in relocs):
        return None
    return payload[code_at:], text, rodata, bss, entries, relocs


# The relocatable DLL image stores offsets from zero. Link each at this
# synthetic address outside the resident N64 RAM range for local analysis.
OVERLAY_BASE = 0x80800000


def _linked_overlay(payload, text, bss, relocs):
    linked = bytearray(payload)
    for word, kind in relocs:
        offset = 4*word
        value = struct.unpack_from('>I', payload, offset)[0]
        if kind == 0:
            if value >= len(payload)+bss:
                return None
            value += OVERLAY_BASE
        elif kind == 1:
            if value>>26 not in (2, 3) or (value&0x3ffffff)<<2 >= text:
                return None
            value += (OVERLAY_BASE&0x0fffffff)>>2
        elif kind == 2:
            if value>>26 != 15:
                return None
            value += OVERLAY_BASE>>16
        else:
            # The chosen segment base does not alter the low half.
            continue
        struct.pack_into('>I', linked, offset, value)
    return linked


def add(config, workdir, image, rom, boot_ranges, log):
    import hashlib
    import json
    from pathlib import Path

    import yaml

    result = find(rom, boot_ranges)
    if result is None:
        return None
    first, second, directory = result
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = [s for s in cfg['segments'] if not isinstance(s, list) or len(s) != 1]
    expanded = bytearray(rom)
    expanded.extend(bytes(-len(expanded)%16+16))
    entries = []
    overlays = 0

    def append(name, vram, payload, text, rodata=0, bss=0, exports=()):
        start = len(expanded)
        expanded.extend(payload)
        parts = [[start, 'c', name]]
        if rodata:
            parts.append([start+text, '.rodata', name])
        if text+rodata < len(payload):
            parts.append([start+text+rodata, 'data', name])
        segment = dict(name=name,type='code',start=start,vram=vram,subsegments=parts)
        if vram == OVERLAY_BASE:
            segment.update(exclusive_ram_id='relocatable_programs', bss_size=bss,
                           symbol_name_format=f'$VRAM_{name}', symbol_name_format_no_rom=f'$VRAM_{name}')
            for index, offset in enumerate(exports):
                entries.append(f'{name}_entry_{index} = 0x{vram+offset:X}; // '
                               f'type:func rom:0x{start+offset:X} segment:{name}\n')
        segments.append(segment)

    for index, (base,text,data) in enumerate((first,second)):
        append(f'resident_program_{index}',base,text+data,len(text))
    for index, span in enumerate(directory,1):
        if span is None:
            continue
        decoded = _inflate_overlay(rom,index,*span)
        if decoded is None:
            return None
        payload,text,rodata,bss,exports,relocs = decoded
        linked = _linked_overlay(payload,text,bss,relocs)
        if linked is None:
            return None
        append(f'packed_overlay_{index:03d}',OVERLAY_BASE,linked,text,rodata,bss,exports)
        overlays += 1
    segments.append([len(expanded)])
    filename = f'{Path(image).stem}.overlays{Path(image).suffix}'
    (workdir/filename).write_bytes(expanded)
    cfg['options']['target_path'] = filename
    cfg['sha1'] = hashlib.sha1(expanded).hexdigest()
    cfg['segments'] = segments
    config.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding='utf-8')
    with (workdir/'symbol_addrs.txt').open('a',encoding='utf-8') as symbols:
        symbols.writelines(entries)
    notes = [f'Includes two verified resident programs and {overlays} relocatable programs.',
             'Relocatable programs use a shared analysis link address. The loader supplies their runtime addresses.']
    (workdir/'analysis-coverage.json').write_text(json.dumps(notes),encoding='utf-8')
    log(f'Unpacked two resident programs and {overlays} relocatable programs')
    return filename
