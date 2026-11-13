"""Cancellable export verification, including the compiler subprocess tree."""
from __future__ import annotations

import os
import signal
import subprocess
import time

from ..hostenv import NO_WINDOW, stop_process_tree
from ..limits import capped


def run(argv, cwd, log, timeout=3600):
    """Wait in short intervals so Stop also works during an otherwise silent rebuild."""
    started = time.monotonic()
    with subprocess.Popen(capped(argv), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW,
                          start_new_session=os.name != 'nt') as proc:
        try:
            while True:
                if hasattr(log, 'check_cancel'):log.check_cancel()
                if time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    stdout, stderr = proc.communicate(timeout=.25)
                    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if proc.poll() is None:
                if os.name != 'nt':
                    try:os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:pass
                stop_process_tree(proc)
