"""The background workers machine drafts (drafts.py) and the permuter (permute.py) share, for every project.

One pool, so "up to four workers" holds for the app as a whole: each part with a pool of its own made eight threads
on a big computer, beside the agents' compiles. Each worker lowers its own CPU priority as it starts (on Linux a
thread's own, and inherited by what it runs: m2c, the compiler, the linker, the permuter's generator), so the app and
the agents come first.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

RESERVED_CORES = 2  # left to the app itself and the agents' compiles
# Past four, the judge's own Python work (about 5 ms of a variant's 16, under the interpreter's one lock) is the
# limit, not the cores, and the app itself waits for that lock. Demo ROM, 12 cores: 1 worker judged 80 variants a
# second, 2 129, 4 172, 8 193; a GUI request took 0.1 ms alone, 6 ms beside 4 workers, 31 ms beside 8.
MAX_WORKERS = 4
WORKERS = max(1, min(MAX_WORKERS, (os.cpu_count() or 1) - RESERVED_CORES))
NICE = 10  # how much lower the workers' CPU priority is

_pool: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def pool() -> ThreadPoolExecutor:
    """The shared workers, started the first time something needs them."""
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="background-worker", initializer=os.nice,
                                       initargs=(NICE,))
        return _pool
