"""The thread CPU report names the thread that holds the processor and where it is in decompnet's code.

The slow-read warnings name victims as often as culprits (a twins fingerprint pass took 0.57 s alone and 44 s in the
live app), so the report reads each thread's own CPU time from the kernel."""
import threading
import time

import pytest

from decompnet import thread_cpu

pytestmark = pytest.mark.skipif(thread_cpu.reader() is None, reason="no per-thread CPU reader on this system")


def _busy(stop):
    while not stop.is_set():
        sum(range(2000))


def test_the_kernel_reader_sees_this_threads_cpu():
    # Compared with this thread's own clock, not a fixed amount: a loaded machine (or CI runner) gives it little
    read = thread_cpu.reader()
    native = threading.get_native_id()
    deadline = time.monotonic() + .2
    while time.monotonic() < deadline:
        sum(range(2000))
    own, seen = time.thread_time(), read()[native]
    assert seen > 0 and abs(seen - own) < .05 + .1 * own


def test_a_busy_thread_is_charged_and_an_idle_one_is_not(tmp_path):
    stop = threading.Event()
    busy = threading.Thread(target=_busy, args=(stop,), name="busy-worker", daemon=True)
    idle = threading.Thread(target=stop.wait, name="idle-worker", daemon=True)
    sampler = thread_cpu.Sampler(tmp_path / "thread-cpu.log", interval=.05, report_every=60)
    busy.start()
    idle.start()
    sampler.start()
    try:
        time.sleep(.6)
    finally:
        stop.set()
        sampler.close()
        busy.join(2)
        idle.join(2)
    recent = dict(sampler.recent(10))
    assert recent.get("busy-worker", 0) > .01
    assert recent.get("idle-worker", 0) < recent["busy-worker"] / 10
    report = (tmp_path / "thread-cpu.log").read_text()
    assert "busy-worker" in report and "of one core" in report
    # Charged to the code it was running (outside decompnet: the innermost frame's file and function)
    assert "test_thread_cpu.py:_busy" in report


def test_the_location_is_the_innermost_decompnet_frame(tmp_path):
    import sys
    # Outside decompnet: the innermost frame's file and function
    assert thread_cpu._location(sys._getframe()) == "test_thread_cpu.py:test_the_location_is_the_innermost_decompnet_frame"
    holder = {}

    def read():  # called from Sampler.start, decompnet's code: that frame is the location, not this one
        holder["frame"] = sys._getframe()
        return {}
    thread_cpu.Sampler(tmp_path / "unused.log", read=read).start().close()
    assert thread_cpu._location(holder["frame"]) == "thread_cpu.py:start"


def test_the_stall_report_lists_processor_time_by_thread(tmp_path):
    from decompnet.hang_diagnostics import Watchdog
    stop = threading.Event()
    busy = threading.Thread(target=_busy, args=(stop,), name="busy-during-stall", daemon=True)
    sampler = thread_cpu.Sampler(tmp_path / "thread-cpu.log", interval=.05)
    busy.start()
    sampler.start()
    try:
        time.sleep(.4)
        report = tmp_path / "event-loop-stacks.log"
        Watchdog(report, cpu=sampler).capture(2.0)
    finally:
        stop.set()
        sampler.close()
        busy.join(2)
    text = report.read_text()
    assert "Processor time by thread" in text and "busy-during-stall" in text


def test_off_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("DECOMP_THREAD_CPU", "0")
    assert thread_cpu.start(tmp_path / "thread-cpu.log") is None
