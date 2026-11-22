"""Rejudge a bounded set of saved leads before asking the machine drafter for fresh C."""
import hashlib
import json
import time
from contextlib import contextmanager

from .. import candidate_quality, candidate_scope, compiler
from ..forks.project import ForkError
from ..network import IN_BUILD, NetworkError
from . import autopilot_claims, draft_reports, source_preparation
from .engine.judging import search_evaluation

ATTEMPTS = 8
PREFIX = 'deep-source-recovery:'


def saved_attempts(store, fid, *, below=None):
    return store.all("""SELECT id FROM main.attempts
        WHERE function_id=? AND compiled=1 AND score IS NOT NULL
          AND (source_hash IS NOT NULL OR source IS NOT NULL)
        GROUP BY COALESCE(source_hash, source)
        HAVING (? IS NULL OR MIN(score) < ?)
        ORDER BY MIN(score), MAX(id) DESC LIMIT ?""", fid, below, below, ATTEMPTS)


def saved_sources(store, attempts):
    """Load the bounded, distinct attempt sources in their measured score order."""
    for row in attempts:
        attempt = store.one('SELECT source FROM attempt_details WHERE id=?', row['id'])
        if attempt and attempt['source'] and attempt['source'].strip():
            yield attempt['source']


@contextmanager
def _hold(net, store, fn, agent, check):
    identities = autopilot_claims.identities(net, store)
    with store.tx(wait=check) as db:
        autopilot_claims.expire(net, db, fn['id'], identities)
        current = db.execute('SELECT status,gone_mark FROM functions WHERE id=?', (fn['id'],)).fetchone()
        held = bool(current and current['status'] == 'open' and current['gone_mark'] is None and
                    not db.execute('SELECT 1 FROM claims WHERE function_id=?', (fn['id'],)).fetchone())
        if held:
            db.execute('INSERT INTO claims VALUES(?,?,?,?)', (fn['id'], agent.id, time.time(), time.time() + 300))
    try:
        yield held
    finally:
        if held:
            with store.tx() as db:
                db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?', (fn['id'], agent.id))


def _rom(net, proj, fn, sources, agent, log):
    from .function_names import canonical_source

    store = net.project_store(proj['id'])
    ctx = net._context(proj['id'], fn['unit'])
    if not ctx:
        return False, 'The function has no compiler header. Check the project context.'
    snapshot = source_preparation.inputs(net, proj, fn)
    best = None
    reason = ''
    with _hold(net, store, fn, agent, log.check_cancel) as held:
        if not held:
            return False, None
        with net.progress.judging(proj['slug'], synchronize=False), search_evaluation():
            for source in sources:
                log.check_cancel()
                source = canonical_source(fn, source)
                if candidate_quality.reported_source(net, fn, source):
                    reason = 'A saved candidate has an unresolved code quality report.'
                    continue
                built = net._compile(proj, ctx['header'], source, fn)
                if not built.ok:
                    reason = _diagnostic(built.log)
                    continue
                candidate, exact, problems = net._evaluate(fn, proj, built.obj)
                if candidate is None:
                    reason = '; '.join(problems)[:300] or 'The compiled candidate could not be compared.'
                    continue
                diff = net._score_candidate(fn, proj, candidate, exact)
                score = diff.score
                if best is None or score < best[0]:
                    best = score, source, built, diff.bytes_match
                if diff.bytes_match:
                    break
            if best is not None:
                def check_publish():
                    log.check_cancel()
                    current = store.one('SELECT * FROM functions WHERE id=?', fn['id'])
                    project = store.one('SELECT * FROM projects WHERE id=?', proj['id'])
                    claim = store.one('SELECT agent_id,expires_at FROM claims WHERE function_id=?', fn['id'])
                    if (not project or not current or current['status'] != 'open' or current['gone_mark'] is not None
                            or current['best_score'] != fn['best_score'] or not claim or claim['agent_id'] != agent.id
                            or claim['expires_at'] <= time.time()
                            or current['meta'] != fn['meta'] or current['rom_offset'] != fn['rom_offset']
                            or net._context(proj['id'], current['unit'])['version'] != ctx['version']
                            or source_preparation.inputs(net, project, current) != snapshot):
                        raise NetworkError('The function or its build inputs changed during C recovery.')

                score, source, built, matched = best
                net._record(fn, agent, score, source, ctx['version'], matched=matched, via='deep-source-recovery',
                            effective_settings=getattr(built, 'effective_settings', None),
                            before_record=check_publish, write_wait=log.check_cancel)
    return best is not None, reason


def _diagnostic(output):
    lines = str(output).splitlines()
    return next((line.strip()[:300] for line in lines if 'error' in line.lower() or 'undeclared' in line.lower()),
                lines[-1][:300] if lines else 'No compiler diagnostic was returned.')


