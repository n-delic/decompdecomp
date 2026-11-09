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
    return slots or (min(256,max(1, os.cpu_count() or 1)) if maximum else min(4, compiler.concurrency_limit()))


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}
        self.rates = deque(maxlen=26)
        self.started = time.monotonic()
        self.cpu = time.process_time()
        self.first_exact_invocations = None

    def add(self, key, value):
        with self.lock:
            self.values[key] = self.values.get(key, 0) + value
            if key in {'considered','generated','invocations','requests'}:
                tick=int(time.monotonic()*5)
                if not self.rates or self.rates[-1][0]!=tick:self.rates.append((tick,{}))
                bucket=self.rates[-1][1]
                bucket[key]=bucket.get(key,0)+value

    def exact(self):
        with self.lock:
            if self.first_exact_invocations is None:
                self.first_exact_invocations=self.values.get('invocations',0)

    def snapshot(self):
        now=time.monotonic()
        elapsed = max(.001, now-self.started)
        with self.lock:
            values = dict(self.values)
            first_exact=self.first_exact_invocations
            recent={}
            for tick,bucket in self.rates:
                if tick/5 >= now-5:
                    for key,value in bucket.items():recent[key]=recent.get(key,0)+value
        rate_seconds=min(5,elapsed)
        gate = getattr(self,'gate',None)
        return {'active_proposal_workers':getattr(getattr(self,'proposals',None),'running',0),
                'active_compiles':gate.active if gate else 0,
                'pool_compiler_invocations':values.get('invocations',0),
                'pool_compile_requests':values.get('requests',0),
                'pool_compiler_invocations_at_first_exact':first_exact,
                'compile_slots':gate.limit if gate else 0,
                'maximum_throughput':getattr(self,'maximum',False),
                'candidates_per_second':recent.get('considered',0)/rate_seconds,
                'generated_per_second':recent.get('generated',0)/rate_seconds,
                'compiles_per_second':recent.get('invocations',0)/rate_seconds,
                'compile_requests_per_second':recent.get('requests',0)/rate_seconds,
                'compiler_queue_seconds':values.get('queue',0),
                'compiler_setup_seconds':values.get('setup',0),
                'compiler_slot_utilization_percent':min(100,max(0,100*values.get('execute',0)/(elapsed*gate.limit))) if gate and gate.limit else 0,
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
        yield


def search(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        log = kwargs.get('log') or args[5]
        with scope(getattr(log, 'compile_pool', None)):return function(*args, **kwargs)
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
        previous = getattr(log, 'compile_pool', None)
        log.compile_pool = pool
        try:
            with compiler._SLOTS.capacity(pool.count):return function(self,slug,minutes,variants,log,*args,**kwargs)
        finally:
            try:pool.close()
            finally:log.compile_pool = previous
    return run
