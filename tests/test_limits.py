"""Limits on the tools decompdecomp runs (limits.py)."""

import subprocess
import sys
import time

import pytest

from decompnet import compiler, limits
from decompnet.forks.project import ForkProject


@pytest.mark.skipif(sys.platform == "win32", reason="no core dumps on Windows")
def test_the_tools_the_app_starts_dump_no_core():
    """A compiler aborting under wibo put up the desktop's crash dialog: what the app starts inherits no core dumps."""
    code = "from decompnet import limits; import subprocess; limits.no_core_dumps(); " \
           "print(subprocess.run(['sh', '-c', 'ulimit -c'], capture_output=True, text=True).stdout.strip())"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "0"


def test_compiler_timeout_stops_its_child(tmp_path):
    marker = tmp_path / 'child-finished'
    child = f"import time,pathlib; time.sleep(0.8); pathlib.Path({str(marker)!r}).write_text('finished')"
    parent = f"import subprocess,time,sys; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(3)"
    cfg = compiler.CompilerConfig(cmd=[], sandbox='none')
    with pytest.raises(subprocess.TimeoutExpired):
        compiler._exec(cfg, tmp_path, [sys.executable, '-c', parent], timeout=0.3)
    time.sleep(0.9)
    assert not marker.exists()


def test_project_build_timeout_stops_its_child(tmp_path):
    marker = tmp_path / 'child-finished'
    child = f"import time,pathlib; time.sleep(0.8); pathlib.Path({str(marker)!r}).write_text('finished')"
    parent = f"import subprocess,time,sys; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(3)"
    ok, message = ForkProject(tmp_path).run([sys.executable, '-c', parent], timeout=0.3)
    assert not ok and 'timed out' in message
    time.sleep(0.9)
    assert not marker.exists()


def test_project_build_timeout_when_parent_exits_but_child_holds_pipe(tmp_path):
    marker = tmp_path / 'child-finished'
    child = f"import time,pathlib; time.sleep(0.8); pathlib.Path({str(marker)!r}).write_text('finished')"
    parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
    ok, message = ForkProject(tmp_path).run([sys.executable, '-c', parent], timeout=0.3)
    assert not ok and 'timed out' in message
    time.sleep(0.9)
    assert not marker.exists()


def test_compiler_limit_configuration(monkeypatch):
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', '64')
    monkeypatch.setenv('DECOMP_COMPILER_TIMEOUT_SECONDS', '12')
    assert limits.compiler_memory_limit() == 64 << 20
    assert limits.compiler_time_limit() == 12


@pytest.mark.skipif(sys.platform != 'darwin', reason='RSS watchdog applies to native macOS processes')
def test_mac_compiler_memory_limit_stops_process(tmp_path, monkeypatch):
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', '16')
    cfg = compiler.CompilerConfig(cmd=[], sandbox='none')
    result = compiler._exec(cfg, tmp_path,
                            [sys.executable, '-c', 'import time; data=bytearray(64 << 20); time.sleep(3)'],
                            timeout=5)
    assert result.returncode != 0
    assert 'exceeded the 16 MiB memory limit' in result.stderr
