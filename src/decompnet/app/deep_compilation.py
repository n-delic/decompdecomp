"""Compile independent ROM variants ahead; judging and publication remain on the search lane."""
import copy
import functools
import queue
import threading
import time
from concurrent.futures import CancelledError, Future
from contextlib import contextmanager

from .. import compile_sessions, compile_sharing, compiler


class Ticket:
    def __init__(self, key, snapshot, run, check):
        self.key, self.snapshot, self.run, self.check = key, snapshot, run, check
        self.future = Future()
        self.stopped = threading.Event()
        self.counted = False

    def cancel(self):
        self.stopped.set()
        return self.future.cancel()

    def checkpoint(self):
        if self.stopped.is_set():raise CancelledError()
        self.check()


class Pool:
    def __init__(self, count=None, *, maximum=False):
        from .deep_throughput import Metrics
        self.count = count or min(4, compiler.concurrency_limit())
        self.maximum = maximum
        self.slots = compiler.Admission(self.count)
        self.metrics = Metrics()
        self.metrics.gate = self.slots
        self.metrics.maximum = maximum
        self.queue = queue.Queue(maxsize=self.count*2)
        self.group = compile_sharing.Group()
        self.workers = []
        self.lock = threading.Lock()
        self.baselines = {}
        self.proposals = None
        self.capacity = threading.BoundedSemaphore(self.count*2)
        self.admission = threading.Lock()

    def proposal_pool(self):
        with self.lock:
            if self.proposals is None:
                from .deep_proposals import Pool
                self.proposals = Pool(self.count)
                self.metrics.proposals = self.proposals
            return self.proposals

    @contextmanager
    def window(self, count, check):
        # Admit a whole small chunk atomically. Partial windows held by several
        # lanes would otherwise deadlock while each waited to submit its remainder.
        acquired = 0
        locked = False
        try:
            if count:
                while not self.admission.acquire(timeout=.05):check()
                locked = True
                for _ in range(count):
                    while not self.capacity.acquire(timeout=.05):check()
                    acquired += 1
                self.admission.release();locked = False
            yield
        finally:
            if locked:self.admission.release()
            for _ in range(acquired):self.capacity.release()

    def submit(self, ticket):
        with self.lock:
            if not self.workers:
                for index in range(self.count):
                    worker = threading.Thread(target=self.work, name=f'deep-compile-{index}', daemon=True)
                    self.workers.append(worker);worker.start()
        while True:
            ticket.checkpoint()
            try:self.queue.put(ticket, timeout=.05);return
            except queue.Full:pass

    def work(self):
        from .workers import lower_priority
        if not self.maximum:lower_priority()
        from .deep_throughput import scope
        with scope(self), compile_sessions.session(), compile_sharing.group(self.group):
            while (ticket := self.queue.get()) is not None:
                if not ticket.future.set_running_or_notify_cancel():continue
                try:
                    ticket.checkpoint()
                    with compiler.cancellable(ticket.checkpoint):result = ticket.run()
                    ticket.checkpoint()
                    ticket.future.set_result(result)
                except BaseException as error:
                    ticket.future.set_exception(error)

    def close(self):
        # Search chunk contexts cancel their own pending/running tickets first.
        if self.proposals is not None:self.proposals.close()
        for _ in self.workers:self.queue.put(None)
        for worker in self.workers:worker.join()


