"""Explicit throughput presets and bounded, source-free live counters."""
import functools
import os
import threading
import time
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
        self.started = time.monotonic()
        self.cpu = time.process_time()

    def add(self, key, value):
        with self.lock:self.values[key] = self.values.get(key, 0) + value

    def snapshot(self):
        elapsed = max(.001, time.monotonic()-self.started)
        with self.lock:values = dict(self.values)
        gate = getattr(self,'gate',None)
        return {'active_compiles':gate.active if gate else 0,
                'compile_slots':gate.limit if gate else 0,
                'maximum_throughput':getattr(self,'maximum',False),
                'candidates_per_second':values.get('considered',0)/elapsed,
                'generated_per_second':values.get('generated',0)/elapsed,
                'compiles_per_second':values.get('invocations',0)/elapsed,
                'compile_requests_per_second':values.get('requests',0)/elapsed,
                'compiler_queue_seconds':values.get('queue',0),
                'compiler_setup_seconds':values.get('setup',0),
                'generation_seconds':values.get('generation',0),
                'history_write_seconds':values.get('history_write',0),
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
