"""Independent deep-search paths with one function budget and one published result."""
import threading
import time
from concurrent.futures import as_completed

from ..network import NetworkError
from .purpose_work import Executor


def validate_threads(count):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 256:
        raise NetworkError('Choose 1–256 threads per function.')
    return count


class Lane:
    def __init__(self, index, count, deadline, ready, preparation=None, shared=None):
        self.index, self.count, self.ready = index, count, ready
        # The threads share one deadline, which any thread's improvement can move back (extendable runs only).
        self.shared = shared if shared is not None else {'deadline': deadline, 'extendable': False}
        self.preflight_done = False
        self.preparation = preparation if preparation is not None else {}

    @property
    def deadline(self):
        return self.shared['deadline']

    @property
    def extendable(self):
        return self.shared['extendable']

    def extend(self, deadline):
        self.shared['deadline'] = max(self.shared['deadline'], deadline)

    def prepared(self):
        self.preflight_done = True
        if self.index == 0:
            self.ready.set()


def run(net, slug, name, minutes, variants, log, profiles, count, *, rank_candidates=False):
    from . import deep_permuter, workers
    proj, before = deep_permuter.validate_function(net, slug, name)
    from ..candidate_scope import key
    sweeps = getattr(net, 'deep_sweeps', None)
    owns_completion = sweeps is not None and not getattr(log, 'sweep_owns_completion', False)
    scope = key(net, proj, before) if owns_completion else None
    # Partition the allowance, including each path's baseline and diagnostics. More
    # threads must never silently multiply the user's requested compile limit.
    count = min(count, variants)
    shared = {'deadline': time.monotonic()+min(minutes*60, getattr(log, 'maximum_seconds', minutes*60)),
              'extendable': getattr(log, 'maximum_seconds', None) is None}
    ready, stopped, lock = threading.Event(), threading.Event(), threading.RLock()
    updates, results = {}, []
    preparation = {}

    def check():
        log.check_cancel()
        if stopped.is_set():
            raise deep_permuter.SearchLimit('Parallel search stopped')
        if net._function(name, slug)['status'] == 'matched':
            raise deep_permuter.SearchLimit('Matched')
        if time.monotonic() >= shared['deadline']:
            raise deep_permuter.SearchLimit('Time limit reached')

    def publish(index, update):
        with lock:
            updates[index] = update
            values = list(updates.values())
            aggregate = dict(update)
            from .search_efficiency import combine
            aggregate['efficiency']=combine([v.get('efficiency') for v in values])
            for field in ('tested', 'skipped', 'rejected', 'visited', 'improvements', 'refinement_rounds',
                          'allocation_trials', 'allocation_captures', 'allocation_diagnostic_compiles'):
                aggregate[field] = sum(v.get(field, 0) for v in values)
            scores = [v['best_score'] for v in values if v.get('best_score') is not None]
            if scores:
                winner = min(values, key=lambda v: v.get('best_score') if v.get('best_score') is not None else float('inf'))
                aggregate.update(best_score=min(scores), best_percent=winner.get('best_percent'))
                if sweeps:
                    sweeps.improvement(before, min(scores))
            aggregate.update(variant_limit=variants + sum(v.get('extra_variants', 0) for v in values), threads_per_function=count,
                             starting_score=before['best_score'])
            log.progress(aggregate)

    def work(index):
        lane = Lane(index, count, shared['deadline'], ready, preparation, shared)
        def output(line):
            with lock:
                log(f'Thread {index+1}/{count}: {line}')
        output.compile_pool = getattr(log, "compile_pool", None)
        output.miss_cache = getattr(log, 'miss_cache', None)
        output.check_cancel = check
        output.progress = lambda update: publish(index, update)
        output.maximum_seconds = max(0, shared['deadline']-time.monotonic())
        try:
            # Preparation can replace private source/header context. Finish it once,
            # before the other paths capture evidence for their independent judges.
            if index:
                while not ready.wait(.1):
                    check()
            check()
            allowance = variants//count + (index < variants % count)
            order = {'rank_candidates': True} if rank_candidates else {}
            return deep_permuter._run(net, slug, name, minutes, allowance, output, profiles, lane=lane, **order)
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
    with Executor(max_workers=count, purpose='Per-function deep search', project=slug, thread_name_prefix='deep-function', initializer=None if getattr(getattr(log, 'compile_pool', None), 'maximum', False) else workers.started_priority) as pool:
        futures = [pool.submit(work, index) for index in range(count)]
        for future in as_completed(futures):
            results.append(future.result())
    current = net._function(name, slug)
    if current['status'] == 'matched' and hasattr(net, '_candidate_checks') and slug not in net.links:
        from .export_source_recovery import run as repair_export
        from .permute import State
        used = sum(v.get('tested', 0) for v in results)
        exported = repair_export(net, net.project_row(slug), {name}, State(), log,
                                 budget=min(4, max(0, variants-used)), deadline=shared['deadline'])
        results.append({'tested': exported['tested']})
    values = list(updates.values())
    winner = min(values, key=lambda v: v.get('best_score') if v.get('best_score') is not None else float('inf')) if values else {}
    result = dict(winner)
    from .search_efficiency import combine
    result['efficiency']=combine([v.get('efficiency') for v in results])
    for field in ('tested', 'skipped', 'rejected', 'improvements', 'refinement_rounds',
                  'allocation_trials', 'allocation_captures', 'allocation_diagnostic_compiles', 'extra_variants'):
        result[field] = sum(v.get(field, 0) for v in results)
    matched = current['status'] == 'matched'
    reason = ('Matched' if matched else 'Time limit reached' if time.monotonic() >= shared['deadline']
              else 'Variant limit reached' if result['tested'] >= variants + result['extra_variants'] else 'Exact diagnostic needs ordinary review' if preparation.get('diagnostic_exact') else 'Search paths finished')
    if not matched and current['best_source']:
        from .targeted_retry import source_key

        key = source_key(current['best_source'])
        for item in results:
            if (residual := item.get('targeted_retry')) and residual['source'] == key:
                result['targeted_retry'] = residual
                break
    result['reviewed_seeds'] = preparation
    result.update(function=name, matched=matched, reason=reason, compiler_profiles=profiles,
                  threads_per_function=count, best_score=current['best_score'], starting_score=before['best_score'],
                  variant_limit=variants + result['extra_variants'])
    log.progress(result)
    log(f'{reason}: {result["tested"]:,} candidates tested across {count} threads. Verified improvements are saved immediately.')
    if owns_completion:
        sweeps.completed(slug, before, minutes, variants, result, scope)
    return result
