"""Import verified executable members of an indexed cartridge archive."""
import hashlib
import json
from pathlib import Path

import yaml

from decompassets import formats
from decompassets.n64 import indexed_runtime_archive


def program_rows(rom):
    return [row for row in indexed_runtime_archive.members(rom)
            if row['compressed'] and 0x80000400 <= row['vram'] < row['ram_end'] <= 0x80800000]


def verify_analysis(rom, expanded):
    """The appended analysis bytes must equal each original archive member exactly."""
    rows = program_rows(rom)
    if not rows or expanded[:len(rom)] != rom:
        return False
    cursor = len(rom)+(-len(rom)%16)+16
    if any(expanded[len(rom):cursor]):
        return False
    for row in rows:
        aligned = (cursor+15)&~15
        if any(expanded[cursor:aligned]):
            return False
        cursor = aligned
        try:
            payload = indexed_runtime_archive.unpack(rom, row)
        except formats.AssetError:
            return False
        if expanded[cursor:cursor+len(payload)] != payload:
            return False
        cursor += len(payload)
    end = (cursor+15)&~15
    return end == len(expanded) and not any(expanded[cursor:end])

def add(config, workdir, image, rom, taken, log):
    from .code import _looks_like_code
    from .loader_code import complete

    rows = program_rows(rom)
    if not rows:
        return None
    expanded = bytearray(rom)
    expanded.extend(bytes(-len(expanded)%16+16))
    programs = []
    for row in rows:
        try:
            payload = indexed_runtime_archive.unpack(rom, row)
        except formats.AssetError:
            return None
        if not 0x1000 <= len(payload) <= row['ram_end']-row['vram'] or not _looks_like_code(payload):
            return None
        expanded.extend(bytes(-len(expanded)%16))
        start = len(expanded)
        expanded.extend(payload)
        scope = (start,len(expanded),row['vram'])
        table = dict(rom_start=start,rom_end=len(expanded),vram=row['vram'])
        found = complete(expanded, [], [scope], taken, log, table_ranges=[table], entries=(row['vram'],))
        if len(found)<4 or min(lo for lo,_,_ in found) != start:
            return None
        text_end = min(len(expanded), (max(hi for _,hi,_ in found)+15)&~15)
        programs.append((row,start,text_end,len(expanded)))
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = [s for s in cfg['segments'] if not isinstance(s,list) or len(s)!=1]
    segments.append(dict(type='bin',start=len(rom)))
    for row,start,text_end,end in programs:
        name = f'archive_program_{row["index"]:04d}'
        parts = [[start,'c',name]]
        if text_end < end:
            parts.append([text_end,'data',name])
        segment = dict(name=name,type='code',start=start,vram=row['vram'],subsegments=parts)
        if sum(other['vram']==row['vram'] for other,_,_,_ in programs)>1:
            segment.update(exclusive_ram_id=f'archive_slot_{row["vram"]:08X}',
                           symbol_name_format=f'$VRAM_{name}',symbol_name_format_no_rom=f'$VRAM_{name}')
        segments.append(segment)
    expanded.extend(bytes(-len(expanded)%16))
    segments.append([len(expanded)])
    filename = f'{Path(image).stem}.archive{Path(image).suffix}'
    (workdir/filename).write_bytes(expanded)
    cfg['options']['target_path'] = filename
    cfg['sha1'] = hashlib.sha1(expanded).hexdigest()
    cfg['segments'] = segments
    config.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding='utf-8')
    (workdir/'analysis-coverage.json').write_text(json.dumps([
        f'Includes {len(programs)} verified executable archive members; code outside detected bodies may remain.',
        'Compressed members are analysed in an appended image. The original cartridge bytes remain intact.']))
    log(f'Unpacked {len(programs)} executable archive members into their declared runtime ranges')
    return filename
