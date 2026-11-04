"""Cheap, read-only inventory of app work; controls delegate to each worker's existing safe stop path."""
import os
import threading
import time

from ..network import NetworkError
from . import workers
from .thread_activity import Sampler


class Monitor:
    def __init__(self, net, jobs, manager):
        self.net, self.jobs, self.manager = net, jobs, manager
        self._sample = None
        self.thread_sampler = Sampler()
        self._lock = threading.Lock()

    def snapshot(self):
        now = time.monotonic()
        cpu = sum(os.times()[:2])
        with self._lock:
            previous = self._sample
            # Repeated requests share a sample, rather than measuring near-zero intervals.
            if previous is None or now-previous[0] >= 1:
                percent = None if previous is None else max(0, 100*(cpu-previous[1])/(now-previous[0]))
                self._sample = (now, cpu, percent)
            percent = self._sample[2]
        rows = []
        def add(kind, key, title, state, project='', detail='', stoppable=True):
            rows.append(dict(kind=kind, key=str(key), title=title, state=state, project=project,
                             detail=str(detail or ''), stoppable=stoppable, cpu_percent=None))
        with self.jobs._lock:
            for jid, job in list(self.jobs.jobs.items()):
                if job['state'] != 'running':
                    continue
                progress = job.get('progress') or {}
                stopping = self.jobs.cancel_events.get(jid)
                add('job', jid, job['kind'], 'stopping' if stopping and stopping.is_set() else 'running',
                    project=progress.get('slug',''), detail=progress.get('phase') or 'Working; stop takes effect at the next cancellation checkpoint.')
        for attr, label in [('drafter','Machine drafts'), ('permuter','Background permuter'), ('twins','Identical-function reuse')]:
            component = getattr(self.net, attr, None)
            for slug, state in list(getattr(component, 'jobs', {}).items()):
                if state.state in ('running', 'update_wait'):
                    stopping = getattr(state, 'stop', None)
                    add(attr, slug, label, 'stopping' if stopping and stopping.is_set() else state.state, slug,
                        getattr(state, 'current', '') or 'Waiting or processing queued functions')
        for slug, state in list(self.net.autopilots.items()):
            if state.get('state') in ('running','update_wait'):
                add('autopilot',slug,'Autopilot','stopping' if state['stop'].is_set() else state['state'],slug,state.get('phase',''))
        for sid, session in list(self.manager.sessions.items()):
            if session.state in ('starting','working','waiting','stopping'):
                add('agent',sid,session.label,session.state,session.slug,session.why)
        with self.net.progress.activity_lock:
            activity = list(self.net.progress.activity.items())
        for slug, state in activity:
            if not state.get('paused'):
                add('verification',slug,'Saved-file verification','running',slug,state.get('phase',''),False)
        for attr, label in [('rooms','Hosted room'),('links','Room connection')]:
            for slug in list(getattr(self.net, attr, {})):
                add('service',f'{attr}:{slug}',label,'service',slug,'Managed in Decompile together',False)
        for attr, label in [('rangechecks','Compiler range checks'),('filecompilers','File compiler checks')]:
            component = getattr(self.net, attr, None)
            for slug in list(getattr(component, '_draining', set())):
                add('service',f'{attr}:{slug}',label,'queued or running',slug,'Automatic evidence checks; managed in project settings',False)
        for surveys, label in [(getattr(self.net,'surveys',{}),'Compiler survey'),
                               (getattr(getattr(self.net,'filecompilers',None),'surveys',{}),'File compiler survey')]:
            for slug, state in list(surveys.items()):
                if state.get('state') == 'running':
                    add('service',f'survey:{label}:{slug}',label,'running',slug,'No safe stop available for this automatic check',False)
        thread_details = self.thread_sampler.snapshot()
        threads = [t['name'] for t in thread_details]
        return dict(items=rows, cpu_percent=percent, cores=os.cpu_count() or 1, threads=threads, thread_details=thread_details,
                    workers=workers.WORKERS, default_workers=workers.default_workers(),
                    cpu_note='App Python process only; 100% means one CPU core. Compiler subprocesses and the window are excluded. Jobs share threads and cannot be assigned accurate individual CPU percentages.')

    def stop(self, kind, key):
        if kind == 'job':
            if not self.jobs.cancel(key):
                raise NetworkError('This job has already finished. Refresh the list.')
        elif kind in ('drafter','permuter','twins'):
            component = getattr(self.net, kind)
            if key not in component.jobs:
                raise NetworkError('This background worker no longer exists. Refresh the list.')
            component.stop(key)
        elif kind == 'autopilot':
            self.net.stop_autopilot(key)
        elif kind == 'agent':
            if key not in self.manager.sessions:
                raise NetworkError('This agent has already stopped. Refresh the list.')
            self.manager.stop(key)
        else:
            raise NetworkError('This service cannot be stopped here. Use its project controls.')
        return {'requested':True}
