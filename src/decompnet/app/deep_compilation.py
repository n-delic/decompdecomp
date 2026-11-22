"""Compile independent ROM variants ahead; judging and publication remain on the search lane."""
import copy
import functools
import queue
import threading
import time
from collections import deque
from concurrent.futures import CancelledError, Future
from contextlib import contextmanager, nullcontext

from .. import compile_sessions, compile_sharing, compile_timing, compile_worker, compiler
from ..candidate_work import preparation as candidate_preparation


class Ticket:
    batch = None  # (group, net, proj, header, fn) when it may compile with others (Pool._run_batch)

    def __init__(self, key, snapshot, run, check):
        self.key, self.snapshot, self.run, self.check = key, snapshot, run, check
        self.future = Future()
        self.stopped = threading.Event()
        self.counted = False
        self.compile_counts = compile_timing.Counts()

    def cancel(self):
        self.stopped.set()
        return self.future.cancel()

    def checkpoint(self):
        if self.stopped.is_set():raise CancelledError()
        self.check()


class Pool:
    def __init__(self, count=None, *, maximum=False, remote=True):
        from .deep_throughput import Metrics
        self.count = count or compiler.concurrency_limit()
        self.maximum = maximum
        self.remote = remote  # compiles in child processes (compile_worker.py); a deep lane compiles in-thread
        # A lane's compile threads compile several candidates of one function per compiler run (batch_compile.py):
        # one run takes one compile slot for up to MAX candidates, so as many more may wait for it.
        from ..batch_compile import MAX as BATCH
        self.batching = 1 if remote else BATCH
        self.unbatchable = set()  # groups whose candidates a batch can't give (their code refers to file-local data)
        self.slots = compiler.Admission(self.count)
        self.metrics = Metrics()
        self.metrics.gate = self.slots
        self.metrics.maximum = maximum
        self.queue = queue.Queue(maxsize=self.count*2*self.batching)
        self.group = compile_sharing.Group()
        self.workers = []
        self.lock = threading.Lock()
        self.baselines = {}
        self.proposals = None
        self.capacity = threading.BoundedSemaphore(self.count*2*self.batching)
        self.admission = threading.Lock()
        self.lanes = 0  # searches preparing or holding a window now

    def proposal_pool(self):
        with self.lock:
            if self.proposals is None:
                from .deep_proposals import Pool
                # Balanced searches also need independent Python interpreters: hundreds
                # of function threads otherwise serialize pure generation behind the GIL.
                self.proposals = Pool(self.count, background=not self.maximum)
                self.metrics.proposals = self.proposals
            return self.proposals

    @contextmanager
    def window(self, tickets, check):
        # Admit a whole small chunk atomically. Partial windows held by several
        # lanes would otherwise deadlock while each waited to submit its remainder.
        # Each permit returns when its own compile ends (or is canceled), not when the lane has judged the
        # whole chunk: a lane judges one candidate at a time, and holding every permit until then let one lane
        # occupy the entire capacity while the compilers sat idle and every other lane queued here. That capped
        # a sweep at one lane's judging speed (about 100 candidates a second) whatever the compile slots.
        acquired = 0
        locked = False
        handed = False
        try:
            if tickets:
                while not self.admission.acquire(timeout=.05):check()
                locked = True
                for _ in tickets:
                    while not self.capacity.acquire(timeout=.05):check()
                    acquired += 1
                self.admission.release();locked = False
                for ticket in tickets:
                    ticket.future.add_done_callback(lambda _:self.capacity.release())
                handed = True
            yield
        finally:
            if locked:self.admission.release()
            if not handed:
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
        from .workers import started_priority
        if not self.maximum:started_priority()
        from .deep_throughput import scope
        # Each compile thread hands its compiles to a child process of its own (compile_worker.py): their Python
        # no longer queues on the app's one interpreter lock.
        with scope(self), compile_sessions.session(), compile_sharing.group(self.group), \
                (compile_worker.remote(background=not self.maximum) if self.remote else nullcontext()):
            pending = deque()
            while (ticket := pending.popleft() if pending else self.queue.get()) is not None:
                if not ticket.future.set_running_or_notify_cancel():continue
                group = [ticket]
                if (self.batching > 1 and getattr(ticket, 'batch', None) is not None
                        and ticket.batch[0] not in self.unbatchable):
                    # Queued candidates of the same function and header join this compiler run.
                    while len(group) < self.batching:
                        try:
                            other = self.queue.get_nowait()
                        except queue.Empty:
                            break
                        if other is None or getattr(other, 'batch', None) is None or other.batch[0] != ticket.batch[0]:
                            pending.append(other)
                            break
                        if other.future.set_running_or_notify_cancel():
                            group.append(other)
                if len(group) > 1:
                    self._run_batch(group)
                else:
                    self._run_one(ticket)

    def _run_one(self, ticket):
        try:
            ticket.checkpoint()
            with compiler.cancellable(ticket.checkpoint), compile_timing.collect(ticket.compile_counts, append=True):
                result = ticket.run()
            ticket.checkpoint()
            ticket.future.set_result(result)
        except BaseException as error:
            ticket.future.set_exception(error)

    def _run_batch(self, group):
        """One compiler run for `group`; a candidate the batch can't take compiles on its own after it."""
        _, net, proj, header, fn = group[0].batch
        results = None
        try:
            group[0].check()
            with compiler.cancellable(group[0].check), compile_timing.collect(group[0].compile_counts, append=True):
                results = net._compile_batch(proj, header, [t.batch_source for t in group], fn)
        except BaseException as error:
            for ticket in group:
                ticket.future.set_exception(error)
            return
        alone = sum(1 for index in range(len(group)) if not results or results[index] is None)
        if alone:
            compile_timing.add('batch_alone', alone)
        if results and alone == len(group) and all(r is None for r in results) and _all_local(group):
            # It compiled, and none could be used: a function that refers to its file's own data. One by one.
            with self.lock:
                self.unbatchable.add(group[0].batch[0])
        for index, ticket in enumerate(group):
            result = results[index] if results else None
            if result is None:
                self._run_one(ticket)
                continue
            if ticket.stopped.is_set():
                ticket.future.set_exception(CancelledError())
            else:
                ticket.future.set_result(result)

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
    header = search.net._context(proj['id'], fn['unit'])['header']
    tickets = {}
    def check():
        search.log.check_cancel()
        if time.monotonic() >= search.deadline:
            from .deep_permuter import SearchLimit
            raise SearchLimit('Time limit reached')
    with pool.lock:pool.lanes += 1
    # Each lane asks for its share of the capacity: one lane asking for all of it held the admission lock while the
    # permits came back one by one, and the other lanes queued behind it. A lane alone still fills the pipeline.
    with pool.lock:share = max(2, pool.count*2*getattr(pool, 'batching', 1)//max(1, pool.lanes))
    try:
        with candidate_preparation():
            for source in sources:
                if len(tickets) >= min(share, search.limit-search.tested):break
                if source in tickets or source == fn['best_source']:continue
                if random_safety.unsafe_update(fn['best_source'], source) or random_safety.invalid_candidate_call(source, fn['name'], header):continue
                key = search._recent_key(proj, fn, source, fork)
                if key is None:continue  # External includes/custom compilers need ordinary fresh capture.
                recent=getattr(search,'_recent_candidates',{}).get(key)
                if recent is not None and recent[0]>=search.best:continue
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
                if hasattr(search.net, '_compile_batch'):
                    # what batch_compile needs: the run's group (one function, one header) and this candidate
                    ticket.batch = ((id(search.net), proj['id'], fn['id'], header), search.net, proj, header, fn)
                    ticket.batch_source = source
                tickets[source] = ticket
        with pool.window(list(tickets.values()), check):
            for ticket in tickets.values():pool.submit(ticket)
            search._ahead = tickets
            try:yield
            finally:
                # Cancel before another lane is admitted into this window.
                discard(search)
    finally:
        with pool.lock:pool.lanes -= 1
        search._ahead = {}
        for ticket in tickets.values():
            if not ticket.counted and not ticket.cancel():
                # Running speculative work consumes allowance even if a better parent
                # makes its result obsolete. Queued work canceled before starting does not.
                search.tested += 1
                ticket.counted = True
            else:ticket.cancel()



def _all_local(group):
    """Whether the last batch's candidates were all set aside for referring to file-local data (batch_compile)."""
    return group[0].compile_counts.values.get('batch_alone_local', 0) >= len(group)  # (this batch's own counts)


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
        try:
            # Policy/history reads around the judge use the same DB snapshot semantics;
            # they must not trigger saved-tree synchronization before each candidate.
            # Revisions are read once per candidate, as within an agent's tool call (Store.versions_held): a
            # candidate asked read_revisions 29 times, each through the connection lock every lane shares.
            from ..store import Store
            with (self.net.progress.view() if hasattr(self.net, "progress") else nullcontext()), Store.versions_held():
                return function(self, agent, proj, fn, source, best, fork)
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
                      'winner_output', 'winner_output_granularity', 'winner_locations', 'winner_scope', 'winner_jump_tables')
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
