"""Find verified donor functions inside larger ROM entries during a manual search."""

import json
from collections import defaultdict

from .. import arch
from ..elfobj import Reloc, mask_relocs
from ..network import NetworkError
from .cross_game_progress import report
from .twins import ACROSS_MIN_BYTES, _key, apply, renames, rom_print


def merged_candidates(net, proj, donors, job):
    """Find unique aligned spans with the same masked bytes and relocation layout."""
    if proj['slug'] in net.rooms or arch.get(proj['arch']).boundary_instruction is None:
        return {}
    prefixes = defaultdict(set)
    for donor in donors:
        if donor.prefix and not donor.asm and donor.size >= ACROSS_MIN_BYTES:
            prefixes[bytes.fromhex(donor.prefix)].add((donor.key, donor.size))
    if not prefixes:
        return {}
    report(job, 'Looking for donor code inside merged functions')
    store = net.project_store(proj['id'])
    matches = {}
    ambiguous = set()
    for row in store.all(
        "SELECT vram,rom_offset,target,relocs FROM functions WHERE project_id=? AND status='open' AND size>?",
        proj['id'], ACROSS_MIN_BYTES,
    ):
        if job.stop.is_set():
            return {}
        raw = row['target']
        if not raw or row['rom_offset'] is None or row['vram'] is None:
            continue
        relocations = sorted((int(o), int(kind)) for o, kind, *_ in json.loads(row['relocs'] or '[]'))
        masked = mask_relocs(raw, [Reloc(o, kind, '', 0) for o, kind in relocations],
                             proj['arch'], bool(proj['big_endian']))
        for offset in range(0, len(raw) - ACROSS_MIN_BYTES + 1, 4):
            for key, size in prefixes.get(masked[offset:offset + 8], ()):
                end = offset + size
                if key in ambiguous or end > len(raw) or size == len(raw) or size % 4:
                    continue
                layout = [(o - offset, kind) for o, kind in relocations if offset <= o < end]
                if _key(masked[offset:end], layout) != key:
                    continue
                if key in matches:
                    ambiguous.add(key)
                    del matches[key]
                else:
                    matches[key] = (row['rom_offset'] + offset, row['vram'] + offset, size)
    return matches


def recover(twins, proj, other, project, donor, fingerprint, location, job):
    """Check both cuts and compile against the proposed span before changing the project's layout."""
    net = twins.net
    slug = proj['slug']
    store = net.project_store(proj['id'])
    offset, start, size = location
    end = start + size
    if net.forks.is_asm(other, donor):
        return None
    original = net.forks._current_source(project, donor)
    if not original:
        return None
    rows = store.all(
        "SELECT * FROM functions WHERE project_id=? AND status='open' AND rom_offset<=? "
        "AND rom_offset+size>=? AND vram-rom_offset=?",
        proj['id'], offset, offset + size, start - offset,
    )
    if len(rows) != 1:
        return None
    fn = rows[0]
    fid = fn['id']
    if store.one("SELECT 1 FROM claims WHERE function_id=?", fid):
        return None
    console = net.console(proj)
    if console.instruction_size(fn) != 4:
        return None
    syms = net.symbols(proj['id'])
    head = fn['vram']
    limit = head + fn['size']
    cuts = [(at, f'func_{at:08X}') for at in (end, start) if head < at < limit]
    if not cuts or any(name in syms.by_name or net._split_runs_on(proj, fn, at) for at, name in cuts):
        return None
    try:
        piece = (console.split_function(fn, start, f'func_{start:08X}')[1]
                 if start != head else console.function_from_row(fn))
        if end != limit:
            piece, _ = console.split_function(
                dict(fn, name=piece.name, vram=start, size=piece.size, rom_offset=piece.rom,
                     asm=console.function_asm(piece)), end, f'func_{end:08X}')
    except ValueError:
        return None
    target = net._make_target(proj, piece, syms)
    if target.code != fn['target'][start - head:end - head]:
        return None
    candidate = dict(fn, name=piece.name, vram=start, size=piece.size, rom_offset=piece.rom,
                     target=target.code, relocs=json.dumps(target.to_json()), asm=console.function_asm(piece))
    found = rom_print(candidate, proj['arch'], bool(proj['big_endian']), syms.label)
    names = renames(fingerprint, found, fingerprint.name or donor['name'].split('@')[0], piece.name)
    if fingerprint.key != found.key or names is None:
        return None
    source = apply(original, names)
    ctx = net._context(proj['id'], fn['unit'])
    report(job, 'Checking a recovered function boundary', game=other['name'], function=piece.name)
    result = net._compile(proj, ctx['header'], source, candidate)
    if not result.ok or job.stop.is_set():
        return None
    linked = console.link_function(result.obj, piece.name, target, syms, net.reader(proj, candidate))
    if not linked.exact:
        return None
    with net.progress.view(), net.progress._project_lock(slug):
        try:
            with store.tx() as db:
                current = db.execute("SELECT * FROM functions WHERE id=? AND status='open'", (fid,)).fetchone()
                if (job.stop.is_set() or slug in net.rooms or current != fn
                        or db.execute("SELECT 1 FROM claims WHERE function_id=?", (fid,)).fetchone()
                        or net._context(proj['id'], fn['unit']) != ctx):
                    return None
                for at, name in cuts:
                    net.split_rom_by_you(slug, fn['name'], f'0x{at:X}', name)
                row = db.execute("SELECT * FROM functions WHERE project_id=? AND name=? AND status='open'",
                                 (proj['id'], piece.name)).fetchone()
                if row is None or row['target'] != target.code:
                    raise NetworkError('The recovered function extent changed while splitting it.')
                final = rom_print(row, proj['arch'], bool(proj['big_endian']), net.symbols(proj['id']).label)
                if final.key != fingerprint.key:
                    raise NetworkError('The recovered function relocations changed while splitting it.')
        except NetworkError:
            net._syms.pop(proj['id'], None)
            raise
    return row, final
