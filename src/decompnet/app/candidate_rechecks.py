"""Bounded, resumable diagnostics for reported candidates; never select source from a batch."""
import difflib
import hashlib
import json
import time

from .. import candidate_quality, candidate_scope, scoring, toolchains
from ..network import NetworkError

SCHEMA = '''CREATE TABLE IF NOT EXISTS candidate_rechecks(
 function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
 source_hash TEXT NOT NULL, attempt_id INTEGER NOT NULL,
 build_scope TEXT NOT NULL, result TEXT NOT NULL, measured_at REAL NOT NULL,
 PRIMARY KEY(function_id,source_hash,attempt_id))'''


def ensure(net):
    with net.store.tx() as db:
        db.execute(SCHEMA)


def alternatives(net, fn, report):
    seen = {report['source_hash']}
    rows = []
    for ident in report['alternatives'][:3]:
        row = net.store.one('SELECT id,source,score FROM attempts WHERE id=? AND function_id=? '
                            'AND compiled=1 AND score IS NOT NULL', ident, fn['id'])
        if not row or not row['source']:
            continue
        digest = hashlib.sha256(row['source'].encode()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            rows.append(row)
    return rows


def stamp(net, proj, fn, alternative):
    # Include symbols referenced only by the alternative, not merely those in today's selected C.
    return candidate_scope.key(net, proj, dict(fn) | {'best_source':alternative['source']})


def saved(net, fn, report, alternative, scope):
    row = net.store.one('SELECT * FROM candidate_rechecks WHERE function_id=? AND source_hash=? AND attempt_id=?',
                        fn['id'], report['source_hash'], alternative['id'])
    return json.loads(row['result']) | {'measured_at':row['measured_at']} if row and row['build_scope'] == scope else None


def listing(net, slug):
    if slug:
        net.project_row(slug)
    ensure(net)
    reports = candidate_quality.listing(net, slug)
    for report in reports:
        fn = net._function(report['function'], report['slug'])
        proj = net.project_row(report['slug'])
        report.update(instructions=fn['n_insns'], starting_score=fn['best_score'])
        report['rechecks'] = []
        for alt in alternatives(net, fn, report):
            scope = stamp(net, proj, fn, alt)
            result = saved(net, fn, report, alt, scope)
            report['rechecks'].append({'attempt_id':alt['id'], 'historical_score':alt['score'],
                                      'measurement':result})
    return sorted(reports, key=lambda r:(r['instructions'] or 0, r['slug'], r['function']))


def evidence(net, slug, function, source_hash, attempt_id):
    ensure(net)
    fn = net._function(function, slug)
    report = next((r for r in candidate_quality.pending(net, fn) if r['source_hash'] == source_hash), None)
    if not report:
        raise NetworkError('This candidate changed or was reviewed. Refresh the recovery queue.')
    alt = next((a for a in alternatives(net, fn, report) if a['id'] == attempt_id), None)
    if not alt:
        raise NetworkError('Choose a saved alternative from this report.')
    measurement = saved(net, fn, report, alt, stamp(net, net.project_row(slug), fn, alt))
    return {'diff':'\n'.join(difflib.unified_diff((fn['best_source'] or '').splitlines(), alt['source'].splitlines(),
                                                fromfile='selected.c', tofile=f'alternative-{attempt_id}.c')),
            'source':alt['source'], 'target':fn['asm'] or '', 'measurement':measurement}


def measure(net, agent, proj, fn, source):
    if net.fork_slug(proj['slug']):
        # Diagnostic mode restores the source/build and never promotes a lower-scoring alternative.
        result = net.forks._attempt(agent, proj['slug'], fn['name'], source, keep=False, quiet=0, diagnostic=True)
        return {'score':result['score'], 'percent':(100.0 if result['matched'] else min(99.9, result['percent'])) if result.get('percent') is not None else None, 'exact':bool(result['matched']),
                'error':result['text'][-2000:] if result['score'] is None else ''}
    context = net._context(proj['id'], fn['unit'])
    compiled = net._compile(proj, context['header'], source, fn)
    if not compiled.ok:
        return {'score':None, 'percent':None, 'exact':False, 'error':compiled.log[-2000:]}
    candidate, exact, problems = net._evaluate(fn, proj, compiled.obj)
    if candidate is None:
        return {'score':None, 'percent':None, 'exact':False, 'error':'The object does not contain the expected function.'}
    diff = scoring.diff(net._target_obj(fn, proj), candidate)
    matched = bool(exact) if exact is not None else diff.bytes_match
    score = 0 if matched else max(1, diff.score)
    return {'score':score, 'percent':100.0 if matched else min(99.9, diff.percent), 'exact':matched,
            'error':'', 'link_notes':problems}


def run(net, slug, limit, log, retry_failed=False):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise NetworkError('Choose 1–200 functions per recovery batch.')
    if not isinstance(retry_failed, bool):
        raise NetworkError('Retry failed measurements must be true or false.')
    if slug in net.links:
        raise NetworkError('Rejudge alternatives on the room host’s project.')
    ensure(net)
    agent, _ = net.local_agent('candidate-recheck')
    reports = candidate_quality.listing(net, slug)
    reports.sort(key=lambda r: net._function(r['function'], slug)['n_insns'] or 0)
    stats = {'functions':0, 'tested':0, 'cached':0, 'busy':0, 'failed':0, 'exact':0}
    for report in reports:
        log.check_cancel()
        if stats['functions'] >= limit:
            break
        fn = net._function(report['function'], slug)
        proj = net.project_row(slug)
        todo = []
        for alt in alternatives(net, fn, report):
            scope = stamp(net, proj, fn, alt)
            previous = saved(net, fn, report, alt, scope)
            if previous and (not retry_failed or previous['score'] is not None):
                stats['cached'] += 1
            else:
                todo.append((alt, scope))
        if not todo:
            continue
        with net.store.tx() as db:
            net._expire_claims(db)
            current = db.execute('SELECT * FROM functions WHERE id=?', (fn['id'],)).fetchone()
            if (current['status'] != 'open' or hashlib.sha256((current['best_source'] or '').encode()).hexdigest() != report['source_hash']):
                continue
            if db.execute('SELECT 1 FROM claims WHERE function_id=?', (fn['id'],)).fetchone():
                stats['busy'] += 1
                continue
            now = time.time()
            db.execute('INSERT INTO claims VALUES(?,?,?,?)', (fn['id'], agent.id, now, now+net.claim_ttl))
        stats['functions'] += 1
        try:
            for alt, scope in todo:
                log.check_cancel()
                with net.store.tx() as db:
                    db.execute('UPDATE claims SET expires_at=? WHERE function_id=? AND agent_id=?',
                               (time.time()+net.claim_ttl, fn['id'], agent.id))
                started = time.monotonic()
                try:
                    result = measure(net, agent, proj, fn, alt['source'])
                except (NetworkError, OSError, ValueError, toolchains.ToolchainError) as error:
                    result = {'score':None, 'percent':None, 'exact':False, 'error':str(error)}
                current = net._function(fn['name'], slug)
                if (current['status'] != 'open' or hashlib.sha256((current['best_source'] or '').encode()).hexdigest() != report['source_hash']
                        or stamp(net, net.project_row(slug), current, alt) != scope):
                    log(f'{fn["name"]}: inputs changed; measurement discarded.')
                    break
                result['seconds'] = round(time.monotonic()-started, 3)
                with net.store.tx() as db:
                    db.execute('INSERT OR REPLACE INTO candidate_rechecks VALUES(?,?,?,?,?,?)',
                               (fn['id'], report['source_hash'], alt['id'], scope, json.dumps(result), time.time()))
                stats['tested'] += 1
                stats['failed'] += int(result['score'] is None)
                stats['exact'] += int(result['exact'])
                log(f'{fn["name"]} · alternative #{alt["id"]}: '+(result['error'] if result['score'] is None else f'score {result["score"]}; review required'))
                log.progress(dict(stats) | {'function':fn['name'], 'limit':limit})
        finally:
            with net.store.tx() as db:
                db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?', (fn['id'], agent.id))
    log('Recovery batch finished. Measurements saved; selected C, matches and quality reports were not changed.')
    return stats
