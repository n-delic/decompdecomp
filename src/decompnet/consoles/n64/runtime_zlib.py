"""Recover a compressed executable from independently observed loaded call targets."""
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

import yaml

from decompassets.n64 import paired_resources

_HEADER = re.compile(rb'\x80[\x00-\x3f]..\x78[\x01\x5e\x9c\xda]', re.DOTALL)
MANIFEST = 'runtime-zlib.json'
_RAW_LIMIT = 8 * 1024 * 1024


def _raw_deflate(data):
    """Decode a complete, bounded raw stream and report the bytes it consumed."""
    try:
        decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        payload = decoder.decompress(data, _RAW_LIMIT + 1)
    except zlib.error:
        return None
    if not decoder.eof or len(payload) > _RAW_LIMIT:
        return None
    return payload, len(data) - len(decoder.unused_data)


def _payload(rom, row):
    if row.get('codec') == 'chunked_mio0':
        from .runtime_mio_chunks import decode
        return decode(rom, row)
    if (type(row.get('offset')) is not int or type(row.get('size')) is not int
            or not 0 <= row['offset'] < row['offset']+row['size'] <= len(rom)):
        return None
    if row.get('codec', 'zlib') == 'zlib':
        return paired_resources.unpack_zlib(rom, row['offset'], row['size'])
    if row.get('codec') == 'raw_deflate':
        decoded = _raw_deflate(memoryview(rom)[row['offset']:row['offset']+row['size']])
        if decoded and decoded[1] == row['size'] and len(decoded[0]) == row.get('expanded'):
            return decoded[0]
        return None
    if row.get('codec') == 'ring_lzss':
        from .runtime_ring import decode
        decoded = decode(memoryview(rom)[row['offset']:row['offset']+row['size']])
        if decoded and decoded[1] == row['size'] and len(decoded[0]) == row.get('expanded'):
            return decoded[0]
        return None
    if row.get('codec') == 'mio0':
        from .runtime_mio import unpack
        decoded = unpack(rom[:row['offset']+row['size']], row['offset'])
        if decoded and decoded[1] == row['size'] and len(decoded[0]) == row.get('expanded'):
            return decoded[0]
        return None
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

    def consider(payload, row):
        if payload is None or len(payload) < 0x10000:
            return
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
                candidates.append(dict(row, vram=base, calls=sorted(calls)))

    for number, match in enumerate(_HEADER.finditer(rom)):
        if number % 128 == 0:
            check()
        offset = match.start()
        if offset % 2:
            continue
        size = struct.unpack_from('>I', rom, offset)[0] & 0x7fffffff
        payload = paired_resources.unpack_zlib(rom, offset, size)
        consider(payload, dict(offset=offset, size=size))
    # Headerless streams have no signature. Aligned starts following padding give a bounded
    # shortlist; exact decompression, three calls and the observed RAM prefix decide identity.
    for number, match in enumerate(re.finditer(rb'\x00{8,}', rom)):
        if number % 128 == 0:
            check()
        end = match.end()
        # A stored DEFLATE block can itself begin with zero bytes. Its aligned
        # start can therefore be just inside the padding run, not at its end.
        offset = end & ~15
        if (end == len(rom) or end - offset > 4 or offset - match.start() < 8
                or rom[offset] & 6 == 6):
            continue
        decoded = _raw_deflate(memoryview(rom)[offset:offset+_RAW_LIMIT * 2])
        if decoded:
            payload, size = decoded
            consider(payload, dict(codec='raw_deflate', offset=offset, size=size, expanded=len(payload)))
    return candidates if len(candidates) == 1 else []


def verify_analysis(rom, expanded, rows):
    """Prove the appended bytes are exactly the declared original compressed streams."""
    if not isinstance(rows, list) or not rows or expanded[:len(rom)] != rom:
        return False
    base_size = rows[0].get('base_size', len(rom)) if isinstance(rows[0], dict) else 0
    if type(base_size) is not int or not len(rom) <= base_size <= len(expanded):
        return False
    if base_size != len(rom):
        from .boot_gzip import verify_analysis as verify_boot
        ranges = rows[0].get('boot_ranges')
        if (not isinstance(ranges, list) or not ranges
                or any(not isinstance(r, (list, tuple)) or len(r) != 3
                       or any(type(n) is not int for n in r) for r in ranges)
                or any(not 0 <= start < end <= len(rom) or not 0x80000000 <= vram < 0x80800000
                       for start, end, vram in ranges)
                or not verify_boot(rom, expanded[:base_size], ranges)):
            return False
    cursor = base_size+(-base_size%16)+16
    if any(expanded[base_size:cursor]):
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


def add(config, workdir, image, rom, taken, rows, log, *, base_image=None, boot_ranges=None):
    from .loader_code import complete

    base = rom if base_image is None else base_image
    if base != rom:
        from .boot_gzip import verify_analysis as verify_boot
        if not boot_ranges or not verify_boot(rom, base, boot_ranges):
            return None
        rows = [dict(row) for row in rows]
        rows[0]['base_size'] = len(base)
        rows[0]['boot_ranges'] = boot_ranges
    expanded = bytearray(base)
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
    segments.append(dict(type='bin',start=len(base)))
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
