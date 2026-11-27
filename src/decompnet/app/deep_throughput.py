"""Explicit throughput presets and bounded, source-free live counters."""
import functools
import os
import threading
import time
from collections import deque
from contextlib import ExitStack, contextmanager

from .. import compile_timing, compiler
from ..network import NetworkError


def options(maximum=False, slots=0):
    if not isinstance(maximum, bool):raise NetworkError('Maximum throughput must be true or false.')
    if isinstance(slots, bool) or not isinstance(slots, int) or not 0 <= slots <= 256:
        raise NetworkError('Choose 1–256 concurrent compiles, or leave blank for automatic.')
    return {'maximum_throughput':maximum, 'compile_slots':slots}


def width(maximum, slots):
    # Balanced searches use the shared compiler limit (cores less one, kept for interactive compiles), like a
    # sweep already did. A fixed 4 left an 18-core machine three-quarters idle on a single function's search.
    return slots or (min(256, 2*max(1, os.cpu_count() or 1)) if maximum else compiler.concurrency_limit())


def lanes(maximum):
    """How many functions a maximum-throughput pass searches at once when nothing is set: a lane spends most of its
    time waiting for its compiles (a 12-lane search used 30-60% of a core per lane on 18 cores), so about one and a
    half per core keep the machine busy. Each lane is an interpreter of its own: this uses memory, not the lock."""
    cores = max(1, os.cpu_count() or 1)
    return -(-3*cores//2) if maximum else compiler.concurrency_limit()


LIVE_COMPILER_COUNTERS = ('executing', 'waiting_search', 'waiting_background', 'waiting_shared')
LIVE_WORKER_COUNTERS = (*LIVE_COMPILER_COUNTERS, 'proposal_workers')
RANK_COUNTERS = ('rank_literals_cpu_ns', 'rank_batches', 'rank_candidates')
# How many compiler runs each judged candidate costs. A compile group is the queued candidates of one function that
# a worker takes together (up to batch_compile.MAX); batch_* say what became of them, ahead_* what became of the
# speculative candidates a lane's lookahead window compiled before they were wanted.
PIPELINE_COUNTERS = ('compile_groups', 'compile_group_tickets', 'batch_runs', 'batched_candidates', 'batch_alone',
                     'batch_alone_local', 'batch_alone_missing', 'batch_ineligible', 'batch_refused', 'batch_failed',
                     'ahead_submitted', 'ahead_used', 'ahead_cancelled', 'ahead_wasted',
                     'group_1', 'group_2', 'group_3_4', 'group_5_8', 'window_1', 'window_2', 'window_3_4', 'window_5_8',
                     *compile_timing.RUN_ORIGINS)


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {"executing":0}
        self.rates = deque(maxlen=26)
        self.started = time.monotonic()
        self.cpu = time.process_time()
        self.first_exact_invocations = None
        self.remote_slots = 0
        self.remote_live = {}

    def add(self, key, value):
        with self.lock:
            self.values[key] = self.values.get(key, 0) + value
            if key in {'considered','generated','invocations','requests'}:
                tick=int(time.monotonic()*5)
                if not self.rates or self.rates[-1][0]!=tick:self.rates.append((tick,{}))
                bucket=self.rates[-1][1]
                bucket[key]=bucket.get(key,0)+value

    def set_remote(self, ident, values):
        with self.lock:
            if values:
                self.remote_live[ident] = values
            else:
                self.remote_live.pop(ident, None)

    def counts(self):
        with self.lock:
            return dict(self.values, proposal_workers=getattr(getattr(self, 'proposals', None), 'running', 0))

    def exact(self):
        with self.lock:
            if self.first_exact_invocations is None:
                self.first_exact_invocations=self.values.get('invocations',0)

    def snapshot(self):
        now=time.monotonic()
        elapsed = max(.001, now-self.started)
        with self.lock:
            values = dict(self.values)
            values['proposal_workers'] = getattr(getattr(self, 'proposals', None), 'running', 0)
            for worker in self.remote_live.values():
                for key in LIVE_WORKER_COUNTERS:
                    values[key] = values.get(key, 0) + worker.get(key, 0)
            first_exact=self.first_exact_invocations
            recent={}
            for tick,bucket in self.rates:
                if tick/5 >= now-5:
                    for key,value in bucket.items():recent[key]=recent.get(key,0)+value
        rate_seconds=min(5,elapsed)
        gate = getattr(self,'gate',None)
        slots = self.remote_slots or (gate.limit if gate else 0)
        return {'active_proposal_workers':values['proposal_workers'],
                'active_compiles':values.get('executing',0),
                'shared_active_compiles':getattr(compiler._SLOTS,'active',0),
                'shared_compile_slots':getattr(compiler._SLOTS,'limit',0) if not self.remote_slots else 0,
                **{'compiler_waiting_'+name:values.get('waiting_'+name,0) for name in ('search','background','shared')},
                **{'compiler_queue_'+name+'_seconds':values.get('queue_'+name,0) for name in ('search','background','shared')},
                'pool_compiler_invocations':values.get('invocations',0),
                'pool_compile_requests':values.get('requests',0),
                'pool_compiler_invocations_at_first_exact':first_exact,
                'compile_slots':slots,
                'rank_literals_cpu_seconds':values.get('rank_literals_cpu_ns', 0) / 1e9,
                'rank_batches':values.get('rank_batches', 0),
                **{name:values.get(name, 0) for name in PIPELINE_COUNTERS},
                'rank_candidates':values.get('rank_candidates', 0),
                'maximum_throughput':getattr(self,'maximum',False),
                'candidates_per_second':recent.get('considered',0)/rate_seconds,
                'generated_per_second':recent.get('generated',0)/rate_seconds,
                'compiles_per_second':recent.get('invocations',0)/rate_seconds,
                'compile_requests_per_second':recent.get('requests',0)/rate_seconds,
                'compiler_queue_seconds':values.get('queue',0),
                'compiler_setup_seconds':values.get('setup',0),
                'compiler_slot_utilization_percent':min(100,max(0,100*values.get('execute',0)/(elapsed*slots))) if slots else 0,
                'generation_seconds':values.get('generation',0),
                'local_proposal_generation_seconds':values.get('proposal_generation',0),
                'cleanup_generation_seconds':values.get('cleanup_generation',0),
                'compiler_execute_seconds':values.get('execute',0),
                'link_seconds':values.get('link',0),
                'local_generation_wait_seconds':values.get('local_generation',0),
                'history_write_seconds':values.get('history_write',0),
                'history_transactions':values.get('history_transactions',0),
                'app_cpu_percent':max(0, 100*(time.process_time()-self.cpu)/elapsed)}


@contextmanager
def scope(pool):
    with ExitStack() as stack:
        if pool is not None:
            stack.enter_context(compile_timing.collect(pool.metrics))
            stack.enter_context(compiler.throughput_slots(pool.maximum, pool.slots))
            if shared := getattr(pool, 'shared_slots', None):
                stack.enter_context(compiler.shared_slots(shared))
        yield


def search(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        log = kwargs.get('log') or args[5]
        with scope(getattr(log, 'compile_pool', None)), compiler.cancellable(log.check_cancel):
            return function(*args, **kwargs)
    return run


def sweep(function):
    @functools.wraps(function)
    def run(self, slug, minutes, variants, log, *args, **kwargs):
        from .deep_compilation import Pool
        cfg = self.config(slug)
        opts = options(cfg.get('maximum_throughput',False),cfg.get('compile_slots',0))
        count = width(opts['maximum_throughput'],opts['compile_slots'])
        if not opts['maximum_throughput'] and not opts['compile_slots']:count = compiler.concurrency_limit()
        pool = Pool(count, maximum=opts['maximum_throughput'])
        pool.shared_slots = compiler._SLOTS.group(count)
        previous = getattr(log, 'compile_pool', None)
        log.compile_pool = pool
        try:
            with (compiler._SLOTS.capacity(pool.count), compile_timing.collect(pool.metrics),
                  compiler.shared_slots(pool.shared_slots)):
                return function(self,slug,minutes,variants,log,*args,**kwargs)
        finally:
            try:pool.close()
            finally:log.compile_pool = previous
    return run
