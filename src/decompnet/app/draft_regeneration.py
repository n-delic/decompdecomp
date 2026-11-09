"""Operator-requested draft refresh with bounded workers, cancellation and preserved matching history."""
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, wait

from .. import scoring, seeds
from ..network import NetworkError
from . import draft_reports
from .purpose_work import Executor


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
    if net.project_store(fn['project_id']).one("SELECT 1 FROM attempts WHERE function_id=? AND compiled=1 AND length(trim(source))>0 LIMIT 1",fn['id']):
        return True
    report = draft_reports.read(net, fn['id'], project_id=fn['project_id'])
    return bool(report.get('compiled') and report.get('compiled_source'))


def thread_count(value=0):
    if type(value) is not int or not 0 <= value <= 64:
        raise ValueError('Choose 1–64 draft threads, or 0 for automatic.')
    return value or min(8, os.cpu_count() or 1)


def generate(net, proj, fn, fork):
    """Generate exactly one fresh draft; no context fallback or repaired variants."""
    if fork:
        with net.project_store(proj['id']).tx() as db:
            db.execute('UPDATE functions SET seed_key=NULL WHERE id=?', (fn['id'],))
        return net.forks.background_draft(proj, net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?',fn['id']))
    key, toolchain, context = net._draft_plan(fn, proj)
    draft = seeds.generate_from_asm(net._asm_text(fn,proj), fn['name'], toolchain, context,
                                    '\n'.join(net._jtbl_asm(proj,fn)), valid=True, strict=True, timeout=20)
    from .engine.briefs import DRAFT_NOTE
    source = DRAFT_NOTE+'\n'+seeds.M2C_MACROS+'\n'+draft if draft else ''
    with net.project_store(proj['id']).tx() as db:
        db.execute('UPDATE functions SET seed=?,seed_key=? WHERE id=?',(source,key,fn['id']))
    return source


def trial(net, agent, proj, fid, source, fork, stop=None):
    """Compile the draft, then bounded private repairs only if it fails; keep the ordinary judge."""
    now = time.time()
    from .local_claims import expiry_ids
    store = net.project_store(proj['id'])
    identities = expiry_ids(net, store)
    with store.tx() as db:
        net._expire_claims(db, **identities)
        fn = db.execute('SELECT * FROM functions WHERE id=?',(fid,)).fetchone()
        if not fn or fn['status']!='open' or db.execute('SELECT 1 FROM claims WHERE function_id=?',(fid,)).fetchone():
            return 'skip', {}
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fid,agent.id,now,now+300))
    report = {'compiled':False,'scored':False,'matched':False,'trials':1,'failures':[], 'family':'raw_m2c'}
    try:
        if fork:
            result = net.forks.attempt(agent,proj['slug'],fn['name'],source,keep=False)
            report.update(compiled=result.get('score') is not None,scored=result.get('score') is not None,
                          matched=bool(result.get('matched')),host_confirmed=bool(result.get('host_confirmed')))
            if not report['compiled']:draft_reports.failure(report,'compiler','raw_m2c',result.get('text','Compilation failed.'))
        else:
            with net.progress.judging(proj['slug']):
                ctx=net._context(proj['id'],fn['unit'])
                from . import initial_draft_repairs
                from .engine.briefs import DRAFT_NOTE
                def candidates():
                    yield 'raw_m2c', source
                    if report['compiled'] or (stop and stop.is_set()):
                        return
                    _, toolchain, context = net._draft_plan(fn, proj)
                    prefix = DRAFT_NOTE+'\n'+seeds.M2C_MACROS+'\n'
                    draft = source.removeprefix(prefix)
                    choices = initial_draft_repairs.recovery_drafts(
                        draft, fn['name'], net._asm_text(fn,proj), proj['arch'], toolchain,
                        context, '\n'.join(net._jtbl_asm(proj,fn)), report)
                    for index, (family, repaired) in enumerate(choices):
                        if index >= 6 or report['compiled'] or (stop and stop.is_set()):
                            return
                        yield family, prefix+repaired
                report['trials'] = 0
                seen = set()
                for family, candidate_source in candidates():
                    if stop and stop.is_set():break
                    if candidate_source in seen:continue
                    seen.add(candidate_source)
                    result=net._compile(proj,ctx['header'],candidate_source,fn)
                    report['trials'] += 1
                    if not result.ok:
                        draft_reports.failure(report,'compiler',family,result.log)
                        continue
                    report.update(compiled=True, compiled_source=candidate_source, family=family)
                    candidate,verdict,_=net._evaluate(fn,proj,result.obj)
                    if candidate is None:
                        draft_reports.failure(report,'link',family,'Compiled C could not be evaluated against this target.')
                    else:
                        diff=scoring.diff(net._target_obj(fn,proj),candidate)
                        if verdict is not None:
                            diff=scoring.DiffResult(0 if verdict else max(1,diff.score),diff.max_score,diff.rows,verdict)
                        net._record(fn,agent,diff.score,candidate_source,ctx['version'],matched=diff.bytes_match,
                                    effective_settings=getattr(result,'effective_settings',None))
                        report.update(scored=True,matched=diff.bytes_match)
                    break
        report['complete']=True
        draft_reports.save(net,fid,report,project_id=proj['id'])
        return ('match' if report['matched'] else 'near' if report['scored'] else 'fail'), report
    finally:
        with net.project_store(proj['id']).tx() as db:
            db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?',(fid,agent.id))


