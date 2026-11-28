"""Carry byte-verified existing entries through a newly generated ROM split."""

import copy
import hashlib
import json
import re

import yaml

from ...rom import split

_ALIGNMENT_BEFORE = 'd10940a77e59962afa3d224185117c2feb94baa71d8393e2da2591840af3438a'
_ALIGNMENT_AFTER = frozenset(('4b90fb5a0c5466bcb0496316b8d40676d437a3d8e0b619cff839c132c0631f85',
                              '904fe2e3576c8a791862c8ff38ab006c55986a5763e9a8db2effd85daefebcb2',
                              'f2b22a0984e7ff4a962f47512b161a3fa0990ef013bd431c18d68d1d905d1ea1'))


def _payload_only_alignment_entries(result, image, rows, payloads, check):
    """Reject a stale fallthrough entry only with independently corroborated SDK alignment."""
    from ...arch.mips_references import references

    # These complete linked bodies occur on both sides of an eight-byte,
    # 16-alignment gap. Independent images have different bytes in that gap.
    if not payloads:
        return set()
    by_start = {(fn.rom, fn.vram): fn for fn in result.functions if fn.rom is not None and fn.vram is not None}
    by_end = {(fn.rom + fn.size, fn.vram + fn.size): fn for fn in result.functions
              if fn.rom is not None and fn.vram is not None}
    candidates = {}
    for row in rows:
        lo, addr, size = (row.get(k) for k in ('rom_offset', 'vram', 'size'))
        if (type(lo) is not int or type(addr) is not int or size != 8 or lo % 16 != 8 or addr % 16 != 8
                or row.get('status') != 'open' or row.get('matched_source')
                or not re.fullmatch(r'func_[0-9a-fA-F]{8}(?:_\w+)?', row.get('name', ''))):
            continue
        try:
            meta = json.loads(row.get('meta') or '{}')
        except (TypeError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get('boundary_decision') or meta.get('real') or meta.get('debug'):
            continue
        head, tail = by_end.get((lo, addr)), by_start.get((lo + size, addr + size))
        if (head is None or tail is None or head.size != 36 or tail.size != 256
                or row.get('target') != image[lo:lo+size]
                or hashlib.sha256(image[head.rom:lo]).hexdigest() != _ALIGNMENT_BEFORE
                or hashlib.sha256(image[tail.rom:tail.rom+tail.size]).hexdigest() not in _ALIGNMENT_AFTER):
            continue
        seen = False
        for entry in (addr, addr+4):
            needle, pos = entry.to_bytes(4, 'big'), 0
            while (at := image.find(needle, pos)) >= 0:
                pos = at + 1
                if at % 4:
                    continue
                if not any(start <= at and at+4 <= end for start, end in payloads):
                    break
                if entry == addr:
                    seen = True
            else:
                continue
            break
        else:
            if seen:
                candidates[addr] = (row['name'], tail.vram)
    if not candidates:
        return set()
    incoming, called = set(), set()
    for fn in result.functions:
        check()
        if fn.rom is None or fn.vram is None or fn.size <= 0 or not 0 <= fn.rom < fn.rom+fn.size <= len(image):
            continue
        for target, _, kind in references(image, fn.rom, fn.rom+fn.size, fn.vram):
            for addr in candidates:
                if addr <= target < addr+8:
                    incoming.add(addr)
            if kind == 'call':
                called.add(target)
    return {name for addr, (name, tail) in candidates.items() if addr not in incoming and tail in called}


def _uncompressed_pointer(image, low, high):
    """Find an aligned target pointer, excluding verified compressed bytes with no decoded pointer."""
    from decompassets import rnc

    if not 0 <= low < high <= 0x100000000:
        return True
    prefixes = b'|'.join(re.escape(value.to_bytes(2, 'big')) for value in range(low >> 16, ((high-1) >> 16)+1))
    pattern = re.compile(b'(?=(' + prefixes + b')..)', re.S)
    compressed = re.compile(rb'(?:RNC|ERZ)[\x01\x02]')
    for hit in pattern.finditer(image):
        at = hit.start()
        if at % 4 or at+4 > len(image):
            continue
        value = int.from_bytes(image[at:at+4], 'big')
        if not low <= value < high or (value-low) % 4:
            continue
        safe = False
        # Only a complete, checksummed block can explain an apparent pointer
        # as compressed bytes. Also inspect its decoded payload for the target.
        for header in compressed.finditer(image, max(0, at-4*1024*1024-18), at):
            start = header.start()
            if start+18 > at or start+18 > len(image):
                continue
            packed = int.from_bytes(image[start+8:start+12], 'big')
            if not 0 < packed <= 4*1024*1024 or not start+18 <= at < start+18+packed:
                continue
            try:
                decoded, size = rnc.unpack_at(image, start, max_output=4*1024*1024, max_packed=4*1024*1024)
            except rnc.RncError:
                continue
            if at+4 > start+size:
                continue
            # Decoded data may be loaded at a different alignment; inspect all
            # byte offsets conservatively, not only aligned ones.
            if any(low <= int.from_bytes(decoded[m.start():m.start()+4], 'big') < high
                   and (int.from_bytes(decoded[m.start():m.start()+4], 'big')-low) % 4 == 0
                   for m in pattern.finditer(decoded) if m.start()+4 <= len(decoded)):
                return True
            safe = True
            break
        if not safe:
            return True
    return False


def _joined_bodies(result, image, rows):
    """Keep an unchanged prior join only while its absorbed tail has no outside entry."""
    from ...arch.mips_references import references

    old = {row['name']:dict(row) for row in rows}
    fresh = {fn.name:fn for fn in result.functions}
    replacements = []
    for tail_name, tail_old in old.items():
        try:
            tail_meta = json.loads(tail_old.get('meta') or '{}')
        except (ValueError, TypeError):
            continue
        if not isinstance(tail_meta, dict):
            continue
        head_name = tail_meta.get('boundary_absorbed_by')
        head_old = old.get(head_name)
        head, tail = fresh.get(head_name), fresh.get(tail_name)
        if not head_old or not head or not tail or tail_old.get('gone_mark') is None:
            continue
        try:
            head_meta = json.loads(head_old.get('meta') or '{}')
        except (ValueError, TypeError):
            continue
        decision = head_meta.get('boundary_decision', {}) if isinstance(head_meta, dict) else {}
        if not isinstance(decision, dict):
            continue
        start, size = head.rom, head.size+tail.size
        if (decision.get('action') != 'join_previous' or head.rom is None or tail.rom is None
                or head.rom+head.size != tail.rom or head.vram+head.size != tail.vram
                or head.segment != tail.segment or head.entry_aliases or tail.entry_aliases
                or head_old.get('rom_offset') != start or head_old.get('vram') != head.vram
                or head_old.get('size') != size or head_old.get('target') != image[start:start+size]
                or tail_old.get('rom_offset') != tail.rom or tail_old.get('vram') != tail.vram
                or tail_old.get('size') != tail.size or tail_old.get('target') != image[tail.rom:tail.rom+tail.size]
                or tail.size > 0x10000
                or any(d.rom is not None and d.size and d.rom < start+size and start < d.rom+d.size
                       for d in getattr(result,'data', ()))):
            continue
        if _uncompressed_pointer(image, tail.vram, tail.vram+tail.size):
            continue
        all_edges = [(target,source,kind) for fn in result.functions if fn.rom is not None
                     for target,source,kind in references(image,fn.rom,fn.rom+fn.size,fn.vram)]
        padding = []
        for row in old.values():
            try:
                meta = json.loads(row.get('meta') or '{}')
            except (ValueError, TypeError):
                continue
            if not isinstance(meta, dict) or not isinstance(meta.get('boundary_decision'), dict):
                continue
            at, addr, length = row.get('rom_offset'), row.get('vram'), row.get('size')
            if (row.get('gone_mark') is None or meta.get('boundary_classification') != 'padding'
                    or meta.get('boundary_decision',{}).get('action') != 'trim_start'
                    or type(at) is not int or type(addr) is not int or type(length) is not int
                    or not 0 < length <= 0x400 or row.get('target') != image[at:at+length]
                    or not any(at <= source < at+length and tail.vram <= target < tail.vram+tail.size
                               for target,source,_ in all_edges)
                    or any(addr <= target < addr+length and not at <= source < at+length
                           for target,source,_ in all_edges)):
                continue
            words = b'|'.join(re.escape(address.to_bytes(4,'big')) for address in range(addr,addr+length,4))
            if not any(match.start()%4 == 0 for match in re.finditer(b'(?=('+words+b'))',image)):
                padding.append((at,at+length))
        edges = [(source,kind) for target,source,kind in all_edges
                 if tail.vram <= target < tail.vram+tail.size
                 and not any(lo <= source < hi for lo,hi in padding)]
        if (not any(kind == 'branch' and start <= source < tail.rom for source,kind in edges)
                or any(kind != 'branch' or not start <= source < start+size for source,kind in edges)):
            continue
        replacements.append((head,tail,split.function_from_bytes(head.name,head_old['target'],start,head.vram,
                                                                  head.segment,True)))
    used = set()
    archives = set()
    for head,tail,joined in replacements:
        if id(head) in used or id(tail) in used:
            continue
        used.update((id(head),id(tail)))
        archives.add(tail.name)
        result.functions = [fn for fn in result.functions if fn is not head and fn is not tail] + [joined]
    result.retained_join_archives = archives
    return len(used)//2


def _require_preserved_joins(result, image, rows):
    """Do not silently discard a previously joined body or its archived tail."""
    from ...network import NetworkError

    old = {row['name']: dict(row) for row in rows}
    archives = getattr(result, 'retained_join_archives', set())
    for tail in old.values():
        try:
            meta = json.loads(tail.get('meta') or '{}')
        except (TypeError, ValueError):
            continue
        head = old.get(meta.get('boundary_absorbed_by')) if isinstance(meta, dict) else None
        if not head or tail.get('gone_mark') is None or head.get('gone_mark') is not None:
            continue
        try:
            decision = json.loads(head.get('meta') or '{}').get('boundary_decision', {})
        except (AttributeError, TypeError, ValueError):
            continue
        start, size, addr = head.get('rom_offset'), head.get('size'), head.get('vram')
        if (not isinstance(decision, dict) or decision.get('action') != 'join_previous'
                or type(start) is not int or type(size) is not int or type(addr) is not int
                or size <= 0 or start < 0 or start+size > len(image)
                or head.get('target') != image[start:start+size]):
            continue
        if any(fn.rom == start and fn.vram == addr and fn.size == size for fn in result.functions):
            archives.add(tail['name'])
            continue
        raise NetworkError(f"Reimport could not preserve the previously joined function {head['name']}. "
                           "The existing project was left unchanged; review its boundaries before reimporting.")
    result.retained_join_archives = archives


def _called_suffixes(result, image, rows, log):
    """Find old called entries swallowed by a new short, fallthrough prefix."""
    from collections import defaultdict

    from ...arch.mips_references import references

    check = getattr(log, 'check_cancel', lambda: None)
    names = {fn.name for fn in result.functions}
    old_names = {row['name'] for row in rows}
    old_starts = {(row['rom_offset'], row['vram']) for row in rows}
    proven_data = [(d.rom, d.vram, d.size) for d in getattr(result, 'data', ())
                   if getattr(d, 'boundary_proven', False) and d.rom is not None and d.vram is not None]
    by_end = defaultdict(list)
    for fn in result.functions:
        if fn.rom is not None and fn.vram is not None:
            by_end[(fn.rom + fn.size, fn.vram - fn.rom)].append(fn)
    candidates = []
    for raw in rows:
        check()
        row = dict(raw)
        lo, size, vram, target = (row.get(k) for k in ('rom_offset', 'size', 'vram', 'target'))
        if (row.get('name') in names or type(lo) is not int or type(size) is not int or type(vram) is not int
                or size <= 0 or size % 4 or lo < 0 or lo + size > len(image)
                or not isinstance(target, bytes) or target != image[lo:lo + size]
                or any(vram-lo == address-start and start < lo+size and lo < start+length
                       for start, address, length in proven_data)):
            continue
        owners = [fn for fn in by_end[(lo + size, vram - lo)] if fn.rom < lo]
        if len(owners) != 1:
            continue
        owner = owners[0]
        if (not 4 <= lo - owner.rom <= 64 or owner.name in old_names
                or (owner.rom, owner.vram) in old_starts):
            continue
        if any(other is not raw and other['rom_offset'] is not None and other['size'] is not None
               and other['rom_offset'] < lo + size and lo < other['rom_offset'] + other['size']
               for other in rows):
            continue
        candidates.append((row, owner))
    if not candidates:
        return []
    by_shift = defaultdict(dict)
    for row, owner in candidates:
        by_shift[row['vram'] - row['rom_offset']][row['vram']] = owner
    incoming = set()
    for fn in result.functions:
        check()
        if fn.rom is None or fn.vram is None or fn.size <= 0 or not 0 <= fn.rom < fn.rom+fn.size <= len(image):
            continue
        wanted = by_shift.get(fn.vram - fn.rom, {})
        if wanted:
            incoming.update(target for target, _, kind in references(image, fn.rom, fn.rom+fn.size, fn.vram)
                            if kind == 'call' and target in wanted and wanted[target] is not fn)
    return [(row, owner) for row, owner in candidates if row['vram'] in incoming]


def _covered_text(cfg, result, image, rows, log):
    """Recover a completely covered data section from independently called old bodies."""
    from collections import defaultdict

    from ...arch.mips import is_frame
    from ...arch.mips_gaps import extent
    from ...arch.mips_references import references

    check = getattr(log, 'check_cancel', lambda: None)
    mappings = split.segments_from_config_data(cfg)
    data_sections = []
    for seg in cfg.get('segments', []):
        if not isinstance(seg, dict) or seg.get('type') != 'code' or not isinstance(seg.get('vram'), int):
            continue
        parent = next((s for s in mappings if s.rom_start == seg.get('start')), None)
        if parent is None:
            continue
        subs = [s for s in seg.get('subsegments', []) if isinstance(s, list) and len(s) >= 2]
        for index, sub in enumerate(subs):
            if sub[1] not in ('data', '.data', 'rodata', '.rodata'):
                continue
            end = subs[index+1][0] if index+1 < len(subs) else parent.rom_end
            if isinstance(sub[0], int) and isinstance(end, int) and sub[0] < end:
                data_sections.append((seg['start'], sub[0], end, parent))
    if not data_sections:
        return None
    incoming = defaultdict(list)
    for fn in result.functions:
        check()
        if fn.rom is None or fn.rom < 0 or fn.vram is None or fn.size <= 0 or fn.rom+fn.size > len(image):
            continue
        for target, at, kind in references(image, fn.rom, fn.rom+fn.size, fn.vram):
            incoming[target].append((at, kind))
    old_names = {r['name'] for r in rows}
    old_addresses = {(r['rom_offset'], r['vram']) for r in rows}
    current_names = {fn.name for fn in result.functions}
    proven = [(d.rom, d.rom+d.size) for d in getattr(result, 'data', ())
              if getattr(d, 'boundary_proven', False) and d.rom is not None]
    for parent_start, lo, hi, parent in data_sections:
        check()
        candidates = []
        for raw in rows:
            row = dict(raw)
            start, size, vram, target = (row.get(k) for k in ('rom_offset','size','vram','target'))
            if (row['name'] in current_names or type(start) is not int or type(size) is not int
                    or type(vram) is not int or size <= 0 or size % 4 or not lo <= start < hi
                    or start+size > parent.rom_end or vram != parent.vram+start-parent.rom_start
                    or not isinstance(target, bytes) or target != image[start:start+size]
                    or not is_frame(int.from_bytes(target[:4], 'big'))
                    or any(a < start+size and start < b for a,b in proven)):
                continue
            proof = extent(image, start, start+size, vram, 'big')
            if proof['direct_flow_closed'] and not proof['step_limit'] and proof['extent_bytes'] == size:
                candidates.append(row)
        candidates.sort(key=lambda row:row['rom_offset'])
        cursor, chosen = lo, []
        for row in candidates:
            if row['rom_offset'] != cursor:
                break
            chosen.append(row)
            cursor += row['size']
            if cursor >= hi:
                break
        if cursor < hi or len(chosen) < 3 or sum(any(kind == 'call' for _,kind in incoming[r['vram']])
                                                  for r in chosen) < 3:
            continue
        suppressed = []
        for fn in result.functions:
            if fn.rom is None:
                continue
            owners = [r for r in chosen if r['rom_offset'] < fn.rom < r['rom_offset']+r['size']
                      and fn.rom+fn.size <= r['rom_offset']+r['size']
                      and fn.vram == r['vram']+fn.rom-r['rom_offset']]
            if not owners:
                if any(r['rom_offset'] < fn.rom+fn.size and fn.rom < r['rom_offset']+r['size'] for r in chosen):
                    suppressed = None
                    break
                continue
            if (len(owners) != 1 or fn.name in old_names or (fn.rom, fn.vram) in old_addresses
                    or incoming[fn.vram]):
                suppressed = None
                break
            suppressed.append(fn)
        if suppressed is None:
            continue
        proposed = copy.deepcopy(cfg)
        segment = next(s for s in proposed['segments'] if isinstance(s, dict) and s.get('start') == parent_start)
        subs = segment['subsegments']
        section = next(s for s in subs if isinstance(s, list) and s and s[0] == lo)
        section[1] = 'c'
        # A new source unit inside an established body generated the false
        # suffix entry. Remove only its unreferenced boundary.
        suppressed_starts = {fn.rom for fn in suppressed}
        segment['subsegments'] = [s for s in subs if not (isinstance(s, list) and s and s[0] in suppressed_starts)]
        return proposed, chosen, suppressed
    return None


def _matched_module_entries(result, image, rows):
    """Retain non-overlapping saved module entries when their analysis bytes are unchanged."""
    names = {fn.name for fn in result.functions}
    ranges = [(fn.rom, fn.rom + fn.size) for fn in result.functions if fn.rom is not None]
    added = 0
    for raw in rows:
        row = dict(raw)
        if row.get('status') != 'matched' or row.get('gone_mark') or row.get('name') in names:
            continue
        try:
            meta = json.loads(row.get('meta') or '{}')['uso']
            section, start, size = meta['section'], meta['start'], meta['size']
            if type(section) is not int:
                continue
            bank_start = result.analysis_banks[str(section)]
            banks = [s for s in result.segments if s.name in (f'donor_uv_{section:X}', f'donor_uso_{section:X}')
                     and s.rom_start == bank_start]
            if len(banks) != 1:
                continue
            bank = banks[0]
        except (KeyError, TypeError, ValueError, StopIteration):
            continue
        lo, vram, target = row.get('rom_offset'), row.get('vram'), row.get('target')
        if (type(bank_start) is not int or type(start) is not int or type(size) is not int
                or start < 0 or size <= 0 or size % 4 or vram != start or row.get('size') != size
                or lo != bank_start + start or lo + size > bank.rom_end or not isinstance(target, bytes)
                or target != image[lo:lo + size] or any(a < lo + size and lo < b for a, b in ranges)):
            continue
        fn = split.function_from_bytes(row['name'], target, lo, vram, bank.name)
        fn.uso = meta
        result.functions.append(fn)
        names.add(row['name'])
        ranges.append((lo, lo + size))
        added += 1
    return added


def preserve(net, project, result, image, rows, log=print):
    """Stage non-overlapping old entries the new split missed, then keep their original identities."""
    from ...rom.export import _export_segments, _fallthrough_entries, _segments, _symbol_lines
    from . import code, runtime_staging

    check = getattr(log, 'check_cancel', lambda: None)
    cfg = yaml.safe_load(result.config_path.read_text(encoding='utf-8'))
    restored = _matched_module_entries(result, image, rows)
    if restored:
        log(f'Kept {restored} matched module entries with unchanged bytes and no overlapping functions.')
    joined = _joined_bodies(result, image, rows)
    _require_preserved_joins(result, image, rows)
    restored += joined
    if joined:
        log(f'Kept {joined} previously proven joins with unchanged bytes and no outside entries.')
    while proposal := _covered_text(cfg, result, image, rows, log):
        proposed, selected, suppressed = proposal
        remaining = [fn for fn in result.functions if all(fn is not gone for gone in suppressed)]
        current = [dict(name=fn.name, rom_offset=fn.rom, size=fn.size, vram=fn.vram,
                        target=b''.join(line.word.to_bytes(4, 'big') for line in fn.lines))
                   for fn in remaining if fn.rom is not None]
        bodies = [dict(rom_start=r['rom_offset'],rom_end=r['rom_offset']+r['size'],vram=r['vram']) for r in selected]
        code._cut_inside(proposed, [(r['rom_offset'],r['rom_offset']+r['size'],r['vram']) for r in selected])

        def prepare_text(config, staged, current=current, selected=selected):
            chosen = {(r['rom_start'],r['vram']) for r in staged}
            population = current + [r for r in selected if (r['rom_offset'],r['vram']) in chosen]
            config = _export_segments(config, population)
            lines = _symbol_lines(net, project, population, segments=_segments(config),
                                  unsized=_fallthrough_entries(population, net.console(project)))
            return config, lines, current

        if not runtime_staging.verify(result.config_path, image, proposed, bodies, log, prepare=prepare_text):
            break
        check()
        split.write_config_atomic(result.config_path, yaml.safe_dump(proposed, sort_keys=False))
        units = split.source_units(result.config_path)
        for row in selected:
            start, size, vram = row['rom_offset'], row['size'], row['vram']
            owners = [name for lo, hi, addr, name in units if lo <= start < start+size <= hi
                      and addr+start-lo == vram]
            unit = owners[0] if len(owners) == 1 else row.get('unit') or f'code_{vram:08X}'
            remaining.append(split.function_from_bytes(row['name'],row['target'],start,vram,unit,True))
        result.functions = remaining
        if hasattr(result, 'data'):
            result.data = [d for d in result.data if getattr(d, 'boundary_proven', False) or d.rom is None or not any(
                r['rom_offset'] < d.rom+d.size and d.rom < r['rom_offset']+r['size'] for r in selected)]
        result.segments = split.segments_from_config(result.config_path)
        cfg = proposed
        restored += len(selected)
        log(f'Kept {len(selected)} independently corroborated existing functions that the new split marked as data.')
    paired = _called_suffixes(result, image, rows, log)
    if paired:
        owners = {id(owner) for _, owner in paired}
        remaining = [fn for fn in result.functions if id(fn) not in owners]
        current = [dict(name=fn.name, rom_offset=fn.rom, size=fn.size, vram=fn.vram,
                        target=b''.join(line.word.to_bytes(4, 'big') for line in fn.lines))
                   for fn in remaining if fn.rom is not None]
        parts = []
        for row, owner in paired:
            prefix_size = row['rom_offset'] - owner.rom
            parts.append(dict(name=owner.name, rom_offset=owner.rom, size=prefix_size, vram=owner.vram,
                              target=image[owner.rom:row['rom_offset']], unit=owner.segment))
            parts.append(dict(row, unit=owner.segment))
        bodies = [dict(rom_start=row['rom_offset'], rom_end=row['rom_offset'] + row['size'], vram=row['vram'])
                  for row in parts]
        proposed = copy.deepcopy(cfg)
        code._cut_inside(proposed, [(r['rom_start'], r['rom_end'], r['vram']) for r in bodies])

        def prepare_suffixes(config, staged):
            population = current + (parts if staged else [])
            config = _export_segments(config, population)
            lines = _symbol_lines(net, project, population, segments=_segments(config),
                                  unsized=_fallthrough_entries(population, net.console(project)))
            return config, lines, current

        log(f'Checking {len(paired)} existing called entries enclosed by new boundaries.')
        if runtime_staging.verify(result.config_path, image, proposed, bodies, log, prepare=prepare_suffixes):
            check()
            split.write_config_atomic(result.config_path, yaml.safe_dump(proposed, sort_keys=False))
            for row in parts:
                remaining.append(split.function_from_bytes(row['name'], row['target'], row['rom_offset'],
                                                           row['vram'], row['unit'], True))
            result.functions = remaining
            result.segments = split.segments_from_config(result.config_path)
            cfg = proposed
            restored += len(paired)
            log(f'Kept {len(paired)} byte-verified called entries enclosed by new boundaries.')
    current = [dict(name=fn.name, rom_offset=fn.rom, size=fn.size, vram=fn.vram,
                    target=b''.join(line.word.to_bytes(4, 'big') for line in fn.lines))
               for fn in result.functions if fn.rom is not None]
    names = {fn.name for fn in result.functions}
    ranges = [(fn['rom_offset'], fn['rom_offset'] + fn['size']) for fn in current]
    # A fresh boundary repair can prove that a former function is data. Byte
    # equality alone cannot overturn that proof; importing records the old C
    # in recovery notes when its function disappears.
    proven_data = [(d.rom, d.vram, d.size) for d in getattr(result, 'data', ())
                   if getattr(d, 'boundary_proven', False) and d.rom is not None and d.vram is not None]
    from .boot_mapping import preserved_startup_trap

    old_rows = [dict(row) for row in rows]
    stale = set()
    if net is not None and any(row.get('size') == 8 and row.get('status') == 'open' for row in old_rows):
        payloads = net.console(project).import_nonpointer_ranges(image, result.segments)
        stale = _payload_only_alignment_entries(result, image, old_rows, payloads, check)
        if stale:
            log(f'Left {len(stale)} stale alignment entries out; their saved C remains in recovery notes.')
    eligible = []
    for raw in old_rows:
        check()
        row = dict(raw)
        lo, size, vram, target = (row.get(key) for key in ('rom_offset', 'size', 'vram', 'target'))
        if (row.get('name') in names or row.get('name') in stale or type(lo) is not int or type(size) is not int or type(vram) is not int
                or size <= 0 or size % 4 or lo < 0 or lo + size > len(image)
                or any(vram-lo == address-start and start < lo+size and lo < start+length
                       for start,address,length in proven_data)
                or preserved_startup_trap(cfg, image, lo, vram, size)
                or not isinstance(target, bytes) or target != image[lo:lo + size]
                or any(a < lo + size and lo < b for a, b in ranges)
                or not code._cut_ok(cfg, (lo, lo + size, vram))):
            continue
        eligible.append(row)
    # Competing old entries need boundary review; neither may be silently chosen.
    eligible = [row for row in eligible if not any(other is not row
                and other['rom_offset'] < row['rom_offset'] + row['size']
                and row['rom_offset'] < other['rom_offset'] + other['size'] for other in eligible)]
    if not eligible:
        return restored
    proposed = copy.deepcopy(cfg)
    code._cut_inside(proposed, [(r['rom_offset'], r['rom_offset'] + r['size'], r['vram']) for r in eligible])
    candidates = [dict(rom_start=r['rom_offset'], rom_end=r['rom_offset'] + r['size'], vram=r['vram'])
                  for r in eligible]

    def prepare(config, selected):
        chosen = {(r['rom_start'], r['vram']) for r in selected}
        population = current + [r for r in eligible if (r['rom_offset'], r['vram']) in chosen]
        config = _export_segments(config, population)
        lines = _symbol_lines(net, project, population, segments=_segments(config),
                              unsized=_fallthrough_entries(population, net.console(project)))
        return config, lines, current

    log(f'Checking {len(eligible)} existing entries the new split did not reproduce.')
    if not runtime_staging.verify(result.config_path, image, proposed, candidates, log, prepare=prepare):
        return restored
    check()
    # Resolve the new units from the staged config after it has passed validation.
    split.write_config_atomic(result.config_path, yaml.safe_dump(proposed, sort_keys=False))
    units = split.source_units(result.config_path)
    for row in eligible:
        lo, size, vram = row['rom_offset'], row['size'], row['vram']
        owners = [name for start, end, address, name in units
                  if start <= lo < lo + size <= end and address + lo - start == vram]
        unit = owners[0] if len(owners) == 1 else row.get('unit') or f'code_{vram:08X}'
        result.functions.append(split.function_from_bytes(row['name'], row['target'], lo, vram, unit, True))
    result.segments = split.segments_from_config(result.config_path)
    log(f'Kept {len(eligible)} byte-verified existing entries in the replacement split.')
    return restored+len(eligible)
