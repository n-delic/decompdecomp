"""Paired Rare deflate text and data streams with a shared runtime slot."""

import hashlib
import json
import struct
from pathlib import Path

import yaml

from ...arch.mips import JR_RA
from ...arch.mips_assets import read as read_code

MARKER = b'\x11\x72'
MAX_IMAGES = 32


def pairs(rom, start):
    """Read contiguous text/data pairs, with zero alignment between programs."""
    from .code import _looks_like_code, _rare

    result = []
    while len(result) < MAX_IMAGES and rom[start:start+2] == MARKER:
        text = _rare(rom,start,headers=(6,),minimum=16)
        if not text:
            break
        data_at = start+text[1]
        if rom[data_at:data_at+2] != MARKER or not (data := _rare(rom,data_at,headers=(6,),minimum=16)):
            break
        text_bytes, data_bytes = text[0],data[0]
        if (len(text_bytes)%16 or len(data_bytes)%16
                or not (_looks_like_code(text_bytes) or len(text_bytes) == 16 and text_bytes[:4] == JR_RA.to_bytes(4,'big'))):
            break
        end = data_at+data[1]
        following = (end+15)&~15
        result.append((start,text_bytes,data_bytes))
        if any(rom[end:following]):
            break
        start = following
    return result


def slot(text, core1, core2, linked, text_size, data_size):
    """The resident loader gives its second program a RAM limit and exact linked text/data bounds."""
    addresses = set()
    for index,(word,) in enumerate(struct.iter_unpack('>I',text)):
        if word>>26 != 3:
            continue
        at = 4*index
        if at < 96 or at+8 > len(text):
            continue
        # A short straight-line setup materializes both RAM arguments.
        lo = at-160 if at >= 160 else 0
        prefix = text[lo:at]
        if not any(value>>16 == 0x3c05 and value&0xffff in (core2>>16,(core2+0x8000)>>16)
                   for value, in struct.iter_unpack('>I',prefix)):
            continue
        for event in read_code(core1+lo,text[lo:at+8])['events']:
            if event['kind'] != 'call' or event['instruction'] != core1+at:
                continue
            args = [arg['value'] if arg and arg['kind'] == 'constant' else None for arg in event['args']]
            limit = args[2]
            if (args[:2] == [0,core2] and args[3:8] == [linked,linked+text_size+data_size,core2,
                                                        core2+text_size,core2+text_size]
                    and limit is not None and core2+text_size+data_size <= limit < 0x80800000 and not limit%16):
                addresses.add(limit)
    return next(iter(addresses)) if len(addresses) == 1 else None


def add(config, workdir, image, rom, packed, placed, locations, log):
    if len(packed) < 4 or rom[packed[0][0]:packed[0][0]+2] != MARKER:
        return None
    programs = pairs(rom,packed[0][0])
    if len(programs) < 4 or programs[1][0] != packed[1][0]:
        return None
    addresses = {start:vram for start,_,vram in placed}
    core1 = addresses.get(locations[0])
    core2 = addresses.get(locations[1])
    if core1 is None or core2 is None or core1+sum(map(len,programs[0][1:])) > core2:
        return None
    linked = programs[0][0]+sum(map(len,programs[0][1:]))
    shared = slot(programs[0][1],core1,core2,linked,*map(len,programs[1][1:]))
    if shared is None or shared+max(sum(map(len,program[1:])) for program in programs[2:]) > 0x80800000:
        return None

    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = [segment for segment in cfg['segments'] if not isinstance(segment,list)]
    expanded = bytearray(rom)
    for index,(_,text,data) in enumerate(programs):
        start = len(expanded)+(-len(expanded)%16)+0x2000
        expanded += bytes(start-len(expanded))+text+data
        name = f'packed_program_{index:02d}'
        segment = dict(name=name,type='code',start=start,vram=(core1,core2,shared)[min(index,2)],
                       subsegments=[[start,'c',name],[start+len(text),'data',name]])
        if index >= 2:
            segment.update(exclusive_ram_id='paired_rare_overlays',symbol_name_format=f'$VRAM_{name}',
                           symbol_name_format_no_rom=f'$VRAM_{name}')
        segments.append(segment)
        segments.append(dict(type='bin',start=len(expanded)))
    segments[-1] = [len(expanded)]
    result = f"{Path(image).stem}.overlays{Path(image).suffix}"
    (workdir/result).write_bytes(expanded)
    cfg['options']['target_path'] = result
    cfg['sha1'] = hashlib.sha1(expanded).hexdigest()
    cfg['segments'] = segments
    config.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding='utf-8')
    notes = [f'Includes {len(programs)} verified paired text and data streams.',
             f'{len(programs)-2} programs share the runtime slot established by the resident loader.']
    (workdir/'analysis-coverage.json').write_text(json.dumps(notes),encoding='utf-8')
    log(f'Unpacked {len(programs)} paired program images, including {len(programs)-2} overlays at one RAM address')
    return result
