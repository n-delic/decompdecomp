"""Operator-requested draft refresh with bounded workers, cancellation and preserved matching history."""
import threading
from concurrent.futures import FIRST_COMPLETED, wait

from ..network import NetworkError
from . import draft_reports, workers


def validate(net, slug):
    proj = net.project_row(slug)
    if not net.can_draft(proj):
        from .. import seeds
        from ..forks import drafts
        cfg = net.cfg(proj)
        if not (net.fork_slug(slug) and drafts.supported(cfg.get('platform') or '') and seeds.available()):
            raise NetworkError('Install the draft decompiler in Setup before regenerating this project.')
    return proj


def has_compiling_c(net, fn):
    """A saved scored source or an unscorable source retained after a successful compile is a baseline."""
    if fn['best_source'] and fn['best_score'] is not None:
        return True
    if net.store.one("SELECT 1 FROM attempts WHERE function_id=? AND compiled=1 AND length(trim(source))>0 LIMIT 1",fn['id']):
        return True
    report = draft_reports.read(net, fn['id'])
    return bool(report.get('compiled') and report.get('compiled_source'))


def run(net, slug, log, *, include_all=False):
    """Refresh missing compiling baselines, optionally every unmatched function, through the ordinary judges."""
    proj = validate(net, slug)
    agent, _ = net.local_agent('autopilot')
    from .boundary_repairs import blocked
    held = blocked(net,proj)
    rows = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY n_insns,id",proj['id'])
    if not include_all:
        rows = [fn for fn in rows if not has_compiling_c(net,fn)]
    stop = getattr(log, 'cancel_event', threading.Event())
    result = {'completed':0, 'total':len(rows), 'compiled':0, 'matched':0, 'skipped':0, 'failed':0}
    def one(fid, slot):
        if stop.is_set() or fid in held or net.store.one('SELECT 1 FROM claims WHERE function_id=?', fid):
            return 'skip', {}
        fn = net.store.one('SELECT * FROM functions WHERE id=?',fid)
        if fn is None or fn['status'] != 'open' or (not include_all and has_compiling_c(net,fn)):
            return 'skip', {}
        link = net.links.get(slug)
        if link and any(c['name'] == fn['name'] for c in link.claims):
            return 'skip', {}
        # Only work that actually starts invalidates its receipt, never skipped or cancelled functions.
        with net.store.tx() as db:
            key = draft_reports.GENERATION_PREFIX+str(fid)
            db.execute("INSERT INTO settings VALUES(?, '1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1",(key,))
        fork = net.fork_slug(slug)
        if fork:
            with net.store.tx() as db:
                db.execute('UPDATE functions SET seed_key=NULL WHERE id=? AND status=\'open\'',(fid,))
            fn = net.store.one('SELECT * FROM functions WHERE id=?',fid)
            net.forks.background_draft(proj,fn)
        else:
            net._seed_for(fn,proj)
        worker = net.local_agent('drafts-'+str(proj['id'])+'-'+str(slot))[0] if link else agent
        if link:
            # Each concurrent function has its own room lease; a local draft never steals a host claim.
            name = link.claim(worker,fn['name'],'easiest')
            if name != fn['name']:
                if name:
                    link.call('/collab/release',worker,function=name,notes='')
                return 'skip', {}
        confirmed = False
        try:
            if stop.is_set():return 'skip', {}
            outcome = (net.forks.autopilot_one if fork else net._autopilot_one)(worker,proj,fid)
            report = draft_reports.read(net,fid)
            if link and not fork:
                current = net.store.one('SELECT * FROM functions WHERE id=?',fid)
                if outcome == 'match':
                    report['host_confirmed'] = False
                    try:
                        verdict = link.attempt(worker,fn['name'],current['matched_source'])
                        report['host_confirmed'] = bool(verdict.get('matched'))
                        if not report['host_confirmed']:
                            draft_reports.failure(report,'host confirmation','room',verdict.get('reason') or 'The host has not confirmed this local match; saved evidence is available for retry.')
                    except NetworkError as error:
                        draft_reports.failure(report,'host confirmation','room',error)
                    draft_reports.save(net,fid,report)
                elif current['best_source'] and current['best_score'] is not None and (
                        fn['best_score'] is None or current['best_score'] < fn['best_score']):
                    try:
                        link.call('/collab/best',worker,function=fn['name'],source=current['best_source'],score=current['best_score'])
                    except NetworkError as error:
                        draft_reports.failure(report,'room sharing','room',error)
                        draft_reports.save(net,fid,report)
                        log(fn['name']+': improved local C saved; room sharing failed: '+str(error)[:300])
            confirmed = bool(report.get('host_confirmed'))
            if link and outcome == 'match' and not confirmed:
                log(fn['name']+': local match saved; awaiting host confirmation.')
                outcome = 'near'
            return outcome, report
        finally:
            if link and not confirmed:
                try:
                    link.call('/collab/release',worker,function=fn['name'],notes='')
                except NetworkError as error:
                    # An accepted match already ends its lease. A disconnected host expires it normally.
                    log(fn['name']+': room lease cleanup: '+str(error)[:300])
    todo = iter(rows); pending = {}; slots = {}
    def fill():
        while not stop.is_set() and len(pending) < min(2,workers.WORKERS):
            row = next(todo,None)
            if row is None:break
            slot = next(i for i in range(2) if i not in slots.values())
            future = workers.pool().submit(one,row['id'],slot)
            pending[future], slots[future] = row, slot
    def progress():
        if hasattr(log, 'progress'):
            log.progress(dict(result, done=result['completed'], phase='Regenerating machine drafts',
                              function=', '.join(row['name'] for row in pending.values()), confirmed=result['matched']))
    fill()
    progress()
    while pending:
        done, _ = wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
        for future in done:
            fid = pending.pop(future)['id']
            slots.pop(future)
            try:
                outcome, report = future.result()
                result['compiled'] += bool(report.get('compiled'))
                result['matched'] += outcome == 'match'
                result['skipped'] += outcome == 'skip'
                result['failed'] += outcome == 'fail'
            except Exception as error:
                result['failed'] += 1
                log('Draft '+str(fid)+': '+str(error)[:500])
            result['completed'] += 1
        fill()
        progress()
    result['cancelled'] = stop.is_set()
    log(f"Draft refresh: {result['completed']} reviewed, {result['compiled']} compiling, {result['matched']} exact matches, {result['skipped']} skipped.")
    return result
