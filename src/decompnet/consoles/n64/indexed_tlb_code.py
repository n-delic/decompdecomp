"""Recover paged executable code when its boot loader proves the index and mapping."""
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path

import yaml

from decompassets.n64 import rzip_catalog

from ...arch.mips_assets import read


def _constant(value):
    return value.get('value') if value and value.get('kind') == 'constant' else None


def _key(words):
    """Find a 32-bit XOR key that is actually used in the following loop."""
    found = set()
    for i, first in enumerate(words[:-1]):
        if first >> 26 != 15:
            continue
        reg = (first >> 16) & 31
        second = words[i+1]
        if not reg or second >> 26 != 13 or (second >> 21) & 31 != reg or (second >> 16) & 31 != reg:
            continue
        if any(w >> 26 == 0 and w & 63 == 38 and reg in ((w >> 21) & 31, (w >> 16) & 31)
               for w in words[i+2:i+20]):
            found.add(((first & 0xffff) << 16) | (second & 0xffff))
    return found


def _code_ranges(words):
    """Find a loader's end-minus-base calculation followed by 4 KiB paging."""
    values = {}
    candidates = set()
    for i, word in enumerate(words):
        op, rs, rt = word >> 26, (word >> 21) & 31, (word >> 16) & 31
        if op == 15:
            values[rt] = (word & 0xffff) << 16
        elif op in (9, 13) and rs in values:
            imm = word & 0xffff
            values[rt] = ((values[rs] + (imm if imm < 0x8000 else imm-0x10000)) & 0xffffffff
                          if op == 9 else values[rs] | imm)
        elif op == 0 and word & 63 == 35 and rs in values and rt in values:
            end, base = values[rs], values[rt]
            if (0x10000000 <= base < end <= 0x20000000 and base % 0x1000 == 0
                    and any(w >> 26 == 0 and w & 63 == 2 and (w >> 6) & 31 == 12
                            for w in words[i+1:i+12])):
                candidates.add((base, end))
    return candidates


def decode(rom, start, key, base, end, data_vram, table_size):
    """Check the complete index and every bounded page, never a partial archive."""
    if (not 0 <= start < len(rom)-8 or not 0x10000000 <= base < end <= 0x20000000
            or base % 0x1000 or end % 4 or not 0x80000400 <= data_vram < 0x80800000
            or not 16 <= table_size <= 0x10000 or table_size % 4):
        return None
    count = (end-base+0xfff)//0x1000
    if count < 4 or (count+1)*4 > table_size or start+4+table_size > len(rom):
        return None
    data_start = struct.unpack_from('>I', rom, start)[0]
    offsets = [struct.unpack_from('>I', rom, start+4+4*i)[0] ^ key for i in range(count+1)]
    if (offsets[0] < 4+4*(count+1) or offsets != sorted(set(offsets))
            or offsets[-1] > data_start or start+data_start >= len(rom)):
        return None
    pages = []
    for index, (lo, hi) in enumerate(zip(offsets, offsets[1:])):
        chunk = rzip_catalog._chunk(rom, start+lo, start+hi)
        expected = min(0x1000, end-base-index*0x1000)
        # The loader rounds compressed members to two-byte boundaries; the
        # unused byte need not be zero and stays in the original cartridge.
        if not chunk or not 0 <= start+hi-chunk[0] <= 1 or len(chunk[1]) != expected:
            return None
        pages.append(chunk[1])
    data = rzip_catalog._chunk(rom, start+data_start, len(rom))
    if not data or data_vram+len(data[1]) > 0x80800000:
        return None
    return b''.join(pages), data[1]


