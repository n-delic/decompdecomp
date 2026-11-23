"""Keep heavy tools from taking the user's computer down.

decompdecomp runs other people's tools on big inputs: project builds, decomp-toolkit's analysis, Ghidra. A runaway
one can eat all memory and swap until the system's out-of-memory killer takes the whole desktop session with it.
On Linux each heavy command runs in its own systemd scope with a memory cap and no swap, so only that command
is stopped.

How memory is capped, system by system (macOS has no systemd and no working RLIMIT_AS, so a watchdog reads `ps`):
- a compiler: Linux, 2 GB of address space each (prlimit, `compiler.LIMITS`); macOS, 2 GB resident per process
  (`compiler_memory_limit`, DECOMP_COMPILER_MEMORY_MB); Windows, none.
- a project build: Linux, the whole build at `default_cap()` (its systemd scope); macOS, the whole build at
  `default_cap()` too, its processes' resident memory added up; Windows, none (a Job Object could cap it: ROADMAP).
Time limits are the same everywhere: a step past its limit is stopped with everything it started (`stop_group`).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
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


COMPILER_MEMORY_MB = 2048  # a compiler's resident memory on macOS, as prlimit's 2 GB address space on Linux
WATCH_SECONDS = 0.25  # how often the macOS watchdog reads the processes' memory
STOP_GRACE_SECONDS = 2  # how long a stopped tool may take to end after being asked, before it's killed


def compiler_memory_limit() -> int:
    """A compiler process's resident memory limit on macOS (Linux's is prlimit's, `compiler.LIMITS`), in bytes;
    DECOMP_COMPILER_MEMORY_MB, set before the app starts, changes it."""
    try:
        mb = int(os.environ.get("DECOMP_COMPILER_MEMORY_MB", str(COMPILER_MEMORY_MB)))
        if mb < 16:
            raise ValueError
    except ValueError:
        mb = COMPILER_MEMORY_MB
    return mb << 20


def compiler_time_limit() -> float:
    """The longest any compile may take, whatever its caller asked, DECOMP_COMPILER_TIMEOUT_SECONDS: a compiler that
    never finished on an agent's code held one of the few compiler slots for good."""
    try:
        seconds = float(os.environ.get("DECOMP_COMPILER_TIMEOUT_SECONDS", "300"))
        return seconds if 0 < seconds < float("inf") else 300
    except ValueError:
        return 300


def _session_of(pid: int) -> int | None:
    try:
        return os.getsid(pid)
    except OSError:  # gone, or not ours to ask about
        return None


def _session_tree(root_pid: int) -> list[tuple[int, int]]:
    """(pid, resident bytes) of every process a tool started with `start_new_session` (its pid is its session's) has
    running: those under it, and those whose parent has gone (a build step that ended while a compiler it started
    still held its output) but that are still in its session. The session is read with getsid, not ps: macOS's
    `ps -o sess` prints 0 for every process, so a compiler in its own process group, its parent gone, was missed."""
    try:
        listed = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=10,
                                check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    sizes: dict[int, int] = {}
    for line in listed.splitlines():
        try:
            pid, parent, rss = map(int, line.split())
        except ValueError:
            continue
        children.setdefault(parent, []).append(pid)
        sizes[pid] = rss * 1024
    under, todo = set(), [root_pid]
    while todo:
        pid = todo.pop()
        if pid not in under:
            under.add(pid)
            todo.extend(children.get(pid, ()))
    return [(pid, rss) for pid, rss in sizes.items() if pid in under or _session_of(pid) == root_pid]


def attach_job(proc: subprocess.Popen) -> None:
    """Windows: put a tool in a Job Object of its own, so everything it starts can be stopped with it, even after
    the tool itself has ended (taskkill /T finds nothing under a process that's gone, and a project build whose step
    exited while a compiler it started kept the output open ran on: #228). Elsewhere the tool's session does this."""
    if os.name != "nt":
        return
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
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


