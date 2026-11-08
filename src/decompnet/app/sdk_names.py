"""Name SDK functions through normal, regression-checked renames, independently of C matching."""
import logging
import re
import time
from collections import Counter

from ..network import NetworkError
from .function_names import BUILTIN_NAMES, generated, record, state
from .sdk_identity import identify, naming_prints

log = logging.getLogger('decompdecomp')


def name_functions(net, slug, job=None):
    """Reuse rejected ROM naming checks only while all their inputs are unchanged."""
    proj = net.project_row(slug)
    if net.cfg(proj).get('kind') == 'fork':
        return _name_functions(net, slug, job)  # Imported source files can change outside the database.
    version = (net.project_store(proj['id']).read_version('inputs', proj['id']), net.store.read_version('sdk_names'))
    cache = net.__dict__.setdefault('_sdk_name_checks', {})
    previous = cache.get(proj['id'])
    if previous and previous[0] == version and time.monotonic() - previous[1] < 60 and job is None:
        return 0
    result = _name_functions(net, slug, job)
    busy = net.project_store(proj['id']).one('SELECT 1 FROM claims c JOIN functions f ON f.id=c.function_id WHERE f.project_id=? LIMIT 1', proj['id'])
    if not busy and (job is None or not job.stop.is_set()):
        cache[proj['id']] = (version, time.monotonic())
    return result


def _name_functions(net, slug, job=None):
    """Use a unique full-size identity from selected libraries; preserve deliberate names.

    ROM names are changed as a batch with matched-code checks. Imported builds use their normal
    file/config rename and rebuild validation, four at a time because each may rebuild the project.
    """
    proj = net.project_row(slug)
    if slug in net.links:
        return 0
    net.console(proj).scan_sdk(net, proj, job)
    if (not net.store.one('SELECT 1 FROM sdk_signatures LIMIT 1') and not net.project_store(proj['id']).one(
            "SELECT 1 FROM functions WHERE project_id=? AND json_extract(meta,'$.sdk_identity') IS NOT NULL LIMIT 1", proj['id'])):
        return 0  # No evidence: do not synchronize imported projects or open their target objects.
    fork = net.fork_slug(slug)
    if not fork and not net.is_rom(proj):
        return 0
    hints = identify(net, proj, {}, include_asm=True)
    if not hints:
        return 0
    prints = naming_prints(net, proj)
    multiplicity = Counter(p.key for p in prints.values())
    rows = net.project_store(proj['id']).all('SELECT id,name,meta,size FROM functions WHERE project_id=?', proj['id'])
    taken = {r['name'] for r in rows}
    if not fork:
        taken |= set(net.symbols(proj['id']).by_name)
    candidates = []
    for row in rows:
        from .purpose_work import checkpoint
        checkpoint()
        from .maintenance import checkpoint
        checkpoint()
        if job is not None and job.stop.is_set():
            return 0
        hint = hints.get(row['id'])
        naming = state(row)
        p = prints.get(row['id'])
        if not hint or not p or naming['named'] or naming['renamed'] or multiplicity[p.key] != 1:
            continue
        names = hint['names']
        if len(names) != 1:
            continue
        name = names[0]
        if not re.fullmatch(r'[A-Za-z_]\w*', name) or generated(name) or name in BUILTIN_NAMES or name in taken:
            continue
        if net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?', row['id']):
            continue
        candidates.append((row, name, hint, p.key))
    counts = Counter(name for _, name, _, _ in candidates)
    candidates = [(r, n, h, k) for r, n, h, k in candidates if counts[n] == 1][:4 if fork else 128]
    if not candidates:
        return 0
    agent, _ = net.local_agent('autopilot')
    message = 'Names inferred from SDK binary signatures'
    if not fork:
        try:
            result = net.rename_symbols(agent, slug, [(r['name'], name) for r, name, _, _ in candidates], message)
        except (NetworkError, OSError, ValueError):
            return 0
        if not result.startswith('✅'):
            log.info('SDK names for %s rejected: %s', slug, result[:1500])
            return 0
    renamed = 0
    for row, name, hint, key in candidates:
        from .maintenance import checkpoint
        checkpoint()
        if fork:
            if job is not None and job.stop.is_set():
                break
            try:
                result = net.rename_symbol(agent, slug, row['name'], name, message)
            except (NetworkError, OSError, ValueError):
                continue
            if not result.startswith('✅'):
                continue
        evidence = (hint.get('evidence', '') + '. ' + f"Exact relocation-normalized {row['size']}-byte function fingerprint {key}; "
                    'one name across selected SDK signatures, unique target fingerprint. Libraries: '
                    + '; '.join(hint['corpora']) + '. Name inferred from library code; not a verified C match.')
        with net.project_store(proj['id']).tx() as db:
            record(db, row['id'], row['name'], name, method='sdk_signature', source_corpora=hint['corpora'],
                   source_name=name, fingerprint=key, evidence=evidence, binary_evidence=hint.get('binary_evidence', []))
        current = net.project_store(proj['id']).one('SELECT name FROM functions WHERE id=?', row['id'])
        net.add_note(agent, slug, evidence, current['name'])
        renamed += 1
    return renamed


def name_projects(net):
    """Backfill existing projects at app start or after indexing a local SDK; never launch model agents."""
    if not net.store.one('SELECT 1 FROM sdk_signatures LIMIT 1'):
        return
    activity = getattr(net, 'project_activity', None)
    for row in net.store.all('SELECT slug FROM projects'):
        if activity is not None and not activity.active(row['slug']):
            continue  # Indexing a folder must not scan or synchronize idle saved projects.
        try:
            name_functions(net, row['slug'])
        except Exception:  # one unavailable checkout must not prevent other projects being named
            log.exception('SDK naming failed for %s', row['slug'])
