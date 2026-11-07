"""Bounded independent profile checks within one function's compiler comparison."""
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import nullcontext

from .. import compile_sessions, compiler
from ..network import NetworkError


def check_profiles(net, proj, fn, candidates, source, cancel):
    """Keep four profiles in flight, preserving candidate order and deterministic ties.

    Each worker owns its prepared compiler workspace. Only the coordinator publishes
    history/settings; the normal compiler resource gates still limit subprocesses.
    """
    stopped = threading.Event()
    def check_cancel():
        cancel()
        if stopped.is_set():
            raise NetworkError('Compiler comparison stopped.')
    def check(profile):
        tc, flags = profile
        with compiler.cancellable(check_cancel), compile_sessions.session(), \
                net.progress.view() if hasattr(net, 'progress') else nullcontext():
            check_cancel()
            return net._judge_with(proj, fn, tc, list(flags), source)
    iterator = iter(candidates)
    pending = deque()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='compiler-profile')
    try:
        for profile in iterator:
            check_cancel()
            pending.append((profile, pool.submit(check, profile)))
            if len(pending) == 4:
                break
        while pending:
            profile, future = pending[0]
            while True:
                check_cancel()
                try:
                    result = future.result(timeout=.1)
                    break
                except TimeoutError:
                    if future.done():
                        result = future.result()  # Propagate a judge's own TimeoutError.
                        break
            pending.popleft()
            yield profile, result
            check_cancel()
            following = next(iterator, None)
            if following is not None:
                pending.append((following, pool.submit(check, following)))
    finally:
        stopped.set()
        for _, future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
