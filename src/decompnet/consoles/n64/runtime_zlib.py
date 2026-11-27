"""Recover a compressed executable from independently observed loaded call targets."""
import hashlib
import json
import re
import struct
from pathlib import Path

import yaml

from decompassets.n64 import paired_resources

_HEADER = re.compile(rb'\x80[\x00-\x3f]..\x78[\x01\x5e\x9c\xda]', re.DOTALL)
MANIFEST = 'runtime-zlib.json'


def _payload(rom, row):
    if row.get('codec') == 'chunked_mio0':
        from .runtime_mio_chunks import decode
        return decode(rom, row)
    if (type(row.get('offset')) is not int or type(row.get('size')) is not int
            or not 0 <= row['offset'] < row['offset']+row['size'] <= len(rom)):
        return None
    if row.get('codec', 'zlib') == 'zlib':
        return paired_resources.unpack_zlib(rom, row['offset'], row['size'])
    if row.get('codec') == 'refpack':
        from decompassets import formats, refpack
        try:
            payload, used = refpack.unpack(memoryview(rom)[row['offset']:row['offset']+row['size']])
        except (formats.AssetError, IndexError):
            return None
        if used != row['size'] or len(payload) != row.get('expanded'):
            return None
        return payload
    return None


def _contiguous_tail(image, start, end, vram):
    """Keep a run of closed bodies immediately following the last verified body."""
    from ...arch.mips_code_recovery import called_extent, leaf_extent

    at, count = start, 0
    while at < min(end, start+0x10000) and count < 128:
        address = vram+at-start
        size = (leaf_extent(image, at, end, address, cpu='vr4300')
                or called_extent(image, at, end, address, cpu='vr4300'))
        if not size:
            break
        at += size
        count += 1
    return at if count >= 2 else start


def find(rom, dumps, targets, occupied=(), check=lambda: None):
    """Require three independently called targets and a long matching RAM prefix."""
    if len(targets) < 3:
        return []
    candidates = []
    for number, match in enumerate(_HEADER.finditer(rom)):
        if number % 128 == 0:
            check()
        offset = match.start()
        if offset % 2:
            continue
        size = struct.unpack_from('>I', rom, offset)[0] & 0x7fffffff
        payload = paired_resources.unpack_zlib(rom, offset, size)
        if payload is None or len(payload) < 0x10000:
            continue
        placements = {}
        for dump in dumps:
            for target in targets:
                at = target & 0x1fffffff
                if at+64 > len(dump):
                    continue
                needle = dump[at:at+64]
                where = payload.find(needle)
                if where < 0 or payload.find(needle, where+1) >= 0:
                    continue
                base = target-where
                if base % 16 or not 0x80000400 <= base < base+len(payload) <= 0x80800000:
                    continue
                start = base & 0x1fffffff
                if start+0x10000 > len(dump) or dump[start:start+0x10000] != payload[:0x10000]:
                    continue
                if any(v < base+len(payload) and base < v+end-begin for begin,end,v in occupied):
                    continue
                placements.setdefault(base, set()).add(target)
        for base, calls in placements.items():
            if len(calls) >= 3:
                candidates.append(dict(offset=offset, size=size, vram=base, calls=sorted(calls)))
    return candidates if len(candidates) == 1 else []


def verify_analysis(rom, expanded, rows):
    """Prove the appended bytes are exactly the declared original compressed streams."""
    if not isinstance(rows, list) or not rows or expanded[:len(rom)] != rom:
        return False
    cursor = len(rom)+(-len(rom)%16)+16
    if any(expanded[len(rom):cursor]):
        return False
    for row in rows:
        if not isinstance(row, dict) or type(row.get('offset')) is not int or type(row.get('size')) is not int:
            return False
        payload = _payload(rom, row)
        if not payload:
            return False
        aligned = (cursor+15)&~15
        if any(expanded[cursor:aligned]) or expanded[aligned:aligned+len(payload)] != payload:
            return False
        cursor = aligned+len(payload)
    end = (cursor+15)&~15
    return end == len(expanded) and not any(expanded[cursor:end])


def add(config, workdir, image, rom, taken, rows, log):
    from .loader_code import complete

    expanded = bytearray(rom)
    expanded.extend(bytes(-len(expanded)%16+16))
    programs = []
    for row in rows:
        payload = _payload(rom, row)
        if not payload:
            return None
        expanded.extend(bytes(-len(expanded)%16))
        start = len(expanded)
        expanded.extend(payload)
        end = len(expanded)
        table = dict(rom_start=start,rom_end=end,vram=row['vram'])
        found = complete(expanded, [], [(start,end,row['vram'])], taken, log,
                         table_ranges=[table], entries=tuple(row['calls'])+(row['vram'],))
        if len(found) < 4:
            return None
        text_start = min(lo for lo,_,_ in found)
        if not start <= text_start < end:
            return None
        last = max(hi for _,hi,_ in found)
        last = _contiguous_tail(expanded, last, end, row['vram']+last-start)
        text_end = min(end, (last+15)&~15)
        programs.append((row,start,text_start,text_end,end))
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = [segment for segment in cfg['segments'] if not isinstance(segment,list) or len(segment) != 1]
    segments.append(dict(type='bin',start=len(rom)))
    for index,(row,start,text_start,text_end,end) in enumerate(programs):
        name = f'runtime_program_{index:04d}'
        parts = ([[start,'bin',name+'_prefix']] if text_start > start else []) + [[text_start,'c',name]]
        if text_end < end:
            parts.append([text_end,'data',name])
        segments.append(dict(name=name,type='code',start=start,vram=row['vram'],subsegments=parts))
    expanded.extend(bytes(-len(expanded)%16))
    segments.append([len(expanded)])
    filename = f'{Path(image).stem}.runtime{Path(image).suffix}'
    (workdir/filename).write_bytes(expanded)
    cfg['options']['target_path'] = filename
    cfg['sha1'] = hashlib.sha1(expanded).hexdigest()
    cfg['segments'] = segments
    config.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding='utf-8')
    (workdir/MANIFEST).write_text(json.dumps(rows),encoding='utf-8')
    coverage = workdir/'analysis-coverage.json'
    try:
        notes = json.loads(coverage.read_text(encoding='utf-8')) if coverage.is_file() else []
    except (OSError, ValueError):
        notes = []
    if not isinstance(notes, list):
        notes = []
    notes = [note for note in notes if isinstance(note, str)]
    notes += [f'Includes {len(programs)} decompressed executable programs corroborated by runtime call targets.',
              'Code outside detected bodies may remain. Original compressed cartridge bytes are retained.']
    coverage.write_text(json.dumps(list(dict.fromkeys(notes))), encoding='utf-8')
    log(f'Unpacked {len(programs)} runtime-observed compressed programs for analysis')
    return filename
