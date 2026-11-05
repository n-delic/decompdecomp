"""Discovery across project modes, separate from destination-judged C transfers.

The catalog includes source-less targets. A signature is evidence of shared code, never matching C.
Only explicit free passes prepare files; opening a function cannot change a project's build.
"""

import time
from collections import defaultdict

from .. import consoles
from ..network import NetworkError
from .twins import ACROSS_MIN_BYTES

TTL = 60


def machine(net, proj):
    cfg = net.cfg(proj)
    if cfg.get('kind') == 'fork':
        con = consoles.of(cfg)
        return con.arch, bool(con.big_endian)
    return proj['arch'], bool(proj['big_endian'])


def compatible(net, proj):
    wanted = machine(net, proj)
    for other in net.store.all('SELECT * FROM projects WHERE id!=?', proj['id']):
        if other['slug'] in net.links:
            continue
        try:
            if machine(net, other) == wanted:
                yield other
        except (NetworkError, ValueError, KeyError):
            continue


def discover(twins, proj, *, job=None, force=False):
    """Cache bounded, read-only discovery; overview polling only reads the completed snapshot."""
    cache = getattr(twins, '_shared_discovery', None)
    if cache is None:
        cache = twins._shared_discovery = {}
    hit = cache.get(proj['id'])
    if not force and hit and time.monotonic() - hit['at'] < TTL:
        return hit
    from .cross_game_progress import report
    net = twins.net
    report(job, 'Fingerprinting this game', game=proj['name'])
    mine = twins.prints(proj)
    rows = {r['id']: r for r in net.store.all('SELECT id,name,status FROM functions WHERE project_id=?', proj['id'])}
    wanted = defaultdict(list)
    for fid, target in mine.items():
        if fid in rows and target.size >= ACROSS_MIN_BYTES and not target.asm:
            wanted[target.key].append(fid)
    found = defaultdict(list)
    others = list(compatible(net, proj))
    for index, other in enumerate(others):
        if job is not None and (job.stop.is_set() or (hasattr(job, 'reuse_deadline') and time.monotonic() >= job.reuse_deadline)):
            break
        report(job, 'Finding shared machine code', game=other['name'], done=index, total=len(others))
        try:
            prints = twins.prints(other)
        except (NetworkError, OSError, ValueError):
            continue
        for donor in net.store.all('SELECT id,name,status FROM functions WHERE project_id=?', other['id']):
            p = prints.get(donor['id'])
            if not p or p.asm or p.key not in wanted:
                continue
            for fid in wanted[p.key]:
                if len(found[fid]) >= 16:
                    continue
                found[fid].append({'name': donor['name'], 'game': other['name'], 'score': 1.0,
                                   'kind': 'verified C in another game' if donor['status'] == 'matched' else 'shared target code; unverified C',
                                   'function_id': donor['id'], 'source_slug': other['slug'],
                                   'verified': donor['status'] == 'matched'})
    report(job, 'Checking local SDK signatures')
    arch, big = machine(net, proj)
    from .sdk_library import allowed
    for sdk in net.store.all('SELECT corpus,name,fingerprint,size FROM sdk_signatures WHERE arch=? AND big_endian=?', arch, big):
        if sdk['size'] < ACROSS_MIN_BYTES or not allowed(net, proj, sdk['corpus']):
            continue
        for fid in wanted.get(sdk['fingerprint'], []):
            if mine[fid].size == sdk['size'] and len(found[fid]) < 24:
                found[fid].append({'name': sdk['name'], 'game': sdk['corpus'], 'score': 1.0,
                                   'kind': 'SDK object signature; name only', 'verified': False})
    ready = sum(rows[fid]['status'] == 'open' and any(h['verified'] for h in items) for fid, items in found.items())
    blocked = sum(rows[fid]['status'] == 'nosource' and any(h['verified'] for h in items) for fid, items in found.items())
    result = {'at': time.monotonic(), 'hints': dict(found), 'detected': len(found), 'ready': ready, 'blocked': blocked,
              'checked_at': time.time()}
    cache[proj['id']] = result
    return result


def summary(twins, proj):
    hit = getattr(twins, '_shared_discovery', {}).get(proj['id'])
    return ({k: hit[k] for k in ('detected', 'ready', 'blocked', 'checked_at')}
            | {'block_reason': hit.get('block_reason', '')}) if hit else None


