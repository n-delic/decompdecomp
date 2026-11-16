"""A dead app's leftovers can't keep the next launch out, and a quit or stop always ends the app (instance_lock.py).

16 November 2026: the app hung, needed SIGKILL, and ten forks it left (never exec'd) kept holding the home's
instance lock, so every relaunch said "already starting for this home"."""
import os
import shutil
import subprocess
import sys
import time

import pytest

from decompnet.app import cli, instance_lock
from decompnet.app.server import QUIT_DEADLINE_SECONDS

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='flock and lsof: POSIX')
needs_lsof = pytest.mark.skipif(not (shutil.which('lsof') or os.path.exists('/usr/sbin/lsof')), reason='needs lsof')

# Holds the lock as a fork of the app would: the same open file, flocked, and never lets go.
HOLDER = ('import fcntl, os, sys, time\n'
          'fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)\n'
          'fcntl.flock(fd, fcntl.LOCK_EX)\n'
          'print("held", flush=True)\n'
          'time.sleep(120)\n')


def _holder(lock):
    proc = subprocess.Popen([sys.executable, '-c', HOLDER, str(lock)], stdout=subprocess.PIPE)
    assert proc.stdout.readline() == b'held\n'
    return proc


def _dead_pid():
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait()
    return proc.pid


def _free(lock):
    import fcntl
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


@needs_lsof
def test_a_dead_apps_leftover_holders_are_stopped_and_the_lock_is_free(tmp_path):
    lock = tmp_path / 'server-instance.writer.lock'
    holder = _holder(lock)
    try:
        instance_lock.owner_file(lock).write_text(str(_dead_pid()))
        assert not _free(lock)
        lines = []
        assert instance_lock.clear(lock, log=lines.append) == [holder.pid]
        assert not instance_lock.alive(holder.pid)  # (it reaps our child: Popen sees no exit status)
        assert _free(lock) and 'left 1 process' in lines[0]
    finally:
        holder.kill()


@needs_lsof
def test_an_app_that_is_still_starting_is_never_touched(tmp_path):
    """A second launch during a long database upgrade: the owner is alive and holds the lock itself."""
    lock = tmp_path / 'server-instance.writer.lock'
    holder = _holder(lock)
    try:
        instance_lock.owner_file(lock).write_text(str(holder.pid))
        assert instance_lock.clear(lock, log=lambda _: None) == []
        assert holder.poll() is None and not _free(lock)
        assert instance_lock.hung_owner(lock) == holder.pid
    finally:
        holder.kill()


@needs_lsof
def test_without_a_recorded_owner_nothing_is_stopped(tmp_path):
    """An app from before the owner file: the launch says it's starting, as before."""
    lock = tmp_path / 'server-instance.writer.lock'
    holder = _holder(lock)
    try:
        assert instance_lock.clear(lock, log=lambda _: None) == []
        assert holder.poll() is None
    finally:
        holder.kill()


@needs_lsof
def test_a_reused_pid_is_not_taken_for_the_app(tmp_path):
    """The recorded owner's pid is alive but doesn't hold the lock: some other process got that pid."""
    lock = tmp_path / 'server-instance.writer.lock'
    lock.touch()
    other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        instance_lock.owner_file(lock).write_text(str(other.pid))
        assert instance_lock.hung_owner(lock) is None
    finally:
        other.kill()
        other.wait()


def test_end_asks_first_then_ends_a_process_that_ignores_it():
    stubborn = subprocess.Popen([sys.executable, '-c', 'import signal, time\n'
                                 'signal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint("ready", flush=True)\n'
                                 'time.sleep(60)\n'], stdout=subprocess.PIPE)
    assert stubborn.stdout.readline() == b'ready\n'
    started = time.monotonic()
    assert instance_lock.end(stubborn.pid, 0.5, log=lambda _: None)
    assert not instance_lock.alive(stubborn.pid) and time.monotonic() - started < 8
    polite = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    assert instance_lock.end(polite.pid, 10, log=lambda _: None)
    assert not instance_lock.alive(polite.pid)


def test_a_started_quit_ends_the_process_even_when_a_thread_hangs():
    """Python waits for non-daemon threads at exit; the deadline ends the process regardless."""
    script = ('import threading, time\n'
              'from decompnet.app.shutdown_progress import exit_after\n'
              'threading.Thread(target=time.sleep, args=(60,)).start()\n'
              'exit_after(0.5)\n')
    started = time.monotonic()
    done = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=30)
    assert done.returncode == 1 and b'did not finish in time' in done.stderr
    assert time.monotonic() - started < 15


def test_stop_waits_longer_than_the_quit_deadline():
    """`decomp stop` sends SIGKILL only after the app's own deadline would have ended it."""
    assert QUIT_DEADLINE_SECONDS < cli.QUIT_WAIT_SECONDS
