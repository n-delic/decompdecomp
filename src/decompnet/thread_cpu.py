"""Which of the app's threads use the processor, and where in the code: a bounded local report.

The app is one Python process, and its threads share one interpreter lock with the event loop. When the machine is
busy, every database read in it logs as slow, so the slow-read warnings name the victims as often as the culprits:
on a v-rally copy, fingerprinting every function for twins took 0.57 s, while the live log showed the same call taking
44 s. Moving work into a process of its own (render_worker.py did it for progress renders) only helps when it is the
work that holds the processor, so this measures that directly: the kernel's CPU time for each thread, sampled every
half second and charged to where the thread is in decompnet's code at that moment.

The kernel lists the process's own threads (macOS: Mach task_threads; Linux: /proc/self/task); they are matched to
Python's threads by native id. Nothing reads another thread's pthread handle, which is unsafe once a thread has
ended. The report holds thread names, code locations and seconds: never frame locals, arguments, SQL or source.
It replaces logs/thread-cpu.log every minute; the stall report (hang_diagnostics) adds the last seconds' figures.
DECOMP_THREAD_CPU=0 turns it off. Windows has no reader yet: the sampler doesn't start there.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import threading
import time
from collections import Counter, deque
from pathlib import Path

_PACKAGE = os.path.dirname(os.path.abspath(__file__)) + os.sep


# ------------------------------------------------------------------------------------- per-thread CPU from the kernel
class _MachThreads:
    """macOS: every thread of this task with its CPU seconds, by the id threading.get_native_id() reports."""
    THREAD_BASIC_INFO, BASIC_COUNT = 3, 10
    THREAD_IDENTIFIER_INFO, IDENTIFIER_COUNT = 4, 6

    def __init__(self):
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self.task = ctypes.c_uint.in_dll(libc, "mach_task_self_").value
        self.task_threads = libc.task_threads
        self.task_threads.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.POINTER(ctypes.c_uint)),
                                      ctypes.POINTER(ctypes.c_uint)]
        self.thread_info = libc.thread_info
        self.thread_info.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        self.port_deallocate = libc.mach_port_deallocate
        self.port_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self.vm_deallocate = libc.vm_deallocate
        self.vm_deallocate.argtypes = [ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]

    def __call__(self) -> dict[int, float]:
        threads = ctypes.POINTER(ctypes.c_uint)()
        count = ctypes.c_uint(0)
        if self.task_threads(self.task, ctypes.byref(threads), ctypes.byref(count)) != 0:
            return {}
        out = {}
        try:
            for index in range(count.value):
                port = threads[index]
                try:
                    ident = (ctypes.c_uint64 * 3)()
                    n = ctypes.c_uint(self.IDENTIFIER_COUNT)
                    if self.thread_info(port, self.THREAD_IDENTIFIER_INFO, ident, ctypes.byref(n)) != 0:
                        continue
                    basic = (ctypes.c_int * self.BASIC_COUNT)()
                    n = ctypes.c_uint(self.BASIC_COUNT)
                    if self.thread_info(port, self.THREAD_BASIC_INFO, basic, ctypes.byref(n)) != 0:
                        continue
                    # user_time and system_time: (seconds, microseconds) each
                    out[ident[0]] = basic[0] + basic[1] / 1e6 + basic[2] + basic[3] / 1e6
                finally:
                    self.port_deallocate(self.task, port)
        finally:
            self.vm_deallocate(self.task, ctypes.cast(threads, ctypes.c_void_p).value or 0,
                               count.value * ctypes.sizeof(ctypes.c_uint))
        return out


class _ProcThreads:
    """Linux: /proc/self/task/<tid>/stat, utime and stime in clock ticks."""

    def __init__(self):
        self.tick = os.sysconf("SC_CLK_TCK")

    def __call__(self) -> dict[int, float]:
        out = {}
        try:
            names = os.listdir("/proc/self/task")
        except OSError:
            return out
        for name in names:
            try:
                with open(f"/proc/self/task/{name}/stat", "rb") as stat:
                    fields = stat.read().rsplit(b")", 1)[1].split()
                out[int(name)] = (int(fields[11]) + int(fields[12])) / self.tick
            except (OSError, ValueError, IndexError):
                continue  # (the thread ended meanwhile)
        return out


def reader():
    """A function returning {native thread id: CPU seconds}, or None where there is none."""
    try:
        if sys.platform == "darwin":
            return _MachThreads()
        if sys.platform.startswith("linux") and os.path.isdir("/proc/self/task"):
            return _ProcThreads()
    except (OSError, AttributeError, ValueError):
        pass
    return None


# ---------------------------------------------------------------------------------------------------- the sampler
def _location(frame) -> str:
    """The innermost frame in decompnet's own code (else the innermost one): `app/twins.py:_build_rom_prints`."""
    innermost = None
    while frame is not None:
        filename = frame.f_code.co_filename
        if innermost is None:
            innermost = frame
        if filename.startswith(_PACKAGE):
            return f"{filename[len(_PACKAGE):]}:{frame.f_code.co_name}"
        frame = frame.f_back
    if innermost is None:
        return "?"
    return f"{os.path.basename(innermost.f_code.co_filename)}:{innermost.f_code.co_name}"