def release_job(proc: subprocess.Popen) -> None:
    """Close a finished tool's Job Object, leaving alone what's in it (the job doesn't kill on close)."""
    if os.name == "nt" and (job := getattr(proc, "_decomp_job", None)):
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(ctypes.c_void_p(job))
        proc._decomp_job = None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop_group(proc: subprocess.Popen, remembered: set[int] | None = None) -> None:
    """Stop a tool started with `start_new_session` (`attach_job` on Windows) and everything it started: every
    process still in its session, and the process groups they're in, since ninja puts each command in a group of its
    own. `remembered`: processes seen in the tree before; each is checked to still be in the session first, so a
    number the system has since given to another process is left alone. Costs nothing when there's nothing left."""
    if os.name == "nt":
        if job := getattr(proc, "_decomp_job", None):
            import ctypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.TerminateJobObject(job, 1)
            kernel.CloseHandle(job)
            proc._decomp_job = None
            return
        from .hostenv import stop_process_tree

        stop_process_tree(proc)
        return
    candidates = {*(remembered or ()), *(pid for pid, _ in _session_tree(proc.pid))}
    if proc.poll() is None:
        candidates.add(proc.pid)
    pids = {pid for pid in candidates if pid != os.getpid() and _session_of(pid) == proc.pid}
    if not pids:
        return
    own_group = os.getpgrp()
    groups = set()
    for pid in pids:
        try:
            groups.add(os.getpgid(pid))
        except OSError:
            pass
    groups.discard(own_group)

    def signal_all(sig: int) -> None:
        for group in groups:
            try:
                os.killpg(group, sig)
            except OSError:  # gone, or (Intel macOS) PermissionError: the processes are signalled one by one below
                pass
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass

    # (the tool itself is our child: until it's reaped it's a zombie, which kill(pid, 0) still finds)
    def running() -> bool:
        return any(proc.poll() is None if pid == proc.pid else _alive(pid) for pid in pids)

    signal_all(signal.SIGTERM)
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline and running():
        time.sleep(0.05)
    if running():  # (what ignored being asked: a compiler that ignores SIGTERM outlived its build)
        signal_all(signal.SIGKILL)


class CompilerMemoryWatchdog:
    """macOS: stop a compiler, or a whole build, that uses more memory than its limit, as Linux's prlimit and systemd
    scopes do there (see the module's docstring). `whole_tree`: the limit is for all its processes together, as a
    build's systemd scope is; otherwise for each process. Not started elsewhere: nothing to do, and Windows has no
    `ps`. The processes it saw are kept in `seen`, for `stop_group`."""

    def __init__(self, proc: subprocess.Popen, limit: int | None = None, whole_tree: bool = False):
        self.proc = proc
        self.limit = limit if limit is not None else compiler_memory_limit()
        self.whole_tree = whole_tree
        self.exceeded = False
        self.seen: set[int] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        if sys.platform == "darwin":
            self._thread = threading.Thread(target=self._watch, name="memory-watchdog", daemon=True)
            self._thread.start()
        return self

    def _watch(self) -> None:
        while not self._stop.wait(WATCH_SECONDS):
            tree = _session_tree(self.proc.pid)
            self.seen.update(pid for pid, _ in tree)
            sizes = [rss for _, rss in tree]
            if (sum(sizes) if self.whole_tree else max(sizes, default=0)) > self.limit:
                self.exceeded = True
                stop_group(self.proc, self.seen)
                return

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=STOP_GRACE_SECONDS + 3)

    def message(self) -> str:
        mb = self.limit >> 20
        if self.whole_tree:
            return (f"stopped: the build used more than {mb} MiB of memory in all (half this computer's, as on Linux), "
                    "so it was stopped before it could slow down or freeze your computer")
        return (f"Compiler process exceeded the {mb} MiB memory limit and was stopped. Reduce concurrency or increase "
                "DECOMP_COMPILER_MEMORY_MB before starting decompdecomp.")
