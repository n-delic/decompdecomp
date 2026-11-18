"""The test suite's own guards (tests/conftest.py): a runaway test can't take the machine's memory."""
import subprocess
import sys
import textwrap

import pytest

from tests import conftest


def test_the_memory_guard_reads_this_process_footprint():
    used = conftest.memory_mb()
    if used is None:
        pytest.skip("this system has no footprint reading")
    assert 1 < used < 1_000_000


def test_a_process_over_the_limit_names_its_test_and_ends(tmp_path):
    if conftest.memory_mb() is None:
        pytest.skip("this system has no footprint reading")
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from tests import conftest
        conftest._running["test"] = "tests/test_example.py::test_runaway"
        hold = bytearray(300 * 2**20)  # (touched: counts as used)
        for i in range(0, len(hold), 4096): hold[i] = 1
        conftest._watch_memory(limit_mb=100, interval=0.05)
    """) % str(conftest._CHECKOUT)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 70
    assert "tests/test_example.py::test_runaway used" in done.stderr and "over the 100 MB" in done.stderr
    assert "most recent call first" in done.stderr  # (every thread's stack follows)
