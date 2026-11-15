"""Admit routine maintenance only for recently viewed projects or explicit work.

Sidebar membership is not activity. Engine-only callers keep their existing policy.
"""
import threading
import time
from contextvars import ContextVar

current_project = ContextVar('current_project', default='')


def active(net, slug):
    policy = getattr(net, 'project_activity', None)
    return policy is None or policy.active(slug)


class Activity:
    def __init__(self, manager, jobs, clock=time.monotonic, lease=30, net=None):
        self.manager, self.jobs, self.clock, self.lease = manager, jobs, clock, lease
        self.net = net
        self.viewed = {}
        self.lock = threading.Lock()
        self.on_view = lambda slug: None

    def touch(self, slug):
        if not slug:return
        with self.lock:self.viewed[slug] = self.clock()
        self.on_view(slug)

    def active(self, slug):
        with self.lock:seen = self.viewed.get(slug, float('-inf'))
        if self.clock() - seen < self.lease:return True
        if any(s.slug == slug and s.state in {'starting','working','waiting'}
               for s in list(self.manager.sessions.values())):return True
        if self.net is not None:
            permuter = getattr(self.net, 'permuter', None)
            if permuter is not None and slug in getattr(permuter, 'explicit', set()) and slug in permuter._running:return True
            state = getattr(self.net, 'autopilots', {}).get(slug, {})
            foreground = state.get('_foreground')
            if state.get('state') == 'running' and foreground is not None and foreground.is_set():return True
            twins = getattr(getattr(self.net, 'twins', None), 'jobs', {}).get(slug)
            if twins and twins.state == 'running' and twins.foreground.is_set():return True
        with self.jobs._lock:
            return any(j['state'] == 'running' and (j.get('activity_project') == slug or j.get('scope', {}).get('project', j.get('scope', {}).get('slug')) == slug)
                       for j in self.jobs.jobs.values())

    def maintain(self, startup, tasks):
        """Queue each project's upgrades on demand, never preload every project store."""
        queued, done = set(), set()
        lock = threading.Lock()
        def opened(slug):
            scheduled = False
            for label, fn in tasks:
                key = (slug, label)
                with lock:
                    if key in queued or key in done:continue
                    queued.add(key)
                    scheduled = True
                def run(log, key=key, fn=fn, slug=slug):
                    try:
                        if not self.active(slug):return
                        check = log.check_cancel
                        def check_active():
                            check()
                            if not self.active(slug):
                                from .maintenance import Cancelled
                                raise Cancelled('Project is idle; maintenance will resume when opened')
                        log.check_cancel = check_active
                        fn(slug, log)
                        with lock:done.add(key)
                    finally:
                        with lock:queued.discard(key)
                startup.add(label, run, project=slug, with_log=True)
            if scheduled:startup.start()
        self.on_view = opened

    def retire(self):
        """Stop only automatic passes; deliberately started searches remain owners."""
        if self.net is None:return
        permuter = getattr(self.net, 'permuter', None)
        if permuter is not None:
            for slug in list(getattr(permuter, '_running', ())):
                if not self.active(slug):permuter.stop(slug)
        for slug, job in list(self.net.twins.jobs.items()):
            if job.state == 'running' and not job.foreground.is_set() and not self.active(slug):job.stop.set()
        for slug, state in list(self.net.autopilots.items()):
            if state.get('state') != 'running' or self.active(slug):continue
            state['stop'].set()
            job = self.net.twins.jobs.get(slug)
            if job and job.foreground is state.get('_foreground'):job.stop.set()