def run(net, slug, log, *, include_all=False, threads=0):
    """Refresh missing compiling baselines, optionally every unmatched function, through the ordinary judges."""
    count = thread_count(threads)
    proj = validate(net, slug)
    agent, _ = net.local_agent('autopilot')
    from .boundary_repairs import blocked
    held = blocked(net,proj)
    rows = net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY n_insns,id",proj['id'])
    if not include_all:
        rows = [fn for fn in rows if not has_compiling_c(net,fn)]
    stop = getattr(log, 'cancel_event', threading.Event())
    active, active_lock = {}, threading.Lock()
    def stage(fid, phase):
        with active_lock:
            active[fid] = (phase, time.monotonic())
    result = {'completed':0, 'total':len(rows), 'compiled':0, 'matched':0, 'skipped':0, 'failed':0}
    def one(fid, slot):
        if stop.is_set() or fid in held or net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?', fid):
            return 'skip', {}
        fn = net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?',fid)
        if fn is None or fn['status'] != 'open' or (not include_all and has_compiling_c(net,fn)):
            return 'skip', {}
        link = net.links.get(slug)
        if link and any(c['name'] == fn['name'] for c in link.claims):
            return 'skip', {}
        # Only work that actually starts invalidates its receipt, never skipped or cancelled functions.
        with net.project_store(proj['id']).tx() as db:
            key = draft_reports.GENERATION_PREFIX+str(fid)
            db.execute("INSERT INTO settings VALUES(?, '1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1",(key,))
        fork = net.fork_slug(slug)
        stage(fid, 'Generating draft')
        try:
            source = generate(net, proj, fn, fork)
        except seeds.DraftError as error:
            report = {'compiled':False,'trials':0,'failures':[],'complete':True}
            draft_reports.failure(report,'generation','raw_m2c',error)
            draft_reports.save(net,fid,report,project_id=proj['id'])
            return 'fail', report
        if not source:
            return 'fail', {}
        worker = net.local_agent('drafts-'+str(proj['id'])+'-'+str(slot))[0] if link else agent
        if link:
            # Each concurrent function has its own room lease; a local draft never steals a host claim.
            stage(fid, 'Requesting host claim')
            name = link.claim(worker,fn['name'],'easiest')
            if name != fn['name']:
                if name:
                    link.call('/collab/release',worker,function=name,notes='')
                return 'skip', {}
        confirmed = False
        try:
            if stop.is_set():return 'skip', {}
            stage(fid, 'Checking compiling C')
            outcome, report = trial(net,worker,proj,fid,source,fork,stop)
            if link and not fork:
                current = net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?',fid)
                if outcome == 'match':
                    report['host_confirmed'] = False
                    try:
                        stage(fid, 'Host confirming local match')
                        verdict = link.attempt(worker,fn['name'],current['matched_source'])
                        report['host_confirmed'] = bool(verdict.get('matched'))
                        if not report['host_confirmed']:
                            draft_reports.failure(report,'host confirmation','room',verdict.get('reason') or 'The host has not confirmed this local match; saved evidence is available for retry.')
                    except NetworkError as error:
                        draft_reports.failure(report,'host confirmation','room',error)
                    draft_reports.save(net,fid,report,project_id=proj['id'])
                elif current['best_source'] and current['best_score'] is not None and (
                        fn['best_score'] is None or current['best_score'] < fn['best_score']):
                    try:
                        stage(fid, 'Sharing improvement')
                        link.call('/collab/best',worker,function=fn['name'],source=current['best_source'],score=current['best_score'])
                    except NetworkError as error:
                        draft_reports.failure(report,'room sharing','room',error)
                        draft_reports.save(net,fid,report,project_id=proj['id'])
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
        while not stop.is_set() and len(pending) < count:
            row = next(todo,None)
            if row is None:break
            slot = next(i for i in range(count) if i not in slots.values())
            stage(row['id'], 'Queued')
            future = pool.submit(one,row['id'],slot)
            pending[future], slots[future] = row, slot
    def progress():
        if hasattr(log, 'progress'):
            with active_lock:
                detail = ', '.join(row['name']+': '+active.get(row['id'],('Queued',time.monotonic()))[0]
                                   for row in pending.values())
            log.progress(dict(result, done=result['completed'], phase='Regenerating machine drafts',
                              function=detail, confirmed=result['matched']))
    # Explicit user work has its own pool, independent of the low-priority background limit.
    with Executor(purpose='Machine draft refresh', project=slug, max_workers=count, thread_name_prefix='draft-refresh') as pool:
        fill()
        progress()
        while pending:
            done, _ = wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
            for future in done:
                fid = pending.pop(future)['id']
                with active_lock:
                    active.pop(fid, None)
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
