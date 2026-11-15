"""Coalesce new matches into bounded, low-priority cross-project searches, with no model calls.

One shared worker consumes bounded general and similar-source budgets for the entire cascade. Engine-only uses stay inert until the server
explicitly enables this service; experiments and tests must opt in. A stopped project stays paused until Run again.
"""

import json
import logging
import threading
import time

from . import free_candidates, named_donors, purpose_work, similar_phase
from .twins import Job

log = logging.getLogger('decompdecomp')


class Cascade:
    def __init__(self, net):
        self.net = net
        self.enabled = False
        self.seconds, self.attempts = 120, 128
        self.iterative_enabled = False
        self.iterative_seconds, self.iterative_attempts = 120, 128
        self.iterative_threads = 0  # functions repaired at once; 0: as many as the computer can spare (workers.py)
        self.pending = set()
        self.paused = set()
        self.lock = threading.Lock()
        self.running = False
        self.active = None

    def configure(self, enabled, seconds=120, attempts=128, iterative_enabled=False, iterative_seconds=120, iterative_attempts=128,
                  iterative_threads=0):
        with self.lock:
            self.iterative_enabled = bool(iterative_enabled)
            self.iterative_seconds = max(1, min(600, int(iterative_seconds)))
            self.iterative_attempts = max(1, min(1024, int(iterative_attempts)))
            self.iterative_threads = max(0, int(iterative_threads or 0))
            self.enabled = bool(enabled)
            self.seconds = max(1, min(600, int(seconds)))
            self.attempts = max(1, min(1024, int(attempts)))
            if not self.enabled:
                self.pending.clear()
                if self.active:
                    self.active.stop.set()

    def pause(self, slug):
        with self.lock:
            self.paused.add(slug)
            self.pending.discard(slug)
            if self.active and self.net.twins.jobs.get(slug) is self.active:
                self.active.stop.set()

    def resume(self, slug):
        with self.lock:
            self.paused.discard(slug)

    def notify(self,donor,fid):
        return self.notify_many(donor,[fid])

    def notify_many(self,donor,fids):
        """One discovery pass for a restored batch; no source payloads enter this scan."""
        if not self.enabled or not fids:return
        rows=self.net.project_store(donor['id']).all(
            "SELECT name,size,status FROM main.functions WHERE project_id=? AND id IN (SELECT value FROM json_each(?)) "
            "AND (status='matched' OR best_source IS NOT NULL AND length(best_source)>0)",donor['id'],json.dumps(list(fids)))
        if not rows:return
        # Merge overlapping size windows, preserving the old per-function predicate
        # without querying every other project once for every restored function.
        intervals=[]
        for start,end in sorted((int(r['size']*.9),int(r['size']/.9)) for r in rows):
            if intervals and start<=intervals[-1][1]:intervals[-1][1]=max(end,intervals[-1][1])
            else:intervals.append([start,end])
        names=[r['name'] for r in rows if r['status']=='matched' and named_donors.meaningful(r['name'])]
        from .project_activity import active
        from .shared_reuse import compatible
        candidates = list(compatible(self.net, donor, active_only=True))
        if any(r['status'] == 'matched' for r in rows) and active(self.net, donor['slug']):
            candidates.append(donor)  # A new verified sibling can unlock functions in its own project.
        projects=[p for p in candidates if self.net.project_store(p['id']).one(
            "SELECT 1 FROM functions WHERE project_id=? AND status IN ('open','nosource') AND "
            "(name IN (SELECT value FROM json_each(?)) OR EXISTS(SELECT 1 FROM json_each(?) w "
            "WHERE size BETWEEN json_extract(w.value,'$[0]') AND json_extract(w.value,'$[1]'))) LIMIT 1",
            p['id'],json.dumps(names),json.dumps(intervals))]
        with self.lock:
            if not self.enabled:
                return
            self.pending.update(p['slug'] for p in projects if p['slug'] not in self.net.links
                                and p['slug'] not in self.paused)
        self.wake()

    def wake(self, job=None):
        with self.lock:
            if not self.enabled or self.running or not self.pending:
                return
            self.running = True
        job = job or Job()
        purpose_work.start('Automatic matched-source reuse', '', self.drain, job, stop=job.stop.set)

    def drain(self, job=None):
        """Two wave-wide budgets, including new matches; no recursive worker per notification."""
        job = job or Job()
        failed = False
        deferred = set()
        free_candidates.budget(job, self.seconds, self.attempts)
        with self.lock:
            self.active = job
        try:
            while not self.exhausted(job):
                with self.lock:
                    available = self.pending - deferred
                    if not self.enabled or not available:
                        break
                    slug = sorted(available)[0]
                    self.pending.remove(slug)
                    from .project_activity import active
                    if slug in self.paused or not active(self.net,slug):
                        continue
                # Keep a notification until the owning pass finishes instead of silently dropping it.
                with self.net.twins._lock:
                    busy = self.net.twins._full_running(slug)
                    if not busy:
                        self.net.twins.jobs[slug] = job
                if busy:
                    with self.lock:
                        self.pending.add(slug)
                    deferred.add(slug)
                    continue
                started = time.monotonic()
                tries, count, size = similar_phase.totals(job)
                try:
                    if not free_candidates.exhausted(job):
                        job.matched += self.net.twins.across_games(slug, job)
                    if not free_candidates.exhausted(job):
                        job.matched += named_donors.run(self.net.twins, slug, job)
                    if not free_candidates.exhausted(job):
                        job.matched += free_candidates.run(self.net.twins, slug, job)
                    job.matched += similar_phase.run(self.net.twins, slug, job)
                    proj = self.net.project_row(slug)
                    total_tries, total_count, total_size = similar_phase.totals(job)
                    with self.net.project_store(proj['id']).tx() as db:
                        db.execute('INSERT INTO reuse_runs(project_id,attempts,matched,bytes,seconds,created_at) VALUES(?,?,?,?,?,?)',
                                   (proj['id'], total_tries - tries, total_count - count,
                                    total_size - size, time.monotonic() - started, time.time()))
                finally:
                    with self.net.twins._lock:
                        # Keep a completed snapshot for the GUI, without making later projects look active here.
                        total_tries, total_count, _ = similar_phase.totals(job)
                        snapshot = Job(state='stopped' if job.stop.is_set() else 'done', matched=total_count - count,
                                       cross_tried=total_tries - tries)
                        snapshot.ordered.set()
                        self.net.twins.jobs[slug] = snapshot
        except Exception:  # an optional heuristic must not interrupt agents or the server
            failed = True
            log.exception('automatic cross-project reuse failed')
        finally:
            job.state = 'stopped' if job.stop.is_set() else 'done'
            with self.lock:
                again = bool(not failed and self.enabled and self.pending - deferred and not self.exhausted(job))
                if not again:
                    # Deferred notifications are woken when the project's owning pass finishes.
                    self.pending.intersection_update(deferred if not failed and self.enabled and not job.stop.is_set() else set())
                self.active = None
                self.running = False
            if again:
                job.state = 'running'
                self.wake(job)
            elif deferred and not failed and not job.stop.is_set():
                # Close the race where the owning pass completed just before running became false.
                with self.net.twins._lock:
                    ready = any(not self.net.twins._full_running(slug) for slug in deferred)
                if ready:
                    self.wake()

    @staticmethod
    def exhausted(job):
        if job.stop.is_set():
            return True
        phase = getattr(job, '_similar_budget', None)
        return free_candidates.exhausted(job) and phase is not None and free_candidates.exhausted(phase)
