"""Bounded attempts from small-function templates and explicitly imported local library C.

Both sources use the ordinary destination compiler/link judge. No guessed template or SDK fingerprint is progress.
"""

import json
import re
import time
from pathlib import Path

from ..arch import mips_templates
from ..forks import csource
from ..network import NetworkError
from ..project import compiler_for
from ..repair_feedback import trial as repair_trial

MAX_SOURCE_FILES = 512
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_HEADER_BYTES = 512 * 1024


def import_sources(net, proj, corpus, path: Path, header: Path | None = None):
    """Pair source definitions with an already-indexed SDK corpus; never execute its build scripts."""
    names = {r['name'] for r in net.store.all('SELECT name FROM sdk_signatures WHERE corpus=? AND arch=? AND big_endian=?',
                                             corpus, proj['arch'], proj['big_endian'])}
    if not names:
        raise NetworkError('Index the SDK object archive first, using the same corpus name.')
    if not path.exists() or not (path.is_dir() or path.suffix == '.c'):
        raise NetworkError('Choose a local C source file or a folder of C files.')
    files = sorted(path.rglob('*.c')) if path.is_dir() else [path]
    if len(files) > MAX_SOURCE_FILES or sum(p.stat().st_size for p in files) > MAX_SOURCE_BYTES:
        raise NetworkError('Choose a smaller SDK source folder (at most 512 C files and 16 MB).')
    if header and (not header.is_file() or header.stat().st_size > MAX_HEADER_BYTES):
        raise NetworkError('Choose a declarations header smaller than 512 KB.')
    context = header.read_text(errors='replace') if header else ''
    found, ambiguous = {}, set()
    for file in files:
        text = file.read_text(errors='replace')
        for name in names & set(re.findall(r'\b[A-Za-z_]\w*\b', text)):
            source = csource.get(text, name)
            if not source or csource.is_stub(source):
                continue
            from .sdk_macros import context as macro_context
            candidate = macro_context(text, source) + source
            if name in found and found[name] != candidate:
                ambiguous.add(name)
            found[name] = candidate
    compiler = net.twins._compiler_key(net.cfg(proj)['compiler'])
    with net.store.tx() as db:
        db.execute('DELETE FROM sdk_sources WHERE corpus=? AND arch=? AND big_endian=?',
                   (corpus, proj['arch'], proj['big_endian']))
        db.executemany('INSERT INTO sdk_sources VALUES(?,?,?,?,?,?,?)',
                       [(corpus, name, proj['arch'], proj['big_endian'], source, context, compiler)
                        for name, source in found.items() if name not in ambiguous])
    return len(found.keys() - ambiguous)


def sdk_symbol_map(signatures, by_fingerprint):
    """Use only unambiguous library identities for declarations in transferred C.

    Tiny getters and aliases can have identical masked bytes but incompatible prototypes.
    Mapping those to one destination name corrupts otherwise useful source headers.
    The destination's ordinary compiler/link judge still decides every candidate.
    """
    identities = {}
    for hint in signatures:
        identities.setdefault(hint['fingerprint'], set()).add(hint['name'])
    names, ambiguous = {}, set()
    for hint in signatures:
        if hint['size'] < 32 or len(identities[hint['fingerprint']]) != 1:
            ambiguous.add(hint['name'])
            continue
        names.setdefault(hint['name'], set()).update(by_fingerprint.get(hint['fingerprint'], set()))
    return {name: next(iter(values)) for name, values in names.items() if len(values) == 1 and name not in ambiguous}


def budget(job, seconds=120, attempts=128):
    if not hasattr(job, 'reuse_deadline'):
        job.reuse_deadline = time.monotonic() + seconds
        job.reuse_limit = attempts
        job.reuse_attempts = 0
        job.reuse_matched = 0
        job.reuse_bytes = 0


def exhausted(job):
    return job.stop.is_set() or job.reuse_attempts >= job.reuse_limit or time.monotonic() >= job.reuse_deadline


