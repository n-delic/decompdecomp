"""Independent deep-search paths with one function budget and one published result."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..network import NetworkError


def validate_threads(count):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 256:
        raise NetworkError('Choose 1–256 threads per function.')
    return count


class Lane:
    def __init__(self, index, count, deadline, ready, preparation=None):
        self.index, self.count, self.deadline, self.ready = index, count, deadline, ready
        self.preflight_done = False
        self.preparation = preparation if preparation is not None else {}

    def prepared(self):
        self.preflight_done = True
        if self.index == 0:
            self.ready.set()


def run(net, slug, name, minutes, variants, log, profiles, count):
    from . import deep_permuter, workers
    proj, before = deep_permuter.validate(net, slug, name, minutes, variants)
    from ..candidate_scope import key
    sweeps = getattr(net, 'deep_sweeps', None)
    scope = key(net, proj, before) if sweeps else None
    # Partition the allowance, including each path's baseline and diagnostics. More
    # threads must never silently multiply the user's requested compile limit.
    count = min(count, variants)
    deadline = time.monotonic()+min(minutes*60, getattr(log, 'maximum_seconds', minutes*60))
    ready, stopped, lock = threading.Event(), threading.Event(), threading.RLock()
    updates, results = {}, []
    preparation = {}

    def check():
        log.check_cancel()
        if stopped.is_set():
            raise deep_permuter.SearchLimit('Parallel search stopped')
        if net._function(name, slug)['status'] == 'matched':
            raise deep_permuter.SearchLimit('Matched')
        if time.monotonic() >= deadline:
            raise deep_permuter.SearchLimit('Time limit reached')

    def publish(index, update):
        with lock:
            updates[index] = update
            values = list(updates.values())
            aggregate = dict(update)
            for field in ('tested', 'skipped', 'rejected', 'visited', 'improvements', 'refinement_rounds',
                          'allocation_trials', 'allocation_captures', 'allocation_diagnostic_compiles'):
                aggregate[field] = sum(v.get(field, 0) for v in values)
            scores = [v['best_score'] for v in values if v.get('best_score') is not None]
            if scores:
                winner = min(values, key=lambda v: v.get('best_score') if v.get('best_score') is not None else float('inf'))
                aggregate.update(best_score=min(scores), best_percent=winner.get('best_percent'))
                if sweeps:
                    sweeps.improvement(before, min(scores))
            aggregate.update(variant_limit=variants, threads_per_function=count,
                             starting_score=before['best_score'])
            log.progress(aggregate)

    def work(index):
        lane = Lane(index, count, deadline, ready, preparation)
        def output(line):
            with lock:
                log(f'Thread {index+1}/{count}: {line}')
        output.compile_pool = getattr(log, "compile_pool", None)
        output.check_cancel = check
        output.progress = lambda update: publish(index, update)
        output.maximum_seconds = max(0, deadline-time.monotonic())
        try:
            # Preparation can replace private source/header context. Finish it once,
            # before the other paths capture evidence for their independent judges.
            if index:
                while not ready.wait(.1):
                    check()
            check()
            allowance = variants//count + (index < variants % count)
            return deep_permuter._run(net, slug, name, minutes, allowance, output, profiles, lane=lane)
        except deep_permuter.SearchLimit as error:
            return dict(updates.get(index, {}), reason=str(error))
        except NetworkError:
            # An in-flight candidate may lose its open-function guard when another
            # path saves an exact match. It is then superseded, not a failed search.
            if net._function(name, slug)['status'] == 'matched':
                return dict(updates.get(index, {}), reason='Matched')
            stopped.set()
            raise
        except BaseException:
            stopped.set()
            raise
        finally:
            lane.prepared()

    log(f'Searching {name} with {count} threads; sharing {minutes} minutes and {variants:,} candidate checks.')
    with ThreadPoolExecutor(count, thread_name_prefix='deep-function', initializer=workers.lower_priority) as pool:
        futures = [pool.submit(work, index) for index in range(count)]
        for future in as_completed(futures):
            results.append(future.result())
    current = net._function(name, slug)
    if current['status'] == 'matched' and hasattr(net, '_candidate_checks') and slug not in net.links:
        from .export_source_recovery import run as repair_export
        from .permute import State
        used = sum(v.get('tested', 0) for v in results)
        exported = repair_export(net, net.project_row(slug), {name}, State(), log,
                                 budget=min(4, max(0, variants-used)), deadline=deadline)
        results.append({'tested': exported['tested']})
    values = list(updates.values())
    winner = min(values, key=lambda v: v.get('best_score') if v.get('best_score') is not None else float('inf')) if values else {}
    result = dict(winner)
    for field in ('tested', 'skipped', 'rejected', 'improvements', 'refinement_rounds',
                  'allocation_trials', 'allocation_captures', 'allocation_diagnostic_compiles'):
        result[field] = sum(v.get(field, 0) for v in results)
    matched = current['status'] == 'matched'
    reason = ('Matched' if matched else 'Time limit reached' if time.monotonic() >= deadline
              else 'Variant limit reached' if result['tested'] >= variants else 'Exact diagnostic needs ordinary review' if preparation.get('diagnostic_exact') else 'Search paths finished')
    result['reviewed_seeds'] = preparation
    result.update(function=name, matched=matched, reason=reason, compiler_profiles=profiles,
                  threads_per_function=count, best_score=current['best_score'], starting_score=before['best_score'],
                  variant_limit=variants)
    log.progress(result)
    log(f'{reason}: {result["tested"]:,} candidates tested across {count} threads. Verified improvements are saved immediately.')
    if sweeps:
        sweeps.completed(slug, before, minutes, variants, result, scope)
    return result