def hints(twins, proj, function, limit=5):
    fn = twins.net._function(function, proj['slug'])
    # A function modal must never crawl every checkout. Explicit passes populate the full catalog.
    cached = getattr(twins, '_shared_discovery', {}).get(proj['id'], {})
    items = list(cached.get('hints', {}).get(fn['id'], []))
    target = twins.prints(proj).get(fn['id'])
    if target and target.size >= ACROSS_MIN_BYTES and not target.asm:
        processor, big = machine(twins.net, proj)
        from .sdk_library import allowed
        for sdk in twins.net.store.all('SELECT corpus,name FROM sdk_signatures WHERE arch=? AND big_endian=? AND fingerprint=? AND size=?',
                                       processor, big, target.key, target.size):
            if allowed(twins.net, proj, sdk['corpus']) and not any(h['name'] == sdk['name'] and h['game'] == sdk['corpus'] for h in items):
                items.append({'name': sdk['name'], 'game': sdk['corpus'], 'score': 1.0,
                              'kind': 'SDK object signature; name only', 'verified': False})
    return sorted(items, key=lambda h: not h['verified'])[:limit]


def transfer(twins, proj, job):
    """ROM or imported donors into imported C, through its unchanged objdiff judge."""
    from .free_candidates import exhausted
    from .twins import WHY_CHARS, _declared, _file_compiler, apply
    net = twins.net
    bounded = job is not None and hasattr(job, 'reuse_limit')
    snapshot = discover(twins, proj, job=job)
    if snapshot['blocked']:
        # Existing scaffold probes object rules and retains the original linked assembly baseline.
        # Unsupported native builds remain visible as blocked, rather than changing their Makefiles.
        from ..forks.scaffold import prepare
        _, fp, _ = net.forks.get(proj['slug'])
        try:
            with net.forks._tree(proj).exclusive(), net.forks._build_locks[proj['id']]:
                units = {row['unit'] for row in net.store.all(
                    "SELECT id,unit FROM functions WHERE project_id=? AND status='nosource'", proj['id'])
                    if any(h['verified'] for h in snapshot['hints'].get(row['id'], []))}
                made = prepare(fp, limit=4, units=units)
            if not made:
                snapshot['block_reason'] = 'No supported C object rule for these assembly-only units; the project needs a build adapter.'
            if made:
                net.forks.sync(proj['slug'])
                snapshot = discover(twins, proj, job=job, force=True)
        except (NetworkError, OSError, ValueError) as error:
            snapshot['block_reason'] = str(error)[:WHY_CHARS]
    agent, _ = net.local_agent('autopilot')
    mine = twins.prints(proj)
    matched = 0
    for fid, candidates in snapshot['hints'].items():
        b = net.store.one("SELECT * FROM functions WHERE id=? AND status='open'", fid)
        if b is None or net.store.one('SELECT 1 FROM claims WHERE function_id=?', fid):
            continue
        for hint in candidates:
            if job is not None and (job.stop.is_set() or (bounded and exhausted(job))):
                return matched
            if not hint['verified']:
                continue
            other = net.project_row(hint['source_slug'])
            a = net.store.one("SELECT * FROM functions WHERE id=? AND status='matched'", hint['function_id'])
            if a is None:
                continue
            if net.fork_slug(other['slug']):
                _, source_project, _ = net.forks.get(other['slug'])
                _, target_project, _ = net.forks.get(proj['slug'])
                if _file_compiler(net.console(other), source_project, a) != \
                        _file_compiler(net.console(proj), target_project, b):
                    continue
            pa = twins.prints(other).get(a['id'])
            pb = mine.get(fid)
            if not pa or not pb or pa.key != pb.key:
                continue
            names = twins._fork_names(pa, pb, a, b)
            if names is None:
                continue
            try:
                if net.fork_slug(other['slug']):
                    source = net.forks.twin_source(proj, a, b, names, theirs=other)
                else:
                    # matched_source includes its local declarations; foreign includes are never copied.
                    header = net._context(other['id'], a['unit'])['header']
                    source = (_declared(header, '', names, a['name']) + apply(a['matched_source'], names)
                              if a['matched_source'] else None)
                if not source or twins._missed(fid, source):
                    continue
                if job is not None:
                    job.cross_candidates += 1
                    job.cross_tried += 1
                    if bounded:
                        job.reuse_attempts += 1
                from .cross_game_progress import report
                report(job, 'Compiling candidate', game=other['name'], function=b['name'], donor=a['name'])
                outcome, why = twins._attempt_twin(agent, proj, a, b, source, origin=f" in {other['name']}", theirs=True)
            except (NetworkError, OSError, ValueError):
                continue
            with net.store.tx() as db:
                db.execute('INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)',
                           (proj['id'], fid, other['id'], a['id'], outcome, why[:WHY_CHARS], time.time()))
                if outcome == 'miss':
                    db.execute('INSERT OR REPLACE INTO twin_misses(function_id,source_hash,project_id,at,why) VALUES(?,?,?,?,?)',
                               (fid, twins._miss_key(source), proj['id'], time.time(), why))
            if outcome == 'match':
                matched += 1
                if job is not None:
                    job.cross_matched += 1
                    if bounded:
                        job.reuse_matched += 1
                        job.reuse_bytes += b['size']
                break
    reason = snapshot.get('block_reason')
    final = discover(twins, proj, job=job, force=True)
    if reason:
        final['block_reason'] = reason
    return matched