class Sampler:
    def __init__(self, path, *, interval=.5, report_every=60, read=None):
        self.path = Path(path)
        self.interval, self.report_every = interval, report_every
        self.read = read if read is not None else reader()
        self.previous: dict[int, float] = {}
        self.by_thread: Counter = Counter()  # since the start
        self.by_place: Counter = Counter()  # (thread, location) since the start
        self.window: deque = deque()  # (when, Counter by thread, Counter by (thread, location)) for the last minute
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.cpu_started = time.process_time()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name="thread-cpu-sampler")

    def sample(self) -> None:
        now = time.monotonic()
        cpu = self.read()
        frames = sys._current_frames()
        names = {t.native_id: (t.name, t.ident) for t in threading.enumerate() if t.native_id is not None}
        threads, places = Counter(), Counter()
        for native, seconds in cpu.items():
            spent = seconds - self.previous.get(native, seconds)
            if spent <= 0:
                continue
            name, ident = names.get(native, ("(not a Python thread)", None))
            threads[name] += spent
            places[(name, _location(frames.get(ident)) if ident in frames else "-")] += spent
        self.previous = cpu
        with self.lock:
            self.by_thread.update(threads)
            self.by_place.update(places)
            self.window.append((now, threads, places))
            while self.window and now - self.window[0][0] > self.report_every:
                self.window.popleft()

    def recent(self, seconds: float) -> list[tuple[str, float]]:
        """CPU seconds by thread over the last `seconds`, most first."""
        now = time.monotonic()
        total = Counter()
        with self.lock:
            for when, threads, _ in self.window:
                if now - when <= seconds:
                    total.update(threads)
        return total.most_common()

    def report(self) -> str:
        with self.lock:
            minute_threads, minute_places = Counter(), Counter()
            for _, threads, places in self.window:
                minute_threads.update(threads)
                minute_places.update(places)
            by_thread, by_place = Counter(self.by_thread), Counter(self.by_place)
        wall = max(time.monotonic() - self.started, 1e-9)
        process = time.process_time() - self.cpu_started
        lines = [f"Thread CPU at {time.strftime('%Y-%m-%d %H:%M:%S %z')}: the app used {process:.0f} s of processor "
                 f"in {wall:.0f} s ({process / wall:.0%} of one core on average)\n"]

        def table(title, rows, limit):
            lines.append(f"\n{title}\n")
            for key, seconds in rows.most_common(limit):
                label = key if isinstance(key, str) else f"{key[0]:<40} {key[1]}"
                lines.append(f"  {seconds:9.1f} s  {label}\n")
        table(f"Last {self.report_every:.0f} s, by thread", minute_threads, 20)
        table(f"Last {self.report_every:.0f} s, by thread and code", minute_places, 30)
        table("Since the start, by thread", by_thread, 30)
        table("Since the start, by thread and code", by_place, 60)
        return "".join(lines)

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(self.report()[:512_000], encoding="utf-8")
        os.replace(temp, self.path)

    def run(self) -> None:
        last_report = time.monotonic()
        while not self.stop.wait(self.interval):
            try:
                self.sample()
                if time.monotonic() - last_report >= self.report_every:
                    last_report = time.monotonic()
                    self.write()
            except Exception:  # (diagnostics never take the app down)
                pass

    def start(self) -> Sampler:
        self.previous = self.read()
        self.thread.start()
        return self

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=1)
        try:
            self.write()
        except OSError:
            pass


def start(path) -> Sampler | None:
    """The app's sampler, or None when it is off or this system has no per-thread reader."""
    if os.environ.get("DECOMP_THREAD_CPU", "").strip() == "0":
        return None
    read = reader()
    if read is None:
        return None
    return Sampler(path, read=read).start()
