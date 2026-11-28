"""Carry byte-verified existing entries through a newly generated ROM split."""

import copy

import yaml

from ...rom import split


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


def preserve(net, project, result, image, rows, log=print):
    """Stage non-overlapping old entries the new split missed, then keep their original identities."""
    from ...rom.export import _export_segments, _fallthrough_entries, _segments, _symbol_lines
    from . import code, runtime_staging

    check = getattr(log, 'check_cancel', lambda: None)
    cfg = yaml.safe_load(result.config_path.read_text(encoding='utf-8'))
    restored = 0
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

    eligible = []
    for raw in rows:
        check()
        row = dict(raw)
        lo, size, vram, target = (row.get(key) for key in ('rom_offset', 'size', 'vram', 'target'))
        if (row.get('name') in names or type(lo) is not int or type(size) is not int or type(vram) is not int
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
