"""Persistent local Ghidra workers for ROM consoles with authoritative function boundaries (#133).

The headless loader imports once; each request disassembles just its function and applies its current header.
JSON replies have a prefix because Ghidra also writes logs to stdout. Readers never block the app's main thread.
No game bytes or source leave the computer. Workers and their decompiler children end when the app quits.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from importlib import resources
from pathlib import Path

from ..hostenv import NO_WINDOW
from ..limits import capped
from ..network import NetworkError
from . import ghidra

START_SECONDS = 180
DRAFT_SECONDS = 90
MAX_WORKERS = 2
_WORKERS = {}
_LOCK = threading.Condition()


class Worker:
    def __init__(self, binary: Path, cancel=None):
        self.lock = threading.Lock()
        self.responses = queue.Queue()
        self.logs = deque(maxlen=25)
        self.serial = 0
        self.active = False
        self.temp = tempfile.TemporaryDirectory(prefix='decompdecomp-ghidra-')
        where = Path(self.temp.name)
        script = resources.files('decompnet.forks').joinpath('ghidra_scripts/DecompWtfServe.java')
        (where / 'DecompWtfServe.java').write_text(script.read_text(encoding='utf-8'), encoding='utf-8')
        argv = [str(ghidra._headless()), str(where), 'drafts', '-import', str(binary),
                '-loader', 'ElfLoader', '-loader-imagebase', '0', '-noanalysis', '-scriptPath', str(where), '-postScript', 'DecompWtfServe.java', '-deleteProject']
        env = ghidra._env()
        # Java on macOS ignores HOME. Keep its preferences, script cache and temporary files with this worker.
        env['JAVA_TOOL_OPTIONS'] = (env.get('JAVA_TOOL_OPTIONS', '')
                                   + f' -Duser.home="{where}" -Djava.io.tmpdir="{where}"').strip()
        try:
            self.proc = subprocess.Popen(capped(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                         env=env, creationflags=NO_WINDOW,
                                         start_new_session=os.name != 'nt')
        except OSError as error:
            self.temp.cleanup()
            raise NetworkError(f"Ghidra couldn't start: {error}. Check Compilers & tools in Setup.") from error
        threading.Thread(target=self._read, daemon=True, name='ghidra-replies').start()
        self.ready = False

    def initialize(self, cancel=None):
        try:
            if not self._reply(START_SECONDS, cancel).get('ready'):
                raise NetworkError('Ghidra did not become ready. Check its draft log and retry.')
            self.ready = True
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.proc.stdout:
                if line.startswith('DECOMP_REPLY:'):
                    try:
                        self.responses.put(json.loads(line.removeprefix('DECOMP_REPLY:')))
                    except ValueError:
                        self.logs.append(line.strip())
                else:
                    self.logs.append(line.strip())
        finally:
            self.responses.put(None)

    def _reply(self, timeout, cancel=None):
        until = time.monotonic() + timeout
        while True:
            if cancel is not None and cancel():
                self.close()
                raise NetworkError('Ghidra drafting was stopped.')
            remaining = until - time.monotonic()
            if remaining <= 0:
                self.close()
                raise NetworkError(f'Ghidra took longer than {timeout} seconds and was stopped. Try a smaller function.')
            try:
                result = self.responses.get(timeout=min(1, remaining))
                break
            except queue.Empty:
                continue
        if result is None:
            raise NetworkError('Ghidra stopped before returning a draft: ' + '\n'.join(self.logs)[-2000:])
        return result

    def ask(self, request, cancel=None):
        with self.lock:
            self.serial += 1
            request = {**request, 'id': self.serial}
            try:
                self.proc.stdin.write(json.dumps(request) + '\n')
                self.proc.stdin.flush()
            except (OSError, ValueError) as error:
                raise NetworkError(f'Ghidra disconnected: {error}. Retry the draft.') from error
            result = self._reply(DRAFT_SECONDS, cancel)
            if result.get('id') != self.serial:
                self.close()
                raise NetworkError('Ghidra returned an out-of-order draft. Retry the draft.')
            if 'error' in result:
                raise NetworkError('Ghidra could not draft this function with the current header: ' + result['error'][:1200])
            return result.get('c') or ''

    def close(self):
        if self.proc.poll() is None:
            try:
                if os.name == 'nt':
                    from ..hostenv import stop_process_tree
                    stop_process_tree(self.proc)
                else:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                    self.proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                if os.name != 'nt':
                    try:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                    except OSError:
                        pass
                self.proc.wait(timeout=5)
        self.temp.cleanup()


def request(binary: Path, payload: dict, cancel=None, generation=None) -> str:
    """Lease a worker: at most two JVMs; another project can reuse capacity once an idle worker is evicted."""
    stat = binary.stat()
    key = (str(binary.resolve()), generation, stat.st_size, stat.st_mtime_ns)
    with _LOCK:
        while True:
            if cancel is not None and cancel():
                raise NetworkError('Ghidra drafting was stopped.')
            current = _WORKERS.get(key)
            if current is not None and current.active:
                _LOCK.wait(timeout=0.25)
                continue
            if current is not None and current.proc.poll() is not None:
                current.close()
                del _WORKERS[key]
                current = None
            if current is None:
                if len(_WORKERS) >= MAX_WORKERS:
                    idle = next((k for k, w in _WORKERS.items() if not w.active), None)
                    if idle is None:
                        _LOCK.wait(timeout=0.25)
                        continue
                    _WORKERS.pop(idle).close()
                current = _WORKERS[key] = Worker(binary, cancel)
            current.active = True
            break
    try:
        if not current.ready:
            current.initialize(cancel)
        return current.ask(payload, cancel)
    finally:
        with _LOCK:
            current.active = False
            _LOCK.notify_all()


def close_all():
    with _LOCK:
        for current in list(_WORKERS.values()):
            current.close()
        _WORKERS.clear()


atexit.register(close_all)
