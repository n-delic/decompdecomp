"""Cancellable export verification, including the compiler subprocess tree."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from ..hostenv import NO_WINDOW, stop_process_tree
from ..limits import capped

BUILD_PROGRESS = 'DECOMP_EXPORT_BUILD_PROGRESS'
EXPORT_STEPS = 5


def progress_step(log, step, phase, **counts):
    if hasattr(log, 'progress'):
        log.progress({'step': step, 'steps': EXPORT_STEPS, 'phase': phase, **counts})


def report_build(step, phase, **counts):
    """Called by generated build scripts when verification supplies a private progress file."""
    if path := os.environ.get(BUILD_PROGRESS):
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({'step': step, 'phase': phase, **counts}) + '\n')


def verify(argv, cwd, log):
    progress_step(log, 4, 'Rebuilding exported image')
    if not hasattr(log, 'progress'):
        return run(argv, cwd, log)
    return run(argv, cwd, log, progress=lambda event: progress_step(log, **event))


def run(argv, cwd, log, timeout=3600, *, progress=None):
    """Poll the build and its counters without interfering with stdout or process-tree cancellation."""
    if progress is None:
        return _run(argv, cwd, log, timeout)
    with tempfile.TemporaryDirectory(prefix='decomp-build-progress-') as temp:
        path = Path(temp) / 'progress'
        path.touch()
        with path.open('rb') as stream:
            return _run(argv, cwd, log, timeout, {**os.environ, BUILD_PROGRESS: str(path)}, stream, progress)


def _run(argv, cwd, log, timeout, env=None, stream=None, progress=None):
    started = time.monotonic()
    buffer = b''
    with subprocess.Popen(capped(argv), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW,
                          start_new_session=os.name != 'nt', env=env) as proc:
        try:
            while True:
                if hasattr(log, 'check_cancel'):log.check_cancel()
                if time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    stdout, stderr = proc.communicate(timeout=.25)
                except subprocess.TimeoutExpired:
                    result = None
                else:
                    result = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
                if stream is not None:
                    *events, buffer = (buffer + stream.read()).split(b'\n')
                    for event in events:
                        progress(json.loads(event))
                if result is not None:
                    if hasattr(log, 'check_cancel'):log.check_cancel()
                    return result
        finally:
            if proc.poll() is None:
                if os.name != 'nt':
                    try:os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:pass
                stop_process_tree(proc)
