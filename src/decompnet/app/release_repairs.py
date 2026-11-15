"""Try a few local repairs after an agent releases its lease, before another agent edits the draft."""
import hashlib
import logging
import threading
import time

from .permute import Permuter, State

log = logging.getLogger('decompdecomp')


def run(net, slug, fn, *, stop=None):
    if slug in net.links or fn['status'] != 'open' or not fn['best_source'] or not fn['best_score']:
        return ''
    state = State(stop=stop if stop is not None else threading.Event())
    agent, _ = net.local_agent('permuter')
    try:
        # _hold rechecks the lease; another agent winning the race simply skips this pass.
        Permuter(net)._repair_round(agent,net.project_row(slug),state,function_id=fn['id'],budget=6,seconds=8)
    except Exception:
        log.exception('Local repair after release failed for %s',fn['name'])
        return '\nLocal repair could not finish; your saved candidate and release are retained.'
    if not state.repairs:
        return ''
    current = net._function(fn['name'],slug)
    outcome = 'MATCH! verified by the normal judge' if current['status']=='matched' else f"best score {current['best_score']}"
    return f'\nAutomatic local repair: {state.repairs} trials without an agent turn; {outcome}. Next claim starts from the best saved source.'


def handoff(net, slug, fn):
    """Queue the existing bounded CPU repair lane after release; one worker and one job per function."""
    sweeps=getattr(net,'deep_sweeps',None)
    if sweeps is None:
        return run(net,slug,fn)  # Coordination/test engines without background jobs retain the synchronous lane.
    if slug in net.links or fn['status']!='open' or not fn['best_source'] or not fn['best_score']:
        return ''
    jobs=sweeps.jobs
    active=sweeps.active.get(slug,{})
    if jobs.jobs.get(active.get('job'),{}).get('state')=='running':
        return '\nCPU handoff: the running deep pass owns this work; no duplicate repair job started. Further proposals remain for its next eligible turn.'
    from .. import candidate_scope, claim_stalls
    scope=(candidate_scope.key(net,net.project_row(slug),fn),hashlib.sha256(fn['best_source'].encode()).hexdigest(),repr(claim_stalls.repair_rules()))
    lock=net.__dict__.setdefault('_repair_handoff_lock',threading.RLock())
    pending=net.__dict__.setdefault('_repair_handoffs',{})
    gate=net.__dict__.setdefault('_repair_handoff_gate',threading.BoundedSemaphore(1))
    with lock:
        if getattr(net,'_repair_handoff_closing',False):
            return '\nCPU handoff deferred while the app stops. Saved C remains eligible for the next deep pass.'
        if fn['id'] in pending:
            return '\nCPU handoff already queued for this function; no duplicate work started.'
        if len(pending)>=8:
            return '\nCPU handoff queue is full. Saved C and remaining proposals are retained for the next deep pass.'
        pending[fn['id']]=scope
        def work(log):
            entered=False
            try:
                log.progress({'phase':'waiting for CPU repair slot','function':fn['name']})
                while not entered:
                    log.check_cancel()
                    entered=gate.acquire(timeout=.1)
                log.check_cancel()
                current=net.project_store(fn['project_id']).one('SELECT * FROM functions WHERE id=? AND project_id=?',fn['id'],fn['project_id'])
                if not current or current['status']!='open' or current['gone_mark'] is not None:
                    return {'skipped':'Function no longer eligible.'}
                fresh_scope=(candidate_scope.key(net,net.project_row(slug),current),hashlib.sha256((current['best_source'] or '').encode()).hexdigest(),repr(claim_stalls.repair_rules()))
                if fresh_scope!=scope:
                    return {'skipped':'Inputs changed; the next deep pass uses the new saved C.'}
                log.progress({'phase':'local repairs; up to 6 trials / 8 seconds','function':fn['name']})
                from . import deep_permuter
                log.maximum_seconds=8
                log.parallel_claims=False  # Background handoffs never search alongside a newly acquired paid claim.
                from ..network import NetworkError
                try:
                    from . import maintenance_tasks
                    try:  # in the permuter worker's process, away from the app's interpreter lock
                        result=maintenance_tasks.deep_run(net,slug,current['name'],1,6,log,maximum_seconds=8,parallel_claims=False)
                    except maintenance_tasks.Unavailable:
                        result=deep_permuter.run(net,slug,current['name'],1,6,log)
                except NetworkError:
                    if net.project_store(fn['project_id']).one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?',fn['id'],time.time()):
                        return {'skipped':'Another worker claimed this function; its lease is unchanged.'}
                    raise
                log.check_cancel()
                return {'function':fn['name'],'search':result,'compile_limit':6,'seconds':8}
            finally:
                if entered:
                    gate.release()
                with lock:
                    pending.pop(fn['id'],None)
                    getattr(net,'_repair_handoff_jobs',{}).pop(fn['id'],None)
        try:
            jid=jobs.start('CPU repair handoff',work,foreground=False,cancel_message='CPU repair handoff cancelled. Saved improvements are retained.')
            net.__dict__.setdefault('_repair_handoff_jobs',{})[fn['id']]=jid
        except ValueError:
            pending.pop(fn['id'],None)
            return '\nCPU handoff deferred while the app prepares an update. Saved C remains eligible for the next deep pass.'
        except BaseException:
            pending.pop(fn['id'],None)
            raise
    return f'\nCPU handoff queued: job {jid}. Up to 6 repair trials / 8 seconds, no AI tokens. Further proposals remain for the deep pass.'


def shutdown(net, jobs):
    lock=net.__dict__.setdefault('_repair_handoff_lock',threading.RLock())
    with lock:
        net._repair_handoff_closing=True
        for jid in list(getattr(net,'_repair_handoff_jobs',{}).values()):
            jobs.cancel(jid)
