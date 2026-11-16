"""The home's instance lock, freed from processes a dead app left holding it.

`cli.run` holds `.decomp/server-instance.writer.lock` (an flock) while the app starts and runs, so a second launch
for the same home waits instead of opening the databases twice. A child forked from the app shares that lock until
it execs or ends. On 16 November 2026 ten forks that never reached exec (spawner.py says why) outlived their
killed parent, still holding it: every relaunch said "already starting for this home" until they were killed by
hand.

The app that takes the lock writes its pid beside it (`<lock>.owner`). A fork shares the lock but isn't that pid.
So when the recorded owner is gone and the lock is still held, whoever holds it is a leftover of a dead app: the
next launch stops those processes and starts. A second app that is really starting (a long database upgrade) owns
the lock under its own live pid and is never touched.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

CLEAR_WAIT_SECONDS = 5.0


def owner_file(lock: Path) -> Path:
    return lock.with_name(lock.name + '.owner')


def claim(lock: Path) -> None:
    """Record this process as the lock's owner (called once the lock is held)."""
    try:
        owner_file(lock).write_text(str(os.getpid()), encoding='utf-8')
    except OSError:
        pass


def owner(lock: Path) -> int | None:
    try:
        return int(owner_file(lock).read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None


def alive(pid: int) -> bool:
    if os.name != 'nt':
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:  # our own child that has ended (tests): reap it
                return False
        except ChildProcessError:
            pass
        except OSError:
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def holders(lock: Path) -> list[int]:
    """The processes with the lock file open (lsof; none where it isn't available)."""
    lsof = shutil.which('lsof') or next((p for p in ('/usr/sbin/lsof', '/usr/bin/lsof') if os.path.exists(p)), None)
    if os.name == 'nt' or not lsof:
        return []
    try:
        out = subprocess.run([lsof, '-t', '--', str(lock)], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return sorted({int(line) for line in out.split() if line.strip().isdigit()} - {os.getpid()})


def stale_holders(lock: Path) -> list[int]:
    """Holders of a lock whose recorded owner is dead: forks of an app that has ended. Empty when the owner lives
    or was never recorded (an app from before this file, or one still starting)."""
    recorded = owner(lock)
    if recorded is None or recorded == os.getpid() or alive(recorded):
        return []
    return [pid for pid in holders(lock) if pid != recorded]


def hung_owner(lock: Path) -> int | None:
    """The recorded owner when it is alive and still holds the lock: this home's app (not a reused pid)."""
    recorded = owner(lock)
    if recorded is None or recorded == os.getpid() or not alive(recorded):
        return None
    return recorded if recorded in holders(lock) else None


def end(pid: int, grace: float, *, log=print) -> bool:
    """Ask a process to end (SIGTERM), end it (SIGKILL) if it hasn't after `grace` seconds; whether it ended."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, CLEAR_WAIT_SECONDS)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        if sig == signal.SIGKILL:
            log(f'It did not stop within {grace:.0f} s; ending it (pid {pid}).')
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not alive(pid):
                return True
            time.sleep(0.1)
    return not alive(pid)


def clear(lock: Path, log=print, *, wait: float = CLEAR_WAIT_SECONDS) -> list[int]:
    """Stop the processes a dead app left holding its lock; returns their pids."""
    stale = stale_holders(lock)
    if not stale:
        return []
    log(f'A previous decompdecomp for this home has ended but left {len(stale)} process(es) holding its lock '
        f'({", ".join(map(str, stale))}); stopping them.')
    for pid in stale:
        try:
            os.kill(pid, signal.SIGKILL)  # a fork stuck before exec doesn't answer SIGTERM
        except OSError:
            pass
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and any(alive(pid) for pid in stale):
        time.sleep(0.05)
    return stale


if __name__ == '__main__':  # pragma: no cover - a manual check: python -m decompnet.app.instance_lock <lock>
    print(stale_holders(Path(sys.argv[1])))
