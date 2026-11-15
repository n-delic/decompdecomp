"""Track only compiler groups launched by this app; verify process birth times before orphan cleanup."""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
from pathlib import Path

_folder: Path | None = None
_lock = threading.Lock()
_groups: dict[int, dict] = {}
_started = False


def snapshot() -> dict[int, tuple[int, str]]:
    if os.name == 'nt':
        return {}
    try:
        from .spawner import run
        out = run(['ps', '-axo', 'pid=,pgid=,lstart='], capture_output=True, text=True, timeout=2)
        rows = {}
        for line in out.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3:
                rows[int(parts[0])] = (int(parts[1]), parts[2].strip())
        return rows
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def cleanup(folder: Path) -> int:
    """Only kill a recorded group whose previous owner is dead and a member's birth identity still matches."""
    if os.name == 'nt':
        return 0
    current = snapshot()
    killed = 0
    for file in folder.glob('*.json'):
        try:
            record = json.loads(file.read_text())
            owner = record['owner']
            if owner in current and current[owner][1] == record.get('owner_birth'):
                continue
            group = record['group']
            if any(current.get(int(pid)) == (group, birth) for pid, birth in record['members'].items()):
                os.killpg(group, signal.SIGKILL)
                killed += 1
            file.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return killed


def configure(folder: Path):
    global _folder, _started
    folder.mkdir(parents=True, exist_ok=True)
    cleanup(folder)
    _folder = folder
    if not _started and os.name != 'nt':
        _started = True
        threading.Thread(target=_monitor, daemon=True, name='compiler-process-tracker').start()


def track(pid: int):
    if _folder is None or os.name == 'nt':
        return
    with _lock:
        _groups[pid] = {'owner': os.getpid(), 'group': pid, 'members': {}}
    # Sample the active groups together once a second; spawning ps per compile slows permutation loops.


def _record():
    current = snapshot()
    if not current:
        return
    with _lock:
        for pid, record in _groups.items():
            record['owner_birth'] = current.get(os.getpid(), (0, None))[1]
            record['members'] = {str(p): birth for p, (group, birth) in current.items() if group == pid}
            if _folder:
                temp = _folder / f'{pid}.tmp'
                temp.write_text(json.dumps(record))
                temp.replace(_folder / f'{pid}.json')


def untrack(pid: int):
    with _lock:
        _groups.pop(pid, None)
        if _folder:
            (_folder / f'{pid}.json').unlink(missing_ok=True)


def _monitor():
    while True:
        threading.Event().wait(1)
        try:
            with _lock:
                active = bool(_groups)
            if active:
                _record()
        except OSError:
            pass  # Tracking must not turn a disk error into a compiler failure.


def finish(proc, *, failed=False):
    """Reap the child and orphan group without hiding an existing failure.

    A denied group signal must still attempt direct-child cleanup, close pipes
    and release tracking. A cleanup failure on an otherwise successful build
    remains an error; log only process metadata, never command/source text.
    """
    cleanup_error = None
    stage = None
    def remember(where, error):
        nonlocal cleanup_error, stage
        if cleanup_error is None:
            cleanup_error, stage = error, where
    try:
        if os.name != 'nt':
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as error:
                remember('group_signal', error)
        if proc.poll() is None:
            from .hostenv import stop_process_tree
            try:stop_process_tree(proc)
            except OSError as error:remember('child_stop', error)
    finally:
        try:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:stream.close()
                    except OSError as error:remember('pipe_close', error)
        finally:
            try:untrack(proc.pid)
            except OSError as error:remember('untrack', error)
    if cleanup_error is not None:
        logging.getLogger('decompdecomp').warning(
            'Compiler process cleanup failed pid=%d errno=%s; primary_failure=%s stage=%s',
            proc.pid, cleanup_error.errno, failed, stage)
        if not failed:
            raise cleanup_error
