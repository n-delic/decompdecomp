"""Bounded, ordered work for the independent steps of a rebuildable export."""
from __future__ import annotations

import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from itertools import islice
from threading import Event

from .. import compiler


@contextmanager
def ordered(work, items, check=lambda: None):
    """Keep only a small window of work in flight and yield results in source order."""
    workers = min(8, max(1, (os.cpu_count() or 2) - 2), compiler.concurrency_limit())
    stopped = Event()

    def check_running():
        if stopped.is_set():
            raise RuntimeError('Export workers stopped.')
        check()

    def run(item):
        check_running()
        with compiler.cancellable(check_running):
            return work(item)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='export') as pool:
        source = iter(items)
        waiting = deque()

        def results():
            while waiting:
                check_running()
                result = waiting.popleft().result()
                for item in islice(source, 1):
                    waiting.append(pool.submit(run, item))
                yield result

        try:
            for item in islice(source, workers):
                waiting.append(pool.submit(run, item))
            yield results()
        finally:
            stopped.set()
            for future in waiting:
                future.cancel()
