"""Limits on the tools decompdecomp runs (limits.py)."""

import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="no core dumps on Windows")
def test_the_tools_the_app_starts_dump_no_core():
    """A compiler aborting under wibo put up the desktop's crash dialog: what the app starts inherits no core dumps."""
    code = "from decompnet import limits; import subprocess; limits.no_core_dumps(); " \
           "print(subprocess.run(['sh', '-c', 'ulimit -c'], capture_output=True, text=True).stdout.strip())"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "0"
