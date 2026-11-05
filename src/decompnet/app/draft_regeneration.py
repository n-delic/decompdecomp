"""Operator-requested draft refresh with bounded workers, cancellation and preserved matching history."""
import json
import threading
from concurrent.futures import FIRST_COMPLETED, wait

from ..network import NetworkError
from . import draft_reports, workers


def validate(net, slug):
    proj = net.project_row(slug)
    if slug in net.links:
        raise NetworkError('Regenerate drafts on the room host so every candidate uses its judge.')
    if not net.can_draft(proj):
        from .. import seeds
        from ..forks import drafts
        cfg = net.cfg(proj)
        if not (net.fork_slug(slug) and drafts.supported(cfg.get('platform') or '') and seeds.available()):
            raise NetworkError('Install the draft decompiler in Setup before regenerating this project.')
    return proj


def run(net, slug, log):
    proj = validate(net, slug)
    # A generation is part of the input receipt; it does not delete sources, attempts or scores.
    with net.store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (proj['id'],)).fetchone()['config'])
        cfg['draft_generation'] = int(cfg.get('draft_generation', 0))+1
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg),proj['id']))
    proj = net.project_row(slug)
    agent, _ = net.local_agent('autopilot')
    from .boundary_repairs import blocked
    held = blocked(net,proj)
    rows = net.store.all("SELECT id,name FROM functions WHERE project_id=? AND status='open' ORDER BY n_insns,id",proj['id'])
    stop = getattr(log, 'cancel_event', threading.Event())
    result = {'completed':0, 'total':len(rows), 'compiled':0, 'matched':0, 'skipped':0, 'failed':0}
    def one(fid):
        if stop.is_set() or fid in held or net.store.one('SELECT 1 FROM claims WHERE function_id=?', fid):
            return 'skip', {}
        fn = net.store.one('SELECT * FROM functions WHERE id=?',fid)
        if fn is None or fn['status'] != 'open':return 'skip', {}
        fork = net.fork_slug(slug)
        if fork:
            with net.store.tx() as db:
                db.execute('UPDATE functions SET seed_key=NULL WHERE id=? AND status=\'open\'',(fid,))
            fn = net.store.one('SELECT * FROM functions WHERE id=?',fid)
            net.forks.background_draft(proj,fn)
        else:
            net._seed_for(fn,proj)
        outcome = (net.forks.autopilot_one if fork else net._autopilot_one)(agent,proj,fid)
        return outcome, draft_reports.read(net,fid)
    todo = iter(rows); pending = {}
    def fill():
        while not stop.is_set() and len(pending) < min(2,workers.WORKERS):
            row = next(todo,None)
            if row is None:break
            pending[workers.pool().submit(one,row['id'])] = row
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
