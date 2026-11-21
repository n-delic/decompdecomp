"""Locate exact imported donor spans in an owned ROM and add reviewed missing entries."""
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .. import consoles
from ..forks.project import ForkProject
from ..network import NetworkError
from ..rom import split
from ..rom.symbols import Symbol

MIN_BYTES = 32  # Short all-purpose stubs give poor evidence for a new boundary.
MAX_BYTES = 256 * 1024
MAX_OBJECT_BYTES = 8 * 1024 * 1024


def _eligible(net, proj):
    console = net.console(proj)
    return net.is_rom(proj) and console.donor_layout


def _compatible(proj, donor_cfg):
    console = consoles.of(donor_cfg)
    return console.arch == proj['arch'] and console.big_endian == bool(proj['big_endian'])


def donors(net, slug):
    proj = net.project_row(slug, sync=False)
    if not _eligible(net, proj):
        return []
    sha1 = net.cfg(proj)['rom']['sha1']
    result = []
    for other in net.project_rows():
        other_cfg = net.cfg(other)
        if other['id'] == proj['id'] or other_cfg.get('kind') != 'fork' or not _compatible(proj, other_cfg):
            continue
        fp = ForkProject(Path(other['root']), create_state=False)
        version = net.cfg(other).get('version')
        try:
            image = fp.original_image(version, {sha1}, net.console(proj).image_ext)
        except OSError:
            continue
        if image is not None:
            result.append({'slug': other['slug'], 'name': other['name']})
    return result


def _bodies(path, names):
    if path.stat().st_size > MAX_OBJECT_BYTES:
        return
    elf = ELFFile(BytesIO(path.read_bytes()))
    table = elf.get_section_by_name('.symtab')
    if table is None or elf['e_machine'] != 'EM_MIPS':
        return
    for symbol in table.iter_symbols():
        size = symbol['st_size']
        section = symbol['st_shndx']
        if (symbol.name not in names or symbol['st_info']['type'] != 'STT_FUNC'
                or not isinstance(section, int) or not MIN_BYTES <= size <= MAX_BYTES or size % 4):
            continue
        source = elf.get_section(section)
        at = symbol['st_value'] - source['sh_addr']
        if source['sh_flags'] & 4 and 0 <= at and at + size <= source['sh_size']:
            body = source.data()[at:at + size]
            if len(body) == size and any(body):
                yield symbol.name, body


def _unique(data, body):
    at = data.find(body)
    if at < 0 or data.find(body, at + 1) >= 0:
        return None
    return at


def _location(body, original, regions, segments, prior):
    overlay_hits = []
    for region in regions:
        offset = region.data.find(body)
        if offset >= 0 and region.data.find(body, offset + 1) >= 0:
            return None
        if offset >= 0 and offset % 4 == 0:
            overlay_hits.append((region, offset))
    raw = _unique(original, body)
    if raw is not None:
        owners = {seg.vram + raw - seg.rom_start for seg in segments
                  if seg.rom_start <= raw and raw + len(body) <= seg.rom_end and (raw - seg.rom_start) % 4 == 0}
        if len(owners) == 1 and not any(region.compressed for region, _ in overlay_hits):
            return {'region': 'raw', 'offset': raw, 'vram': owners.pop(), 'position': raw}
    if len(overlay_hits) == 1:
        region, offset = overlay_hits[0]
        if not region.compressed:
            # The uncompressed module can already have an address assigned by the import.
            if raw != region.offset + offset:
                return None
            if any(seg.rom_start <= raw < seg.rom_end for seg in segments):
                return None
        key = str(region.offset)
        return {'region': key, 'offset': offset, 'vram': offset,
                'position': prior[key] + offset if key in prior else None}
    return None


