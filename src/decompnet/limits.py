"""Keep heavy tools from taking the user's computer down.

decompdecomp runs other people's tools on big inputs: project builds, decomp-toolkit's analysis, Ghidra. A runaway
one can eat all memory and swap until the system's out-of-memory killer takes the whole desktop session with it.
On Linux each heavy command runs in its own systemd scope with a memory cap and no swap, so only that command
is stopped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache


def _total_memory() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 8 << 30


def default_cap() -> int:
    """Half the machine's memory, between 2 and 8 GB."""
    return max(2 << 30, min(8 << 30, _total_memory() // 2))


@lru_cache(maxsize=1)
def _systemd_run() -> str | None:
    exe = shutil.which("systemd-run")
    if not exe or not os.environ.get("XDG_RUNTIME_DIR"):
        return None
    try:  # the user manager has to be reachable (not the case in some containers or SSH sessions)
        ok = subprocess.run([exe, "--user", "--scope", "--quiet", "-p", "MemoryMax=64M", "--", "true"],
                            capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    return exe if ok else None


def capped(argv: list[str], memory: int | None = None, stop_all: bool = True) -> list[str]:
    """`argv`, wrapped so it's stopped when it uses more than `memory` bytes (default: default_cap()). Over the cap
    the kernel kills the command's biggest process, and systemd then stops the rest of it; with `stop_all` False the
    rest carries on without that process, for a tool that copes: Ghidra's analysis of a 6 MB game ran one decompiler
    process up to 6 GB, and when the kernel killed it at the cap systemd stopped Ghidra too, which then said nothing
    about why. Ghidra starts another decompiler for the next function."""
    exe = _systemd_run()
    if exe is None:
        return list(argv)
    mb = (memory or default_cap()) >> 20
    keep = [] if stop_all else ["-p", "OOMPolicy=continue"]
    return [exe, "--user", "--scope", "--quiet", "-p", f"MemoryMax={mb}M", "-p", "MemorySwapMax=0", *keep, "--", *argv]


def no_core_dumps() -> None:
    """No core dumps from this process or anything it starts: a compiler run through wibo aborts on a Windows
    function wibo doesn't have, and a period compiler can crash on odd C, so the desktop's crash reporter put up a
    dialog for each one an agent's attempt hit. Children inherit the limit, and systemd-coredump honours it."""
    try:
        import resource
    except ImportError:  # Windows: no core dumps to stop
        return
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, resource.getrlimit(resource.RLIMIT_CORE)[1]))
    except (ValueError, OSError):
        pass


def killed_for_memory(returncode: int) -> bool:
    return returncode in (137, -9)


def compiler_memory_limit() -> int:
    """Per-process resident memory limit on macOS, configurable in MiB before starting the app."""
    try:
        mb = int(os.environ.get('DECOMP_COMPILER_MEMORY_MB', '2048'))
        if mb < 16:
            raise ValueError
    except ValueError:
        mb = 2048
    return mb << 20


def _process_memory_tree(root_pid: int) -> list[tuple[int, int]]:
    """Snapshot resident sizes (bytes) of a process and its descendants on macOS."""
    try:
        result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,rss='], capture_output=True,
                                text=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    children = {}
    sizes = {}
    for line in result.stdout.splitlines():
        try:
            pid, parent, rss = map(int, line.split())
        except ValueError:
            continue
        children.setdefault(parent, []).append(pid)
        sizes[pid] = rss * 1024
    pending, seen = [root_pid], set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        pending.extend(children.get(pid, ()))
    return [(pid, sizes[pid]) for pid in seen if pid in sizes]


def compiler_time_limit() -> float:
    """Absolute wall-time ceiling, including compiler subprocesses that never finish."""
    try:
        seconds = float(os.environ.get('DECOMP_COMPILER_TIMEOUT_SECONDS', '300'))
        return seconds if 0 < seconds < float('inf') else 300
    except ValueError:
        return 300


def attach_job(proc) -> None:
    """Keep a Windows driver's children addressable after the driver itself exits."""
    if os.name != 'nt':
        return
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = ctypes.c_void_p
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.AssignProcessToJobObject.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return
    if not kernel.AssignProcessToJobObject(job, int(proc._handle)):
        kernel.CloseHandle(job)
        return
    proc._decomp_job = job


def stop_group(proc) -> None:
    """Stop a tool and descendants launched in their own process group."""
    if os.name == 'nt':
        if job := getattr(proc, '_decomp_job', None):
            import ctypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.TerminateJobObject(job, 1)
            kernel.CloseHandle(job)
            proc._decomp_job = None
            return
        from .hostenv import stop_process_tree
        stop_process_tree(proc)
        return
    import signal
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS can refuse a group signal after the driver exits or while its
        # descendants change state. Stop the processes we still own individually.
        for pid, _ in reversed(_process_memory_tree(proc.pid)):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if proc.poll() is None:
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError):
                pass


class CompilerMemoryWatchdog:
    """Watch native macOS compiler/build trees and stop the whole isolated group on excess RSS."""
    def __init__(self, proc, limit=None, timeout=None):
        import threading
        self.proc = proc
        self.limit = limit if limit is not None else compiler_memory_limit()
        self.exceeded = False
        self.timed_out = False
        self.timeout = timeout
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        import sys
        import threading
        if sys.platform == 'darwin' or self.timeout is not None:
            self._thread = threading.Thread(target=self._watch, name='compiler-memory-watchdog', daemon=True)
            self._thread.start()
        return self

    def _watch(self):
        import sys
        import time
        started = time.monotonic()
        while not self._stop.wait(0.25):
            self.timed_out = self.timeout is not None and time.monotonic() - started >= self.timeout
            self.exceeded = sys.platform == 'darwin' and any(rss > self.limit for _, rss in _process_memory_tree(self.proc.pid))
            if self.timed_out or self.exceeded:
                stop_group(self.proc)
                return

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def message(self):
        return (f'Compiler or build process exceeded the {self.limit // (1 << 20)} MiB memory limit and was stopped. '
                'Reduce concurrency or increase DECOMP_COMPILER_MEMORY_MB before starting decompdecomp.')
