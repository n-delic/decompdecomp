"""Thread defaults and priority for independent, purpose-owned background groups."""

from __future__ import annotations

import os
import sys
import threading

from .purpose_work import Executor

RESERVED_CORES = 2  # left to the app itself and the agents' compiles
# More workers match faster when a compile is slow (a period compiler run through Rosetta or WSL) and cost the app
# some of its snappiness: the judge's own Python work (about 5 ms of a variant's 16) runs under the interpreter's one
# lock. Demo ROM, 12 cores: 1 worker judged 80 variants a second, 2 129, 4 172, 8 193; a GUI request took 0.1 ms
# alone, 6 ms beside 4 workers, 31 ms beside 8. Hence a setting, and a default that keeps two cores free.
CORES = os.cpu_count() or 1


def default_workers() -> int:
    """What this computer can spare for the workers: its cores less the ones left to the app and the agents."""
    return max(1, CORES - RESERVED_CORES)


def clamp(threads) -> int:
    """`threads` (a setting; nothing, 0 or junk means "automatic") as a count of workers. There is no upper limit:
    the user knows whether a slow compiler or an idle computer can use more threads than it has cores."""
    try:
        wanted = int(threads or 0)
    except (TypeError, ValueError):
        wanted = 0
    return default_workers() if wanted <= 0 else wanted


WORKERS = default_workers()  # default width of new purpose-owned groups
NICE = 10  # how much lower the workers' CPU priority is

_lock = threading.Lock()


def lower_priority() -> None:
    """Lower only this worker, without lowering the GUI or the agent threads beside it."""
    from ..compiler import background_thread
    background_thread()
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.GetCurrentThread.restype = ctypes.c_void_p
        kernel.SetThreadPriority.argtypes = (ctypes.c_void_p, ctypes.c_int)
        kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
    elif sys.platform == "darwin":
        import ctypes

        # Darwin's nice value belongs to the process, so os.nice would slow the GUI too.
        # Set this worker's QoS instead. Utility threads still let the interactive window run first.
        system = ctypes.CDLL(None)
        system.pthread_set_qos_class_self_np.argtypes = (ctypes.c_uint, ctypes.c_int)
        system.pthread_set_qos_class_self_np(0x11, 0)  # QOS_CLASS_UTILITY
    else:
        os.nice(NICE)


def configure(threads=None) -> int:
    """Set the default width of newly created task groups; running groups keep theirs."""
    global WORKERS
    with _lock:WORKERS = clamp(threads)
    return WORKERS


def pool(purpose='Background task', project='', stop=None) -> Executor:
    """Return an independent group; callers must close it after their operation."""
    return Executor(WORKERS, purpose=purpose, project=project, stop=stop, initializer=lower_priority)