def _preview(net, slug, donor_slug, log):
    proj = net.project_row(slug, sync=False)
    if not _eligible(net, proj) or not isinstance(donor_slug, str):
        raise NetworkError('Choose an imported donor with the same original MIPS ROM.')
    other = net.project_row(donor_slug, sync=False)
    if net.cfg(other).get('kind') != 'fork' or not _compatible(proj, net.cfg(other)):
        raise NetworkError('Choose a compatible imported decompilation for this exact ROM.')
    cfg = net.cfg(proj)
    rom_cfg = cfg['rom']
    directory = Path(proj['root'])
    original = (directory / (rom_cfg.get('program') or rom_cfg['file'])).read_bytes()
    sha = hashlib.sha1(original).hexdigest()
    fp = ForkProject(Path(other['root']), create_state=False)
    if sha != rom_cfg['sha1'] or fp.original_image(
            net.cfg(other).get('version'), {sha}, net.console(proj).image_ext) is None:
        raise NetworkError('Both projects need the same original ROM. Check the donor’s local base ROM.')
    image, segments = net._rom(proj)
    analysis_hash = hashlib.sha1(image).hexdigest()
    if not image.startswith(original) or (rom_cfg.get('analysis_sha1') and rom_cfg['analysis_sha1'] != analysis_hash):
        raise NetworkError('The current analysis image has a different layout from the original ROM.')
    console = net.console(proj)
    regions = console.donor_text_regions(original)
    prior = cfg.get('donor_overlays', {})
    existing = net.project_store(proj['id']).all(
        'SELECT name,vram,rom_offset,size,status FROM functions WHERE project_id=? AND rom_offset IS NOT NULL', proj['id'])
    symbols = defaultdict(set)
    for row in net.project_store(other['id']).all(
        'SELECT name,unit,meta FROM functions WHERE project_id=?', other['id']):
        symbols[row['unit']].add(json.loads(row['meta'] or '{}').get('symbol') or row['name'].partition('@')[0])
    seen = {}
    check = getattr(log, 'check_cancel', lambda: None)
    update = getattr(log, 'progress', lambda **_: None)
    units = fp.units()
    for done, unit in enumerate(units):
        check()
        update(phase='Checking donor function bytes', done=done, total=len(units))
        if not symbols[unit.name] or unit.target is None:
            continue
        path = unit.target.resolve()
        if not path.is_relative_to(fp.root.resolve()) or not path.is_file():
            continue
        try:
            for name, code in _bodies(path, symbols[unit.name]):
                check()
                found = _location(code, original, regions, segments, prior)
                if found is None:
                    continue
                key = (found['region'], found['offset'], len(code))
                if key in seen:
                    continue
                seen[key] = {**found, 'size': len(code), 'name': name}
        except (ELFError, OSError, ValueError):
            continue
    proposals = []
    for _, entry in sorted(seen.items()):
        pos = entry['position']
        overlaps = [row for row in existing if pos is not None and
                    row['rom_offset'] < pos + entry['size'] and pos < row['rom_offset'] + row['size']]
        if any(r['rom_offset'] == pos and r['size'] == entry['size'] for r in overlaps):
            continue
        state = 'review' if overlaps else 'new'
        proposals.append({**entry, 'state': state, 'overlaps': [r['name'] for r in overlaps]})
    by_region = defaultdict(list)
    for entry in proposals:
        by_region[entry['region']].append(entry)
    for entries in by_region.values():
        active = []
        for entry in entries:
            active = [p for p in active if p['offset'] + p['size'] > entry['offset']]
            if active:
                entry['state'] = 'review'
                for peer in active:
                    peer['state'] = 'review'
            active.append(entry)
    counts = Counter((entry['state'], entry['region'] != 'raw') for entry in proposals)
    banks = [{'id': str(r.offset), 'name': r.name, 'size': len(r.data), 'compressed': r.compressed}
             for r in regions]
    token = hashlib.sha256(json.dumps([sha, analysis_hash, donor_slug, prior, proposals, banks], sort_keys=True).encode()).hexdigest()
    report = {'donor': donor_slug, 'token': token, 'overlays': banks, 'functions': proposals,
              'counts': {'raw': counts['new', False], 'overlay': counts['new', True],
                         'review': counts['review', False] + counts['review', True]}}
    return proj, cfg, image, regions, report


def preview(net, slug, donor_slug, log=print):
    return _preview(net, slug, donor_slug, log)[-1]


