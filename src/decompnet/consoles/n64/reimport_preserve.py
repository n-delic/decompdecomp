"""Carry byte-verified existing entries through a newly generated ROM split."""

import copy

import yaml

from ...rom import split


def preserve(net, project, result, image, rows, log=print):
    """Stage non-overlapping old entries the new split missed, then keep their original identities."""
    from ...rom.export import _export_segments, _fallthrough_entries, _segments, _symbol_lines
    from . import code, runtime_staging

    check = getattr(log, 'check_cancel', lambda: None)
    cfg = yaml.safe_load(result.config_path.read_text(encoding='utf-8'))
    current = [dict(name=fn.name, rom_offset=fn.rom, size=fn.size, vram=fn.vram,
                    target=b''.join(line.word.to_bytes(4, 'big') for line in fn.lines))
               for fn in result.functions if fn.rom is not None]
    names = {fn.name for fn in result.functions}
    ranges = [(fn['rom_offset'], fn['rom_offset'] + fn['size']) for fn in current]
    eligible = []
    for raw in rows:
        check()
        row = dict(raw)
        lo, size, vram, target = (row.get(key) for key in ('rom_offset', 'size', 'vram', 'target'))
        if (row.get('name') in names or type(lo) is not int or type(size) is not int or type(vram) is not int
                or size <= 0 or size % 4 or lo < 0 or lo + size > len(image)
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
        return 0
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
        return 0
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
    return len(eligible)