def run(twins, slug, job):
    """Try recognisable leaf patterns and exact SDK fingerprints before spending CPU on wider similarity."""
    from . import repair_mappings, repair_preflight, reuse_traversal, wrapper_families
    from .address_repairs import variants as address_variants
    from .sdk_library import allowed
    from .sdk_source_repairs import variant as sdk_variant
    from .similar_matches import REPAIR_PERCENT, trial_key
    from .twins import apply

    net = twins.net
    proj = net.project_row(slug)
    # Object-project templates need per-file source context; other processors need their own template reader.
    if not net.is_rom(proj) or proj['arch'] != 'mips' or net.fork_slug(slug) or slug in net.links:
        return 0
    budget(job)
    agent, _ = net.local_agent('autopilot')
    cfg = net.cfg(proj)
    wrappers = wrapper_families.index(net,proj)
    prints = twins.prints(proj)
    matched = 0
    sdk_maps = {}
    by_fingerprint = {}
    names = net.store.all('SELECT id,vram,name FROM functions WHERE project_id=?', proj['id'])
    function_names = {r['vram']: r['name'] for r in names}
    names_by_id = {r['id']: r['name'] for r in names}
    for fid, fingerprint in prints.items():
        if fid in names_by_id:
            by_fingerprint.setdefault(fingerprint.key, set()).add(names_by_id[fid])
    symbols = [(r['name'], r['address']) for r in net.store.all('SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    # Leave half of the remaining wall time for wider cross-project discovery. Cached template/address
    # work must not prevent that phase from ever starting; both still share the original compile budget.
    phase_deadline = time.monotonic() + max(0, job.reuse_deadline-time.monotonic()) / 2
    functions = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY size,id", proj['id'])
    with reuse_traversal.rotating(net, proj['id'], functions,
                                  ready=lambda: not exhausted(job) and time.monotonic() < phase_deadline) as rows:
        for fn in rows:
            if exhausted(job) or time.monotonic() >= phase_deadline:
                break
            if net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id']):
                continue
            fingerprint = prints.get(fn['id'])
            if fingerprint is None or fingerprint.asm:
                continue
            settings = compiler_for(cfg, fn['vram'], fn)
            candidates = wrapper_families.candidates(net,proj,fn,wrappers)
            candidates += [('template', 'MIPS leaf template', code) for code in mips_templates.candidates(fn, bool(proj['big_endian']), function_names)]
            if fn['best_source'] and fn['best_score'] is not None and 0 < fn['best_score'] <= (100 - REPAIR_PERCENT) * max(1, fn['n_insns'] or 0):
                repair_mappings.recover(twins, proj, fn, fn['best_source'])
                candidates.insert(0, ('address-probe', 'saved near miss', fn['best_source']))
                learned = repair_mappings.variant(twins, proj, fn, fn['best_source'])
                if learned:
                    candidates.insert(0, ('learned-symbols', 'verified donor mappings', learned))
            from .sdk_hypotheses import hints as library_hints
            near_names = [hint['name'] for hint in library_hints(net,proj,fn)]
            sdk = net.store.all('SELECT DISTINCT s.* FROM sdk_sources s JOIN sdk_signatures i '
                                'ON s.corpus=i.corpus AND s.name=i.name AND s.arch=i.arch AND s.big_endian=i.big_endian '
                                'WHERE i.arch=? AND i.big_endian=? AND (i.fingerprint=? OR s.name=? OR s.name IN (SELECT value FROM json_each(?))) AND s.compiler=? ORDER BY s.corpus,s.name',
                                proj['arch'], proj['big_endian'], fingerprint.key, fn['name'], json.dumps(near_names), twins._compiler_key(settings))
            sdk = [row for row in sdk if allowed(net, proj, row['corpus'])][:3]
            sdk_candidates = []
            for row in sdk:
                corpus = row['corpus']
                if corpus not in sdk_maps:
                    signatures = net.store.all('SELECT name,fingerprint,size FROM sdk_signatures WHERE corpus=? AND arch=? AND big_endian=?',
                                               corpus, proj['arch'], proj['big_endian'])
                    sdk_maps[corpus] = sdk_symbol_map(signatures, by_fingerprint)
                names = {**sdk_maps[corpus], row['name']: fn['name']}
                sdk_candidates.append(('library', corpus + '/' + row['name'], apply(row['header'] + '\n' + row['source'], names)))
            # Typed local SDK source is a hypothesis even when compiler flags changed its fingerprint.
            # The ordinary byte judge, not its name or library signature, decides whether it matches.
            candidates = sdk_candidates + candidates
            misses = {r['signature']: r for r in net.store.all(
                "SELECT signature,feedback FROM reuse_trials WHERE function_id=? AND outcome='miss' AND length(signature)=64", fn['id'])}
            for kind, origin, source in candidates:
                if exhausted(job) or time.monotonic() >= phase_deadline:
                    return matched
                if kind in ('address-probe', 'address-repair', 'learned-symbols'):
                    repair_mappings.propagate(net, fn, fn['best_source'], source)
                context = net._context(proj['id'], fn['unit'])['header']
                if repair_preflight.invalid_call(source, fn['name'], context):
                    continue
                key = trial_key(source, context, settings, fn, [kind, symbols])
                previous = misses.get(key)
                if previous:
                    if kind == 'library':
                        repaired = sdk_variant(net, proj, fn, source, fingerprint, job)
                        if repaired:
                            candidates.append(('library-repair', origin, repaired))
                    if kind in ('address-probe', 'learned-symbols'):
                        candidates.extend(('address-repair', origin, v) for v in
                                          address_variants(source, json.loads(previous['feedback']), fn,
                                                           bool(proj['big_endian']), {name for name, _ in symbols}))
                    continue
                job.reuse_attempts += 1
                try:
                    with repair_trial({'kind': 'automatic candidate', 'family': kind, 'origin': origin}):
                        result = net.try_quietly(agent, proj, fn, source)
                except NetworkError:
                    continue  # busy work remains retryable
                outcome = 'match' if result['matched'] else 'miss'
                reason = origin + '; ' + ('byte match' if result['matched'] else f"score {result.get('score')}")
                with net.store.tx() as db:
                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                               (fn['id'], key, outcome, reason, json.dumps(result)))
                    db.execute('INSERT INTO free_match_attempts(project_id,function_id,strategy,outcome,reason,created_at) VALUES(?,?,?,?,?,?)',
                               (proj['id'], fn['id'], kind, outcome, reason, time.time()))
                if kind == 'library' and not result['matched']:
                    repaired = sdk_variant(net, proj, fn, source, fingerprint, job)
                    if repaired:
                        candidates.append(('library-repair', origin, repaired))
                if kind in ('address-probe', 'learned-symbols') and not result['matched']:
                    candidates.extend(('address-repair', origin, v) for v in
                                      address_variants(source, result, fn, bool(proj['big_endian']),
                                                       {name for name, _ in symbols}))
                if result['matched']:
                    if kind in ('address-probe', 'address-repair', 'learned-symbols'):
                        repair_mappings.accepted(net, fn, source)
                    matched += 1
                    job.reuse_matched += 1
                    job.reuse_bytes += fn['size']
                    net.add_note(agent, slug, 'Autopilot matched from ' + origin, fn['name'])
                    break
    return matched
