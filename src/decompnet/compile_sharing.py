"""Coalesce only simultaneous identical compilation work, never judge or publish results.

No persistent compiler cache: subsequent requests compile again. Owners keep their
normal cancellation/artifact semantics and followers receive independent result objects.
"""
import copy
import threading
from concurrent.futures import Future

_lock = threading.Lock()
_pending = {}


def run(key, build):
    with _lock:
        future = _pending.get(key)
        owner = future is None
        if owner:
            future = _pending[key] = Future()
    if not owner:
        return copy.deepcopy(future.result())
    try:
        result = build()
        future.set_result(result)
        return result
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _lock:
            _pending.pop(key, None)
