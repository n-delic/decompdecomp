import os
import subprocess
import sys

import pytest

from decompnet import limits


def test_limit_configuration(monkeypatch):
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', '512')
    assert limits.compiler_memory_limit() == 512 * 1024 * 1024
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', 'invalid')
    assert limits.compiler_memory_limit() == 2048 * 1024 * 1024


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS process tree watchdog')
def test_watchdog_stops_memory_hungry_child_and_parent():
    child = 'import time; a=bytearray(80*1024*1024); time.sleep(15)'
    parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",sys.argv[1]]); time.sleep(15)'
    proc = subprocess.Popen([sys.executable, '-c', parent, child], start_new_session=True)
    try:
        with limits.CompilerMemoryWatchdog(proc, limit=40 * 1024 * 1024) as watchdog:
            proc.wait(timeout=10)
        assert watchdog.exceeded
        assert 'memory limit' in watchdog.message()
    finally:
        if proc.poll() is None:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_process_tree_parses_descendants(monkeypatch):
    class Result:
        stdout = '10 1 100\n11 10 200\n12 11 300\n90 1 900\n'
    monkeypatch.setattr(limits.subprocess, 'run', lambda *args, **kwargs: Result())
    assert dict(limits._process_memory_tree(10)) == {10: 102400, 11: 204800, 12: 307200}


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS process tree watchdog')
def test_compiler_reports_memory_stop(tmp_path, monkeypatch):
    from decompnet.compiler import CompilerConfig, _exec
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', '40')
    script = 'import time; a=bytearray(80*1024*1024); time.sleep(15)'
    result = _exec(CompilerConfig(cmd=[]), tmp_path, [sys.executable, '-c', script], 10)
    assert result.returncode != 0
    assert 'exceeded the 40 MiB memory limit' in result.stderr


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS process tree watchdog')
def test_imported_project_build_reports_memory_stop(tmp_path, monkeypatch):
    from decompnet.forks.project import ForkProject
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', '40')
    monkeypatch.setattr(ForkProject, '_docker_only_macos', lambda _: False)
    project = ForkProject(tmp_path)
    script = 'import time; a=bytearray(80*1024*1024); time.sleep(15)'
    ok, output = project.run([sys.executable, '-c', script])
    assert not ok
    assert 'exceeded the 40 MiB memory limit' in output


@pytest.mark.skipif(os.name == 'nt', reason='POSIX isolated process groups')
def test_wall_timeout_kills_driver_and_child(tmp_path):
    from decompnet.compiler import CompilerConfig, _exec
    child = tmp_path / 'child.pid'
    script = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); open(sys.argv[1],"w").write(str(p.pid)); time.sleep(60)'
    with pytest.raises(subprocess.TimeoutExpired):
        _exec(CompilerConfig(cmd=[]), tmp_path, [sys.executable, '-c', script, str(child)], .4)
    pid = int(child.read_text())
    # A reparented zombie is dead too; it must not keep consuming CPU.
    out = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
    assert out.returncode != 0 or out.stdout.strip().startswith('Z')


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process registry')
def test_orphan_cleanup_checks_birth_and_owner(tmp_path, monkeypatch):
    import json

    from decompnet import compiler_processes as tracking
    records = [
        {'owner':10,'owner_birth':'old','group':20,'members':{'21':'child-birth'}},
        {'owner':11,'owner_birth':'alive','group':30,'members':{'31':'other'}},
        {'owner':12,'owner_birth':'dead','group':40,'members':{'41':'old-birth'}},
    ]
    for i, record in enumerate(records): (tmp_path / f'{i}.json').write_text(json.dumps(record))
    monkeypatch.setattr(tracking, 'snapshot', lambda: {21:(20,'child-birth'),11:(1,'alive'),31:(30,'other'),41:(40,'reused-pid')})
    killed = []
    monkeypatch.setattr(tracking.os, 'killpg', lambda pid, sig: killed.append(pid))
    assert tracking.cleanup(tmp_path) == 1
    assert killed == [20]  # Neither another running app nor a reused PID is terminated.


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-group timeout regression')
def test_silent_imported_build_times_out_while_reading_stdout(tmp_path, monkeypatch):
    from decompnet.forks.project import ForkProject
    monkeypatch.setattr(ForkProject, '_docker_only_macos', lambda _: False)
    script = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])'
    ok, output = ForkProject(tmp_path).run([sys.executable, '-c', script], timeout=.5)
    assert not ok and 'timed out' in output


def test_cancelled_compiler_closes_pipes_even_while_exception_is_retained(tmp_path,monkeypatch):
    from decompnet import compiler
    popen=subprocess.Popen;processes=[]
    def start(*args,**kwargs):
        proc=popen(*args,**kwargs);processes.append(proc);return proc
    monkeypatch.setattr(compiler.subprocess,'Popen',start)
    def cancel():raise RuntimeError('cancelled')
    with compiler.cancellable(cancel), pytest.raises(RuntimeError,match='cancelled'):
        compiler._exec(compiler.CompilerConfig(cmd=[],sandbox='none'),tmp_path,
                       [sys.executable,'-c','import time;time.sleep(30)'],10)
    # Retaining Popen must not retain its stdout/stderr descriptors after cancellation.
    proc=processes[0]
    assert proc.poll() is not None
    assert proc.stdout.closed and proc.stderr.closed


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process group cleanup')
@pytest.mark.parametrize('cancelled', [False, True])
def test_denied_group_cleanup_preserves_cancellation_closes_pipes_and_unregisters(tmp_path, monkeypatch, caplog, cancelled):
    import errno
    import logging

    from decompnet import compiler, compiler_processes
    from decompnet.app.deep_permuter import SearchLimit
    processes=[]
    popen=subprocess.Popen
    def start(*args,**kwargs):
        proc=popen(*args,**kwargs);processes.append(proc);return proc
    monkeypatch.setattr(compiler.subprocess,'Popen',start)
    def deny_group(pid,sig):raise PermissionError(errno.EPERM,'Synthetic signal permission denied')
    monkeypatch.setattr(compiler.os,'killpg',deny_group)
    removed=[]
    monkeypatch.setattr(compiler_processes,'untrack',removed.append)
    def check():
        if cancelled:raise SearchLimit('Time limit reached')
    try:
        with compiler.cancellable(check), caplog.at_level(logging.WARNING,logger='decompdecomp'):
            with pytest.raises(SearchLimit if cancelled else PermissionError,match='Time limit reached' if cancelled else 'Synthetic signal permission denied'):
                compiler._exec(compiler.CompilerConfig(cmd=[],sandbox='none'),tmp_path,
                               [sys.executable,'-c','import time;time.sleep(30)' if cancelled else 'pass'],10)
        proc=processes[0]
        assert proc.poll() is not None
        assert proc.stdout.closed and proc.stderr.closed
        assert removed == [proc.pid]
        assert 'Compiler process cleanup failed' in caplog.text
        assert f'primary_failure={cancelled}' in caplog.text
    finally:
        # The same regression can be run against the broken cleanup implementation.
        for proc in processes:
            compiler.stop_process_tree(proc)
            for stream in (proc.stdout,proc.stderr):
                if stream is not None:stream.close()
