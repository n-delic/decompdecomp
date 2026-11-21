"""Map resident tables whose overlay loader copies and consumes seven-word records."""

import struct
from dataclasses import dataclass
from itertools import pairwise

import yaml

_ROW = struct.Struct('>7I')


@dataclass(frozen=True)
class Bank:
    start: int
    end: int
    vram: int
    text_start: int
    text_end: int
    ram_end: int


def _directory(image, at, size, floor):
    if not 0 <= at <= len(image)-size:
        return set()
    result = set()
    for flag, base, ram_end, start, end, text, text_end in _ROW.iter_unpack(image[at:at+size]):
        if not (base or ram_end or start or end or text or text_end):
            if flag:
                return set()
            continue
        if (flag not in (0, 1) or not floor <= start < end <= len(image)
                or not 0x80000400 <= base <= text < text_end <= base+end-start <= ram_end <= 0x80800000
                or (start | end | base | ram_end | text | text_end)%4):
            return set()
        result.add(Bank(start,end,base,start+text-base,start+text_end-base,ram_end))
    return result


def read(image, taken):
    result = set()
    for start, end, vram in taken:
        words = [w for w, in struct.iter_unpack('>I', image[start:end])]
        for index in range(len(words)-32):
            high, low, limit = words[index:index+3]
            reg, bound = (high>>16)&31, (limit>>16)&31
            if high>>26 != 15 or low>>16 != (0x2400 | reg<<5 | reg) or limit>>21 != (0x120 | reg):
                continue
            distance = limit & 0xffff
            if not reg or not bound or not 2*_ROW.size <= distance <= 64*_ROW.size or distance%_ROW.size not in (0,24):
                continue
            copy = words[index+3:index+15]
            if (0x2400000c | reg<<21 | reg<<16) not in copy or not any(
                    w>>26 == 5 and (w>>21)&31 == reg and (w>>16)&31 == bound and w&0x8000 for w in copy):
                continue
            consumer = words[index+15:index+130]
            offsets = {w & 0xffff for w in consumer if w>>26 == 0x23}
            if not {4,8,12,16,20,24} <= offsets:
                continue
            immediate = low & 0xffff
            address = ((high & 0xffff)<<16)+(immediate if immediate < 0x8000 else immediate-0x10000)
            size = distance+(4 if distance%_ROW.size else 0)
            if banks := _directory(image,start+address-vram,size,max(hi for _,hi,_ in taken)):
                result.update(banks)
    banks = sorted(result,key=lambda bank:bank.start)
    if len(banks)<2 or any(left.end>right.start for left,right in pairwise(banks)):
        return []
    return banks


def add(config, image, found, banks, log):
    from .code import insert_segments

    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    # An inferred continuous segment may cross multiple physical banks.
    # Keep only inferred segments wholly outside the directory's extent.
    first, last = banks[0].start, banks[-1].end
    outside = []
    for start, end, base in found:
        if start < first:
            outside.append((start,min(end,first),base))
        if end > last:
            offset = max(start,last)
            outside.append((offset,end,base+offset-start))
    insert_segments(config,cfg,outside,len(image))
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = {s[0] if isinstance(s,list) else s['start']:s for s in cfg['segments']}
    for start, end, base in outside:
        if any(base < bank.ram_end and bank.vram < base+end-start for bank in banks):
            segments[start]['exclusive_ram_id'] = 'copied_overlays'
    for bank in banks:
        if bank.text_start>bank.start:
            segments[bank.start] = dict(type='bin',start=bank.start)
        name = f'copied_overlay_{bank.start:08X}'
        parts = [[bank.text_start,'c',name]]
        if bank.text_end<bank.end:
            parts.append([bank.text_end,'data',name])
        base = bank.vram+bank.text_start-bank.start
        segment = dict(name=name,type='code',start=bank.text_start,vram=base,subsegments=parts)
        if (any(other != bank and bank.vram < other.ram_end and other.vram < bank.ram_end for other in banks)
                or any(base < bank.ram_end and bank.vram < base+end-start for start,end,base in outside)):
            segment.update(exclusive_ram_id='copied_overlays',symbol_name_format=f'$VRAM_{name}',
                           symbol_name_format_no_rom=f'$VRAM_{name}')
        segments[bank.text_start] = segment
        segments.setdefault(bank.end,dict(type='bin',start=bank.end))
    cfg['segments'] = [segments[key] for key in sorted(segments)]
    config.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding='utf-8')
    log(f'Mapped {len(banks)} overlay banks and instruction-cache ranges from copied loader tables.')
