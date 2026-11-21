"""Import raw DMA banks whose headers corroborate the loader's section directory."""

import json
import re
import struct
from dataclasses import dataclass
from itertools import pairwise

import yaml

from ...arch.mips import is_frame


@dataclass(frozen=True)
class Bank:
    index: int
    start: int
    end: int
    base: int
    text: int
    data: int


def banks(image):
    identified = []
    for hit in re.finditer(b'MWo2', image):
        start = hit.start()
        if start%16 or start+68 > len(image):
            continue
        index, base, size = struct.unpack_from('>3I',image,start+4)
        end = start+size
        if (not 0 < index <= 128 or not 0x80000400 <= base < base+size <= 0x80800000
                or base%16 or size%16 or size < 80 or end > len(image)
                or any(image[start+16:start+32]) or not is_frame(struct.unpack_from('>I',image,start+64)[0])):
            continue
        name = image[start+32:start+64].split(b'\0',1)[0]
        if not name or not all(32 <= c <= 126 for c in name):
            continue
        header = struct.pack('>4I',base,base+size,start,end)
        boundaries = set()
        for occurrence in re.compile(re.escape(header)).finditer(image,0,start):
            at = occurrence.start()
            if at%4 or at+32>start:
                continue
            text, data, data_start, end_ram = struct.unpack_from('>4I',image,at+16)
            if text == base+64 and text < data <= end_ram == base+size and data == data_start and data%4 == 0:
                boundaries.add(data)
        if len(boundaries)==1:
            data = boundaries.pop()
            identified.append(Bank(index,start,end,base,start+64,start+data-base))
    if len(identified)<2 or len({b.base for b in identified})==len(identified):
        return []
    if any(a.end != b.start or a.index+1 != b.index for a,b in pairwise(identified)):
        return []
    return identified


def add(config, workdir, rom, resident, records, log):
    from .code import insert_segments

    occupied_start, occupied_end = records[0].start, records[-1].end
    resident = [(start,end,vram) for start,end,vram in resident if end<=occupied_start or start>=occupied_end]
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    insert_segments(config,cfg,resident,len(rom))
    segments = {segment['start']:segment for segment in cfg['segments'] if isinstance(segment,dict)}
    for bank in records:
        segments[bank.start] = dict(type='bin',start=bank.start)
        name = f'dma_overlay_{bank.index:02d}'
        parts = [[bank.text,'c',name]]
        if bank.data<bank.end:
            parts.append([bank.data,'data',name])
        segments[bank.text] = dict(type='code',name=name,start=bank.text,vram=bank.base+64,subsegments=parts,
                                  exclusive_ram_id=f'dma_{bank.base:08X}',symbol_name_format=f'$VRAM_{name}',
                                  symbol_name_format_no_rom=f'$VRAM_{name}')
    if occupied_end<len(rom):
        segments.setdefault(occupied_end,dict(type='bin',start=occupied_end))
    cfg['segments'] = [segments[key] for key in sorted(segments)]+[[len(rom)]]
    config.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding='utf-8')
    (workdir/'analysis-coverage.json').write_text(json.dumps([
        f'Includes {len(records)} raw DMA banks with text and data bounds verified by the loader directory.'
    ]),encoding='utf-8')
    log(f'Imported {len(records)} raw DMA overlays at their declared addresses')
