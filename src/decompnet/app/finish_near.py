"""An explicit, cancellable near-match pass reusing persisted iterative frontiers and the normal judge."""
import hashlib
import threading
import time

from ..network import NetworkError
from . import free_candidates, iterative_repairs
from .twins import Job

SCHEMA='''CREATE TABLE IF NOT EXISTS near_pass_visits(
 function_id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, scope TEXT NOT NULL, visited REAL NOT NULL)'''


def validate(net, slug, seconds, attempts):
    proj=net.project_row(slug)
    if not net.is_rom(proj) or proj['arch']!='mips' or net.fork_slug(slug) or slug in net.links:
        raise NetworkError('Finish near matches currently needs a MIPS ROM project. Other projects can use per-function repairs.')
    if isinstance(seconds,bool) or not isinstance(seconds,int) or not 1<=seconds<=600:
        raise ValueError('Choose 1–600 seconds.')
    if isinstance(attempts,bool) or not isinstance(attempts,int) or not 1<=attempts<=1024:
        raise ValueError('Choose 1–1024 candidate attempts.')
    return proj


def scope(net, proj, fn):
    from .. import candidate_scope
    return hashlib.sha256(repr((fn['best_source'],fn['best_score'],candidate_scope.key(net,proj,fn))).encode()).hexdigest()


def ordered(net, proj, rows):
    """Visit untouched/changed inputs first, then oldest visits, then closest normalized scores."""
    with net.project_store(proj['id']).tx() as db:db.execute(SCHEMA)
    visits={r['function_id']:r for r in net.project_store(proj['id']).all('SELECT * FROM near_pass_visits WHERE project_id=?',proj['id'])}
    def key(fn):
        visit=visits.get(fn['id'])
        age=visit['visited'] if visit and visit['scope']==scope(net,proj,fn) else 0
        return age,fn['best_score']/max(1,fn['n_insns']),fn['id']
    return sorted(rows,key=key)


def visited(net, proj, fn):
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO near_pass_visits VALUES(?,?,?,?)',(fn['id'],proj['id'],scope(net,proj,fn),time.time()))


def run(net, slug, seconds, attempts, log):
    validate(net,slug,seconds,attempts)
    phase=Job(stop=getattr(log,'cancel_event',threading.Event()))
    phase.finish_near=True
    free_candidates.budget(phase,seconds,attempts)
    started=time.monotonic()
    def progress(completed,total,name):
        elapsed=time.monotonic()-started
        if not phase.stop.is_set():
            log.progress(dict(completed=completed,total=total,function=name,matched=phase.reuse_matched,
                              attempts=phase.reuse_attempts,elapsed_seconds=elapsed,
                              matches_per_minute=phase.reuse_matched*60/max(elapsed,0.001)))
    phase.near_progress=progress
    log('Finishing closest saved C with local repairs. Existing paths resume; claimed functions are skipped.')
    matched=iterative_repairs.run(net.twins,slug,phase)
    elapsed=time.monotonic()-started
    result=dict(matched=matched,attempts=phase.reuse_attempts,seconds=elapsed,
                matches_per_minute=matched*60/max(elapsed,.001),resumable=True)
    if not phase.stop.is_set():log(f'Finished: {matched} matches from {phase.reuse_attempts} attempts in {elapsed:.1f}s. Run again to continue pending paths.')
    return result
