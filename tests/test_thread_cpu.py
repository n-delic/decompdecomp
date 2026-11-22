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
    # the stdlib or test code beneath it is noted, not mistaken for the location
    assert thread_cpu._location(holder["frame"]) == "thread_cpu.py:start (in test_thread_cpu.py:read)"


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


def test_library_code_is_named_by_its_module(tmp_path):
    """The proposal workers spend their time in decomprepair; the report says which module, not the caller."""
    source = tmp_path / "decomprepair" / "example_repairs.py"
    source.parent.mkdir()
    source.write_text("import sys\ndef rewrite():\n    return sys._getframe()\n")
    namespace = {}
    exec(compile(source.read_text(), str(source), "exec"), namespace)
    assert thread_cpu._location(namespace["rewrite"]()) == "decomprepair/example_repairs.py:rewrite"
    assert thread_cpu._library("/x/site-packages/decompassets/rnc.py") == "decompassets/rnc.py"
    assert thread_cpu._library("/x/site-packages/other/rnc.py") is None


def test_every_process_reports_and_the_figures_add_up(tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMP_HOME", str(tmp_path))
    stop = threading.Event()
    busy = threading.Thread(target=_busy, args=(stop,), name="busy-worker", daemon=True)
    busy.start()
    sampler = thread_cpu.start_worker("proposal", interval=.05)
    assert sampler is not None
    try:
        time.sleep(.5)
    finally:
        stop.set()
        busy.join(2)
        sampler.close()
    folder = tmp_path / ".decomp" / "logs"
    assert (folder / f"thread-cpu-proposal-{__import__('os').getpid()}.log").exists()
    report = thread_cpu.merged(folder)
    assert "proposal (1 processes)" in report and "test_thread_cpu.py:_busy" in report


def test_old_helper_reports_are_forgotten(tmp_path):
    import os
    old, new = tmp_path / "thread-cpu-proposal-1.log", tmp_path / "thread-cpu-proposal-2.json"
    old.write_text("x")
    new.write_text("{}")
    os.utime(old, (time.time() - 3 * 86400,) * 2)
    thread_cpu._forget_old(tmp_path)
    assert not old.exists() and new.exists()


def test_a_database_call_is_named_by_the_code_that_asked(tmp_path):
    """payload_storage's wrapper holds every query's CPU; the report names the callers instead (live run: 80 of 340 s)."""
    forwarder = tmp_path / "payload_storage.py"
    forwarder.write_text("import sys\ndef call(inner):\n    return inner()\n")
    (tmp_path / "decomprepair").mkdir()
    asker = tmp_path / "decomprepair" / "asks.py"
    asker.write_text("def ask(run):\n    return run()\n")
    namespace, wrapper = {}, {}
    exec(compile(forwarder.read_text(), str(thread_cpu._PACKAGE + "payload_storage.py"), "exec"), wrapper)
    exec(compile(asker.read_text(), str(asker), "exec"), namespace)
    import sys
    seen = namespace["ask"](lambda: wrapper["call"](lambda: thread_cpu._location(sys._getframe())))
    assert seen == "sql: decomprepair/asks.py:ask"
