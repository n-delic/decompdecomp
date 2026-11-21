"""Independent similar-source budget, shared across an entire automatic reuse wave.

Forward progress, cancellation and ordinary cross-game counters to the owning job;
keep this phase's limits separate so earlier searches cannot starve it.
"""
import threading
import time

from . import free_candidates, similar_matches


class Budget:
    def __init__(self, parent, seconds, attempts):
        object.__setattr__(self, '_parent', parent)
        object.__setattr__(self, '_local', {})
        free_candidates.budget(self, seconds, attempts)
        self._local.update(cross_started=time.monotonic(), cross_seconds=seconds if seconds != float('inf') else 0)

    def __getattr__(self, name):
        if name.startswith('reuse_') or name in ('cross_started', 'cross_seconds'):
            try:
                return self._local[name]
            except KeyError:
                raise AttributeError(name) from None
        return getattr(self._parent, name)

    def __setattr__(self, name, value):
        if name.startswith('reuse_'):
            self._local[name] = value
        else:
            if name == 'progress' and not getattr(self._parent, 'cross_exhaustive', False):
                value.update(attempts=self.reuse_attempts, attempt_limit=self.reuse_limit,
                             confirmed=self.reuse_matched, second_limit=self.cross_seconds)
            setattr(self._parent, name, value)


def run(twins, slug, job):
    phase = getattr(job, '_similar_budget', None)
    if phase is None:
        manual = getattr(job, 'cross_exhaustive', False)
        phase = Budget(job, float('inf') if manual else similar_matches.MAX_SECONDS,
                       float('inf') if manual else similar_matches.MAX_ATTEMPTS)
        job._similar_budget = phase
    try:
        if getattr(job, '_parallel_donors', False):
            return parallel(twins, slug, phase)
        return similar_matches.run(twins, slug, phase)
    finally:
        job.similar_attempts = phase.reuse_attempts
        job.similar_matched = phase.reuse_matched
        job.similar_bytes = phase.reuse_bytes


def totals(job):
    return tuple(getattr(job, a, 0) + getattr(job, b, 0) for a, b in
                 [('reuse_attempts', 'similar_attempts'), ('reuse_matched', 'similar_matched'),
                  ('reuse_bytes', 'similar_bytes')])


class SearchStopped(Exception):
    """The manual search was cancelled by its owning job."""


def search_partition(net, slug, function_ids, stop, progress):
    """Judge a disjoint subset of this project's functions in one interpreter."""
    from .. import compiler
    from ..compile_sessions import session
    from . import maintenance
    from .twins import Job

    job = Job(stop=stop)
    job.cross_exhaustive = True
    job.promote()
    free_candidates.budget(job, seconds=float('inf'), attempts=float('inf'))
    last_report = 0.0

    def report(snapshot):
        nonlocal last_report
        now = time.monotonic()
        if now - last_report >= .25:
            last_report = now
            progress(snapshot)
    job.cross_report = report
    def check_cancel():
        if stop.is_set():
            raise SearchStopped()

    try:
        with maintenance.foreground(job.foreground), maintenance.cancellation(stop), session(), compiler.cancellable(check_cancel):
            similar_matches.run(net.twins, slug, job, function_ids=function_ids)
    except (SearchStopped, maintenance.Cancelled):
        pass
    return {'tried': job.cross_tried, 'candidates': job.cross_candidates, 'matched': job.cross_matched,
            'bytes': job.reuse_bytes}


def parallel(twins, slug, job):
    from .. import compiler
    from . import deep_lanes, workers
    from .cross_game_progress import report
    from .purpose_work import Executor

    net = twins.net
    proj = net.project_row(slug)
    if not similar_matches.destination_supported(net, proj):
        return 0
    if job.stop.is_set():
        return 0
    targets = [row['id'] for row in net.project_store(proj['id']).all(
        "SELECT id FROM main.functions WHERE project_id=? AND status='open' AND size BETWEEN ? AND ? ORDER BY size DESC, id",
        proj['id'], similar_matches.MIN_BYTES, similar_matches.MAX_BYTES)]
    width = min(workers.WORKERS, compiler.concurrency_limit(), len(targets))
    if width < 2:
        return similar_matches.run(twins, slug, job) if targets else 0

    report(job, 'Preparing verified donor indexes')
    for other in similar_matches.donor_projects(net, proj).values():
        if job.stop.is_set():
            return 0
        twins.index_project(other)

    # Balance long and short functions across lanes. A destination and its history
    # belong to one lane for the entire pass, including its follow-up repairs.
    partitions = [targets[index::width] for index in range(width)]
    lanes = deep_lanes.Lanes(net, width, width, maximum=True)
    lock = threading.Lock()
    snapshots = {}
    finished = {}
    positions = {}
    initial_tried, initial_matched = job.cross_tried, job.cross_matched

    def publish():
        # Budget.progress normally shows a single search. The owning job should
        # display the totals across the completed stages and all running lanes.
        active = [snapshots[index] for index in snapshots if index not in finished]
        attempts = sum(item['tried'] for item in finished.values()) + sum(item.get('attempts', 0) for item in active)
        matched = sum(item['matched'] for item in finished.values()) + sum(item.get('confirmed', 0) for item in active)
        snapshot = {
            'phase': f'Checking similar donors with {width} workers',
            'attempts': initial_tried + attempts, 'confirmed': initial_matched + matched,
            'done': sum(positions.values()), 'total': 2 * len(targets),
            'function': next((item['function'] for item in reversed(active) if item.get('function')), ''),
        }
        job.progress = snapshot
        if callback := getattr(job, 'cross_report', None):
            callback(snapshot)

    def search(item):
        index, ids = item
        class Log:
            def check_cancel(self):
                if job.stop.is_set():
                    raise SearchStopped()

            def progress(self, snapshot):
                self.check_cancel()
                with lock:
                    snapshots[index] = snapshot
                    if snapshot.get('done') is not None:
                        positions[index] = snapshot['done']
                    publish()
        lane_log = Log()
        try:
            try:
                result = lanes.donors(slug, ids, lane_log)
            except deep_lanes.Unavailable:
                lane_log.check_cancel()
                result = search_partition(net, slug, ids, job.stop, lane_log.progress)
        except SearchStopped:
            return
        with lock:
            finished[index] = result
            positions[index] = 2 * len(ids)
            publish()

    try:
        with lock:
            publish()
        with Executor(max_workers=width, purpose='Cross-game donor workers', project=slug, stop=job.stop.set) as pool:
            list(pool.map(search, enumerate(partitions)))
    finally:
        lanes.close()
    job.cross_tried += sum(result['tried'] for result in finished.values())
    job.cross_candidates += sum(result['candidates'] for result in finished.values())
    job.reuse_bytes += sum(result['bytes'] for result in finished.values())
    matched = sum(result['matched'] for result in finished.values())
    job.cross_matched += matched
    job.reuse_matched += matched
    job.reuse_attempts += sum(result['tried'] for result in finished.values())
    return matched