def apply(net, slug, donor_slug, token, log=print, *, include_overlays=False):
    proj, cfg, image, regions, report = _preview(net, slug, donor_slug, log)
    if not token or token != report['token']:
        raise NetworkError('The ROM layout or donor evidence changed. Preview the changes again.')
    selected = [item for item in report['functions'] if item['state'] == 'new'
                and (include_overlays or item['region'] == 'raw')]
    if not selected:
        return {'added': 0}
    if slug in net.links or slug in net.rooms:
        raise NetworkError('Leave the shared room before adding donor functions.')
    check = getattr(log, 'check_cancel', lambda: None)
    check()
    update = getattr(log, 'progress', lambda **_: None)
    directory = Path(proj['root'])
    console = net.console(proj)
    store = net.project_store(proj['id'])
    config = json.loads(proj['config'])
    banks = config.setdefault('donor_overlays', {})
    bank_data = {str(reg.offset): reg for reg in regions}
    needed = {item['region'] for item in selected if item['region'] != 'raw'}
    assembled = bytearray(image)
    file = None
    for key in sorted(needed - banks.keys(), key=int):
        reg = bank_data[key]
        assembled.extend(bytes(-len(assembled) % 16))
        offset = len(assembled)
        banks[key] = offset
        assembled.extend(reg.data)
        config['segments'].append(vars(split.Segment(f'donor_uso_{int(key):X}', offset, len(assembled), 0)))
    if len(assembled) != len(image):
        digest = hashlib.sha1(assembled).hexdigest()
        path = f'donor-analysis-{digest[:16]}{Path(cfg["rom"]["file"]).suffix}'
        file = directory / path
        config['rom'].setdefault('donor_analysis_base', cfg['rom']['file'])
        config['rom'].setdefault('program', cfg['rom']['file'])
        config['rom'].update(file=path, analysis_sha1=digest)
    with net.progress.view(), net.progress._project_lock(slug), store.tx() as db:
        current = db.execute('SELECT config FROM projects WHERE id=?', (proj['id'],)).fetchone()
        if current['config'] != proj['config'] or slug in net.rooms or slug in net.links:
            raise NetworkError('The project changed while reviewing the donor. Preview it again.')
        peers = db.execute('SELECT rom_offset,size FROM functions WHERE project_id=? AND rom_offset IS NOT NULL',
                           (proj['id'],)).fetchall()
        syms = net.symbols(proj['id']).copy()
        for done, item in enumerate(selected):
            check()
            update(phase='Adding verified entry points', done=done, total=len(selected))
            key = item['region']
            pos = item['position'] if key == 'raw' else banks[key] + item['offset']
            size, vram = item['size'], item['vram']
            if any(row['rom_offset'] < pos + size and pos < row['rom_offset'] + row['size'] for row in peers):
                raise NetworkError('A candidate now overlaps a function. Preview the changes again.')
            unit = (next(s['name'] for s in config['segments'] if s['rom_start'] <= pos < s['rom_end'])
                    if key == 'raw' else f'donor_uso_{int(key):X}')
            name = f'func_{vram:08X}_donor_{pos:X}'
            if name in syms.by_name:
                raise NetworkError('A candidate name is already used. Preview the changes again.')
            fn = split.function_from_bytes(name, assembled[pos:pos + size], pos, vram, unit, bool(proj['big_endian']))
            syms.alias(Symbol(name, vram, size, 'func'))
            target = net._make_target(proj, fn, syms)
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,unit,vram,rom_offset,status,meta) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,'open',?)", (proj['id'], name, size, len(fn.lines), target.code,
                        json.dumps(target.to_json()), console.function_asm(fn), unit, vram, pos,
                        json.dumps({'donor_boundary': {'project': donor_slug, 'symbol': item['name'], 'region': key}})))
            db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',
                       (proj['id'], name, vram, size, 'func'))
        if file is not None:
            fd, temp = tempfile.mkstemp(prefix='.donor-analysis-', dir=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(assembled)
                os.replace(temp, file)
            finally:
                Path(temp).unlink(missing_ok=True)
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(config), proj['id']))
        net._event(db, proj['id'], None, 'boundary', f'Added {len(selected)} verified donor extents as open functions')
        for cache in (net._roms, net._syms, net._xrefs, net._ops, net._warmed):
            cache.pop(proj['id'], None)
        net.bump_generation(proj['id'])
    net.twins.forget(proj)
    net.progress.foreground_changed(slug)
    return {'added': len(selected)}