@contextmanager
def chunk(search, agent, proj, fn, sources, fork):
    pool = getattr(search, 'compile_pool', None)
    if (pool is None or fork or not hasattr(search.net, 'compiled_ahead')
            or len(getattr(search, 'compiler_profiles', [])) > 1):
        yield;return
    from . import random_safety
    from .repair_preflight import invalid_call
    header = search.net._context(proj['id'], fn['unit'])['header']
    tickets = {}
    def check():
        search.log.check_cancel()
        if time.monotonic() >= search.deadline:
            from .deep_permuter import SearchLimit
            raise SearchLimit('Time limit reached')
    try:
        for source in sources:
            if len(tickets) >= min(pool.count*2, search.limit-search.tested):break
            if source in tickets or source == fn['best_source']:continue
            if random_safety.unsafe_update(fn['best_source'], source) or invalid_call(source, fn['name'], header):continue
            key = search._recent_key(proj, fn, source, fork)
            if key is None:continue  # External includes/custom compilers need ordinary fresh capture.
            scope = getattr(search, '_primed_scopes', {}).get(key)
            if scope is not None:
                import hashlib
                import json
                signature = 'deep-v1:'+hashlib.sha256((scope+source).encode()).hexdigest()
                feedback = getattr(search, '_primed_trials', {}).get((fn['id'], signature))
                if feedback:
                    value = json.loads(feedback)
                    score = value.get('score') if isinstance(value, dict) else value
                    if isinstance(score, int) and score >= search.best:continue
            snapshot = search.net._compile_snapshot(proj, fn, header, source)
            ticket = Ticket(key[1], snapshot,
                            lambda source=source:search.net._compile(proj, header, source, fn), check)
            tickets[source] = ticket
        with pool.window(len(tickets), check):
            for ticket in tickets.values():pool.submit(ticket)
            search._ahead = tickets
            try:yield
            finally:
                # Cancel before another lane is admitted into this window.
                discard(search)
    finally:
        search._ahead = {}
        for ticket in tickets.values():
            if not ticket.counted and not ticket.cancel():
                # Running speculative work consumes allowance even if a better parent
                # makes its result obsolete. Queued work canceled before starting does not.
                search.tested += 1
                ticket.counted = True
            else:ticket.cancel()



def discard(search):
    for ticket in getattr(search, '_ahead', {}).values():
        if not ticket.counted and not ticket.cancel():
            search.tested += 1
            ticket.counted = True
        else:ticket.cancel()
    search._ahead = {}


def prepared(function):
    @functools.wraps(function)
    def run(self, agent, proj, fn, source, best, fork):
        previous = getattr(self, '_ahead_current', None)
        ticket = getattr(self, '_ahead', {}).get(source)
        if ticket is not None and ticket.counted:ticket = None
        self._ahead_current = ticket
        try:return function(self, agent, proj, fn, source, best, fork)
        finally:self._ahead_current = previous
    return run


def shared(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        log = kwargs.get('log') or args[5]
        pool = getattr(log, 'compile_pool', None)
        with compile_sharing.group(pool.group if pool else None):return function(*args, **kwargs)
    return run


def baseline(search, agent, proj, fn, state, fork, build):
    pool = getattr(search, 'compile_pool', None)
    key = search._recent_key(proj, fn, fn['best_source'], fork) if pool else None
    if key is None:return build()
    with pool.lock:
        future = pool.baselines.get(key)
        owner = future is None
        if owner:
            future = pool.baselines[key] = Future()
    if owner:
        try:
            measured = build()
            fields = ('starting_score', 'best', 'baseline_measured', 'winner_diff', 'winner_source',
                      'winner_output', 'winner_output_granularity', 'winner_locations', 'winner_scope')
            payload = (measured, {name:copy.deepcopy(getattr(search, name, None)) for name in fields})
            future.set_result(payload)
            with pool.lock:
                while len(pool.baselines)>16:
                    old = next((k for k, value in pool.baselines.items() if value.done()), None)
                    if old is None:break
                    del pool.baselines[old]
            return measured
        except BaseException as error:
            future.set_exception(error)
            with pool.lock:pool.baselines.pop(key, None)
            raise
    while not future.done():
        search.check()
        try:future.result(timeout=.05)
        except TimeoutError:pass
    measured, fields = copy.deepcopy(future.result())
    # A baseline can repair broken prototypes or discover an exact match. Reuse
    # only a non-winning baseline whose captured inputs have remained unchanged.
    if measured[0] is None or measured[0] <= 0 or key != search._recent_key(proj, fn, fn['best_source'], fork):return build()
    for name, value in fields.items():setattr(search, name, value)
    if fields.get('winner_diff'):
        search.net.keep_diff(agent.id, fn['id'], fields['winner_diff'][0])
    return measured[0], 0