def prepare(net, proj, fn, log):
    """Return the current function row. A failed receipt is valid only for the same inputs and leads."""
    slug, pid, fid = proj['slug'], proj['id'], fn['id']
    if slug in net.links:
        return fn  # Only a room host owns the authoritative source and judge.
    store = net.project_store(pid)
    # A sweep can wait behind many searches before reaching this function.
    proj = store.one('SELECT * FROM projects WHERE id=?', pid)
    if proj is None:
        return fn
    fn = store.one('SELECT * FROM functions WHERE id=? AND project_id=?', fid, pid) or fn
    if fn['status'] != 'open' or fn['gone_mark'] is not None or (fn['best_source'] and fn['best_score'] is not None):
        return fn

    def claimed():
        return store.one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fid, time.time())

    if claimed():
        return fn
    fork = net.fork_slug(slug)
    attempts = saved_attempts(store, fid)
    can_draft = net.can_draft(proj)
    newest = store.one("""SELECT MAX(id) AS id FROM main.attempts WHERE function_id=? AND compiled=1
                          AND score IS NOT NULL AND (source_hash IS NOT NULL OR source IS NOT NULL)""", fid)['id']
    scope = candidate_scope.key(net, proj, fn)
    signature = hashlib.sha256(repr((scope, fn['name'], fn['meta'], fn['best_source'], fn['best_score'], newest,
                                     [r['id'] for r in attempts], can_draft, draft_reports.REPAIR_REVISION,
                                     draft_reports.generation(net, fid, project_id=pid),
                                     net.cfg(proj).get('draft_generation', 0))).encode()).hexdigest()
    key = PREFIX + str(fid)
    previous = store.one('SELECT value FROM settings WHERE key=?', key)
    if previous and json.loads(previous['value']).get('scope') == signature:
        return fn

    sources = [fn['best_source'], *saved_sources(store, attempts)]
    sources = [source for source in dict.fromkeys(sources) if source and source.strip()]

    log(f"{fn['name']}: Preparing scored C from saved attempts and machine drafts.")
    agent = net.attribution_agent('autopilot')
    reason = 'No saved scored source was available.'
    try:
        with compiler.cancellable(log.check_cancel):
            if sources and fork:
                best_score = float('inf')
                for source in sources:
                    log.check_cancel()
                    if claimed():
                        return fn
                    if candidate_quality.reported_source(net, fn, source):
                        reason = 'A saved candidate has an unresolved code quality report.'
                        continue
                    # Let the imported build protect matched neighbours and restore unsuccessful edits.
                    result = net.forks.attempt(agent, slug, fn['name'], source, keep=False, quiet=best_score)
                    if result['matched']:
                        break
                    if result['score'] is None:
                        reason = _diagnostic(result['text'])
                    else:
                        best_score = min(best_score, result['score'])
            elif sources:
                scored, diagnostic = _rom(net, proj, fn, sources, agent, log)
                if not scored and diagnostic is None:
                    return fn
                reason = diagnostic or reason

            current = store.one('SELECT * FROM functions WHERE id=?', fid) or fn
            if current['status'] == 'open' and (not current['best_source'] or current['best_score'] is None):
                if claimed():
                    return current
                if can_draft:
                    autopilot = net.forks.autopilot_one if fork else net._autopilot_one
                    outcome = autopilot(agent, proj, fid, retry_draft=True)
                    if outcome == 'skip':
                        return current  # Claims can arrive between the precheck and the drafter.
                    report = draft_reports.read(net, fid, project_id=pid)
                    if failures := report.get('failures'):
                        failure = failures[-1]
                        reason = f"Machine draft {failure['phase']}: {_diagnostic(failure['reason'])}"
    except (ForkError, NetworkError, OSError) as error:
        log(f"{fn['name']}: C recovery deferred. {error}")
        return store.one('SELECT * FROM functions WHERE id=?', fid) or fn

    log.check_cancel()
    current = store.one('SELECT * FROM functions WHERE id=?', fid) or fn
    if current['status'] == 'matched' or (current['best_source'] and current['best_score'] is not None):
        log(f"{fn['name']}: " + ('Matched during C recovery.' if current['status'] == 'matched' else 'Recovered scored C for deep search.'))
    elif current['status'] == 'open':
        with store.tx(wait=log.check_cancel) as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                       (key, json.dumps({'scope': signature, 'reason': reason})))
        log(f"{fn['name']}: Still waiting for scored C. {reason}")
    return current


def issues(store, pid):
    """Expose the last recovery blocker while the function still needs scored C."""
    rows = store.all(f"""SELECT f.name, json_extract(s.value, '$.reason') AS reason
        FROM main.functions f JOIN settings s ON s.key=? || f.id
        WHERE f.project_id=? AND f.status='open' AND f.{IN_BUILD}
          AND (f.best_score IS NULL OR f.best_source IS NULL)
        ORDER BY f.name""", PREFIX, pid)
    return [{'function': row['name'], 'error': row['reason'],
             'action': 'Open the function to inspect its machine draft or submit C that compiles.', 'kind': 'source'} for row in rows]


def reset(db, pid):
    db.execute("DELETE FROM settings WHERE key IN (SELECT ? || id FROM main.functions WHERE project_id=? AND status='open')",
               (PREFIX, pid))