def find(rom, boot_ranges):
    """Bind the archive, XOR key, code bounds and data address to boot instructions."""
    from .boot_gzip import _coalesce_boot_ranges

    candidates = []
    for lo, hi, vram in _coalesce_boot_ranges(boot_ranges):
        if lo >= 0x10000 or hi <= lo:
            continue
        hi = min(hi, 0x10000, len(rom))
        events = [e for e in read(vram, rom[lo:hi])['events'] if e.get('kind') == 'call']
        for first in events:
            args = first['args']
            source, dest, size = map(_constant, args[:3])
            if source is None or dest is None or size != 16:
                continue
            for later in events:
                if not first['instruction'] < later['instruction'] <= first['instruction']+0x200:
                    continue
                if later['target'] != first['target'] or _constant(later['args'][0]) != source+4:
                    continue
                # The first DMA briefly uses this buffer for the header. A
                # separate loader call must also name it as its output.
                if not any(first['instruction'] < e['instruction'] < later['instruction']
                           and e['target'] != first['target'] and _constant(e['args'][1]) == dest
                           for e in events):
                    continue
                table_size = _constant(later['args'][2])
                if table_size is None:
                    continue
                at = lo+later['instruction']-vram
                before = rom[max(lo, at-0x100):at]
                after = rom[at:min(hi, at+0x100)]
                words_before = struct.unpack(f'>{len(before)//4}I', before[:len(before)//4*4])
                words_after = struct.unpack(f'>{len(after)//4}I', after[:len(after)//4*4])
                for base, end in _code_ranges(words_before):
                    for key in _key(words_after):
                        if payload := decode(rom, source, key, base, end, dest, table_size):
                            candidates.append(dict(start=source, key=key, base=base, end=end,
                                                   data_vram=dest, table_size=table_size, payload=payload))
    return candidates[0] if len(candidates) == 1 else None


def verify_analysis(rom, expanded, boot_ranges):
    """Require the appended bytes to be exactly the loader's original pages and data."""
    if expanded[:len(rom)] != rom or not (program := find(rom, boot_ranges)):
        return False
    cursor = len(rom)+(-len(rom)%16)+16
    if any(expanded[len(rom):cursor]):
        return False
    for payload in program['payload']:
        aligned = (cursor+15)&~15
        if any(expanded[cursor:aligned]) or expanded[aligned:aligned+len(payload)] != payload:
            return False
        cursor = aligned+len(payload)
    end = (cursor+15)&~15
    return end == len(expanded) and not any(expanded[cursor:end])


def _after_traps(code, base):
    """Keep a called entry after an isolated trap from being swallowed by splat."""
    from ...arch.mips import jal_targets

    words = struct.unpack(f'>{len(code)//4}I', code[:len(code)//4*4])
    calls = Counter(jal_targets(words, base & 0xf0000000))
    cuts = []
    for index, word in enumerate(words):
        if word & 0xfc00003f != 12:  # syscall
            continue
        after = index+1
        while after < min(len(words), index+9) and words[after] == 0:
            after += 1
        if after > index+1 and calls[base+4*after] >= 3:
            cuts.append(4*after)
    return cuts


def add(config, workdir, image, rom, boot_ranges, log):
    if not (program := find(rom, boot_ranges)):
        return None
    from ...rom import split

    code, data = program['payload']
    # The loader proves the addresses, but an existing boot mapping must not
    # overlap either range. Ambiguous banks need separate overlay evidence.
    mappings = [(s.rom_start, s.rom_end, s.vram) for s in split.segments_from_config(config)]
    if any(v < address+size and address < v+hi-lo for lo, hi, v in mappings
           for address, size in ((program['base'], len(code)), (program['data_vram'], len(data)))):
        return None
    expanded = bytearray(rom)
    expanded.extend(bytes(-len(expanded)%16+16))
    code_start = len(expanded)
    expanded.extend(code)
    expanded.extend(bytes(-len(expanded)%16))
    data_start = len(expanded)
    expanded.extend(data)
    expanded.extend(bytes(-len(expanded)%16))
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = [s for s in cfg['segments'] if not isinstance(s, list) or len(s) != 1]
    segments.extend([
        dict(type='bin', start=len(rom)),
        dict(name='paged_program', type='code', start=code_start, vram=program['base'],
             subsegments=[[code_start, 'c', 'paged_program']]
             + [[code_start+offset, 'c', f'paged_program_{offset:X}'] for offset in _after_traps(code, program['base'])]),
        dict(name='paged_data', type='code', start=data_start, vram=program['data_vram'],
             subsegments=[[data_start, 'data', 'paged_data']]),
        [len(expanded)],
    ])
    filename = f'{Path(image).stem}.paged{Path(image).suffix}'
    (workdir/filename).write_bytes(expanded)
    cfg['options']['target_path'] = filename
    cfg['sha1'] = hashlib.sha1(expanded).hexdigest()
    cfg['segments'] = segments
    config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
    (workdir/'analysis-coverage.json').write_text(json.dumps([
        'Includes the loader-verified paged program and its separately loaded data.',
        'Runtime RAM observations do not yet trace calls through this virtual TLB mapping.',
        'Code outside detected bodies may remain. Original compressed cartridge bytes are retained.']))
    log(f'Unpacked {(len(code)+0xfff)//0x1000} loader-indexed code pages and their data')
    return filename
