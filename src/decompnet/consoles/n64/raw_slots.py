"""Raw cartridge banks loaded into one RAM slot by a resident five-word directory."""

import struct
from dataclasses import dataclass

import yaml

from ...arch.mips import JR_RA

_ENTRY = struct.Struct('>5I')
_CART = 0xb0000000


@dataclass(frozen=True)
class Slot:
    start: int
    end: int
    vram: int


def _directory(image, at, floor):
    records = []
    previous_end = floor
    for offset in range(at, min(at+8*_ENTRY.size, len(image)-_ENTRY.size+1), _ENTRY.size):
        vram, ram_end, cart, cart_end, alternate = _ENTRY.unpack_from(image, offset)
        start, end, shorter = cart-_CART, cart_end-_CART, alternate-_CART
        if (not 0x80000400 <= vram < ram_end <= 0x80800000 or vram%4
                or not previous_end <= start < end <= len(image) or (start | end | shorter)%4
                or not start < shorter <= end or vram+end-start > ram_end
                or records and (vram != records[0].vram or start != previous_end)
                or JR_RA.to_bytes(4, 'big') not in image[start:min(start+0x1000,end)]):
            break
        records.append(Slot(start,end,vram))
        previous_end = end
    return records if len(records) >= 2 else []


def read(image, taken):
    """Locate a directory only when resident code references it and uses its record fields."""
    for start, end, vram in taken:
        words = list(struct.unpack_from(f'>{(end-start)//4}I', image, start))
        for index, (lui, addiu) in enumerate(zip(words, words[1:])):
            if lui>>26 != 15 or addiu>>26 != 9 or (lui>>16)&31 != (addiu>>21)&31:
                continue
            if (lui & 0xffff)>>12 != 8:
                continue
            window = words[index+2:index+40]
            # A five-word index scaling sequence precedes loads of the RAM
            # base, RAM end and the two cartridge bounds from small offsets.
            shifts = sum(word>>26 == 0 and word & 0x7ff == 0x80 for word in window)
            loads = {word & 0xffff for word in window if word>>26 == 0x23}
            if shifts < 2 or not {0,4,8,12} <= loads:
                continue
            immediate = addiu & 0xffff
            address = ((lui & 0xffff)<<16)+(immediate if immediate < 0x8000 else immediate-0x10000)
            at = address-vram+start
            if not 0 <= at <= len(image)-2*_ENTRY.size or at%4:
                continue
            if records := _directory(image,at,max(end for _,end,_ in taken)):
                return records
    return []


def add(config, image, found, records, log):
    """Give each bank a distinct symbol scope and keep uncovered physical bytes opaque."""
    from .code import insert_segments

    by_record = []
    for record in records:
        ranges = [(max(lo,record.start),min(hi,record.end)) for lo,hi,_ in found
                  if lo < record.end and record.start < hi]
        if not ranges:
            return False
        # Preserve already-detected source unit boundaries inside each bank.
        contiguous = []
        for lo,hi in sorted(ranges):
            if contiguous and lo < contiguous[-1][1]:
                contiguous[-1][1] = max(hi,contiguous[-1][1])
            else:
                contiguous.append([lo,hi])
        by_record.append(contiguous)
    first, last = records[0].start, records[-1].end
    outside = []
    for lo,hi,vram in found:
        if lo < first:
            outside.append((lo,min(hi,first),vram))
        if hi > last:
            start = max(lo,last)
            outside.append((start,hi,vram+start-lo))
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    starts = {s[0] if isinstance(s,list) else s['start'] for s in cfg['segments']}
    if any(any(ranges[0][0] < start < ranges[-1][1] for start in starts) for ranges in by_record):
        return False
    insert_segments(config,cfg,outside,len(image))
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = {s[0] if isinstance(s,list) else s['start']:s for s in cfg['segments']}
    for index,(record,ranges) in enumerate(zip(records,by_record)):
        name = f'raw_slot_{index:02d}'
        lo,hi = ranges[0][0],ranges[-1][1]
        parts = []
        for number,(start,end) in enumerate(ranges):
            if parts and ranges[number-1][1] < start:
                parts.append([ranges[number-1][1], 'bin', f'{name}_gap_{number}'])
            parts.append([start,'c',f'{name}_{number:02d}'])
        segments[lo] = dict(name=name,type='code',start=lo,vram=record.vram+lo-record.start,
                            subsegments=parts,exclusive_ram_id=f'raw_slot_{records[0].vram:08X}',
                            symbol_name_format=f'$VRAM_{name}',symbol_name_format_no_rom=f'$VRAM_{name}')
        segments.setdefault(hi,dict(type='bin',start=hi))
    for lo,_,vram in outside:
        if vram >= records[0].vram:
            # Splat treats all global segments as one continuous address range.
            # Separately inferred high text must not span the declared slot.
            segments[lo]['exclusive_ram_id'] = f'load_{lo:08X}'
    cfg['segments'] = [segments[key] for key in sorted(segments)]
    config.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding='utf-8')
    log(f'Mapped {len(records)} raw banks at their declared shared RAM slot.')
    return True
